"""Rootless, Linux-only Python execution with no benchmark or host data mounts.

The trusted controller owns AppWorld and grading. This process can only send
JSON requests to a validated public API broker. No HTTP service is opened.
This is a shared-kernel research sandbox, not a VM security guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
from typing import Callable


class IsolationError(RuntimeError):
    pass


class SandboxTimeout(TimeoutError):
    pass


def real_time() -> float:
    # AppWorld freezes time.monotonic in its host process.
    return time.clock_gettime(time.CLOCK_MONOTONIC)


@dataclass(frozen=True)
class SandboxProfile:
    bubblewrap: Path
    bubblewrap_sha256: str
    python_prefix: Path
    client_packages: Path
    temporary_root: Path
    memory_bytes: int = 536870912
    cpu_seconds: int = 15
    step_wall_seconds: float = 15
    max_code_bytes: int = 65536
    max_frame_bytes: int = 1048576
    max_output_chars: int = 12000

    @classmethod
    def from_config(cls, config: dict) -> SandboxProfile:
        p, limits = config["paths"], config["limits"]
        return cls(Path(p["bubblewrap"]), p["bubblewrap_sha256"],
                   Path(p["python_prefix"]), Path(p["client_packages"]),
                   Path(p["storage_root"]) / "tmp",
                   **{k: limits[k] for k in ("memory_bytes", "cpu_seconds",
                                             "step_wall_seconds", "max_code_bytes",
                                             "max_frame_bytes", "max_output_chars")})


BLOCKED_SYSCALLS = (
    "fork", "vfork", "clone", "clone3", "unshare", "setns", "mount", "umount2",
    "pivot_root", "ptrace", "process_vm_readv", "process_vm_writev", "bpf",
    "perf_event_open", "keyctl", "add_key", "request_key", "open_by_handle_at",
    "socket", "socketpair", "connect", "bind", "listen", "accept", "accept4",
)


def export_seccomp(destination) -> None:
    """Export a default-allow filter with explicit process/network escape denies."""
    lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_rule_add.restype = ctypes.c_int
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.seccomp_export_bpf.restype = ctypes.c_int
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise IsolationError("Cannot initialize the required seccomp filter")
    try:
        for name in BLOCKED_SYSCALLS:
            number = lib.seccomp_syscall_resolve_name(name.encode())
            if number < 0:
                raise IsolationError("Unknown required syscall: " + name)
            if lib.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0) != 0:
                raise IsolationError("Cannot deny syscall: " + name)
        if lib.seccomp_export_bpf(context, destination.fileno()) != 0:
            raise IsolationError("Cannot export the required seccomp filter")
        destination.seek(0)
    finally:
        lib.seccomp_release(context)


def sandbox_command(profile: SandboxProfile, seccomp_fd: int) -> list[str]:
    prefix = profile.python_prefix
    worker = Path(__file__).with_name("worker.py").resolve()
    args = ["/usr/bin/prlimit", "--as=" + str(profile.memory_bytes),
            "--cpu=" + str(profile.cpu_seconds), "--nofile=128", "--core=0",
            "--fsize=1048576", "--", str(profile.bubblewrap),
            "--unshare-user", "--unshare-pid", "--unshare-net", "--unshare-ipc",
            "--unshare-uts", "--unshare-cgroup-try", "--die-with-parent",
            "--new-session", "--cap-drop", "ALL", "--clearenv",
            "--hostname", "rsi-agent", "--proc", "/proc", "--dev", "/dev",
            "--dir", "/tmp", "--dir", "/work", "--dir", "/work/home",
            "--ro-bind", "/usr/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu",
            "--symlink", "usr/lib", "/lib", "--dir", "/usr/lib64",
            "--symlink", "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
            "/usr/lib64/ld-linux-x86-64.so.2", "--symlink", "usr/lib64", "/lib64",
            "--ro-bind", str(prefix / "lib"), "/runtime/lib",
            "--tmpfs", "/runtime/lib/python3.12/site-packages",
            "--remount-ro", "/runtime/lib/python3.12/site-packages",
            "--ro-bind", str((prefix / "bin/python3.12").resolve()), "/runtime/bin/python3.12",
            "--symlink", "python3.12", "/runtime/bin/python",
            "--ro-bind", str(worker), "/app/worker.py"]
    # Only small public clock dependencies enter the client; never AppWorld.
    for name in ("freezegun", "dateutil", "six.py"):
        args += ["--ro-bind", str(profile.client_packages / name), "/clientlib/" + name]
    environment = {"PATH": "/runtime/bin", "HOME": "/work/home", "TMPDIR": "/tmp",
                   "LANG": "C.UTF-8", "PYTHONHOME": "/runtime",
                   "PYTHONPATH": "/clientlib", "PYTHONNOUSERSITE": "1",
                   "PYTHONDONTWRITEBYTECODE": "1", "CUDA_VISIBLE_DEVICES": "",
                   "MAX_OUTPUT_CHARS": str(profile.max_output_chars),
                   "MAX_FRAME_BYTES": str(profile.max_frame_bytes)}
    for key, value in environment.items():
        args += ["--setenv", key, value]
    return args + ["--chdir", "/work", "--remount-ro", "/dev",
                   "--remount-ro", "/proc", "--remount-ro", "/",
                   "--seccomp", str(seccomp_fd), "--", "/runtime/bin/python", "-S", "-u",
                   "/app/worker.py"]


class SandboxSession:
    """Persistent Python variables, bounded frames, mandatory isolation, no fallback."""

    def __init__(self, profile: SandboxProfile, *, apps: list[str], task_datetime: str):
        if sys.platform != "linux":
            raise IsolationError("Bubblewrap execution is only supported on Linux")
        if hashlib.sha256(profile.bubblewrap.read_bytes()).hexdigest() != profile.bubblewrap_sha256:
            raise IsolationError("Bubblewrap binary does not match the pinned SHA256")
        if profile.bubblewrap.stat().st_mode & 0o6000:
            raise IsolationError("A setuid/setgid Bubblewrap binary is not accepted")
        self.profile = profile
        self.buffer = b""
        self.calls = 0
        self.closed = False
        profile.temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=profile.temporary_root) as filter_file:
            export_seccomp(filter_file)
            self.process = subprocess.Popen(
                sandbox_command(profile, filter_file.fileno()),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=0, close_fds=True, pass_fds=(filter_file.fileno(),),
                start_new_session=True, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        try:
            deadline = real_time() + profile.step_wall_seconds
            self._send({"kind": "init", "apps": apps, "task_datetime": task_datetime}, deadline)
            ready = self._receive(deadline)
            if ready != {"kind": "ready"}:
                raise IsolationError("Client did not confirm initialization")
        except BaseException:
            self.close()
            raise

    def _send(self, value: dict, deadline: float) -> None:
        data = (json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n").encode()
        if len(data) > self.profile.max_frame_bytes:
            raise IsolationError("Outbound protocol frame exceeds the limit")
        while data:
            remaining = deadline - real_time()
            if remaining <= 0 or not select.select([], [self.process.stdin], [], max(0, remaining))[1]:
                raise SandboxTimeout("Client input deadline exceeded")
            try:
                written = os.write(self.process.stdin.fileno(), data)
                data = data[written:]
            except BlockingIOError:
                continue

    def _receive(self, deadline: float) -> dict:
        while b"\n" not in self.buffer:
            remaining = deadline - real_time()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], max(0, remaining))[0]:
                raise SandboxTimeout("Client output deadline exceeded")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise IsolationError("Client exited before a complete response: " + self.buffer[:500].decode(errors="replace"))
            self.buffer += chunk
            if len(self.buffer) > self.profile.max_frame_bytes:
                raise IsolationError("Inbound protocol frame exceeds the limit")
        line, self.buffer = self.buffer.split(b"\n", 1)
        try:
            value = json.loads(line)
        except (ValueError, RecursionError) as exc:
            raise IsolationError("Invalid client protocol: " + line[:300].decode(errors="replace")) from exc
        if not isinstance(value, dict):
            raise IsolationError("Client protocol must be an object")
        return value

    def execute(self, code: str, dispatch: Callable[[dict], object]) -> dict:
        if self.closed or self.process.poll() is not None:
            raise IsolationError("A closed client cannot execute another action")
        if not isinstance(code, str) or len(code.encode()) > self.profile.max_code_bytes:
            raise ValueError("Python action exceeds the configured code limit")
        deadline = real_time() + self.profile.step_wall_seconds
        try:
            self._send({"kind": "execute", "code": code}, deadline)
            while True:
                message = self._receive(deadline)
                if message.get("kind") == "result":
                    if set(message) != {"kind", "output", "error_type"} or not isinstance(message["output"], str):
                        raise IsolationError("Invalid execution result schema")
                    if len(message["output"]) > self.profile.max_output_chars + 200:
                        raise IsolationError("Execution output exceeds the limit")
                    return message
                if set(message) != {"kind", "id", "app", "api", "kwargs"} or message.get("kind") != "call":
                    raise IsolationError("Only public API calls are accepted from the client")
                self.calls += 1
                if type(message["id"]) is not int or message["id"] != self.calls:
                    raise IsolationError("Invalid API sequence number")
                try:
                    result = dispatch(message)
                    response = {"id": self.calls, "result": result}
                except Exception as exc:
                    response = {"id": self.calls, "error": str(exc)[:500]}
                self._send(response, deadline)
        except BaseException:
            self.close()
            raise

    def close(self) -> dict:
        if not self.closed:
            self.closed = True
            if self.process.poll() is None:
                # Do not depend on untrusted Python honoring a shutdown request.
                os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=10)
            self.process.stdin.close()
            self.process.stdout.close()
        return {"client_pid": self.process.pid, "return_code": self.process.returncode,
                "stopped": self.process.poll() is not None, "api_requests": self.calls,
                "process_creation_denied": True}
