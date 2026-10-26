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
import threading
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


def download_model(manifest: dict, destination: Path, mirror: str = "huggingface") -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    required = sum(x["bytes"] for x in manifest["files"] if not (destination / x["path"]).exists())
    if shutil.disk_usage(destination).free < required * 2 + 1024 ** 3:
        raise RuntimeError("Insufficient free disk for model and temporary download")

    def fetch_small(record: dict) -> None:
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

    chunks = destination / ".chunks"
    chunks.mkdir(exist_ok=True)
    chunk_size = 64 * 1024 * 1024
    progress_lock = threading.Lock()
    progress = [0]

    def fetch_range(item) -> None:
        record, start, end = item
        part = chunks / (record["path"] + "." + str(start))
        expected = end - start + 1
        if not part.exists() or part.stat().st_size != expected:
            temporary = part.with_name(part.name + ".partial")
            for attempt in range(3):
                try:
                    offset = temporary.stat().st_size if temporary.exists() else 0
                    if offset > expected:
                        raise ValueError("Oversized partial range")
                    if offset < expected:
                        interval = str(start + offset) + "-" + str(end)
                        if mirror == "modelscope":
                            url = "https://modelscope.cn/models/" + manifest["model_id"] + "/resolve/master/" + record["path"]
                        else:
                            url = "https://huggingface.co/" + manifest["model_id"] + "/resolve/" + manifest["revision"] + "/" + record["path"]
                        request = urllib.request.Request(url + "?rsi_range=" + interval,
                                                         headers={"Range": "bytes=" + interval})
                        with urllib.request.urlopen(request, timeout=60) as response:
                            if response.status != 206 or not response.headers.get("Content-Range", "").startswith("bytes " + interval + "/"):
                                raise ValueError("Server did not return the exact requested range")
                            with temporary.open("ab" if offset else "wb") as stream:
                                remaining = expected - offset
                                while remaining:
                                    block = response.read(min(1024 * 1024, remaining))
                                    if not block:
                                        raise EOFError("Incomplete model range")
                                    stream.write(block)
                                    remaining -= len(block)
                    if temporary.stat().st_size != expected:
                        raise ValueError("Range size mismatch")
                    temporary.replace(part)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2)
        with progress_lock:
            progress[0] += 1
            if progress[0] % 16 == 0:
                print("completed weight ranges=" + str(progress[0]), flush=True)

    lock = destination / ".download.lock"
    with lock.open("x"):
        try:
            weights = [x for x in manifest["files"] if x["path"].endswith(".safetensors")]
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(fetch_small, [x for x in manifest["files"] if x not in weights]))
            pending = []
            ranges = []
            for record in weights:
                if (destination / record["path"]).exists():
                    verify_file(destination / record["path"], record)
                    continue
                pending.append(record)
                ranges.extend((record, start, min(start + chunk_size, record["bytes"]) - 1)
                              for start in range(0, record["bytes"], chunk_size))
            with ThreadPoolExecutor(max_workers=12) as pool:
                list(pool.map(fetch_range, ranges))
            for record in pending:
                partial = destination / (record["path"] + ".partial")
                with partial.open("wb") as output:
                    for start in range(0, record["bytes"], chunk_size):
                        with (chunks / (record["path"] + "." + str(start))).open("rb") as stream:
                            shutil.copyfileobj(stream, output, 8 * 1024 * 1024)
                verify_file(partial, record)
                partial.rename(destination / record["path"])
                for start in range(0, record["bytes"], chunk_size):
                    (chunks / (record["path"] + "." + str(start))).unlink()
                print("assembled and SHA256 verified " + record["path"], flush=True)
            report = verify_model(manifest, destination)
            report["weight_transport"] = mirror
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
    parser.add_argument("--mirror", choices=("huggingface", "modelscope"), default="huggingface")
    args = parser.parse_args()
    if args.phase == "packages":
        report = prepare_packages(args.destination)
    else:
        if args.manifest is None:
            parser.error("--manifest is required for model operations")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        report = (download_model(manifest, args.destination, args.mirror) if args.phase == "download-model"
                  else verify_model(manifest, args.destination))
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
