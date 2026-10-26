#!/usr/bin/env python3
"""Prepare a pinned public model or Linux wheels on the connected workstation."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

REQUIREMENTS = ["transformers==5.3.0", "huggingface-hub==1.3.0",
                "tokenizers==0.22.2", "safetensors==0.7.0", "numpy==2.2.6"]


def digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verify_file(path: Path, record: dict) -> None:
    if path.is_symlink() or path.stat().st_size != record["bytes"]:
        raise ValueError("Unexpected size or symlink: " + path.name)
    if record.get("sha256"):
        if digest(path) != record["sha256"]:
            raise ValueError("SHA256 mismatch: " + path.name)
    elif record.get("git_blob"):
        content = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if actual != record["git_blob"]:
            raise ValueError("Git blob mismatch: " + path.name)
    else:
        raise ValueError("Missing upstream hash: " + path.name)


def verify_model(manifest: dict, destination: Path) -> dict:
    for record in manifest["files"]:
        if Path(record["path"]).name != record["path"]:
            raise ValueError("Only flat upstream paths are allowed")
        verify_file(destination / record["path"], record)
    return {"model_id": manifest["model_id"], "revision": manifest["revision"],
            "files_verified": len(manifest["files"]), "weight_bytes": manifest["weight_bytes"]}


def download_model(manifest: dict, destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    required = sum(x["bytes"] for x in manifest["files"] if not (destination / x["path"]).exists())
    if shutil.disk_usage(destination).free < required + 1024 ** 3:
        raise RuntimeError("Insufficient free disk for model and temporary download")

    def fetch(record: dict) -> None:
        if Path(record["path"]).name != record["path"]:
            raise ValueError("Only flat model paths are allowed")
        final = destination / record["path"]
        if final.exists():
            verify_file(final, record)
            print("verified existing " + final.name, flush=True)
            return
        partial = destination / (record["path"] + ".partial")
        for attempt in range(3):
            try:
                offset = partial.stat().st_size if partial.exists() else 0
                if offset > record["bytes"]:
                    raise ValueError("Oversized partial download")
                if offset < record["bytes"]:
                    url = "https://huggingface.co/" + manifest["model_id"] + "/resolve/" + manifest["revision"] + "/" + record["path"]
                    headers = {"Range": "bytes=" + str(offset) + "-"} if offset else {}
                    request = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(request, timeout=90) as response:
                        resumed = response.status == 206
                        if resumed and not response.headers.get("Content-Range", "").startswith("bytes " + str(offset) + "-"):
                            raise ValueError("Unexpected HTTP content range")
                        with partial.open("ab" if resumed else "wb") as stream:
                            for block in iter(lambda: response.read(8 * 1024 * 1024), b""):
                                stream.write(block)
                verify_file(partial, record)
                partial.rename(final)
                print("downloaded " + final.name + " bytes=" + str(record["bytes"]), flush=True)
                return
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2)

    lock = destination / ".download.lock"
    with lock.open("x"):
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(fetch, manifest["files"]))
            report = verify_model(manifest, destination)
            (destination / "rsi-model-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            return report
        finally:
            lock.unlink()


def prepare_packages(destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "download", "--no-cache-dir",
                    "--only-binary=:all:", "--platform", "manylinux_2_17_x86_64",
                    "--platform", "manylinux2014_x86_64", "--python-version", "312",
                    "--abi", "cp312", "--dest", str(destination), *REQUIREMENTS], check=True)
    files = [{"path": x.name, "bytes": x.stat().st_size, "sha256": digest(x)}
             for x in sorted(destination.glob("*.whl"))]
    report = {"schema_version": "student-wheelhouse/v1", "requirements": REQUIREMENTS,
              "target": "Linux x86_64, CPython 3.12; reuses project torch 2.6.0+cu124", "files": files}
    (destination / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (destination / "requirements.lock").write_text("\n".join(REQUIREMENTS) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("download-model", "verify-model", "packages"))
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    if args.phase == "packages":
        report = prepare_packages(args.destination)
    else:
        if args.manifest is None:
            parser.error("--manifest is required for model operations")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        report = (download_model if args.phase == "download-model" else verify_model)(manifest, args.destination)
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
