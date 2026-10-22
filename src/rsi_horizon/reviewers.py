"""Reviewers and private Harness guidance for the local demo."""

from __future__ import annotations

from typing import Protocol

from .contracts import Action, ArtifactRef, Observation, ReviewDecision, TaskSpec


class Reviewer(Protocol):
    artifact_id: str
    harness: ArtifactRef
    reviewer: ArtifactRef

    def review(self, task: TaskSpec, observation: Observation, action: Action) -> ReviewDecision: ...


class RuleReviewer:
    """A private K-like reviewer that repairs an invalid next-stage action."""

    def __init__(self, harness_id: str = "toy-k0", reviewer_id: str = "rule-reviewer-v0") -> None:
        self.artifact_id = reviewer_id
        self.harness = ArtifactRef("harness", harness_id, source_hash="toy-private-rules")
        self.reviewer = ArtifactRef("reviewer", reviewer_id, source_hash="stdlib-demo")

    def review(self, task: TaskSpec, observation: Observation, action: Action) -> ReviewDecision:
        del task
        stage_index = int(observation.state_label.split("_", 1)[1])
        expected = Action("advance", {"index": stage_index})
        if action == expected:
            return ReviewDecision("PASS", action, "action matches private K state rule", self.reviewer, self.harness)
        return ReviewDecision(
            "REPLACE",
            expected,
            "private K repaired an action inconsistent with the current dependency state",
            self.reviewer,
            self.harness,
        )
