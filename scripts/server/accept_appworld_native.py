#!/usr/bin/env python3
"""Trusted, CPU-only AppWorld acceptance, not an agent sandbox or a model run.

Only fixed public read code and the official solution for one train/dev task are
executed. Protected data, official solution source and detailed grading reports
stay outside Git and are never exported as student training trajectories.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
import tomllib
import zipfile

DATA_SHA256 = "fd9f9608c2ec71ed0ac25c3633a738b9129a318a129e31230425b9188e508250"
PUBLIC_READ = "print(apis.supervisor.show_profile())"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and ":" not in name and "\\" not in name


def select_data(name: str, task_id: str) -> bool:
    if not safe_member(name):
        raise ValueError("Unsafe path in the official data archive")
    return (name in {"data/LICENSE", "data/README_BEFORE_SHARING.md", "data/version.txt"}
            or name.startswith(("data/api_docs/", "data/base_dbs/", "data/datasets/"))
            or name.startswith("data/tasks/" + task_id + "/"))


def prepare_data(bundle: Path, appworld_root: Path, task_id: str) -> dict:
    from appworld.common.constants import PASSWORD, SALT
    from appworld.common.utils import decrypt_bytes

    if sha256(bundle) != DATA_SHA256:
        raise ValueError("Official data bundle SHA256 mismatch")
    target = appworld_root / "data"
    if target.exists():
        raise FileExistsError("Refusing to replace an existing AppWorld data directory")
    decrypted = decrypt_bytes(bundle.read_bytes(), PASSWORD, SALT)
    files = []
    with zipfile.ZipFile(io.BytesIO(decrypted)) as archive:
        split = None
        for candidate in ("train", "dev"):
            ids = archive.read("data/datasets/" + candidate + ".txt").decode().split()
            if task_id in ids:
                split = candidate
                break
        if split is None:
            raise ValueError("Native acceptance only permits official train/dev tasks")
        entries = [item for item in archive.infolist() if not item.is_dir() and select_data(item.filename, task_id)]
        if not any(item.filename == "data/LICENSE" for item in entries):
            raise ValueError("Official data redistribution license is missing")
        appworld_root.mkdir(parents=True, exist_ok=True)
        for item in entries:
            destination = appworld_root / item.filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(item)
            with destination.open("xb") as output:
                output.write(data)
            files.append({"path": item.filename, "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()})
    required = ["data/version.txt", "data/LICENSE",
                "data/tasks/" + task_id + "/specs.json",
                "data/tasks/" + task_id + "/ground_truth/compiled_solution.py"]
    for name in required:
        if not (appworld_root / name).is_file():
            raise ValueError("Missing required task asset: " + name)
    report = {"schema_version": "appworld-native-data/v1", "task_id": task_id,
              "official_split": split, "data_bundle_sha256": DATA_SHA256,
              "data_version": (target / "version.txt").read_text().strip(),
              "scope": "public assets and one train/dev task; not a full benchmark installation",
              "file_count": len(files), "total_bytes": sum(f["bytes"] for f in files),
              "license": "Protected assets require encrypted public redistribution; keep unpacked assets out of Git",
              "files": files}
    (appworld_root / "native-data-manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def evaluate_stats(world) -> dict:
    # Official TestTracker records failed requirements instead of raising.
    # A no-op negative is expected to fail; it must still produce full stats.
    tracker = world.evaluate(suppress_errors=True)
    stats = tracker.to_dict(stats_only=True)
    stats.update({"pass_count": tracker.pass_count, "fail_count": tracker.fail_count})
    if stats["num_tests"] <= 0:
        raise RuntimeError("The official verifier executed no tests")
    return stats


def run_checks(config: dict, appworld_root: Path, run_id: str) -> dict:
    from appworld import AppWorld
    from importlib.metadata import version

    if version("appworld") != config["appworld_version"]:
        raise ValueError("AppWorld package version does not match the acceptance config")
    task_id = config["task_id"]
    data_manifest = json.loads((appworld_root / "native-data-manifest.json").read_text())
    if data_manifest["task_id"] != task_id or data_manifest["official_split"] not in {"train", "dev"}:
        raise ValueError("Data manifest task/split mismatch")
    original = {item["path"]: item["sha256"] for item in data_manifest["files"]}
    for name, expected in original.items():
        if sha256(appworld_root / name) != expected:
            raise ValueError("An original data asset changed before acceptance: " + name)
    solution_path = appworld_root / "data/tasks" / task_id / "ground_truth/compiled_solution.py"
    solution = solution_path.read_text() + "\nsolution(apis, requester)\n"
    cases = {}
    for name in ("no_op_before", "official_solution", "reset_no_op"):
        experiment = run_id + "_" + name
        if (appworld_root / "experiments/outputs" / experiment).exists():
            raise FileExistsError("Refusing to overwrite an existing AppWorld experiment")
        started = time.monotonic()
        with AppWorld(task_id=task_id, experiment_name=experiment,
                      load_ground_truth=True, random_seed=config["seed"],
                      max_interactions=config["max_interactions"],
                      max_api_calls_per_interaction=config["max_api_calls_per_interaction"],
                      timeout_seconds=config["execution_timeout_seconds"]) as world:
            if "supervisor" not in world.task.api_docs:
                raise RuntimeError("Public supervisor API documentation is unavailable")
            profile_output = world.execute(PUBLIC_READ)
            profile = json.loads(profile_output)
            public_profile_hash = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
            if name == "official_solution":
                result = world.execute(solution)
                if result.startswith("Execution failed."):
                    raise RuntimeError("Official solution failed; inspect private AppWorld logs on the server")
            completed = world.task_completed()
            stats = evaluate_stats(world)
        cases[name] = {"experiment_name": experiment, "public_profile_sha256": public_profile_hash,
                       "task_marked_complete": completed, "official_verifier": stats,
                       "wall_seconds": round(time.monotonic() - started, 3)}
        print(json.dumps({"case": name, "success": stats["success"],
                          "num_tests": stats["num_tests"]}), flush=True)
    unchanged = all(sha256(appworld_root / name) == expected for name, expected in original.items())
    checks = {"public_api_read": True,
              "negative_before_fails": not cases["no_op_before"]["official_verifier"]["success"],
              "official_solution_passes": cases["official_solution"]["official_verifier"]["success"],
              "reset_negative_fails": not cases["reset_no_op"]["official_verifier"]["success"],
              "reset_public_profile_matches": len({c["public_profile_sha256"] for c in cases.values()}) == 1,
              "original_data_unchanged": unchanged}
    return {"schema_version": "appworld-native-acceptance/v1",
            "status": "pass" if all(checks.values()) else "fail",
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id, "official_split": data_manifest["official_split"],
            "appworld_version": version("appworld"), "python": sys.version,
            "python_executable": sys.executable, "appworld_root": str(appworld_root),
            "run_id": run_id, "seed": config["seed"],
            "solution_sha256": sha256(solution_path), "checks": checks, "cases": cases,
            "model_calls": 0, "gpu_used": False, "sandbox_isolation_verified": False,
            "claim_limit": "native trusted environment smoke only; not agent, B0/B1, Harbor or RSI results",
            "training_export_allowed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare-data", "run"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    root = Path(config["paths"]["storage_root"]).resolve()
    appworld_root = Path(config["paths"]["appworld_root"]).resolve()
    cache = Path(config["paths"]["cache_root"]).resolve()
    for path in (appworld_root, cache, args.output.resolve()):
        if not path.is_relative_to(root):
            raise ValueError("Native acceptance paths must stay under the allocated project root")
    if not re.fullmatch(r"[0-9a-f]+_[0-9]+", config["task_id"]):
        raise ValueError("Invalid task ID")
    if args.output.exists():
        raise FileExistsError("Refusing to replace an existing acceptance report")
    os.environ.update({"APPWORLD_ROOT": str(appworld_root), "APPWORLD_CACHE": str(cache),
                       "IPYTHONDIR": str(cache / "ipython"), "CUDA_VISIBLE_DEVICES": "",
                       "PYTHONNOUSERSITE": "1"})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        if args.phase == "prepare-data":
            if args.bundle is None:
                parser.error("prepare-data requires --bundle")
            report = prepare_data(args.bundle.resolve(), appworld_root, config["task_id"])
            report = {k: v for k, v in report.items() if k != "files"}
        else:
            if not args.run_id or not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_id):
                parser.error("run requires a safe, unique --run-id")
            report = run_checks(config, appworld_root, args.run_id)
    except Exception as exc:
        report = {"status": "fail", "phase": args.phase, "error_type": type(exc).__name__,
                  "error": str(exc), "model_calls": 0, "gpu_used": False,
                  "claim_limit": "engineering acceptance only"}
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        raise
    report["config_sha256"] = sha256(args.config)
    report["script_sha256"] = sha256(Path(__file__).resolve())
    report["git_commit"] = subprocess.check_output(
        ["git", "-C", str(Path(__file__).resolve().parents[2]), "rev-parse", "HEAD"],
        text=True).strip()
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report, indent=2), flush=True)
    return int(report.get("status") == "fail")


if __name__ == "__main__":
    raise SystemExit(main())
