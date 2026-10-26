#!/usr/bin/env python3
"""Real student P0. Uses no teacher, official solution, training or test split."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time
import tomllib

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))
from rsi_horizon.contracts import ArtifactRef
from rsi_horizon.integrations.appworld_rootless import RootlessAppWorldEnvironment, public_task
from rsi_horizon.model_policy import SYSTEM_PROMPT, PythonAgentStudent, SubprocessModel, parse_python_response
from rsi_horizon.runner import run_episode, write_episode


def write_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def gpu_preflight(gpu_uuid: str) -> dict:
    if not re.fullmatch(r"GPU-[a-f0-9-]+", gpu_uuid):
        raise ValueError("Use the physical GPU UUID, not a guessed cuda index")
    output = subprocess.check_output(["nvidia-smi", "--id=" + gpu_uuid,
        "--query-gpu=uuid,name,memory.total,memory.free,utilization.gpu", "--format=csv,noheader,nounits"], text=True)
    rows = [x.split(", ") for x in output.strip().splitlines()]
    if len(rows) != 1 or rows[0][0] != gpu_uuid:
        raise RuntimeError("Selected GPU could not be resolved uniquely")
    uuid, name, total, free, utilization = rows[0]
    processes = subprocess.check_output(["nvidia-smi", "--query-compute-apps=gpu_uuid,pid", "--format=csv,noheader"], text=True)
    if int(free) < 60000 or int(utilization) > 5 or any(x.strip().startswith(gpu_uuid + ",") for x in processes.splitlines()):
        raise RuntimeError("Selected GPU is busy or has insufficient free memory; no other process was stopped")
    return {"uuid": uuid, "name": name, "total_mib": int(total), "free_mib": int(free),
            "utilization_percent": int(utilization), "checked_at_utc": datetime.now(timezone.utc).isoformat()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--gpu-uuid", required=True)
    parser.add_argument("--profile", choices=("engineering-smoke", "baseline"), default="engineering-smoke")
    parser.add_argument("--model-config", type=Path, default=PROJECT / "configs/models/qwen35_9b.toml")
    parser.add_argument("--environment-config", type=Path, default=PROJECT / "configs/server/appworld_rootless_p0.toml")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_id):
        parser.error("Use a unique safe run id")
    config = tomllib.loads(args.model_config.read_text(encoding="utf-8"))
    world_config = tomllib.loads(args.environment_config.read_text(encoding="utf-8"))
    storage = Path(world_config["paths"]["storage_root"])
    root = storage / "runs/student" / args.run_id
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    backend = env = student = None
    phase = "preflight"
    started = time.monotonic()
    manifest = {"run_id": args.run_id, "profile": args.profile, "condition": "P0-engineering" if args.profile == "engineering-smoke" else "B0",
                "task_id": world_config["task_id"], "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True).strip(),
                "working_tree_clean": not subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT, text=True).strip(),
                "model_id": config["model_id"], "revision": config["revision"], "reviewer_enabled": False,
                "official_solution_loaded": False, "training_performed": False, "test_split_accessed": False,
                "source_sha256": {}}
    for name in ["src/rsi_horizon/runner.py", "src/rsi_horizon/model_policy.py", "src/rsi_horizon/integrations/appworld_rootless.py", "scripts/server/model_worker.py", "scripts/server/run_student_episode.py"]:
        manifest["source_sha256"][name] = hashlib.sha256((PROJECT / name).read_bytes()).hexdigest()
    (root / "config_resolved.toml").write_text(args.environment_config.read_text(encoding="utf-8"), encoding="utf-8")
    write_json(root / "student_resolved.json", config)
    write_json(root / "launch-arguments.json", vars(args) | {"model_config": str(args.model_config), "environment_config": str(args.environment_config)})
    try:
        phase = "model_artifact_verification"
        from prepare_student import verify_model
        artifact_manifest = json.loads((PROJECT / config["artifact_manifest"]).read_text(encoding="utf-8"))
        if artifact_manifest["revision"] != config["revision"]:
            raise ValueError("Model configuration differs from the frozen artifact manifest")
        verified = verify_model(artifact_manifest, Path(config["model_path"]))
        write_json(root / "model-artifacts-verified.json", verified)
        phase = "gpu_preflight"
        gpu = gpu_preflight(args.gpu_uuid)
        write_json(root / "environment_snapshot.json", {"gpu_preflight": gpu, "python": sys.version, "platform": platform.platform()})
        if not manifest["working_tree_clean"]:
            raise RuntimeError("Commit the code before running a recorded experiment")
        phase = "model_startup"
        backend = SubprocessModel(config, config_path=args.model_config.resolve(), gpu_uuid=args.gpu_uuid,
                                  log_path=root / "model-worker.log")
        write_json(root / "model-ready.json", backend.ready)
        phase = "model_protocol_smoke"
        smoke = {k: v for k, v in config["engineering_smoke"].items() if k != "max_steps"}
        smoke["max_new_tokens"] = 128
        response = backend.generate([{"role": "user", "content": "Return exactly one Python code block containing print(2 + 2), with no other text."}], smoke)
        parse_python_response(response["text"], thinking=False)
        write_json(root / "model-smoke.json", response)
        phase = "student_episode"
        task = public_task(world_config)
        student = PythonAgentStudent(backend, config, smoke=args.profile == "engineering-smoke")
        manifest["generation"] = student.generation
        manifest["system_prompt_sha256"] = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
        env = RootlessAppWorldEnvironment(world_config, run_directory=root / "environment", experiment_name=args.run_id)
        steps = config["engineering_smoke"]["max_steps"] if args.profile == "engineering-smoke" else world_config["limits"]["max_steps"]
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("Episode wall budget exhausted")))
        signal.alarm(1800)
        try:
            result, trajectory, audit = run_episode(task, student, env, max_steps=steps)
        finally:
            signal.alarm(0)
        # Even invalid model output should get an independently recorded final state where possible.
        verifier = result.verifier
        if not verifier and env.world is not None and not env.finished:
            verifier = env.verify(task)
        write_episode(root / "episode", result, trajectory, audit,
                      refs={"student": ArtifactRef("model", student.artifact_id), "run": ArtifactRef("run", args.run_id)})
        write_json(root / "verifier_result.json", verifier)
        write_json(root / "run_summary.json", {"result": result.to_dict(), "official_verifier": verifier,
            "model_calls_for_episode": len(student.calls), "model_protocol_smoke_calls": 1,
            "input_tokens": sum(x["input_tokens"] for x in student.calls),
            "output_tokens": sum(x["output_tokens"] for x in student.calls),
            "claim_limit": "single train task; engineering profile is not a formal B0 score"})
        category = "model_output_error" if result.error and "StudentActionError" in result.error else "manual_review_required"
        if result.status in {"infra_error", "verifier_error"}:
            category = result.status
        write_json(root / "failure_taxonomy.json", {"category": "none" if result.success else category,
            "first_deviation": None, "annotation_status": "manual review pending"})
        manifest["status"] = "completed"
        manifest["student_task_success"] = result.success
        manifest["official_success"] = verifier.get("success")
        manifest["pipeline_pass"] = bool(trajectory) and verifier.get("num_tests", 0) > 0 and result.error is None and result.status not in {"infra_error", "verifier_error"}
        print(json.dumps({"run_id": args.run_id, "status": result.status, "steps": result.steps, "official_success": verifier.get("success"), "pipeline_pass": manifest["pipeline_pass"]}), flush=True)
        return 0 if manifest["pipeline_pass"] else 2
    except Exception as exc:
        import traceback
        traceback.print_exc()
        manifest.update({"status": "error", "phase": phase, "error_type": type(exc).__name__, "error": str(exc)})
        return 1
    finally:
        cleanup_errors = []
        for name, resource in [("environment", env), ("model_backend", backend)]:
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:
                    cleanup_errors.append({"resource": name, "error_type": type(exc).__name__, "error": str(exc)})
        if cleanup_errors:
            manifest.update({"status": "error", "pipeline_pass": False, "cleanup_errors": cleanup_errors})
        if student is not None:
            with (root / "model-generations-private.jsonl").open("x", encoding="utf-8") as stream:
                for row in student.generations:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["elapsed_seconds"] = time.monotonic() - started
        write_json(root / "manifest.json", manifest)
        if cleanup_errors:
            raise RuntimeError("Resource cleanup failed; inspect manifest.json")


if __name__ == "__main__":
    raise SystemExit(main())
