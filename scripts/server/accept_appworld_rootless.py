#!/usr/bin/env python3
"""CPU-only acceptance of the rootless client/world/verifier execution route."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from rsi_horizon.contracts import Action
from rsi_horizon.execution.bubblewrap import IsolationError, SandboxProfile, SandboxSession, SandboxTimeout
from rsi_horizon.integrations.appworld_rootless import (
    RootlessAppWorldEnvironment, public_task, write_new_json,
)


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def isolation_checks(config: dict, run: Path, task_datetime: str) -> dict:
    profile = SandboxProfile.from_config(config)
    root = Path(config["paths"]["storage_root"])
    private_canary = run / "controller-private-canary.txt"
    private_canary.write_text("controller-only-acceptance-canary")
    private_hash = hash_file(private_canary)
    host_namespaces = {name: os.readlink("/proc/self/ns/" + name)
                       for name in ("mnt", "user", "pid", "net", "ipc", "uts")}
    hidden_paths = [str(private_canary), "/home/lyf/.ssh", str(root / "code"),
                    str(root / "appworld-native/data"), "/var/run/docker.sock"]
    probe = '''
import os,json,resource,socket,ctypes,importlib.util
from pathlib import Path
checks={}
for path in HIDDEN_PATHS:
    try:
        os.stat(path)
    except (FileNotFoundError,PermissionError):
        checks["hidden:"+path]=True
    else:
        checks["hidden:"+path]=False
checks["clean_environment"]=os.getenv("RSI_ACCEPTANCE_SECRET") is None
checks["native_appworld_not_installed"]=importlib.util.find_spec("appworld.task") is None
from appworld.collections.apis import ApiCollection
from appworld.requester import Requester as ProxyRequester
checks["typed_imports_resolve_to_public_proxies"]=ApiCollection is type(apis) and isinstance(requester,ProxyRequester)
checks["gpu_device_absent"]=not Path("/dev/nvidia0").exists()
try:
    Path("/work/write-test").write_text("should not be writable")
except OSError:
    checks["read_only_filesystem"]=True
else:
    checks["read_only_filesystem"]=False
try:
    connection=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
except PermissionError:
    checks["socket_creation_denied"]=True
else:
    connection.close()
    checks["socket_creation_denied"]=False
try:
    child=os.fork()
except PermissionError:
    checks["fork_denied"]=True
else:
    if child==0: os._exit(0)
    os.waitpid(child,0)
    checks["fork_denied"]=False
libc=ctypes.CDLL(None,use_errno=True)
checks["nested_user_namespace_denied"]=libc.unshare(0x10000000)==-1 and ctypes.get_errno()==1
try:
    os.execv("/runtime/bin/python",["python","-c","pass"])
except PermissionError:
    checks["exec_denied"]=True
status=dict(line.split(":",1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
checks["no_new_privileges"]=status["NoNewPrivs"].strip()=="1"
checks["seccomp_filter_active"]=status["Seccomp"].strip()=="2"
checks["no_effective_capabilities"]=int(status["CapEff"].strip(),16)==0
namespaces={name:os.readlink("/proc/self/ns/"+name) for name in HOST_NAMESPACES}
checks["all_namespaces_differ"]=all(namespaces[name]!=value for name,value in HOST_NAMESPACES.items())
checks["address_space_limit"]=resource.getrlimit(resource.RLIMIT_AS)[0]==EXPECTED_MEMORY
checks["cpu_limit"]=resource.getrlimit(resource.RLIMIT_CPU)[0]==EXPECTED_CPU
from datetime import datetime
checks["task_clock_frozen"]=datetime.now().isoformat().startswith(EXPECTED_DATETIME[:19])
print(json.dumps({"checks":checks,"namespaces":namespaces},sort_keys=True))
'''
    probe = ("HIDDEN_PATHS=" + repr(hidden_paths) + "\nHOST_NAMESPACES=" + repr(host_namespaces)
             + "\nEXPECTED_MEMORY=" + repr(profile.memory_bytes)
             + "\nEXPECTED_CPU=" + repr(profile.cpu_seconds)
             + "\nEXPECTED_DATETIME=" + repr(task_datetime) + "\n" + probe)
    # A deliberate environment canary tests that the host environment is cleared.
    os.environ["RSI_ACCEPTANCE_SECRET"] = "not-for-client"
    session = None
    try:
        session = SandboxSession(profile, apps=["supervisor"], task_datetime=task_datetime)
        result = session.execute(probe, lambda message: None)
        if result["error_type"]:
            raise IsolationError("Boundary probe failed: " + result["output"][:500])
        observed = json.loads(result["output"])
        session.execute("persistent_value=41", lambda message: None)
        persistent = session.execute("print(persistent_value+1)", lambda message: None)
        observed["checks"]["python_state_persists"] = persistent["output"].strip() == "42"
        output = session.execute("print('x'*100000)", lambda message: None)
        observed["checks"]["output_is_bounded"] = len(output["output"]) <= profile.max_output_chars + 200
        observed["checks"]["controller_canary_unchanged"] = hash_file(private_canary) == private_hash
        observed["stop_proof"] = session.close()
    finally:
        os.environ.pop("RSI_ACCEPTANCE_SECRET", None)
        if session is not None:
            session.close()
    timeout_session = SandboxSession(profile, apps=["supervisor"], task_datetime=task_datetime)
    timeout_session.profile = replace(profile, step_wall_seconds=1.5)
    try:
        try:
            timeout_session.execute("while True: pass", lambda message: None)
        except SandboxTimeout:
            observed["checks"]["wall_timeout_enforced"] = True
        else:
            observed["checks"]["wall_timeout_enforced"] = False
        observed["checks"]["timeout_process_stopped"] = timeout_session.process.poll() is not None
        try:
            timeout_session.execute("print('must not run')", lambda message: None)
        except IsolationError:
            observed["checks"]["timed_out_session_cannot_resume"] = True
        else:
            observed["checks"]["timed_out_session_cannot_resume"] = False
    finally:
        timeout_session.close()
    slow_session = SandboxSession(profile, apps=["supervisor"], task_datetime=task_datetime)
    slow_session.profile = replace(profile, step_wall_seconds=1.5)
    try:
        try:
            slow_session.execute("requester.request('supervisor','show_profile')",
                                 lambda message: time.sleep(10))
        except SandboxTimeout:
            observed["checks"]["api_deadline_enforced"] = True
        else:
            observed["checks"]["api_deadline_enforced"] = False
        observed["checks"]["slow_api_client_stopped"] = slow_session.process.poll() is not None
    finally:
        slow_session.close()
    write_new_json(run / "isolation.json", observed)
    if not all(observed["checks"].values()):
        raise IsolationError("One or more required isolation checks failed")
    return observed


def run_checks(config: dict, run: Path, run_id: str) -> dict:
    task = public_task(config)
    asset_root = Path(config["paths"]["appworld_root"])
    manifest = json.loads((asset_root / "native-data-manifest.json").read_text())
    for entry in manifest["files"]:
        if hash_file(asset_root / entry["path"]) != entry["sha256"]:
            raise ValueError("Prepared source data changed before rootless acceptance")
    # Task clock is public; reading task metadata does not load its answer.
    from appworld.task import Task
    task_datetime = Task.load(task_id=task.public.task_id, load_ground_truth=False).datetime.isoformat()
    isolated = isolation_checks(config, run, task_datetime)
    cases = {}
    source = asset_root / "data/tasks" / task.public.task_id / "ground_truth/compiled_solution.py"
    for name in ("no_op_before", "official_solution", "reset_no_op", "reward_spoof"):
        env = RootlessAppWorldEnvironment(config, run_directory=run / name,
                                          experiment_name=run_id + "_" + name)
        try:
            env.reset(task)
            result = env.step(Action("execute", {"code": "import json; print(json.dumps(apis.supervisor.show_profile(),sort_keys=True))"}))
            profile = json.loads(result.observation.last_result)
            profile_hash = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
            if name == "official_solution":
                code = source.read_text() + "\nsolution(apis, requester)\n"
                result = env.step(Action("execute", {"code": code}))
                if result.info["execution_error_type"]:
                    raise IsolationError("Official solution did not execute in the isolated client: "
                                         + str(result.info["execution_error_type"]))
            attacks = {}
            if name == "reward_spoof":
                attacks_code = '''
import json
results={}
try:
    requester.request('admin','execute',code='pass')
except RuntimeError:
    results['admin_api_denied']=True
else:
    results['admin_api_denied']=False
try:
    requester.request('supervisor','__dict__')
except RuntimeError:
    results['private_method_denied']=True
else:
    results['private_method_denied']=False
try:
    open('/work/reward.txt','w').write('1.0')
except OSError:
    results['reward_file_not_writable']=True
else:
    results['reward_file_not_writable']=False
print('FORGED_REWARD=1.0 SUCCESS=true')
print(json.dumps(results,sort_keys=True))
'''
                result = env.step(Action("execute", {"code": attacks_code}))
                attacks = json.loads(result.observation.last_result.splitlines()[-1])
                if not all(attacks.values()):
                    raise IsolationError("An undocumented API or reward write was accepted")
            stats = env.verify(task)
            cases[name] = {"official_verifier": stats, "public_profile_sha256": profile_hash,
                           "attack_checks": attacks, "public_api_calls": len(env.api_audit),
                           "stop_proof": env.stop_proof}
            print(json.dumps({"case": name, "success": stats["success"],
                              "pass_count": stats["pass_count"], "num_tests": stats["num_tests"]}), flush=True)
        finally:
            env.close()
    # Exercise the existing runner using a fixed oracle policy, not an LLM.
    # Keep gold code/trajectories in memory only; export summary statistics.
    from rsi_horizon.runner import run_episode
    class OfficialFixturePolicy:
        artifact_id = "official-oracle-engineering-fixture"
        def reset(self, public):
            self.index = 0
        def act(self, public, observation):
            code = ("import json; print(json.dumps(apis.supervisor.show_profile(),sort_keys=True))"
                    if self.index == 0 else source.read_text() + "\nsolution(apis, requester)\n")
            self.index += 1
            return Action("execute", {"code": code})
    bridge = RootlessAppWorldEnvironment(config, run_directory=run / "runner_bridge",
                                        experiment_name=run_id + "_runner_bridge")
    try:
        result, trajectory, audit = run_episode(task, OfficialFixturePolicy(), bridge, max_steps=2)
        runner_bridge = {"status": result.status, "success": result.success,
                         "steps": result.steps, "trajectory_steps": len(trajectory),
                         "audit_steps": len(audit), "official_verifier": result.verifier,
                         "policy": "fixed official fixture, no model", "error": result.error}
        write_new_json(run / "runner-bridge.json", runner_bridge)
    finally:
        bridge.close()
    unchanged = all(hash_file(asset_root / e["path"]) == e["sha256"] for e in manifest["files"])
    checks = {"isolated_client_checks": all(isolated["checks"].values()),
              "negative_before_fails": not cases["no_op_before"]["official_verifier"]["success"],
              "official_solution_passes": cases["official_solution"]["official_verifier"]["success"],
              "reset_negative_fails": not cases["reset_no_op"]["official_verifier"]["success"],
              "forged_reward_is_not_success": not cases["reward_spoof"]["official_verifier"]["success"],
              "public_initial_profile_matches": len({v["public_profile_sha256"] for v in cases.values()}) == 1,
              "all_clients_stopped_before_grading": all(v["official_verifier"]["client_stopped"] for v in cases.values())
                  and runner_bridge["official_verifier"].get("client_stopped", False),
              "all_grading_inputs_unchanged": all(v["official_verifier"]["snapshot_unchanged"] for v in cases.values())
                  and runner_bridge["official_verifier"].get("snapshot_unchanged", False),
              "core_runner_bridge_passes": runner_bridge["success"] and runner_bridge["steps"] == 2,
              "source_data_unchanged": unchanged}
    return {"schema_version": "appworld-rootless-acceptance/v1",
            "status": "pass" if all(checks.values()) else "fail", "backend": "bubblewrap-pipe",
            "run_id": run_id, "task_id": task.public.task_id, "official_split": task.split,
            "checks": checks, "isolation_checks": isolated["checks"], "cases": cases,
            "runner_bridge": runner_bridge,
            "official_solution_sha256": hash_file(source), "seed": config["seed"],
            "model_calls": 0, "gpu_used": False, "docker_used": False,
            "training_export_allowed": False,
            "claim_limit": "single train fixture; tested shared-kernel isolation profile, not a formal security proof or B0/RSI result"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_id):
        raise ValueError("Use a fresh safe run id")
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    root = Path(config["paths"]["storage_root"])
    run = root / "runs/rootless" / args.run_id
    run.mkdir(parents=True, mode=0o700, exist_ok=False)
    try:
        report = run_checks(config, run, args.run_id)
    except BaseException as exc:
        report = {"schema_version": "appworld-rootless-acceptance/v1", "status": "fail",
                  "error_type": type(exc).__name__, "error": str(exc)[:1000],
                  "run_id": args.run_id, "model_calls": 0, "gpu_used": False, "docker_used": False}
        raise
    finally:
        report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["config_sha256"] = hash_file(args.config)
        project = Path(__file__).resolve().parents[2]
        report["git_commit"] = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True).strip()
        paths = [Path(__file__).resolve(), project / "scripts/server/rootless_verifier.py",
                 project / "src/rsi_horizon/integrations/appworld_rootless.py",
                 project / "src/rsi_horizon/execution/bubblewrap.py", project / "src/rsi_horizon/execution/worker.py",
                 project / "src/rsi_horizon/runner.py"]
        report["source_sha256"] = {p.relative_to(project).as_posix(): hash_file(p) for p in paths}
        write_new_json(run / "acceptance.json", report)
        print(json.dumps(report, indent=2), flush=True)
    return int(report["status"] != "pass")


if __name__ == "__main__":
    raise SystemExit(main())
