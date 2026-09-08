import copy
import json
import tempfile
import unittest
from pathlib import Path

from wikiskill.data import Dataset
from wikiskill.datasets import prepare_dataset
from wikiskill.demo import build_demo, write_demo_dataset
from wikiskill.evolution import EvolutionConfig
from wikiskill.experiments import run_ablation, run_experiment, transfer_matrix, import_results, analyze_history
from wikiskill.statistics import average_reports, compare_methods
from test_http_cli import model_server


class AblationTests(unittest.TestCase):
    def test_wiki_access_is_independent_and_training_only(self):
        for inference in (False, True):
            for proposer in (False, True):
                with self.subTest(inference=inference, proposer=proposer), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary) / "run"
                    engine = build_demo(root, iterations=2)
                    engine.config = EvolutionConfig(iterations=2, inference_wiki_access=inference, proposer_wiki_access=proposer)
                    engine.initialize()
                    engine.evolve()
                    maintainer_calls = list((root / "raw/optimizer").rglob("maintainer.json"))
                    self.assertEqual(len(maintainer_calls), 2 if proposer else 0)
                    for path in (root / "raw/traces").rglob("*.json"):
                        row = json.loads(path.read_text())
                        self.assertEqual("Wiki available for this training rollout" in row["messages"][0]["content"], inference)
                    for path in (root / "raw/evaluations").rglob("validation-*.json"):
                        row = json.loads(path.read_text())
                        self.assertNotIn("Wiki available for this training rollout", row["messages"][0]["content"])
                    for path in (root / "raw/optimizer").rglob("proposer.json"):
                        row = json.loads(path.read_text())
                        context = json.loads(row["messages"][1]["content"])
                        self.assertEqual("skill_impact" in context, proposer)
                        self.assertEqual("wiki_index" in context, proposer)
                    history = analyze_history(root)
                    self.assertEqual(history["accepted_updates"], 1)
                    self.assertEqual(history["iterations"][0]["dynamics"]["wiki_patterns"], 1 if proposer else 0)

    def test_ablation_and_transfer_execute_all_configured_cells(self):
        with tempfile.TemporaryDirectory() as temporary, model_server() as (url, captured):
            root = Path(temporary)
            data = root / "data"
            write_demo_dataset(data)
            config = {"models": {role: {"base_url": url, "model": role, "api_key_env": "", "retries": 0}
                                  for role in ("inference", "maintainer", "proposer")},
                      "evolution": {"iterations": 1}}
            result = run_ablation(root / "ablations", data, config, [42])
            self.assertEqual(len(result["variants"]), 4)
            self.assertEqual(len(result["comparison"]["macro_average"]["scores"]), 5)
            plan = {"benchmarks": {"generic": {"sources": {
                "source-a": ["ablations/inference-0_proposer-1/seed-42"],
                "source-b": ["ablations/inference-1_proposer-0/seed-42"]},
                "targets": {"target": config["models"]["inference"]}}}}
            matrix = transfer_matrix(root / "transfer", plan, root)
            cells = matrix["benchmarks"]["generic"]["target"]
            self.assertEqual(cells["no_skill"]["report"]["score"], 0.25)
            self.assertEqual(cells["source-a"]["report"]["score"], 0.5)
            self.assertEqual(cells["source-b"]["report"]["score"], 0.5)


class StatisticsTests(unittest.TestCase):
    def test_independent_environment_seeds_share_comparison_contract_but_changed_assets_do_not(self):
        from wikiskill.benchmarks.base import comparison_identity
        from wikiskill.data import digest
        from wikiskill.statistics import compare_reports
        left_identity = {"name": "alfworld", "options": {"seed": 41, "max_steps": 50}, "asset_hashes": {"game": "first"}}
        right_identity = copy.deepcopy(left_identity)
        right_identity["options"]["seed"] = 42
        self.assertEqual(comparison_identity(left_identity), comparison_identity(right_identity))
        left = self.report("b", [1])
        right = self.report("b", [1])
        left["benchmark_digest"] = digest(comparison_identity(left_identity))
        right_identity["asset_hashes"]["game"] = "changed"
        right["benchmark_digest"] = digest(comparison_identity(right_identity))
        with self.assertRaisesRegex(ValueError, "assets and scoring"):
            compare_reports(left, right)

    @staticmethod
    def report(benchmark, scores):
        return {"benchmark": benchmark, "split": "test", "dataset_digest": benchmark,
                "tasks": [{"task_id": f"{benchmark}-{index}", "score": value} for index, value in enumerate(scores)]}

    def test_macro_weights_benchmarks_equally_and_groups_ties(self):
        methods = {
            "best": {"small": [self.report("small", [1, 1])], "large": [self.report("large", [1] * 30)]},
            "tied": {"small": [self.report("small", [1, 1])], "large": [self.report("large", [1] * 30)]},
            "weaker": {"small": [self.report("small", [0, 0])], "large": [self.report("large", [1] * 30)]},
        }
        result = compare_methods(methods)
        macro = result["macro_average"]
        self.assertEqual(macro["scores"]["weaker"], 0.5)
        self.assertEqual(macro["top_tier"], ["best", "tied"])
        self.assertIsNone(macro["sole_best"])
        self.assertTrue(macro["best_vs_others"]["weaker"]["significant"])
        self.assertFalse(macro["best_vs_others"]["tied"]["significant"])
        self.assertEqual(result, compare_methods(methods))

    def test_average_runs_aligns_by_task_and_rejects_partial_reports(self):
        first = self.report("b", [0, 1])
        second = self.report("b", [1, 1])
        second["tasks"].reverse()
        combined = average_reports([first, second])
        self.assertEqual(combined["score"], 0.75)
        second["tasks"].pop()
        with self.assertRaisesRegex(ValueError, "different task IDs"):
            average_reports([first, second])


class DatasetPreparationTests(unittest.TestCase):
    def test_explicit_splits_conversion_and_comparator_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = [{"id": f"q{i}", "question": f"Choose {i}", "answer": "A", "choices": [str(i), "other"]} for i in range(6)]
            splits = {"train": [f"q{i}" for i in range(4)], "validation": ["q4"], "test": ["q5"]}
            prepared = root / "prepared"
            report = prepare_dataset("live_math", records, splits, prepared, root)
            self.assertEqual(report["sizes"], {"train": 4, "validation": 1, "test": 1})
            self.assertEqual(Dataset.load(prepared).train[0].context["choices"], ["0", "other"])
            config = {"models": {role: {"base_url": "http://localhost/v1", "model": "unused", "api_key_env": ""}
                                 for role in ("inference", "maintainer", "proposer")},
                      "runtime": {"benchmark": {"name": "live_math"}}}
            scores = import_results(prepared, [{"task_id": "q5", "score": 1}], "live_math", "external", "model", 42, config)
            self.assertEqual(scores["score"], 1)
            with self.assertRaises(ValueError):
                import_results(prepared, [{"task_id": "wrong", "score": 1}], "live_math", "external", "model", 42, config)
            with self.assertRaisesRegex(ValueError, "Paper split sizes"):
                prepare_dataset("live_math", records, splits, root / "wrong-sizes", root, paper_sizes=True)


if __name__ == "__main__":
    unittest.main()
