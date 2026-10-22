"""Environment protocols and a deterministic state-chain benchmark for smoke tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .contracts import Action, BenchmarkTask, Observation, StepOutcome


class Environment(Protocol):
    def reset(self, task: BenchmarkTask) -> Observation: ...

    def step(self, action: Action) -> StepOutcome: ...

    def verify(self, task: BenchmarkTask) -> dict[str, object]: ...


@dataclass
class ToyStateChain:
    """A tiny stateful environment whose success requires ordered dependencies.

    It is an engineering smoke target, not a long-horizon benchmark result.  The
    required operation count is kept in the runner/environment task record and
    is not exposed as a hidden label.  A real adapter should replace this verifier with the
    trusted snapshot/verifier protocol of its benchmark.
    """

    max_steps_extra: int = 2

    def __post_init__(self) -> None:
        self.task: TaskSpec | None = None
        self.index = 0
        self.steps = 0
        self.invalid_actions = 0
        self.last_result: str | None = None

    def reset(self, task: BenchmarkTask) -> Observation:
        if int(task.private_data.get("operation_count", 0)) <= 0:
            raise ValueError(f"task has no operations: {task.public.task_id}")
        self.task = task
        self.index = 0
        self.steps = 0
        self.invalid_actions = 0
        self.last_result = None
        return self._observation()

    def _observation(self) -> Observation:
        assert self.task is not None
        return Observation(
            task_id=self.task.public.task_id,
            step=self.steps,
            state_label=f"stage_{self.index}",
            available_actions=("advance", "finish"),
            last_result=self.last_result,
        )

    def validate_action(self, action: Action, observation: Observation) -> str | None:
        if action.name == "advance":
            if set(action.arguments) != {"index"}:
                return "advance action requires exactly one integer index"
            if not isinstance(action.arguments["index"], int) or isinstance(action.arguments["index"], bool):
                return "advance index must be an integer"
            return None
        if action.name == "finish" and not action.arguments:
            return None
        return f"unsupported action or unexpected arguments: {action.name}"

    def step(self, action: Action) -> StepOutcome:
        if self.task is None:
            raise RuntimeError("reset must be called before step")
        self.steps += 1
        required = int(self.task.private_data["operation_count"])
        action_error = self.validate_action(action, self._observation())
        is_valid = action_error is None and action.name == "advance" and action.arguments.get("index") == self.index
        if is_valid:
            self.index += 1
            self.last_result = "accepted"
        elif action.name == "finish" and action_error is None and self.index == required:
            self.last_result = "finished"
        else:
            self.invalid_actions += 1
            self.last_result = "invalid_action"
        done = self.index >= required
        return StepOutcome(
            observation=self._observation(),
            done=done,
            reward=1.0 if done else 0.0,
            info={
                "valid_action": is_valid,
                "invalid_actions": self.invalid_actions,
                "action_error": action_error,
            },
        )

    def verify(self, task: BenchmarkTask) -> dict[str, object]:
        if self.task is None or self.task.public.task_id != task.public.task_id:
            return {"status": "verifier_error", "reason": "task state is missing or mismatched"}
        required = int(task.private_data["operation_count"])
        success = self.index == required
        return {
            "status": "success" if success else "failure",
            "success": success,
            "completed_stages": self.index,
            "required_operations": required,
            "environment_steps": self.steps,
            "invalid_actions": self.invalid_actions,
        }
