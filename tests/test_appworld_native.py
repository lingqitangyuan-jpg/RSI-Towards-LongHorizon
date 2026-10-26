import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/server/accept_appworld_native.py"
spec = importlib.util.spec_from_file_location("appworld_native_acceptance", SCRIPT)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeAcceptanceTests(unittest.TestCase):
    def test_only_selected_task_assets_are_extracted(self):
        task = "07b42fd_1"
        self.assertTrue(native.select_data("data/tasks/07b42fd_1/dbs/supervisor.jsonl", task))
        self.assertFalse(native.select_data("data/tasks/unseen_1/ground_truth/answer.json", task))
        self.assertTrue(native.select_data("data/LICENSE", task))

    def test_archive_path_cannot_escape_allocated_root(self):
        for name in ("../outside", "/tmp/outside", "data/../../outside", "C:/outside", "data\\outside"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                native.select_data(name, "07b42fd_1")

    def test_no_tests_does_not_count_as_verifier_success(self):
        tracker = SimpleNamespace(to_dict=lambda **kwargs: {"success": True, "num_tests": 0},
                                  pass_count=0, fail_count=0)
        world = SimpleNamespace(evaluate=lambda **kwargs: tracker)
        with self.assertRaisesRegex(RuntimeError, "no tests"):
            native.evaluate_stats(world)
