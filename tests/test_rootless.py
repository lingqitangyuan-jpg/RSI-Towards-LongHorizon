import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rsi_horizon.execution.bubblewrap import IsolationError, SandboxProfile, SandboxSession, sandbox_command
from rsi_horizon.integrations.appworld_rootless import (
    RootlessAppWorldEnvironment, directory_fingerprint, validate_public_call,
)
from rsi_horizon.contracts import BenchmarkTask, TaskSpec


class RootlessTests(unittest.TestCase):
    def load_worker(self):
        spec = importlib.util.spec_from_file_location("sandbox_worker_test", Path(__file__).resolve().parents[1] / "src/rsi_horizon/execution/worker.py")
        worker = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"MAX_FRAME_BYTES": "1048576", "MAX_OUTPUT_CHARS": "12000"}):
            spec.loader.exec_module(worker)
        return worker

    def test_proxy_route_names_do_not_capture_endpoint_keyword_arguments(self):
        worker = self.load_worker()
        worker.emit = Mock()
        worker.receive = lambda: {"id": worker.CALL_ID, "result": {"public": True}}
        app = worker.App("api_docs")
        app.show_api_doc(app_name="supervisor", api_name="show_profile")
        envelope = worker.emit.call_args.args[0]
        self.assertEqual((envelope["app"], envelope["api"]), ("api_docs", "show_api_doc"))
        self.assertEqual(envelope["kwargs"], {"app_name": "supervisor", "api_name": "show_profile"})
        worker.Requester().request("api_docs", "show_api_doc", app_name="supervisor", api_name="show_profile")
        self.assertEqual(worker.emit.call_args.args[0]["kwargs"], envelope["kwargs"])

    def test_python_last_expression_is_visible_and_variables_persist(self):
        worker = self.load_worker()
        namespace = {}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            worker.execute_python("x = 40\nx + 2", namespace)
            worker.execute_python("x += 1\nprint(x)", namespace)
            worker.execute_python("None", namespace)
        self.assertEqual(output.getvalue(), "42\n41\n")
        self.assertEqual(namespace["x"], 41)

    def test_admin_and_undocumented_endpoints_never_reach_world(self):
        docs = {"supervisor": {"show_profile": {}}, "admin": {"execute": {}}}
        for app, api in [("admin", "execute"), ("supervisor", "__dict__"), ("supervisor", "evaluate")]:
            with self.subTest(app=app, api=api), self.assertRaises(PermissionError):
                validate_public_call({"kind": "call", "id": 1, "app": app, "api": api, "kwargs": {}}, docs)

    def test_client_cannot_send_a_grading_command(self):
        with self.assertRaises(ValueError):
            validate_public_call({"kind": "grade", "success": True}, {})

    def test_only_documented_public_call_is_valid(self):
        message = {"kind": "call", "id": 1, "app": "supervisor", "api": "show_profile", "kwargs": {}}
        self.assertEqual(validate_public_call(message, {"supervisor": {"show_profile": {}}}),
                         ("supervisor", "show_profile", {}))

    def test_empty_or_changed_snapshot_is_not_a_stable_grading_input(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)
            with self.assertRaises(IsolationError):
                directory_fingerprint(path)
            (path / "state.json").write_text("one")
            before = directory_fingerprint(path)["sha256"]
            (path / "state.json").write_text("two")
            self.assertNotEqual(directory_fingerprint(path)["sha256"], before)

    def test_missing_stop_proof_refuses_snapshot_and_grading(self):
        env = RootlessAppWorldEnvironment.__new__(RootlessAppWorldEnvironment)
        env.finished = False
        env.world = Mock()
        env.client = Mock()
        env.client.close.return_value = {"stopped": False}
        env.task = BenchmarkTask(TaskSpec("fixture", "appworld", "0.1.0", "task"), "train", "family")
        with self.assertRaisesRegex(IsolationError, "stop proof"):
            env.verify(env.task)
        env.world._save_state.assert_not_called()

    def test_api_budget_is_enforced_before_dispatch(self):
        env = RootlessAppWorldEnvironment.__new__(RootlessAppWorldEnvironment)
        env.config = {"limits": {"max_api_calls_per_step": 1}}
        env.calls_this_step = 1
        with self.assertRaises(PermissionError):
            env._dispatch({})

    def test_command_never_mounts_benchmark_home_or_host_root(self):
        profile = SandboxProfile(Path("/private/bwrap"), "hash", Path("/runtime-source"),
                                 Path("/small-public-dependencies"), Path("/private/tmp"))
        command = sandbox_command(profile, 9)
        for flag in ("--unshare-user", "--unshare-pid", "--unshare-net", "--clearenv", "--seccomp"):
            self.assertIn(flag, command)
        self.assertNotIn("--share-net", command)
        self.assertNotIn("--bind", command)
        for index, value in enumerate(command):
            if value == "--ro-bind":
                source = command[index + 1]
                self.assertNotIn(source, ("/", "/home", "/nfs-data", "/run"))
                self.assertNotIn("appworld-native/data", source)

    def test_binary_hash_mismatch_never_launches_a_fallback(self):
        with tempfile.TemporaryDirectory() as name:
            binary = Path(name) / "bwrap"
            binary.write_bytes(b"wrong")
            profile = SandboxProfile(binary, "0" * 64, Path(name), Path(name), Path(name))
            with patch("rsi_horizon.execution.bubblewrap.sys.platform", "linux"), \
                    patch("rsi_horizon.execution.bubblewrap.subprocess.Popen") as process:
                with self.assertRaisesRegex(IsolationError, "SHA256"):
                    SandboxSession(profile, apps=[], task_datetime="2026-01-01")
                process.assert_not_called()
