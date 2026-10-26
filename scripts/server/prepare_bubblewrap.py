#!/usr/bin/env python3
"""Prepare on Windows, verify/install privately on Linux; never use sudo.

The pinned Ubuntu security package is authenticated through its signed Release
metadata on the server. Existing verified installations are not extracted again.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request

BASE = "https://security.ubuntu.com/ubuntu/"
VERSION = "0.6.1-1ubuntu0.3"
PACKAGE = "bubblewrap_" + VERSION + "_amd64.deb"
PACKAGE_SHA256 = "706763714e5d19fc7ab2572a6418506e3a4a5d5960e99309707b58117e6b40cb"
BINARY_SHA256 = "bf6cf3d4456665f5d80c14eb79a76e0ce87b39a0883e614a161658b4ec33492c"
INDEX_PATH = "main/binary-amd64/Packages.gz"
STORAGE = Path("/nfs-data/sdf/lyf/rsi-long-horizon")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_stage(stage: Path) -> dict:
    index = stage / "Packages.gz"
    expected = [line.split() for line in (stage / "InRelease").read_text().splitlines()
                if line.strip().endswith(INDEX_PATH) and len(line.split()[0]) == 64]
    if expected != [[sha256(index), str(index.stat().st_size), INDEX_PATH]]:
        raise ValueError("Package index does not match the signed SHA256 entry")
    records = [part for part in gzip.decompress(index.read_bytes()).decode().split("\n\n")
               if part.startswith("Package: bubblewrap\n")]
    if len(records) != 1:
        raise ValueError("Expected exactly one Bubblewrap package entry")
    fields = dict(line.split(": ", 1) for line in records[0].splitlines()
                  if ": " in line and not line.startswith(" "))
    if (fields["Version"] != VERSION or fields["SHA256"] != PACKAGE_SHA256
            or fields["Filename"] != "pool/main/b/bubblewrap/" + PACKAGE):
        raise ValueError("Signed package metadata differs from the pinned release; review before upgrading")
    package = stage / PACKAGE
    if sha256(package) != PACKAGE_SHA256 or package.stat().st_size != int(fields["Size"]):
        raise ValueError("Bubblewrap package hash or size mismatch")
    return {"package": "bubblewrap", "version": VERSION, "source": BASE,
            "filename": PACKAGE, "sha256": PACKAGE_SHA256,
            "inrelease_sha256": sha256(stage / "InRelease"),
            "packages_index_sha256": sha256(index)}


def prepare(stage: Path) -> dict:
    stage.mkdir(parents=True, exist_ok=True)
    for name, path in [("InRelease", "dists/jammy-security/InRelease"),
                       ("Packages.gz", "dists/jammy-security/" + INDEX_PATH),
                       (PACKAGE, "pool/main/b/bubblewrap/" + PACKAGE)]:
        destination = stage / name
        if not destination.exists():
            with urllib.request.urlopen(BASE + path, timeout=60) as response:
                data = response.read(20_000_001)
            if len(data) > 20_000_000:
                raise ValueError("Unexpectedly large upstream artifact")
            with destination.open("xb") as output:
                output.write(data)
    report = verify_stage(stage)
    report["signature_verification"] = "pending Linux gpgv verification; not authenticated by hashes alone"
    return report


def install(stage: Path) -> dict:
    if os.name != "posix" or os.geteuid() == 0:
        raise RuntimeError("Install as the ordinary allocated Linux user, without sudo")
    signature = subprocess.run(["gpgv", "--keyring", "/usr/share/keyrings/ubuntu-archive-keyring.gpg",
                                str(stage / "InRelease")], check=True, text=True, capture_output=True)
    report = verify_stage(stage)
    prefix = STORAGE / "tools" / ("bubblewrap-" + VERSION)
    binary = prefix / "usr/bin/bwrap"
    already_installed = prefix.exists()
    if not already_installed:
        prefix.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["dpkg-deb", "--extract", str(stage / PACKAGE), str(prefix)], check=True)
    if not binary.is_file() or sha256(binary) != BINARY_SHA256 or binary.stat().st_mode & 0o6000:
        raise RuntimeError("Private binary is incomplete, modified or setuid; refusing to overwrite it")
    report.update({"signature_verification": "gpgv Ubuntu archive keyring: pass",
                   "signature_output": signature.stderr.strip(),
                   "binary": str(binary), "binary_sha256": sha256(binary),
                   "binary_mode": oct(binary.stat().st_mode & 0o7777),
                   "already_installed": already_installed, "uid": os.geteuid(),
                   "runtime_version": subprocess.check_output([str(binary), "--version"], text=True).strip(),
                   "sudo_used": False, "system_package_database_modified": False})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "install"))
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.report and args.report.exists():
        raise FileExistsError("Refusing to overwrite an earlier installation report")
    report = prepare(args.stage) if args.phase == "prepare" else install(args.stage)
    report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
