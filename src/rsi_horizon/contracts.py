"""Stable data contracts shared by environments, agents, reviewers and runners.

The contracts intentionally use only the Python standard library.  They are the
boundary between the small local demo and later adapters for Harbor/AppWorld,
training backends, or a HyperAgents-style outer loop.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EpisodeStatus = Literal["success", "failure", "timeout", "infra_error", "verifier_error"]
ReviewDecisionType = Literal["PASS", "REPLACE"]
_PRIVATE_OBSERVATION_KEYS = {
    "audit_private",
    "private_data",
    "reason",
    "review",
    "reviewer",
    "verifier",
}


def _validate_public_value(value: Any, *, field_name: str) -> None:
    if isinstance(value, dict):
        leaked = _PRIVATE_OBSERVATION_KEYS.intersection(value)
        if leaked:
            raise ValueError(f"private fields in public observation {field_name}: {', '.join(sorted(leaked))}")
        for child in value.values():
            _validate_public_value(child, field_name=field_name)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _validate_public_value(child, field_name=field_name)


@dataclass(frozen=True)
class ArtifactRef:
    """A versioned reference to an object that may be inherited across rounds."""

    kind: Literal["model", "harness", "reviewer", "improver", "task_course", "run"]
    artifact_id: str
    parent_id: str | None = None
    source_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskSpec:
    """The public task view passed to student and reviewer policies."""

    task_id: str
    benchmark_id: str
    benchmark_version: str
    instruction: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkTask:
    """Runner/environment record; never pass this object to an agent."""

    public: TaskSpec
    split: Literal["train", "dev", "test", "challenge", "toy"]
    group_id: str | None
    step_budget: int | None = None
    dependency_depth: int | None = None
    annotation_source: str = "unknown"
    private_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Observation:
    """Public environment observation; private verifier state must not appear here."""

    task_id: str
    step: int
    state_label: str
    available_actions: tuple[str, ...]
    last_result: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_public_value(self.metadata, field_name="metadata")

    def to_dict(self) -> dict[str, Any]:
        _validate_public_value(self.metadata, field_name="metadata")
        data = asdict(self)
        data["available_actions"] = list(self.available_actions)
        return data


@dataclass(frozen=True)
class Action:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StepOutcome:
    observation: Observation
    done: bool
    reward: float
    info: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewDecision:
    """Private audit decision.  ``reason`` never enters the student-visible log."""

    decision: ReviewDecisionType
    accepted_action: Action
    reason: str
    reviewer: ArtifactRef
    harness: ArtifactRef

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["accepted_action"] = self.accepted_action.to_dict()
        data["reviewer"] = self.reviewer.to_dict()
        data["harness"] = self.harness.to_dict()
        return data


@dataclass(frozen=True)
class TrajectoryStep:
    step: int
    observation_before: Observation
    original_action: Action
    accepted_action: Action
    observation_after: Observation
    reviewed: bool
    decision: ReviewDecisionType | None

    def student_visible_dict(self) -> dict[str, Any]:
        """Return only the accepted interaction, suitable for training/export."""

        return {
            "step": self.step,
            "observation_before": self.observation_before.to_dict(),
            "accepted_action": self.accepted_action.to_dict(),
            "observation_after": self.observation_after.to_dict(),
        }

    def audit_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["observation_before"] = self.observation_before.to_dict()
        data["original_action"] = self.original_action.to_dict()
        data["accepted_action"] = self.accepted_action.to_dict()
        data["observation_after"] = self.observation_after.to_dict()
        return data


@dataclass(frozen=True)
class EpisodeResult:
    task_id: str
    status: EpisodeStatus
    success: bool
    reward: float
    steps: int
    reviewed_steps: int
    replacements: int
    budget_passes: int
    error: str | None = None
    verifier: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
