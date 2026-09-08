from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from wikiskill.data import Dataset, Task, score_answer
from wikiskill.demo import PURPOSE, SKILL, demo_dataset
from wikiskill.patches import apply_edits, apply_proposal
from wikiskill.statistics import compare_reports
from wikiskill.storage import Workspace, read_skill_directory


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.ws = Workspace(Path(self.temporary.name) / "run")
        self.ws.initialize({"fixture": True})

    def tearDown(self):
        self.temporary.cleanup()

    def test_raw_records_cannot_be_overwritten(self):
        self.ws.immutable_record("raw/traces/a.json", {"original": True})
        with self.assertRaises(FileExistsError):
            self.ws.immutable_record("raw/traces/a.json", {"original": False})
        with self.assertRaises(ValueError):
            self.ws.write("raw/traces/a.json", "overwrite")

    def test_maintainer_can_submit_only_required_fields_when_no_patterns_change(self):
        self.ws.apply_wiki_update({"update_index": "# Pattern index\n", "append_log": "No new patterns this iteration."}, 1, "empty-patterns")
        self.assertIn("No new patterns", self.ws.wiki_files()["wiki/logs.md"])
        self.assertEqual(list((self.ws.root / "wiki/patterns").glob("*.md")), [])

    def test_pattern_edit_validation_precedes_writes(self):
        original = self.ws.wiki_files()
        update = {"create_patterns": [{"name": "new.md", "content": "A pattern"}],
                  "update_patterns": [{"name": "missing.md", "edits": [{"op": "append", "content": "x"}]}],
                  "update_index": "index", "append_log": "log"}
        with self.assertRaises(ValueError):
            self.ws.apply_wiki_update(update, 1, "first")
        self.assertEqual(original, self.ws.wiki_files())

    def test_index_must_keep_existing_pages(self):
        first = {"create_patterns": [{"name": "one.md", "content": "one"}], "update_patterns": [],
                 "update_index": "- [one](wiki/patterns/one.md)", "append_log": "created"}
        self.ws.apply_wiki_update(first, 1, "a")
        with self.assertRaises(ValueError):
            self.ws.apply_wiki_update({"create_patterns": [], "update_patterns": [],
                "update_index": "empty", "append_log": "forgot existing page"}, 2, "b")
        self.assertIn("one.md", self.ws.wiki_files()["wiki/index.md"])

    def test_candidate_edits_leave_active_bundle_unchanged(self):
        skills = {"arithmetic_rules": {"SKILL.md": SKILL, "PURPOSE.md": PURPOSE}}
        original = copy.deepcopy(skills)
        proposal = {"action": "patch", "name": "arithmetic_rules", "edits": [
            {"op": "replace", "target": "Enable multiplication.", "content": "Enable subtraction."}]}
        candidate = apply_proposal(skills, proposal, 2)
        self.assertEqual(skills, original)
        self.assertIn("Enable subtraction.", candidate["arithmetic_rules"]["SKILL.md"])
        self.ws.materialize_skills(candidate)
        self.assertEqual(read_skill_directory(self.ws.root / "skills"), candidate)

    def test_patch_targets_are_unique_and_operations_compose(self):
        result = apply_edits("abc", [{"op": "insert_after", "target": "b", "content": "X"},
                                    {"op": "replace", "target": "Xc", "content": "Y"},
                                    {"op": "append", "content": "!"}])
        self.assertEqual(result, "abY!")
        for target in ("missing", "a", ""):
            with self.assertRaises(ValueError):
                apply_edits("aaa", [{"op": "replace", "target": target, "content": "x"}])

    def test_file_paths_and_symlinks_stay_inside_workspace(self):
        with self.assertRaises(ValueError):
            self.ws.write("../outside.txt", "x")
        outside = Path(self.temporary.name) / "outside.md"
        outside.write_text("private")
        (self.ws.root / "wiki/patterns/link.md").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.ws.wiki_files()

    def test_second_writer_is_refused(self):
        with self.ws.lock():
            with self.assertRaisesRegex(RuntimeError, "Another process"):
                with self.ws.lock():
                    pass


class DataTests(unittest.TestCase):
    def test_split_input_and_identifier_overlap_is_rejected(self):
        dataset = demo_dataset()
        repeated = Task("new-id", dataset.train[0].prompt, dataset.train[0].answer)
        with self.assertRaisesRegex(ValueError, "different splits"):
            Dataset(dataset.train, (repeated,), dataset.test)
        with self.assertRaisesRegex(ValueError, "Duplicate task id"):
            Dataset(dataset.train, (dataset.train[0],), dataset.test)

    def test_scoring_variants(self):
        self.assertEqual(score_answer("B", Task("t", "q", ["A", "B"])), 1)
        self.assertEqual(score_answer("ＴＯＴＡＬ   amount", Task("t", "q", "total amount", metric="normalized")), 1)
        self.assertEqual(score_answer("1.00001", Task("t", "q", 1, metric="numeric", tolerance=0.0001)), 1)
        self.assertEqual(score_answer("NaN", Task("t", "q", "NaN", metric="numeric")), 0)
        self.assertEqual(score_answer("inf", Task("t", "q", "inf", metric="numeric")), 0)

    def test_paired_bootstrap_matches_task_ids(self):
        common = {"split": "test", "dataset_digest": "same"}
        baseline = {**common, "tasks": [{"task_id": "a", "score": 0}, {"task_id": "b", "score": 0}]}
        candidate = {**common, "tasks": [{"task_id": "b", "score": 1}, {"task_id": "a", "score": 1}]}
        report = compare_reports(baseline, candidate)
        self.assertEqual(report["mean_delta"], 1)
        self.assertEqual(report["delta_interval_95"], [1, 1])
        self.assertEqual(report["wins"], 2)
        candidate["dataset_digest"] = "different"
        with self.assertRaises(ValueError):
            compare_reports(baseline, candidate)


if __name__ == "__main__":
    unittest.main()
