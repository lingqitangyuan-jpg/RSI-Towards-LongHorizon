import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rsi_horizon.benchmark import load_manifest
from rsi_horizon.contracts import Action, ArtifactRef, Observation
from rsi_horizon.environment import ToyStateChain
from rsi_horizon.integrations.harness_zero import inspect_checkout
from rsi_horizon.integrations.hyperagents import Archive, ImproverCandidate
from rsi_horizon.policies import DriftStudent
from rsi_horizon.reviewers import RuleReviewer
from rsi_horizon.runner import run_episode, write_episode
from rsi_horizon.sft_export import export_sft_jsonl


ROOT = Path(__file__).resolve().parents[1]


class MvpTests(unittest.TestCase):
    def test_manifest_has_disjoint_task_ids_and_explicit_depth(self):
        manifest, tasks = load_manifest(ROOT / "configs/toy_benchmark.toml")
        self.assertEqual(manifest["task_count"], 2)
        self.assertEqual({task.split for task in tasks}, {"train", "test"})
        self.assertTrue(all(task.dependency_depth for task in tasks))

    def test_reviewer_recovers_state_chain(self):
        _, tasks = load_manifest(ROOT / "configs/toy_benchmark.toml")
        task = tasks[1]
        bare, _, _ = run_episode(task, DriftStudent(), ToyStateChain())
        reviewed, trajectory, audit = run_episode(task, DriftStudent(), ToyStateChain(), RuleReviewer())
        self.assertFalse(bare.success)
        self.assertTrue(reviewed.success)
        self.assertGreaterEqual(reviewed.replacements, 1)
        self.assertEqual(len(trajectory), len(audit))
        self.assertEqual(audit[0]["review"]["decision"], "PASS")
        self.assertEqual(audit[1]["review"]["decision"], "REPLACE")
        self.assertNotIn("reason", trajectory[1].student_visible_dict())

    def test_toy_run_is_deterministic(self):
        _, tasks = load_manifest(ROOT / "configs/toy_benchmark.toml")
        first = run_episode(tasks[0], DriftStudent(), ToyStateChain(), RuleReviewer())
        second = run_episode(tasks[0], DriftStudent(), ToyStateChain(), RuleReviewer())
        self.assertEqual(first[0].to_dict(), second[0].to_dict())
        self.assertEqual(
            [step.student_visible_dict() for step in first[1]],
            [step.student_visible_dict() for step in second[1]],
        )
        self.assertEqual(first[2], second[2])

    def test_reviewer_budget_is_finite_and_visible_in_metrics(self):
        _, tasks = load_manifest(ROOT / "configs/toy_benchmark.toml")
        result, _, _ = run_episode(tasks[1], DriftStudent(), ToyStateChain(), RuleReviewer(), max_replacements=0)
        self.assertFalse(result.success)
        self.assertEqual(result.budget_passes, result.steps)
        self.assertEqual(result.budget_passes, 6)

    def test_private_task_record_is_not_passed_to_student(self):
        _, tasks = load_manifest(ROOT / "configs/toy_benchmark.toml")
        task = tasks[1]
        student = DriftStudent(drift_at=None)
        original = student.act

        def guarded(public_task, observation):
            self.assertFalse(hasattr(public_task, "private_data"))
            self.assertFalse(hasattr(public_task, "dependency_depth"))
            return original(public_task, observation)

        student.act = guarded
        result, _, _ = run_episode(task, student, ToyStateChain())
        self.assertTrue(result.success)

    def test_artifacts_separate_public_and_private_logs(self):
        _, tasks = load_manifest(ROOT / "configs/toy_benchmark.toml")
        result, trajectory, audit = run_episode(tasks[0], DriftStudent(), ToyStateChain(), RuleReviewer())
        with tempfile.TemporaryDirectory() as tmp:
            write_episode(tmp, result, trajectory, audit, refs={})
            public = Path(tmp, "student_visible.jsonl").read_text(encoding="utf-8")
            private = Path(tmp, "audit_private.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("private K repaired", public)
            self.assertIn("private K repaired", private)
            json.loads(Path(tmp, "summary.json").read_text(encoding="utf-8"))

    def test_sft_export_uses_only_train_public_trajectory(self):
        _, tasks = load_manifest(ROOT / "configs/toy_benchmark.toml")
        task = tasks[0]
        _, trajectory, _ = run_episode(task, DriftStudent(), ToyStateChain(), RuleReviewer())
        with tempfile.TemporaryDirectory() as tmp:
            public_path = Path(tmp, "student_visible.jsonl")
            write_episode(Path(tmp, "episode"), run_episode(task, DriftStudent(), ToyStateChain(), RuleReviewer())[0], trajectory, [], refs={})
            public_path.write_text(
                Path(tmp, "episode", "student_visible.jsonl").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            output = Path(tmp, "sft.jsonl")
            count = export_sft_jsonl(task, public_path, output)
            self.assertEqual(count, len(trajectory))
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["provenance"]["trajectory_view"], "student_visible")
            self.assertEqual(rows[0]["input"]["history"], [])
            self.assertEqual(len(rows[1]["input"]["history"]), 1)
            self.assertNotIn("verifier", output.read_text(encoding="utf-8"))
            self.assertNotIn("reason", output.read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError):
                export_sft_jsonl(task, public_path, output)
            with self.assertRaises(ValueError):
                export_sft_jsonl(tasks[1], public_path, Path(tmp, "test-sft.jsonl"))

    def test_schema_rejection_and_reset_errors_are_classified(self):
        _, tasks = load_manifest(ROOT / "configs/toy_benchmark.toml")

        class InvalidStudent:
            artifact_id = "invalid-student"

            def reset(self, task):
                del task

            def act(self, task, observation):
                del task, observation
                return Action("unknown")

        result, _, audit = run_episode(tasks[0], InvalidStudent(), ToyStateChain())
        self.assertEqual(result.status, "failure")
        self.assertIn("target schema", result.verifier["reason"])
        self.assertEqual(audit[0]["review_status"], "reviewer_disabled")

        class BrokenEnvironment(ToyStateChain):
            def reset(self, task):
                raise RuntimeError("backend unavailable")

        result, _, _ = run_episode(tasks[0], DriftStudent(), BrokenEnvironment())
        self.assertEqual(result.status, "infra_error")

    def test_public_observation_rejects_nested_private_metadata(self):
        with self.assertRaisesRegex(ValueError, "verifier"):
            Observation("toy-task", 0, "stage_0", ("advance",), metadata={"nested": {"verifier": {"answer": 1}}})

    def test_harness_zero_reference_checkout_is_inspectable(self):
        repo = ROOT / "sources/repo/Harness_zero/harness-zero-main"
        plan = inspect_checkout(repo)
        self.assertTrue(plan.appworld_agent.exists())
        self.assertTrue(plan.reviewer_entry.exists())

    def test_hyperagents_candidate_archive_preserves_lineage_and_rejects_duplicates(self):
        archive = Archive()
        parent = ArtifactRef("improver", "improver-v0")
        candidate = ImproverCandidate(
            ref=ArtifactRef("improver", "improver-v1", parent_id=parent.artifact_id),
            parent=parent,
            patch_path=None,
            evaluation_path=None,
        )
        archive.add(candidate)
        self.assertEqual(archive.items(), (candidate,))
        with self.assertRaises(ValueError):
            archive.add(candidate)


if __name__ == "__main__":
    unittest.main()
