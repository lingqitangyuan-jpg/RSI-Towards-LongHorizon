#!/usr/bin/env python3
"""Install only AppWorld source and known training fixtures from a pinned snapshot.

The complete archive is retained separately. This is not official AppWorld data
installation and is not a complete converted benchmark checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tarfile

TRAIN_FIXTURES = ("07b42fd_1", "287e338_3", "2a163ab_3")
SOURCE_PREFIXES = ("src/", "envs/appworld/", "harness_bank/", "tests/")
ROOT_FILES = {"LICENSE", "README.md", "pyproject.toml", "uv.lock", ".env.example", ".gitignore"}


def selected(name: str) -> bool:
    return (name in ROOT_FILES or name.startswith(SOURCE_PREFIXES)
            or name == "data/appworld-harbor/split_train147.txt"
            or any(name.startswith("data/appworld-harbor/tasks/" + task + "/") for task in TRAIN_FIXTURES))


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def install(archive: Path, manifest: Path, destination: Path) -> dict:
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    canonical = json.dumps(metadata["files"], ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != metadata["content_sha256"]:
        raise ValueError("Snapshot file manifest does not match its content hash")
    if sha256(archive) != metadata["archive_sha256"]:
        raise ValueError("Reference archive SHA256 mismatch")
    expected = {item["path"]: item for item in metadata["files"] if selected(item["path"])}
    required = {"LICENSE", "pyproject.toml", "uv.lock", "envs/appworld/validate.py"}
    if not required.issubset(expected):
        raise ValueError("Snapshot lacks required reference source files")
    train_file = "data/appworld-harbor/split_train147.txt"
    if train_file not in expected:
        raise ValueError("Snapshot lacks the training task list")
    destination.mkdir(parents=True, exist_ok=False)
    extracted = set()
    with tarfile.open(archive) as source:
        for member in source:
            if member.name not in expected:
                continue
            if not member.isfile() or member.name in extracted:
                raise ValueError("Selected archive member is not a unique regular file: " + member.name)
            source.extract(member, destination, filter="data")
            extracted.add(member.name)
    if extracted != expected.keys():
        raise ValueError("Archive does not contain all selected source files")
    for name, item in expected.items():
        if sha256(destination / name) != item["sha256"]:
            raise ValueError("Extracted reference file SHA256 mismatch: " + name)
    train_ids = set((destination / train_file).read_text().split())
    if not set(TRAIN_FIXTURES).issubset(train_ids):
        raise ValueError("A fixture is not in the reference training split")
    report = {k: v for k, v in metadata.items() if k != "files"}
    report.update({
        "installation_scope": "appworld_source_and_three_training_fixtures",
        "complete_reference_checkout": False,
        "official_appworld_data_installed": False,
        "selected_file_count": len(expected),
        "selected_files_sha256_verified": len(extracted),
        "training_fixtures": list(TRAIN_FIXTURES),
        "destination": str(destination),
    })
    (destination / ".rsi-reference-snapshot.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(install(args.archive.resolve(), args.manifest.resolve(), args.destination.resolve()), indent=2))


if __name__ == "__main__":
    main()
