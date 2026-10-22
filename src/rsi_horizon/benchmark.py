"""Benchmark manifests and task loading."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .contracts import BenchmarkTask, TaskSpec


def _source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: str | Path) -> tuple[dict[str, Any], list[BenchmarkTask]]:
    """Load a small TOML manifest without adding a dependency."""

    import tomllib

    manifest_path = Path(path).resolve()
    with manifest_path.open("rb") as handle:
        raw = tomllib.load(handle)
    benchmark = raw.get("benchmark", {})
    benchmark_id = str(benchmark["id"])
    version = str(benchmark["version"])
    tasks: list[BenchmarkTask] = []
    for raw_task in raw.get("tasks", []):
        task_id = str(raw_task["task_id"])
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", task_id):
            raise ValueError(f"task_id must be a safe single path component: {task_id!r}")
        split = str(raw_task.get("split", "toy"))
        if split not in {"train", "dev", "test", "challenge", "toy"}:
            raise ValueError(f"unsupported split for {task_id}: {split!r}")
        step_budget = int(raw_task["step_budget"]) if raw_task.get("step_budget") is not None else None
        if step_budget is not None and step_budget <= 0:
            raise ValueError(f"step_budget must be positive for {task_id}")
        public = TaskSpec(
                task_id=task_id,
                benchmark_id=benchmark_id,
                benchmark_version=version,
                instruction=str(raw_task["instruction"]),
            )
        tasks.append(BenchmarkTask(
                public=public,
                split=split,
                group_id=raw_task.get("group_id"),
                step_budget=step_budget,
                dependency_depth=(int(raw_task["dependency_depth"]) if raw_task.get("dependency_depth") is not None else None),
                annotation_source=str(raw_task.get("annotation_source", "manual_toy")),
                private_data=dict(raw_task.get("private_data", {})),
            )
        )
    if not tasks:
        raise ValueError(f"manifest contains no tasks: {manifest_path}")
    ids = [task.public.task_id for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("task_id values must be unique")
    manifest = {
        "path": str(manifest_path),
        "source_hash": _source_hash(manifest_path),
        "benchmark": benchmark,
        "task_count": len(tasks),
        "tasks": [
            {
                "public": task.public.to_dict(),
                "split": task.split,
                "group_id": task.group_id,
                "step_budget": task.step_budget,
                "dependency_depth": task.dependency_depth,
                "annotation_source": task.annotation_source,
            }
            for task in tasks
        ],
    }
    return manifest, tasks


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
