"""Real student policy; only accepted public actions enter its next context."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import queue
import re
import subprocess
import threading

from .contracts import Action, Observation, TaskSpec
from .policies import StudentActionError

SYSTEM_PROMPT = """You solve AppWorld tasks by executing Python against public simulated app APIs.
Return exactly one Python code block, with no prose outside it. Use print to inspect results.
The variables apis and requester already exist; Python variables persist between steps.
Discover apps with apis.api_docs.show_app_descriptions(). Discover endpoints with
apis.api_docs.show_api_descriptions(app_name='APP'). Read one endpoint with
apis.api_docs.show_api_doc(app_name='APP', api_name='ENDPOINT') before guessing parameters.
Read your public profile with apis.supervisor.show_profile(); simulated account credentials
are available through apis.supervisor.show_account_passwords(). Use keyword API arguments.
Finish with apis.supervisor.complete_task(answer=...) when an answer is required,
otherwise apis.supervisor.complete_task(). Submission is not proof of grading success.
Only documented public APIs affect the task. The execution filesystem is read-only;
host files, network, subprocesses and GPU are unavailable. Standard Python imports work.
If an execution error occurs, earlier API changes may persist: inspect before retrying.
You receive only public observations. Never seek hidden tests, correct solutions or grading files.
"""


def parse_python_response(text: str, *, thinking: bool) -> str:
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    elif thinking or "<think>" in text:
        raise StudentActionError("Incomplete thinking output; no final action was executed")
    match = re.fullmatch(r"\s*```(?:python|py)\s*\n(.*?)\n```\s*", text, re.DOTALL)
    if not match or "```" in match.group(1):
        raise StudentActionError("Expected exactly one final Python block")
    code = match.group(1)
    try:
        ast.parse(code)
    except SyntaxError as exc:
        raise StudentActionError("Invalid Python syntax in model output") from exc
    if not code.strip():
        raise StudentActionError("Empty model action")
    return code


class SubprocessModel:
    """Host-side inference process, separate from AppWorld and execution sandbox."""

    def __init__(self, config: dict, *, config_path: Path, gpu_uuid: str, log_path: Path):
        if not re.fullmatch(r"GPU-[a-f0-9-]+", gpu_uuid):
            raise ValueError("Select an explicit physical GPU UUID")
        import os
        self.config = config
        self.queue = queue.Queue()
        self.process = None
        self.log = log_path.open("x", encoding="utf-8")
        self.request_id = 0
        worker = Path(__file__).resolve().parents[2] / "scripts/server/model_worker.py"
        env = os.environ.copy()
        storage = Path(config["model_path"]).parent.parent
        env.update({"CUDA_VISIBLE_DEVICES": gpu_uuid, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false",
                    "HF_HOME": str(storage / "cache/huggingface"), "TORCH_HOME": str(storage / "cache/torch"),
                    "PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1"})
        try:
            self.process = subprocess.Popen([config["python"], str(worker), "--config", str(config_path)],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                            text=True, encoding="utf-8", bufsize=1, env=env)
            threading.Thread(target=self._read, daemon=True).start()
            self.ready = self._receive(config["startup_timeout_seconds"])
            if self.ready.get("kind") != "ready":
                raise RuntimeError("Inference startup failed; inspect model-worker.log")
        except BaseException:
            self.close()
            raise

    def _read(self) -> None:
        try:
            for line in self.process.stdout:
                if len(line) > 2_000_000:
                    self.queue.put(RuntimeError("Oversized model response"))
                    return
                self.queue.put(json.loads(line))
        except BaseException as exc:
            self.queue.put(exc)
        finally:
            self.queue.put(EOFError("Inference worker exited"))

    def _receive(self, timeout: float) -> dict:
        try:
            item = self.queue.get(timeout=timeout)
        except queue.Empty as exc:
            self.close()
            raise TimeoutError("Model worker deadline exceeded") from exc
        if isinstance(item, BaseException):
            raise item
        return item

    def generate(self, messages: list[dict], generation: dict) -> dict:
        if self.process.poll() is not None:
            raise RuntimeError("Inference worker is not running")
        self.request_id += 1
        request = {"id": self.request_id, "messages": messages, "generation": generation}
        self.process.stdin.write(json.dumps(request, ensure_ascii=True) + "\n")
        self.process.stdin.flush()
        result = self._receive(self.config["request_timeout_seconds"])
        if result.get("id") != self.request_id:
            raise RuntimeError("Model response id mismatch")
        if result.get("kind") == "error":
            if result.get("error_type") == "ContextBudgetExceeded":
                raise StudentActionError("Configured context budget exceeded; history was not truncated")
            raise RuntimeError("Inference error: " + result.get("error_type", "unknown"))
        if result.get("kind") != "generation":
            raise RuntimeError("Unexpected inference response")
        return result

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=8)
        if not self.log.closed:
            self.log.close()


class PythonAgentStudent:
    def __init__(self, backend, config: dict, *, smoke: bool = False):
        self.backend = backend
        self.config = config
        self.generation = dict(config["generation"])
        if smoke:
            self.generation.update({k: v for k, v in config["engineering_smoke"].items() if k != "max_steps"})
        self.artifact_id = config["model_id"] + "@" + config["revision"]
        self.calls = []
        self.generations = []
        self.messages = []

    def reset(self, task: TaskSpec) -> None:
        self.task_id = task.task_id
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.calls = []
        self.generations = []
        self.waiting_for_action = False
        self.first = True

    def act(self, task: TaskSpec, observation: Observation) -> Action:
        if task.task_id != self.task_id or observation.task_id != self.task_id:
            raise ValueError("Public task mismatch")
        if self.waiting_for_action:
            raise RuntimeError("Previous action was not acknowledged by runner")
        if self.first:
            self.messages.append({"role": "user", "content": json.dumps({
                "instruction": task.instruction, "observation": observation.to_dict()}, ensure_ascii=False)})
            self.first = False
        response = self.backend.generate(self.messages, self.generation)
        self.generations.append(response)
        self.calls.append({k: v for k, v in response.items() if k not in {"text", "kind", "id"}})
        code = parse_python_response(response["text"], thinking=self.generation["enable_thinking"])
        self.waiting_for_action = True
        return Action("execute", {"code": code})

    def observe_accepted(self, action: Action, observation_after: Observation) -> None:
        if action.name != "execute" or not self.waiting_for_action:
            raise ValueError("Missing accepted Python action")
        self.messages.extend([
            {"role": "assistant", "content": "```python\n" + action.arguments["code"] + "\n```"},
            {"role": "user", "content": json.dumps(observation_after.to_dict(), ensure_ascii=False)},
        ])
        self.waiting_for_action = False
