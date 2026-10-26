"""Student policies used by the local demo."""

from __future__ import annotations

from typing import Protocol

from .contracts import Action, Observation, TaskSpec


class StudentActionError(ValueError):
    """Invalid model output or exhausted student context, counted as task failure."""


class StudentPolicy(Protocol):
    artifact_id: str

    def reset(self, task: TaskSpec) -> None: ...

    def act(self, task: TaskSpec, observation: Observation) -> Action: ...


class DriftStudent:
    """A deterministic student with an injectable state-tracking error.

    The deliberate drift makes the demo exercise audit/replace and timeout
    paths.  It is not a model and must never be used as research evidence.
    """

    def __init__(self, drift_at: int | None = 1, artifact_id: str = "student-demo-v0") -> None:
        self.drift_at = drift_at
        self.artifact_id = artifact_id
        self._cursor = 0

    def reset(self, task: TaskSpec) -> None:
        self._cursor = 0

    def act(self, task: TaskSpec, observation: Observation) -> Action:
        del task
        index = int(observation.state_label.split("_", 1)[1])
        self._cursor += 1
        if self.drift_at is not None and index == self.drift_at:
            return Action("advance", {"index": index + 1})
        return Action("advance", {"index": index})
