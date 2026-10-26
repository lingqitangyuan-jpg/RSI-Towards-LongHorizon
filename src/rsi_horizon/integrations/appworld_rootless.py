"""AppWorld adapter: isolated code -> public API broker -> trusted world.

The client has no AppWorld installation, data, grader, host network or writable
host mount. Official grading runs in a new process only after the client exits.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from ..contracts import Action, BenchmarkTask, Observation, StepOutcome, TaskSpec
from ..execution.bubblewrap import IsolationError, SandboxProfile, SandboxSession


def write_new_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def directory_fingerprint(path: Path) -> dict:
    files = []
    for file in sorted(path.rglob("*")):
        if file.is_symlink():
            raise IsolationError("Symlinks are not allowed in a grading snapshot")
        if file.is_file():
            files.append({"path": file.relative_to(path).as_posix(),
                          "bytes": file.stat().st_size,
                          "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
    if not files:
        raise IsolationError("An empty directory is not a grading snapshot")
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {"sha256": digest, "file_count": len(files), "files": files}


def configure_paths(config: dict) -> None:
    root = Path(config["paths"]["storage_root"]).resolve()
    appworld_root = Path(config["paths"]["appworld_root"]).resolve()
    if not appworld_root.is_relative_to(root):
        raise ValueError("AppWorld assets must stay under the allocated storage root")
    cache = root / "cache/appworld-rootless"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.update({"APPWORLD_ROOT": str(appworld_root), "APPWORLD_CACHE": str(cache),
                       "IPYTHONDIR": str(cache / "ipython"),
                       "TMPDIR": str(root / "tmp"), "CUDA_VISIBLE_DEVICES": "",
                       "PYTHONNOUSERSITE": "1"})


def public_task(config: dict) -> BenchmarkTask:
    configure_paths(config)
    from appworld.task import Task
    root = Path(config["paths"]["appworld_root"])
    manifest = json.loads((root / "native-data-manifest.json").read_text())
    task_id = config["task_id"]
    if manifest["task_id"] != task_id or manifest["official_split"] not in {"train", "dev"}:
        raise ValueError("Only the prepared official train/dev task is permitted")
    loaded = Task.load(task_id=task_id, load_ground_truth=False)
    return BenchmarkTask(TaskSpec(task_id, "appworld", config["data_version"], loaded.instruction),
                         manifest["official_split"], task_id.split("_")[0],
                         step_budget=config["limits"]["max_steps"],
                         annotation_source="official task; dependency depth not annotated")


def validate_public_call(message: dict, docs: dict) -> tuple[str, str, dict]:
    if set(message) != {"kind", "id", "app", "api", "kwargs"} or message["kind"] != "call":
        raise ValueError("Only public API call envelopes are accepted")
    app, api, kwargs = message["app"], message["api"], message["kwargs"]
    if not isinstance(app, str) or not isinstance(api, str) or not isinstance(kwargs, dict):
        raise ValueError("Invalid public API call types")
    if app == "admin" or app not in docs or api not in docs[app]:
        raise PermissionError("Only documented public APIs are available")
    if any(not isinstance(key, str) for key in kwargs):
        raise ValueError("API argument names must be strings")
    return app, api, kwargs


class RootlessAppWorldEnvironment:
    """Implements reset/step/verify for the existing episode runner.

    Always use close() in a finally block around an episode, including when a
    policy or reviewer fails before the runner reaches verify().
    """

    def __init__(self, config: dict, *, run_directory: Path, experiment_name: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", experiment_name):
            raise ValueError("A safe, unique experiment name is required")
        self.config = config
        self.directory = run_directory.resolve()
        root = Path(config["paths"]["storage_root"]).resolve()
        if not self.directory.is_relative_to(root):
            raise ValueError("Run outputs must stay under the allocated storage root")
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        self.experiment_name = experiment_name
        self.world = None
        self.client = None
        self.steps = 0
        self.calls_this_step = 0
        self.api_audit = []
        self.stop_proof = None
        self.finished = False

    def reset(self, task: BenchmarkTask) -> Observation:
        if self.world is not None or self.finished:
            raise IsolationError("Create a new adapter/run directory for each reset")
        if task.public.task_id != self.config["task_id"] or task.split not in {"train", "dev"}:
            raise ValueError("Task does not match the prepared acceptance task")
        configure_paths(self.config)
        if version("appworld") != self.config["appworld_version"]:
            raise ValueError("AppWorld version does not match the pinned profile")
        from appworld import AppWorld
        target = Path(self.config["paths"]["appworld_root"]) / "experiments/outputs" / self.experiment_name
        if target.exists():
            raise FileExistsError("Refusing to overwrite a previous AppWorld experiment")
        try:
            self.world = AppWorld(task_id=task.public.task_id, experiment_name=self.experiment_name,
                                  load_ground_truth=False, random_seed=self.config["seed"])
            if self.world.task.ground_truth is not None:
                raise IsolationError("The public world must not load ground truth")
            if task.public.instruction != self.world.task.instruction:
                raise ValueError("Public instruction does not match the official task")
            self.task = task
            self.docs = {name: data for name, data in self.world.task.api_docs.items() if name != "admin"}
            self.apis = self.world.shell.user_ns["apis"]
            self.client = SandboxSession(SandboxProfile.from_config(self.config),
                                         apps=list(self.docs), task_datetime=self.world.task.datetime.isoformat())
            return self._observation(None)
        except BaseException:
            self.close()
            raise

    def _observation(self, result: dict | None) -> Observation:
        return Observation(self.task.public.task_id, self.steps,
                           "ready" if self.steps == 0 else "step_" + str(self.steps),
                           ("execute",), None if result is None else result["output"],
                           {"public_apps": list(self.docs),
                            "task_datetime": self.world.task.datetime.isoformat(),
                            "execution_error_type": None if result is None else result["error_type"]})

    def validate_action(self, action: Action, observation: Observation | None = None) -> str | None:
        if action.name != "execute" or set(action.arguments) != {"code"}:
            return "execute requires exactly one code argument"
        code = action.arguments["code"]
        if not isinstance(code, str) or len(code.encode()) > self.config["limits"]["max_code_bytes"]:
            return "code must be a string within the configured byte budget"
        return None

    def _dispatch(self, message: dict):
        self.calls_this_step += 1
        if self.calls_this_step > self.config["limits"]["max_api_calls_per_step"]:
            raise PermissionError("Public API budget exhausted")
        app, api, kwargs = validate_public_call(message, self.docs)
        self.api_audit.append({"step": self.steps, "app": app, "api": api})
        # No shell, eval, private requester method, or arbitrary world method.
        result = self.apis[app][api](**kwargs)
        return json.loads(json.dumps(result, default=str, allow_nan=False))

    def step(self, action: Action) -> StepOutcome:
        if self.world is None or self.client is None or self.finished:
            raise IsolationError("No active public execution session")
        error = self.validate_action(action)
        if error:
            raise ValueError(error)
        if self.steps >= self.config["limits"]["max_steps"]:
            raise ValueError("Episode action budget exhausted")
        self.calls_this_step = 0
        self.world.requester.reset_request_count()
        result = self.client.execute(action.arguments["code"], self._dispatch)
        self.steps += 1
        self.world.requester.reset_request_count()
        # This is the public supervisor completion status, not private grading.
        done = self.world.task_completed()
        return StepOutcome(self._observation(result), done, 0.0,
                           {"api_calls": self.calls_this_step, "execution_error_type": result["error_type"]})

    def verify(self, task: BenchmarkTask) -> dict:
        if self.finished or self.world is None or self.client is None:
            raise IsolationError("No live episode to stop and grade")
        if task.public.task_id != self.task.public.task_id:
            raise IsolationError("Verifier task mismatch")
        # This evidence comes from Popen, never from the client protocol.
        self.stop_proof = self.client.close()
        if not self.stop_proof["stopped"]:
            raise IsolationError("Client stop proof is missing; grading is refused")
        self.stop_proof["observed_at_utc"] = datetime.fromtimestamp(
            time.clock_gettime(time.CLOCK_REALTIME), timezone.utc).isoformat()
        write_new_json(self.directory / "stop-proof.json", self.stop_proof)
        self.world._save_state(self.world.output_db_home_path_on_disk)
        self.world.save_logs()
        snapshot_directory = Path(self.world.output_db_home_path_on_disk)
        snapshot = directory_fingerprint(snapshot_directory)
        write_new_json(self.directory / "snapshot-manifest.json", snapshot)
        write_new_json(self.directory / "api-audit.json", {"calls": self.api_audit})
        self.world.close()
        self.world = None
        self.finished = True
        verifier = Path(__file__).resolve().parents[3] / "scripts/server/rootless_verifier.py"
        output = self.directory / "verifier.json"
        result = subprocess.run([sys.executable, str(verifier), "--task-id", task.public.task_id,
                                 "--experiment", self.experiment_name,
                                 "--snapshot-sha256", snapshot["sha256"], "--output", str(output)],
                                capture_output=True, text=True, timeout=90, check=False)
        if result.returncode or not output.exists():
            raise IsolationError("Independent verifier failed; inspect controller-only records")
        stats = json.loads(output.read_text())
        if stats["snapshot_sha256"] != snapshot["sha256"] or stats["num_tests"] <= 0:
            raise IsolationError("Verifier did not score the trusted non-empty snapshot")
        if directory_fingerprint(snapshot_directory)["sha256"] != snapshot["sha256"]:
            raise IsolationError("The grading input changed during independent verification")
        return {"status": "success" if stats["success"] else "failure", **stats,
                "client_stopped": True, "snapshot_unchanged": True}

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
        if self.world is not None:
            self.world.close()
            self.world = None
        self.finished = True
