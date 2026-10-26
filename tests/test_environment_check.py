import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/server/check_environment.py"
spec = importlib.util.spec_from_file_location("environment_check", SCRIPT)
checker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checker
spec.loader.exec_module(checker)


class EnvironmentCheckTests(unittest.TestCase):
    def test_docker_cli_does_not_hide_daemon_permission_error(self):
        result = subprocess.CompletedProcess([], 1, stdout="client metadata" * 100, stderr="permission denied")
        with patch.object(checker.shutil, "which", return_value="/usr/bin/docker"), \
             patch.object(checker.subprocess, "run", return_value=result):
            check = checker._docker_daemon_check()
        self.assertEqual(check.status, "fail")
        self.assertIn("permission denied", check.detail)

    def test_missing_data_is_failure_in_acceptance_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks = checker._data_checks(Path(tmp) / "absent", required=True)
        self.assertTrue(all(check.status == "fail" for check in checks))

    def test_split_file_alone_does_not_prove_tasks_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "datasets").mkdir()
            (root / "datasets/train.txt").write_text("train_1\n")
            checks = checker._data_checks(root, required=True)
        check = next(c for c in checks if c.name == "appworld_split_train")
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.value["missing_count"], 3)

    def test_disk_probe_handles_new_nested_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            check = checker._disk_check("disk", Path(tmp) / "new/nested/run", 0)
        self.assertEqual(check.status, "ok")

    def test_split_cannot_escape_task_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "datasets").mkdir()
            (root / "datasets/train.txt").write_text("../../private\n")
            checks = checker._data_checks(root, required=True)
        check = next(c for c in checks if c.name == "appworld_split_train")
        self.assertEqual(check.status, "fail")
        self.assertIn("invalid task IDs", check.value["examples"][0])


if __name__ == "__main__":
    unittest.main()
