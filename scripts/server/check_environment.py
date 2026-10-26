#!/usr/bin/env python3
"""Read-only checks for the RSI long-horizon server environment.

The checker deliberately does not install packages, download data, start
containers, or submit scheduler jobs.  It is safe to run on a login node and
its JSON output can be saved as an environment snapshot for a run.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PYTHON_MIN = (3, 12)
PYTHON_MAX_EXCLUSIVE = (3, 14)
DEFAULT_MIN_FREE_GB = 20.0
COMMAND_TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class Check:
    """One check result with a stable, JSON-serializable representation."""

    name: str
    status: str
    detail: str
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }
        if self.value is not None:
            result["value"] = self.value
        return result


def _status(ok: bool, detail: str, value: Any = None) -> tuple[str, str, Any]:
    return ("ok" if ok else "fail", detail, value)


def _run_command(command: Iterable[str], timeout: int = COMMAND_TIMEOUT_SECONDS) -> tuple[bool, str, int | None]:
    """Run a version/query command without invoking a shell."""

    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError:
        return False, "not found", None
    except subprocess.TimeoutExpired:
        return False, "timed out", None
    except OSError as exc:
        return False, f"could not execute: {exc}", None

    parts = (completed.stderr, completed.stdout) if completed.returncode else (completed.stdout, completed.stderr)
    output = "\n".join(part.strip() for part in parts if part.strip())
    # Keep snapshots bounded; command output is diagnostic, not an artifact.
    output = output[:500]
    return completed.returncode == 0, output, completed.returncode


def _command_check(name: str, command: list[str], *, required: bool = True) -> Check:
    executable = shutil.which(command[0])
    if executable is None:
        status = "fail" if required else "warn"
        detail = f"{command[0]} is not on PATH"
        return Check(name, status, detail, {"executable": None, "command": command})

    ok, output, returncode = _run_command(command)
    status = "ok" if ok else ("fail" if required else "warn")
    detail = output or (f"exit code {returncode}" if returncode is not None else "command failed")
    return Check(
        name,
        status,
        detail,
        {"executable": executable, "command": command, "returncode": returncode},
    )


def _path_check(name: str, path: Path, *, required: bool = True, kind: str = "path") -> Check:
    exists = path.exists()
    is_expected_kind = path.is_dir() if kind == "directory" else path.is_file() if kind == "file" else True
    ok = exists and is_expected_kind
    status = "ok" if ok else ("fail" if required else "warn")
    if not exists:
        detail = "does not exist"
    elif not is_expected_kind:
        detail = f"exists but is not a {kind}"
    else:
        detail = f"{kind} exists"
    return Check(name, status, detail, {"path": str(path), "exists": exists, "kind": kind})


def _disk_check(name: str, path: Path, min_free_gb: float) -> Check:
    probe = path if path.exists() else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError as exc:
        return Check(name, "fail", f"cannot query disk usage: {exc}", {"path": str(probe)})

    free_gb = usage.free / (1024**3)
    total_gb = usage.total / (1024**3)
    ok = free_gb >= min_free_gb
    detail = f"{free_gb:.2f} GiB free of {total_gb:.2f} GiB (minimum {min_free_gb:.2f} GiB)"
    return Check(
        name,
        "ok" if ok else "fail",
        detail,
        {
            "path": str(probe),
            "free_gib": round(free_gb, 3),
            "total_gib": round(total_gb, 3),
            "minimum_free_gib": min_free_gb,
        },
    )


def _python_check() -> Check:
    version = sys.version_info[:3]
    supported = PYTHON_MIN <= version < PYTHON_MAX_EXCLUSIVE
    detail = (
        f"Python {platform.python_version()}"
        if supported
        else f"Python {platform.python_version()} is outside >=3.12,<3.14"
    )
    return Check(
        "python",
        "ok" if supported else "fail",
        detail,
        {"version": platform.python_version(), "executable": sys.executable},
    )


def _docker_daemon_check() -> Check:
    check = _command_check("docker_daemon", ["docker", "info", "--format", "{{.ServerVersion}}"])
    if check.status != "ok":
        return check
    # A Docker client binary alone does not prove the daemon is usable.
    if not any(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+].*)?", line.strip())
               for line in check.detail.splitlines()):
        return Check("docker_daemon", "fail", "Docker returned no server version", check.value)
    return check


def _data_checks(root: Path, required: bool) -> list[Check]:
    checks = [_path_check("appworld_data_root", root, required=required, kind="directory")]
    for name in ("api_docs", "base_dbs", "datasets", "tasks", "version.txt"):
        checks.append(_path_check("appworld_data_" + name.replace(".", "_"), root / name,
                                  required=required, kind="file" if name == "version.txt" else "directory"))
    for split in ("train", "dev", "test_normal"):
        path = root / "datasets" / (split + ".txt")
        check = _path_check("appworld_split_" + split, path, required=required, kind="file")
        if check.status == "ok":
            ids = path.read_text(encoding="utf-8").split()
            valid_ids = [task_id for task_id in ids if task_id not in {".", ".."} and all(
                c.isascii() and (c.isalnum() or c in "_-") for c in task_id)]
            missing = []
            if len(valid_ids) != len(ids) or len(set(ids)) != len(ids) or not ids:
                missing.append("empty, duplicate or invalid task IDs")
            for task_id in valid_ids:
                task = root / "tasks" / task_id
                for name in ("specs.json", "dbs", "ground_truth"):
                    if not (task / name).exists():
                        missing.append(task_id + "/" + name)
            if missing:
                check = Check(check.name, "fail" if required else "warn",
                              "incomplete official task data",
                              {"task_count": len(ids), "missing_count": len(missing), "examples": missing[:5]})
            else:
                check = Check(check.name, "ok", "split and task inputs exist; contents were not read",
                              {"task_count": len(ids), "path": str(path)})
        checks.append(check)
    return checks


def _gpu_check() -> Check:
    """Query visible GPUs when nvidia-smi is available, without changing state."""

    executable = shutil.which("nvidia-smi")
    if executable is None:
        return Check("nvidia_smi", "fail", "nvidia-smi is not on PATH", {"executable": None})

    command = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ]
    ok, output, returncode = _run_command(command)
    if not ok:
        return Check(
            "nvidia_smi",
            "fail",
            output or (f"exit code {returncode}" if returncode is not None else "command failed"),
            {"executable": executable, "command": command, "returncode": returncode},
        )

    gpus = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if fields:
            gpus.append(
                {
                    "name": fields[0],
                    "memory_mib": fields[1] if len(fields) > 1 else None,
                    "driver_version": fields[2] if len(fields) > 2 else None,
                }
            )
    return Check(
        "nvidia_smi",
        "ok",
        f"{len(gpus)} visible GPU(s)",
        {"executable": executable, "gpu_count": len(gpus), "gpus": gpus},
    )


def build_report(
    *,
    project_root: Path,
    harness_zero_root: Path,
    appworld_data_root: Path,
    run_root: Path,
    min_free_gb: float,
    require_appworld_data: bool = False,
) -> dict[str, Any]:
    """Collect all checks and return a stable report dictionary."""

    checks = [
        _python_check(),
        _command_check("git", ["git", "--version"]),
        _command_check("uv", ["uv", "--version"]),
        _command_check("docker", ["docker", "--version"]),
        _docker_daemon_check(),
        _command_check("docker_compose", ["docker", "compose", "version"]),
        _gpu_check(),
        _path_check("project_root", project_root, kind="directory"),
        _path_check("harness_zero_root", harness_zero_root, kind="directory"),
        _path_check("harness_zero_pyproject", harness_zero_root / "pyproject.toml", kind="file"),
        _path_check("harness_zero_lock", harness_zero_root / "uv.lock", kind="file"),
        _path_check("run_root", run_root, required=False, kind="directory"),
        _disk_check("project_disk", project_root, min_free_gb),
        _disk_check("run_disk", run_root, min_free_gb),
    ]
    checks.extend(_data_checks(appworld_data_root, require_appworld_data))
    failures = [check.name for check in checks if check.status == "fail"]
    warnings = [check.name for check in checks if check.status == "warn"]
    return {
        "schema_version": "environment-check/v2",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "user": os.environ.get("USER") or os.environ.get("USERNAME"),
        },
        "paths": {
            "project_root": str(project_root),
            "harness_zero_root": str(harness_zero_root),
            "appworld_data_root": str(appworld_data_root),
            "run_root": str(run_root),
        },
        "checks": [check.to_dict() for check in checks],
        "summary": {
            "scope": "deployment_preflight_only_not_benchmark_acceptance",
            "status": "fail" if failures else "ok",
            "failure_count": len(failures),
            "warning_count": len(warnings),
            "failures": failures,
            "warnings": warnings,
        },
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    script_project_root = Path(__file__).resolve().parents[2]
    configured_project_root = os.environ.get("RSI_PROJECT_ROOT")
    configured_harness_zero_root = os.environ.get("RSI_HZ_ROOT")
    configured_appworld_data_root = os.environ.get("RSI_APPWORLD_DATA_ROOT")
    configured_run_root = os.environ.get("RSI_RUN_ROOT")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(configured_project_root) if configured_project_root else script_project_root,
    )
    parser.add_argument(
        "--hz-root",
        "--harness-zero-root",
        dest="harness_zero_root",
        type=Path,
        default=Path(configured_harness_zero_root) if configured_harness_zero_root else None,
        help="Harness-Zero checkout (defaults to <project>/sources/repo/Harness_zero/harness-zero-main).",
    )
    parser.add_argument(
        "--appworld-data-root",
        type=Path,
        default=Path(configured_appworld_data_root) if configured_appworld_data_root else None,
        help="Official AppWorld data directory. Missing data is reported as a warning during setup.",
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path(configured_run_root) if configured_run_root else None,
        help="Directory for run artifacts; it need not exist before the first run.",
    )
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=DEFAULT_MIN_FREE_GB,
        help=f"Minimum free disk space required on project/run volumes (default: {DEFAULT_MIN_FREE_GB:g}).",
    )
    parser.add_argument("--output", type=Path, help="Write the JSON report to this file as well as stdout.")
    parser.add_argument(
        "--require-appworld-data",
        action="store_true",
        help="Treat missing/incomplete official data as failures, including task inputs and grading assets.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 when any required check fails; otherwise exit 0 after printing the report.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    project_root = args.project_root.expanduser().resolve()
    harness_zero_root = (
        args.harness_zero_root.expanduser().resolve()
        if args.harness_zero_root is not None
        else project_root / "sources" / "repo" / "Harness_zero" / "harness-zero-main"
    )
    appworld_data_root = (
        args.appworld_data_root.expanduser().resolve()
        if args.appworld_data_root is not None
        else project_root / "data" / "appworld"
    )
    run_root = (
        args.run_root.expanduser().resolve()
        if args.run_root is not None
        else project_root / "runs"
    )
    if not math.isfinite(args.min_free_gb) or args.min_free_gb < 0:
        raise SystemExit("--min-free-gb must be finite and non-negative")

    report = build_report(
        project_root=project_root,
        harness_zero_root=harness_zero_root,
        appworld_data_root=appworld_data_root,
        run_root=run_root,
        min_free_gb=args.min_free_gb,
        require_appworld_data=args.require_appworld_data,
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(encoded, end="")
    if args.output is not None:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")

    if args.strict and report["summary"]["status"] == "fail":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
