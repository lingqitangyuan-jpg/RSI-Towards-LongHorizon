"""Small artifact/archive boundary inspired by HyperAgents lineage handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..contracts import ArtifactRef


@dataclass(frozen=True)
class ImproverCandidate:
    """A candidate A/K program; evaluation is external and never implicit."""

    ref: ArtifactRef
    parent: ArtifactRef | None
    patch_path: Path | None
    evaluation_path: Path | None
    metadata: dict[str, str] = field(default_factory=dict)


class Archive:
    """Append-only candidate index.  It does not decide that a candidate improved."""

    def __init__(self) -> None:
        self._items: list[ImproverCandidate] = []

    def add(self, candidate: ImproverCandidate) -> None:
        if any(item.ref.artifact_id == candidate.ref.artifact_id for item in self._items):
            raise ValueError(f"duplicate artifact: {candidate.ref.artifact_id}")
        self._items.append(candidate)

    def items(self) -> tuple[ImproverCandidate, ...]:
        return tuple(self._items)
