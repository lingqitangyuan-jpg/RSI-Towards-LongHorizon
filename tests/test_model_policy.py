from copy import deepcopy
import importlib.util
import tempfile
from unittest.mock import patch
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rsi_horizon.contracts import Action, BenchmarkTask, Observation, TaskSpec
from rsi_horizon.model_policy import PythonAgentStudent, parse_python_response
from rsi_horizon.policies import StudentActionError
from rsi_horizon.runner import run_episode
from rsi_horizon.benchmark import load_manifest
from rsi_horizon.environment import ToyStateChain
from rsi_horizon.policies import DriftStudent
from rsi_horizon.reviewers import RuleReviewer


class FakeBackend:
    def __init__(self):
        self.histories = []

    def generate(self, messages, generation):
        self.histories.append(deepcopy(messages))
        return {"kind": "generation", "id": len(self.histories), "text": "```python\nprint('ORIGINAL')\n```",
                "input_tokens": 10, "output_tokens": 5, "finish_reason": "eos"}


class ModelPolicyTests(unittest.TestCase):
    def test_eos_decoding_newline_does_not_break_strict_python_protocol(self):
        spec = importlib.util.spec_from_file_location("model_worker", Path(__file__).resolve().parents[1] / "scripts/server/model_worker.py")
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        pieces = {10: chr(96) * 3 + "python\nprint(4)\n" + chr(96) * 3, 9: "<|im_end|>\n", 0: "<|endoftext|>"}
        decode = lambda ids: "".join(pieces[x] for x in ids)
        generated = [10, 9, 0]
        with self.assertRaises(StudentActionError):
            parse_python_response(decode(generated), thinking=False)
        clean = worker.trim_terminal_tokens(generated, {9, 0})
        self.assertEqual(parse_python_response(decode(clean), thinking=False), "print(4)")
        self.assertEqual(worker.trim_terminal_tokens([9, 10], {9, 0}), [9, 10])
        self.assertEqual(generated, [10, 9, 0])

    def test_download_lock_cleanup_and_existing_lock_are_platform_safe(self):
        spec = importlib.util.spec_from_file_location("prepare_student", Path(__file__).resolve().parents[1] / "scripts/server/prepare_student.py")
        prepare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prepare)
        manifest = {"model_id": "test/no-network", "revision": "fixture", "weight_bytes": 0, "files": []}
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            lock = destination / ".download.lock"
            self.assertEqual(prepare.download_model(manifest, destination)["files_verified"], 0)
            self.assertFalse(lock.exists())
            with patch.object(prepare, "verify_model", side_effect=ValueError("fixture failure")):
                with self.assertRaises(ValueError):
                    prepare.download_model(manifest, destination)
            self.assertFalse(lock.exists())
            lock.write_text("another downloader", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                prepare.download_model(manifest, destination)
            self.assertEqual(lock.read_text(encoding="utf-8"), "another downloader")

    def test_runner_notifies_student_of_accepted_actions_after_replacement(self):
        class ObserverStudent(DriftStudent):
            def __init__(self):
                super().__init__(drift_at=1)
                self.accepted = []
            def observe_accepted(self, action, after):
                self.accepted.append(action)
        _, tasks = load_manifest(Path(__file__).resolve().parents[1] / "configs/toy_benchmark.toml")
        student = ObserverStudent()
        result, trajectory, _ = run_episode(tasks[0], student, ToyStateChain(), RuleReviewer())
        self.assertTrue(result.success)
        self.assertEqual(student.accepted, [x.accepted_action for x in trajectory])
        self.assertTrue(any(x.original_action != x.accepted_action for x in trajectory))

    def test_only_final_code_is_executed_after_complete_thinking(self):
        raw = "consider ```python\nprint('not final')\n```\n</think>\n```python\nprint(4)\n```"
        self.assertEqual(parse_python_response(raw, thinking=True), "print(4)")

    def test_incomplete_thinking_cannot_execute_a_code_block(self):
        with self.assertRaises(StudentActionError):
            parse_python_response("thinking ```python\nprint(4)\n```", thinking=True)

    def test_multiple_blocks_or_prose_or_invalid_syntax_are_rejected(self):
        for value in ["Here is code:\n```python\nprint(4)\n```", "```python\nprint(4)\n```\n```python\nprint(5)\n```", "```python\nif (\n```"]:
            with self.subTest(value=value), self.assertRaises(StudentActionError):
                parse_python_response(value, thinking=False)

    def test_replaced_proposal_never_enters_next_student_history(self):
        config = {"model_id": "test", "revision": "fixed", "generation": {"enable_thinking": False}, "engineering_smoke": {}}
        backend = FakeBackend()
        student = PythonAgentStudent(backend, config)
        task = TaskSpec("task", "appworld", "0.1.0", "public task")
        before = Observation("task", 0, "ready", ("execute",))
        after = Observation("task", 1, "step_1", ("execute",), "public result")
        student.reset(task)
        student.act(task, before)
        student.observe_accepted(Action("execute", {"code": "print('ACCEPTED')"}), after)
        student.act(task, after)
        history = str(backend.histories[-1])
        self.assertIn("ACCEPTED", history)
        self.assertNotIn("ORIGINAL", history)
        self.assertNotIn("review_reason", history)

    def test_student_format_error_is_a_task_failure_not_infrastructure(self):
        class InvalidStudent:
            def reset(self, task): pass
            def act(self, task, observation): raise StudentActionError("invalid final code")
        class EmptyEnvironment:
            def reset(self, task): return Observation("task", 0, "ready", ("execute",))
        task = BenchmarkTask(TaskSpec("task", "appworld", "0.1.0", "public"), "train", "family", step_budget=1)
        result, trajectory, audit = run_episode(task, InvalidStudent(), EmptyEnvironment())
        self.assertEqual(result.status, "failure")
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
