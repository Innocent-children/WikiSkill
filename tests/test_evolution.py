from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wikiskill.agents import InferenceAgent
from wikiskill.data import Task, digest
from wikiskill.demo import ScriptedModel, build_demo
from wikiskill.model import ChatResponse, ModelError


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "experiment"
        self.engine = build_demo(self.root)
        self.engine.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def test_strict_gate_persistent_wiki_and_early_stop(self):
        result = self.engine.evolve()
        self.assertEqual(result["baseline_score"], 0.25)
        self.assertEqual(result["best_score"], 1)
        self.assertEqual(result["iteration"], 5)
        self.assertEqual([x["outcome"] for x in result["history"]],
                         ["accepted", "rejected", "rejected", "accepted", "accepted"])
        state = self.engine.workspace.read_state()
        skill = state["skills"]["arithmetic_rules"]["SKILL.md"]
        self.assertNotIn("Negate all answers.", skill)
        self.assertNotIn("Check formatting carefully.", skill)
        self.assertIn("Enable exponentiation.", skill)
        impact = (self.root / "wiki/skill-impact.md").read_text()
        self.assertIn("Negate all answers.", impact)
        self.assertIn("Check formatting carefully.", impact)
        self.assertIn("Iteration 2", (self.root / "wiki/logs.md").read_text())
        before = digest(self.engine.workspace.wiki_files())
        report = self.engine.evaluate()
        self.assertEqual(report["score"], 1)
        self.assertEqual(digest(self.engine.workspace.wiki_files()), before)
        self.assertEqual([x["task_id"] for x in report["tasks"]], [f"test-{i}" for i in range(4)])
        for path in (self.root / "raw/optimizer").rglob("*.json"):
            text = path.read_text()
            self.assertNotIn('"task_id": "test-', text)
            self.assertNotIn('"task_id": "validation-', text)

    def test_resume_preserves_raw_and_repairs_projection(self):
        self.engine = build_demo(self.root, iterations=1)
        self.engine.evolve()
        hashes = {path: path.read_bytes() for path in (self.root / "raw").rglob("*.json")}
        # The atomic checkpoint owns accepted skills; restart reconstructs file projections.
        (self.root / "skills/arithmetic_rules/SKILL.md").unlink()
        continued = build_demo(self.root).evolve()
        self.assertEqual(continued["iteration"], 5)
        for path, original in hashes.items():
            self.assertEqual(path.read_bytes(), original)
        self.assertTrue((self.root / "skills/arithmetic_rules/SKILL.md").exists())

    def test_infrastructure_failure_keeps_accepted_skills_and_can_restart_iteration(self):
        class FailingModel(ScriptedModel):
            fail = True

            def complete(self, messages, tools):
                task = json.loads(messages[1]["content"])
                if self.fail and task["id"].startswith("validation-") and "Negate all answers." in messages[0]["content"]:
                    raise ModelError("Simulated unavailable model")
                return super().complete(messages, tools)

        model = FailingModel("inference")
        self.engine.inference.model = model
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            self.engine.evolve()
        state = self.engine.workspace.read_state()
        self.assertEqual(state["iteration"], 1)
        self.assertEqual(state["best_score"], 0.5)
        self.assertNotIn("Negate all answers.", state["skills"]["arithmetic_rules"]["SKILL.md"])
        self.assertIn("Iteration 2", (self.root / "wiki/logs.md").read_text())
        errors = list((self.root / "raw/events").glob("*-error.json"))
        self.assertEqual(len(errors), 1)
        self.assertNotIn("validation_score", json.loads(errors[0].read_text()))
        model.fail = False
        result = self.engine.evolve()
        self.assertEqual(result["best_score"], 1)
        self.assertEqual(len(result["history"]), 5)

    def test_no_action_skips_candidate_validation(self):
        class NoAction(ScriptedModel):
            def complete(self, messages, tools):
                return self.call("finish", {"proposal": {"action": "no_action"}}, len(messages))

        self.engine = build_demo(self.root, iterations=1)
        self.engine.proposer.model = NoAction("proposer")
        result = self.engine.evolve()
        self.assertEqual(result["history"], [{"iteration": 1, "outcome": "no_action", "validation_score": None}])
        self.assertEqual(len(list((self.root / "raw/evaluations").rglob("validation-*.json"))), 4)

    def test_perfect_baseline_stops_before_training(self):
        class Perfect(ScriptedModel):
            def complete(self, messages, tools):
                modified = [dict(item) for item in messages]
                modified[0]["content"] += " Enable multiplication. Enable subtraction. Enable exponentiation."
                return super().complete(modified, tools)

        self.engine.inference.model = Perfect("inference")
        result = self.engine.evolve()
        self.assertEqual(result["iteration"], 0)
        self.assertEqual(result["best_score"], 1)
        self.assertFalse(list((self.root / "raw/traces").rglob("*.json")))
        self.assertFalse(list((self.root / "raw/optimizer").rglob("*.json")))

    def test_changed_experiment_refuses_resume(self):
        self.engine.inference.system_prompt += " changed"
        with self.assertRaisesRegex(ValueError, "settings differ"):
            self.engine.evolve()

    def test_scoring_failure_preserves_completed_conversation(self):
        def invalid_scorer(prediction, task):
            raise RuntimeError("Scoring service unavailable")

        self.engine.scorer = invalid_scorer
        with self.assertRaisesRegex(RuntimeError, "Scoring service"):
            self.engine._rollouts(self.engine.dataset.validation, {}, "raw/evaluations/scorer", "test")
        trace = json.loads((self.root / "raw/evaluations/scorer/validation-0.json").read_text())
        self.assertEqual(trace["messages"][-1]["role"], "assistant")
        self.assertIn("<answer>", trace["messages"][-1]["content"])

    def test_turn_limit_is_failure_even_when_expected_answer_is_empty(self):
        class NeverFinishes(ScriptedModel):
            def complete(self, messages, tools):
                return self.call("missing_tool", {}, len(messages))

        self.engine.inference = InferenceAgent(NeverFinishes("inference"), max_turns=1)
        score, traces = self.engine._rollouts((Task("empty", "Do a task", ""),), {},
            "raw/evaluations/turn-limit", "test")
        self.assertEqual(score, 0)
        self.assertEqual(traces[0]["termination"], "turn_limit")


if __name__ == "__main__":
    unittest.main()
