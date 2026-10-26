#!/usr/bin/env python3
"""Create a project-only inference venv, verify wheels, and leave base unchanged."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    base = Path("/nfs-data/sdf/lyf/rsi-long-horizon")
    prefix = base / "envs/student-qwen35-py312"
    source_python = base / "envs/rsi-py312/bin/python"
    if args.report.exists() or prefix.exists():
        raise FileExistsError("Refusing to overwrite an existing inference environment or report")
    if not args.wheelhouse.resolve().is_relative_to(base):
        raise ValueError("Wheelhouse must be in the allocated project directory")
    manifest = json.loads((args.wheelhouse / "manifest.json").read_text())
    wheels = []
    for record in manifest["files"]:
        file = args.wheelhouse / record["path"]
        if file.name != record["path"] or file.is_symlink() or file.stat().st_size != record["bytes"]:
            raise ValueError("Invalid wheel entry")
        if hashlib.sha256(file.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("Wheel checksum mismatch")
        wheels.append(str(file))
    subprocess.run([str(source_python), "-m", "venv", "--system-site-packages", str(prefix)], check=True)
    python = str(prefix / "bin/python")
    subprocess.run([python, "-m", "pip", "install", "--no-index", "--no-deps", *wheels], check=True)
    subprocess.run([python, "-m", "pip", "check"], check=True)
    probe = "import torch, transformers; from transformers import Qwen3_5ForCausalLM; import json; print(json.dumps({'torch':torch.__version__,'transformers':transformers.__version__,'cuda_runtime':torch.version.cuda,'model_class':Qwen3_5ForCausalLM.__name__}))"
    versions = json.loads(subprocess.check_output([python, "-c", probe], text=True))
    report = {"status": "pass", "prefix": str(prefix), "base_torch_reused": True,
              "wheel_count": len(wheels), "versions": versions, "gpu_used": False,
              "public_environment_modified": False}
    with args.report.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
