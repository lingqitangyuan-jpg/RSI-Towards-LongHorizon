#!/usr/bin/env python3
"""Build an offline Git bundle and hash-pinned third-party snapshot.

Run from a clean, committed project. Output belongs outside the Git checkout.
The reference snapshot preserves LICENSE files and is not added to Git history.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

EXCLUDED_DIRS = {".git", "__pycache__", ".venv", ".pytest_cache", ".mypy_cache", "node_modules"}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def snapshot_reference(source: Path, destination: Path) -> dict:
    files = []
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Reference symlink requires an explicit policy: {rel}")
        if not path.is_file():
            continue
        if path.name in {".env", ".DS_Store"} or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.name.startswith(".env.") and path.name != ".env.example":
            continue
        files.append({"path": rel.as_posix(), "size": path.stat().st_size, "sha256": sha256(path)})
    canonical = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    target = destination / ("harness-zero-" + digest[:16] + ".tar.gz")
    with target.open("xb") as stream, gzip.GzipFile(fileobj=stream, mode="wb", mtime=0, filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for item in files:
                path = source / item["path"]
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise RuntimeError(f"Source changed while packaging: {path}")
                info = tarfile.TarInfo(item["path"])
                info.size = len(data)
                # ZIP reference copies may lose Unix executable bits. Consumers
                # should invoke scripts with bash/python instead of relying on mode.
                info.mode = 0o755 if path.suffix == ".sh" else 0o644
                archive.addfile(info, io.BytesIO(data))
    manifest = {
        "schema_version": "reference-snapshot/v1",
        "name": "harness-zero",
        "upstream": "https://github.com/metaevo-ai/harness-zero",
        "revision_type": "local_content_snapshot_not_upstream_commit",
        "content_sha256": digest,
        "archive": target.name,
        "archive_sha256": sha256(target),
        "file_count": len(files),
        "uncompressed_bytes": sum(item["size"] for item in files),
        "files": files,
    }
    (destination / "harness-zero.snapshot.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--hz-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    project = args.project_root.resolve()
    source = args.hz_root.resolve()
    output = args.output_dir.resolve()
    if output == project or project in output.parents:
        parser.error("Output must be outside the project Git checkout")
    if git(project, "status", "--porcelain"):
        parser.error("Commit intended project changes before packaging")
    for name in ("LICENSE", "pyproject.toml", "uv.lock", "envs/appworld/validate.py"):
        if not (source / name).is_file():
            parser.error(f"Incomplete reference checkout: {name}")
    output.mkdir(parents=True, exist_ok=False)
    commit = git(project, "rev-parse", "HEAD")
    bundle = output / ("project-" + commit[:12] + ".bundle")
    git(project, "bundle", "create", str(bundle), "HEAD")
    reference = snapshot_reference(source, output)
    manifest = {
        "schema_version": "offline-deployment/v1",
        "project_commit": commit,
        "project_origin": git(project, "remote", "get-url", "origin"),
        "project_bundle": bundle.name,
        "project_bundle_sha256": sha256(bundle),
        "reference_snapshot": {k: v for k, v in reference.items() if k != "files"},
        "excludes": ["model weights", "Conda environments", "run outputs", "credentials"],
        "includes": ["Git-tracked project and history", "local Harness-Zero reference with its converted benchmark tasks"],
        "official_appworld_data_included": False,
    }
    (output / "deployment.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksums = [f"{sha256(path)}  {path.name}" for path in sorted(output.iterdir()) if path.is_file()]
    (output / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in manifest.items() if k != "excludes"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
