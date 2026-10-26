from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rsi_horizon.contracts import Action, BenchmarkTask, Observation, TaskSpec
from rsi_horizon.model_policy import PythonAgentStudent, parse_python_response
from rsi_horizon.policies import StudentActionError
from rsi_horizon.runner import run_episode


class FakeBackend:
    def __init__(self):
        self.histories = []

    def generate(self, messages, generation):
        self.histories.append(deepcopy(messages))
        return {"kind": "generation", "id": len(self.histories), "text": "```python\nprint('ORIGINAL')\n```",
                "input_tokens": 10, "output_tokens": 5, "finish_reason": "eos"}


class ModelPolicyTests(unittest.TestCase):
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
