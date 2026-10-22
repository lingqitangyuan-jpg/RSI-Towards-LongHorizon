"""Episode runner and artifact writer.

The runner keeps three boundaries explicit:
1. the student-visible accepted trajectory;
2. the private audit trajectory containing original proposals and review reasons;
3. the independent verifier result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import Action, ArtifactRef, BenchmarkTask, EpisodeResult, ReviewDecision, TrajectoryStep
from .environment import Environment
from .policies import StudentPolicy
from .reviewers import Reviewer


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def run_episode(
    task: BenchmarkTask,
    student: StudentPolicy,
    environment: Environment,
    reviewer: Reviewer | None = None,
    *,
    max_steps: int | None = None,
    max_reviews: int | None = None,
    max_replacements: int | None = None,
) -> tuple[EpisodeResult, list[TrajectoryStep], list[dict[str, Any]]]:
    step_limit = max_steps if max_steps is not None else task.step_budget
    if step_limit is None or step_limit <= 0:
        raise ValueError(f"episode requires a positive max_steps or task step_budget: {task.public.task_id}")
    trajectory: list[TrajectoryStep] = []
    audit: list[dict[str, Any]] = []
    reviewed_steps = replacements = budget_passes = 0
    verifier: dict[str, Any] = {}
    try:
        student.reset(task.public)
        observation = environment.reset(task)
        terminal_failure: dict[str, Any] | None = None
        for step in range(step_limit):
            original = student.act(task.public, observation)
            budget_exhausted = (
                reviewer is not None
                and ((max_reviews is not None and reviewed_steps >= max_reviews)
                     or (max_replacements is not None and replacements >= max_replacements))
            )
            decision: ReviewDecision | None = (
                reviewer.review(task.public, observation, original)
                if reviewer is not None and not budget_exhausted
                else None
            )
            if budget_exhausted:
                budget_passes += 1
            if decision is None:
                accepted = original
                decision_name = None
            else:
                reviewed_steps += 1
                accepted = decision.accepted_action
                decision_name = decision.decision
                if decision.decision == "REPLACE":
                    replacements += 1
            audit.append({
                "step": step,
                "task_id": task.public.task_id,
                "original_action": original.to_dict(),
                "accepted_action": accepted.to_dict(),
                "review_status": (
                    "budget_exhausted" if budget_exhausted
                    else "reviewed" if decision is not None
                    else "reviewer_disabled"
                ),
                "review": decision.to_dict() if decision is not None else None,
            })
            validator = getattr(environment, "validate_action", None)
            action_error = validator(accepted, observation) if validator else None
            if action_error:
                terminal_failure = {
                    "status": "failure",
                    "success": False,
                    "reason": f"action rejected by target schema: {action_error}",
                    "environment_steps": len(trajectory),
                }
                break
            outcome = environment.step(accepted)
            trajectory.append(
                TrajectoryStep(
                    step=step,
                    observation_before=observation,
                    original_action=original,
                    accepted_action=accepted,
                    observation_after=outcome.observation,
                    reviewed=decision is not None,
                    decision=decision_name,
                )
            )
            observation = outcome.observation
            if outcome.done:
                break
        verifier = terminal_failure if terminal_failure is not None else environment.verify(task)
    except Exception as exc:  # preserve infrastructure failures as data
        result = EpisodeResult(task.public.task_id, "infra_error", False, 0.0, len(trajectory), reviewed_steps, replacements, budget_passes, repr(exc))
        return result, trajectory, audit
    if verifier.get("status") == "verifier_error":
        result = EpisodeResult(task.public.task_id, "verifier_error", False, 0.0, len(trajectory), reviewed_steps, replacements, budget_passes, str(verifier.get("reason")), verifier)
    elif verifier.get("status") == "infra_error":
        result = EpisodeResult(task.public.task_id, "infra_error", False, 0.0, len(trajectory), reviewed_steps, replacements, budget_passes, str(verifier.get("reason")), verifier)
    else:
        success = bool(verifier.get("success"))
        timed_out = not success and len(trajectory) >= step_limit
        status = "timeout" if timed_out else ("success" if success else "failure")
        result = EpisodeResult(task.public.task_id, status, success, 1.0 if success else 0.0, len(trajectory), reviewed_steps, replacements, budget_passes, verifier=verifier)
    return result, trajectory, audit


def write_episode(out_dir: str | Path, result: EpisodeResult, trajectory: list[TrajectoryStep], audit: list[dict[str, Any]], *, refs: dict[str, ArtifactRef]) -> None:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise FileExistsError(f"refusing to overwrite episode artifacts in non-empty directory: {root}")
    _write_jsonl(root / "student_visible.jsonl", [step.student_visible_dict() for step in trajectory])
    _write_jsonl(root / "audit_private.jsonl", audit)
    (root / "summary.json").write_text(json.dumps({"result": result.to_dict(), "artifacts": {k: v.to_dict() for k, v in refs.items()}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_manifest(tasks: list[BenchmarkTask], output_dir: str | Path, *, mode: str, student_factory, environment_factory, reviewer_factory=None, max_reviews: int | None = None, max_replacements: int | None = None) -> list[EpisodeResult]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty run directory: {root}")
    results: list[EpisodeResult] = []
    for task in tasks:
        student = student_factory()
        env = environment_factory()
        reviewer = reviewer_factory() if reviewer_factory else None
        result, trajectory, audit = run_episode(task, student, env, reviewer, max_reviews=max_reviews, max_replacements=max_replacements)
        refs = {"student": ArtifactRef("model", student.artifact_id), "run": ArtifactRef("run", f"demo-{mode}")}
        if reviewer:
            refs.update({"reviewer": reviewer.reviewer, "harness": reviewer.harness})
        write_episode(root / task.public.task_id, result, trajectory, audit, refs=refs)
        results.append(result)
    summary = {"mode": mode, "task_count": len(tasks), "results": [result.to_dict() for result in results]}
    (root / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results
