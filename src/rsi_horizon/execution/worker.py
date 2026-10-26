"""Inside-only Python client: public API proxies and persistent execution state."""
from __future__ import annotations

import contextlib
import ctypes
import errno
import functools
import json
import os
import sys
from importlib.machinery import ModuleSpec
from types import ModuleType

INPUT = sys.stdin
OUTPUT = sys.stdout
FRAME_LIMIT = int(os.environ["MAX_FRAME_BYTES"])
OUTPUT_LIMIT = int(os.environ["MAX_OUTPUT_CHARS"])


def emit(value):
    OUTPUT.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n")
    OUTPUT.flush()


def receive():
    value = INPUT.readline(FRAME_LIMIT + 1)
    if not value or len(value.encode()) > FRAME_LIMIT:
        raise EOFError("Missing or oversized controller frame")
    return json.loads(value)


def deny_exec():
    # Installed after interpreter startup; the inherited filter already blocks
    # forks, threads, namespace changes and sockets before startup.
    lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x7FFF0000)
    if not context:
        raise RuntimeError("Cannot initialize exec restriction")
    try:
        for name in (b"execve", b"execveat"):
            number = lib.seccomp_syscall_resolve_name(name)
            if number < 0 or lib.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0):
                raise RuntimeError("Cannot configure exec restriction")
        if lib.seccomp_load(context):
            raise RuntimeError("Cannot activate exec restriction")
    finally:
        lib.seccomp_release(context)


class App(ModuleType):
    def __init__(self, name):
        super().__init__("apis." + name)
        self.app_name = name

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return functools.partial(call, self.app_name, name)


CALL_ID = 0


def call(app_name, api_name, **kwargs):
    global CALL_ID
    CALL_ID += 1
    emit({"kind": "call", "id": CALL_ID, "app": app_name, "api": api_name, "kwargs": kwargs})
    response = receive()
    if response.get("id") != CALL_ID:
        raise RuntimeError("Mismatched public API response")
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]


class Requester:
    def request(self, app_name, api_name, **kwargs):
        return call(app_name, api_name, **kwargs)


class OutputBuffer:
    def __init__(self):
        self.head = ""
        self.tail = ""
        self.count = 0

    def write(self, value):
        self.count += len(value)
        remaining = max(0, OUTPUT_LIMIT // 2 - len(self.head))
        self.head += value[:remaining]
        self.tail = (self.tail + value[remaining:])[-OUTPUT_LIMIT // 2:]
        return len(value)

    def flush(self):
        pass

    def text(self):
        if self.count <= OUTPUT_LIMIT:
            return self.head + self.tail
        return self.head + "\n[output truncated]\n" + self.tail


def main():
    initialization = receive()
    if initialization.get("kind") != "init":
        raise ValueError("Initialization required")
    from freezegun import freeze_time
    freeze_time(initialization["task_datetime"]).start()
    deny_exec()
    apis = ModuleType("apis")
    apis.__path__ = []
    for name in initialization["apps"]:
        app = App(name)
        setattr(apis, name, app)
        sys.modules["apis." + name] = app
    sys.modules["apis"] = apis
    requester = Requester()
    requester_module = ModuleType("requester")
    requester_module.request = requester.request
    sys.modules["requester"] = requester_module
    # Official compiled solutions import these two types for annotations.
    # Supply proxy-only aliases, not the native AppWorld package or any loader
    # that could import its task/database/grading implementation.
    compatibility = {}
    for name in ("appworld", "appworld.collections", "appworld.collections.apis", "appworld.requester"):
        module = ModuleType(name)
        module.__path__ = []
        module.__spec__ = ModuleSpec(name, loader=None, origin="rsi-public-proxy", is_package=True)
        compatibility[name] = module
        sys.modules[name] = module
    compatibility["appworld"].collections = compatibility["appworld.collections"]
    compatibility["appworld"].requester = compatibility["appworld.requester"]
    compatibility["appworld.collections"].apis = compatibility["appworld.collections.apis"]
    compatibility["appworld.collections.apis"].ApiCollection = type(apis)
    compatibility["appworld.requester"].Requester = Requester
    namespace = {"__name__": "__main__", "apis": apis, "requester": requester}
    emit({"kind": "ready"})
    while True:
        message = receive()
        if set(message) != {"kind", "code"} or message["kind"] != "execute":
            raise ValueError("Only Python execution requests are accepted")
        output = OutputBuffer()
        error_type = None
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                exec(compile(message["code"], "<agent-action>", "exec"), namespace)
        except BaseException as exc:
            error_type = type(exc).__name__
            output.write(type(exc).__name__ + ": " + str(exc)[:500])
        emit({"kind": "result", "output": output.text(), "error_type": error_type})


if __name__ == "__main__":
    main()
