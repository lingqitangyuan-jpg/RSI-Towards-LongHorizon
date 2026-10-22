"""Command-line entry point for the dependency-free MVP."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

from .benchmark import load_manifest
from .environment import ToyStateChain
from .policies import DriftStudent
from .reviewers import RuleReviewer
from .runner import run_manifest
from .sft_export import export_sft_jsonl


def _run(args: argparse.Namespace) -> int:
    manifest, tasks = load_manifest(args.manifest)
    if args.split:
        tasks = [task for task in tasks if task.split == args.split]
    if not tasks:
        raise SystemExit("no tasks remain after split filter")
    selected_ids = {task.public.task_id for task in tasks}
    manifest["tasks"] = [row for row in manifest["tasks"] if row["public"]["task_id"] in selected_ids]
    manifest["task_count"] = len(tasks)
    manifest["selected_splits"] = sorted({task.split for task in tasks})
    output = Path(args.output).resolve() if args.output else Path("runs") / ("toy-demo-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty run directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    common = dict(
        tasks=tasks,
        output_dir=output / "bare",
        mode="bare",
        student_factory=lambda: DriftStudent(drift_at=args.drift_at),
        environment_factory=ToyStateChain,
    )
    bare = run_manifest(**common)
    reviewed = run_manifest(
        tasks,
        output / "reviewed",
        mode="reviewed",
        student_factory=lambda: DriftStudent(drift_at=args.drift_at),
        environment_factory=ToyStateChain,
        reviewer_factory=RuleReviewer,
    )
    summary = {
        "manifest": manifest,
        "configuration": {
            "environment": "toy-state-chain",
            "student": "drift-demo-v0",
            "drift_at": args.drift_at,
            "reviewer": "rule-reviewer-v0",
            "max_reviews": None,
            "max_replacements": None,
        },
        "bare_successes": sum(item.success for item in bare),
        "reviewed_successes": sum(item.success for item in reviewed),
        "bare": [item.to_dict() for item in bare],
        "reviewed": [item.to_dict() for item in reviewed],
        "notice": "Toy run validates contracts and logging only; it is not benchmark evidence.",
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _export_sft(args: argparse.Namespace) -> int:
    _, tasks = load_manifest(args.manifest)
    matches = [task for task in tasks if task.public.task_id == args.task_id]
    if not matches:
        raise SystemExit(f"unknown task_id: {args.task_id}")
    count = export_sft_jsonl(matches[0], args.trajectory, args.output)
    print(json.dumps({"output": str(Path(args.output).resolve()), "examples": count}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="rsi-horizon")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("demo", help="run the deterministic toy benchmark")
    run.add_argument("--manifest", default="configs/toy_benchmark.toml")
    run.add_argument("--output", default=None, help="new/empty run directory; default is a UTC timestamp under runs/")
    run.add_argument("--split", choices=["train", "dev", "test", "challenge", "toy"], default=None)
    run.add_argument("--drift-at", type=int, default=1)
    run.set_defaults(handler=_run)
    export_sft = sub.add_parser("export-sft", help="export training examples from a student-visible trajectory")
    export_sft.add_argument("--manifest", default="configs/toy_benchmark.toml")
    export_sft.add_argument("--task-id", required=True)
    export_sft.add_argument("--trajectory", required=True, help="path to student_visible.jsonl")
    export_sft.add_argument("--output", required=True, help="new JSONL output path")
    export_sft.set_defaults(handler=_export_sft)
    args = parser.parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
