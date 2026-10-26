#!/usr/bin/env python3
"""Trusted grading only. Never import or execute client code or client rewards."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from rsi_horizon.integrations.appworld_rootless import directory_fingerprint, write_new_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--snapshot-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(os.environ["APPWORLD_ROOT"]).resolve()
    snapshot = root / "experiments/outputs" / args.experiment / "tasks" / args.task_id / "dbs"
    if not snapshot.resolve().is_relative_to(root) or directory_fingerprint(snapshot)["sha256"] != args.snapshot_sha256:
        raise ValueError("The trusted snapshot does not match the requested grading input")
    from appworld.evaluator import evaluate_task
    tracker = evaluate_task(task_id=args.task_id, experiment_name=args.experiment, suppress_errors=True)
    if tracker.num_tests <= 0 or tracker.pass_count + tracker.fail_count != tracker.num_tests:
        raise ValueError("Official verifier test counts are incomplete")
    report = {"schema_version": "appworld-rootless-verifier/v1", "success": bool(tracker.success),
              "num_tests": tracker.num_tests, "pass_count": tracker.pass_count,
              "fail_count": tracker.fail_count, "snapshot_sha256": args.snapshot_sha256,
              "verifier_process_id": os.getpid(), "task_id": args.task_id}
    write_new_json(args.output, report)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
