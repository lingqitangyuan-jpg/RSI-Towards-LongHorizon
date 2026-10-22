"""Export canonical SFT examples from student-visible trajectories only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import BenchmarkTask

_FORBIDDEN_KEYS = {
    "audit_private",
    "dependency_depth",
    "private_data",
    "review_reason",
    "reason",
    "review",
    "reviewer",
    "verifier",
}


def _check_public(value: Any, *, context: str) -> None:
    if isinstance(value, dict):
        leaked = _FORBIDDEN_KEYS.intersection(value)
        if leaked:
            raise ValueError(f"private fields in {context}: {', '.join(sorted(leaked))}")
        for child in value.values():
            _check_public(child, context=context)
    elif isinstance(value, list):
        for child in value:
            _check_public(child, context=context)


def export_sft_jsonl(
    task: BenchmarkTask,
    trajectory_path: str | Path,
    output_path: str | Path,
) -> int:
    """Write training examples for a train task from its public trajectory.

    The exporter intentionally accepts the public-view JSONL path only. It does
    not read audit logs, verifier results, task-private data, or evaluation rows.
    """

    if task.split != "train":
        raise ValueError(f"SFT export is restricted to train tasks, got split={task.split!r}")
    source = Path(trajectory_path)
    target = Path(output_path)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite SFT dataset: {target}")
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    examples: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    for row in rows:
        if set(row) != {"step", "observation_before", "accepted_action", "observation_after"}:
            raise ValueError("trajectory row does not match the student-visible schema")
        _check_public(row, context="student-visible trajectory")
        if row["observation_before"].get("task_id") != task.public.task_id:
            raise ValueError("trajectory task_id does not match the selected training task")
        examples.append({
            "schema_version": "rsi_horizon.sft.v1",
            "task_id": task.public.task_id,
            "benchmark_id": task.public.benchmark_id,
            "benchmark_version": task.public.benchmark_version,
            "step": row["step"],
            "input": {
                "instruction": task.public.instruction,
                "history": list(history),
                "observation": row["observation_before"],
            },
            "target_action": row["accepted_action"],
            "provenance": {"trajectory_view": "student_visible"},
        })
        history.append({
            "observation": row["observation_before"],
            "action": row["accepted_action"],
            "observation_after": row["observation_after"],
        })
    if not examples:
        raise ValueError("student-visible trajectory contains no examples")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        for example in examples:
            handle.write(json.dumps(example, ensure_ascii=False) + "\n")
    return len(examples)
