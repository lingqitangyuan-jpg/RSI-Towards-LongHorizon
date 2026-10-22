"""Non-invasive Harness-Zero integration boundary.

This module does not import Harbor or call a model.  It records what a future
adapter must supply and checks that a reference checkout has the expected
student/reviewer/training entry points.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HarnessZeroPlan:
    repo: Path
    student_entry: Path
    reviewer_entry: Path
    train_entry: Path
    appworld_agent: Path
    appworld_environment: Path

    def to_dict(self) -> dict[str, str]:
        return {field: str(getattr(self, field)) for field in self.__dataclass_fields__}


def inspect_checkout(repo: str | Path) -> HarnessZeroPlan:
    root = Path(repo).resolve()
    paths = {
        "student_entry": root / "src/harness_zero/student.py",
        "reviewer_entry": root / "src/harness_zero/review.py",
        "train_entry": root / "src/harness_zero/train.py",
        "appworld_agent": root / "envs/appworld/agent.py",
        "appworld_environment": root / "envs/appworld/harbor_environment.py",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("reference Harness-Zero checkout is incomplete: " + ", ".join(missing))
    return HarnessZeroPlan(repo=root, **paths)
