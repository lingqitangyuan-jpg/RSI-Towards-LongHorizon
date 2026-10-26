"""Create a private Python environment from existing, read-only Conda archives."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--base", type=Path, default=Path("/opt/miniforge3"))
    parser.add_argument("--env-name", default="rsi-py312")
    args = parser.parse_args()
    root = args.root.resolve()
    base = args.base.resolve()
    if root == Path("/home") or Path("/home") in root.parents or "sdk" in root.parts:
        raise SystemExit("Refusing /home or the unavailable sdk disk")
    if args.env_name in {".", ".."} or Path(args.env_name).name != args.env_name:
        raise SystemExit("--env-name must be a single directory name")
    prefix = root / "envs" / args.env_name
    if (prefix / "conda-meta" / "history").exists():
        raise SystemExit(f"Existing environment left unchanged: {prefix}")
    records = {}
    for path in (base / "conda-meta").glob("*.json"):
        record = json.loads(path.read_text())
        records[record["name"]] = record
    if not records.get("python", {}).get("version", "").startswith("3.12."):
        raise SystemExit("This bootstrap expects cached Python 3.12")
    selected = {}
    pending = ["python", "pip", "setuptools", "wheel"]
    while pending:
        name = pending.pop()
        if name.startswith("__") or name in selected:
            continue
        if name not in records:
            raise SystemExit(f"Dependency missing from base metadata: {name}")
        record = records[name]
        selected[name] = record
        pending.extend(dep.split()[0] for dep in record.get("depends", []))
    archives = []
    for name, record in sorted(selected.items()):
        stem = f"{name}-{record['version']}-{record['build']}"
        candidates = [base / "pkgs" / record.get("fn", f"{stem}.conda"),
                      base / "pkgs" / f"{stem}.conda",
                      base / "pkgs" / f"{stem}.tar.bz2"]
        archive = next((p for p in candidates if p.is_file()), None)
        if archive is None:
            raise SystemExit(f"Archive missing; no download attempted: {stem}")
        archives.append(archive)
    for relative in ["envs", "cache/conda/pkgs", "cache/xdg", "tmp", "reports"]:
        (root / relative).mkdir(parents=True, exist_ok=True)
    spec = root / "reports" / "conda-cache-explicit.txt"
    spec.write_text("@EXPLICIT\n" + "\n".join(p.as_uri() for p in archives) + "\n")
    source = {"base": str(base), "prefix": str(prefix), "packages": [
        {key: record.get(key) for key in ("name", "version", "build", "fn", "sha256", "md5")}
        for _, record in sorted(selected.items())]}
    (root / "reports" / "conda-cache-source.json").write_text(json.dumps(source, indent=2) + "\n")
    env = dict(os.environ, CONDA_ENVS_PATH=str(root / "envs"),
               CONDA_PKGS_DIRS=str(root / "cache/conda/pkgs"),
               XDG_CACHE_HOME=str(root / "cache/xdg"), TMPDIR=str(root / "tmp"))
    command = [str(base / "bin/conda"), "create", "--yes", "--offline", "--copy",
               "--prefix", str(prefix), "--file", str(spec)]
    print(f"Creating {prefix} from {len(archives)} cached archives", flush=True)
    subprocess.run(command, check=True, env=env)
    subprocess.run([str(prefix / "bin/python"), "--version"], check=True, env=env)
    subprocess.run([str(prefix / "bin/python"), "-m", "pip", "--version"], check=True, env=env)


if __name__ == "__main__":
    main()
