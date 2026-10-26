#!/usr/bin/env python3
"""Resolve Linux AppWorld CPU dependencies and download a verified offline wheelhouse.

The resolver runs locally, including on Windows. Do not resolve Linux platform
markers with the host Windows pip: uv resolves the target and strips markers
before pip downloads the exact Linux wheels without re-resolving dependencies.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

PLATFORM = "x86_64-manylinux_2_35"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    wheelhouse = output / "wheelhouse"
    wheelhouse.mkdir(exist_ok=True)
    lock = output / "requirements-linux.lock"
    source = args.requirements.resolve()
    source_hash = sha256(source)
    saved_input = output / "requirements.in"
    if saved_input.exists() and sha256(saved_input) != source_hash:
        parser.error("Use a new output directory after changing input requirements")
    if not saved_input.exists():
        saved_input.write_bytes(source.read_bytes())
    environment = dict(os.environ)
    environment["UV_CACHE_DIR"] = str(output.parent / "cache/uv")
    environment["PIP_CACHE_DIR"] = str(output.parent / "cache/pip")
    resolve = [str(args.uv.resolve()), "pip", "compile", str(saved_input),
               "--python-platform", PLATFORM, "--python-version", "3.12",
               "--no-python-downloads", "--no-header", "--no-annotate",
               "--generate-hashes", "--no-build", "--index-url", "https://pypi.org/simple",
               "--output-file", str(lock)]
    if not lock.exists():
        result = subprocess.run(resolve, env=environment, capture_output=True, text=True, timeout=600)
        (output / "resolve.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise SystemExit((output / "resolve.log").read_text(encoding="utf-8")[-5000:])
    download = [sys.executable, "-m", "pip", "download", "--no-deps",
                "--only-binary=:all:", "--require-hashes",
                "--implementation", "cp", "--python-version", "3.12",
                "--abi", "cp312", "--dest", str(wheelhouse),
                "--index-url", "https://pypi.org/simple", "-r", str(lock)]
    # Ubuntu 22.04 on the accepted node has glibc 2.35. pip's cross-platform
    # flag does not expand every older manylinux minor tag automatically.
    for minor in range(35, 4, -1):
        download.extend(["--platform", f"manylinux_2_{minor}_x86_64"])
    for legacy in ("manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64"):
        download.extend(["--platform", legacy])
    result = subprocess.run(download, env=environment, capture_output=True, text=True, timeout=900)
    (output / "download.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise SystemExit((output / "download.log").read_text(encoding="utf-8")[-5000:])
    wheels = [{"path": p.relative_to(output).as_posix(), "bytes": p.stat().st_size,
               "sha256": sha256(p)} for p in sorted(wheelhouse.glob("*.whl"))]
    if not wheels:
        raise RuntimeError("No Linux wheels were downloaded")
    report = {"schema_version": "appworld-native-wheelhouse/v1",
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "target": {"platform": PLATFORM, "python": "3.12", "gpu": False},
              "input_sha256": source_hash, "lock_sha256": sha256(lock),
              "resolver": subprocess.check_output([str(args.uv.resolve()), "--version"], text=True).strip(),
              "commands": [resolve, download], "wheel_count": len(wheels),
              "total_wheel_bytes": sum(w["bytes"] for w in wheels), "wheels": wheels}
    manifest = output / "manifest.json"
    manifest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    files = [saved_input, lock, manifest, *sorted(wheelhouse.glob("*.whl"))]
    sums = [sha256(p) + "  " + p.relative_to(output).as_posix() for p in files]
    (output / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in report.items() if k not in {"wheels", "commands"}}, indent=2))


if __name__ == "__main__":
    main()
