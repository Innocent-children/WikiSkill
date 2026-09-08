from __future__ import annotations

import json
import unittest

from wikiskill.agents import InferenceAgent, SkillProposer, sample_traces
from wikiskill.data import Task, TaskInput, extract_answer
from wikiskill.demo import PURPOSE, SKILL, ScriptedModel
from wikiskill.environment import FileView, document_tools
from wikiskill.model import ChatResponse


class AgentTests(unittest.TestCase):
    def test_inference_receives_skill_content_and_only_public_task_assets(self):
        class Inspector:
            identity = {"adapter": "test"}

            def complete(inner, messages, tools):
                self.assertIn(SKILL, messages[0]["content"])
                self.assertNotIn("PURPOSE_PRIVATE", str(messages))
                self.assertNotIn("SECRET_LABEL", str(messages))
                self.assertEqual(json.loads(messages[1]["content"]),
                                 {"id": "example", "prompt": "Read the document.", "available_files": ["document.txt"]})
                if len(messages) == 2:
                    return ScriptedModel.call("read_file", {"path": "wiki/index.md"}, 1)
                self.assertIn("outside", messages[-1]["content"])
                return ChatResponse({"role": "assistant", "content": "<answer>done</answer>"})

        task = Task("example", "Read the document.", "SECRET_LABEL", {"document.txt": "content"})
        result = InferenceAgent(Inspector(), environment=document_tools).run(task.public(), {
            "arithmetic_rules": {"SKILL.md": SKILL, "PURPOSE.md": "PURPOSE_PRIVATE"}})
        self.assertEqual(extract_answer(str(result.output)), "done")

    def test_proposer_requires_four_distinct_accessible_traces(self):
        class EarlyFinisher:
            identity = {"adapter": "test"}
            step = 0
            errors = []

            def complete(inner, messages, tools):
                if messages[-1]["role"] == "tool" and "error" in messages[-1]["content"]:
                    inner.errors.append(messages[-1]["content"])
                paths = [None, "raw/evaluations/heldout.json", "traces/t0", "traces/t0",
                         "traces/t1", "traces/t2", None, "traces/t3", None]
                path = paths[inner.step]
                inner.step += 1
                if path:
                    return ScriptedModel.call("read_file", {"path": path}, inner.step)
                return ScriptedModel.call("finish", {"proposal": {"action": "create", "name": "arithmetic_rules",
                    "skill_md": SKILL, "purpose_md": PURPOSE}}, inner.step)

        model = EarlyFinisher()
        traces = [{"task_id": f"t{i}", "prediction": "wrong", "answer": "right", "score": 0} for i in range(4)]
        wiki = {"wiki/index.md": "index", "wiki/skill-impact.md": "history",
                "wiki/patterns/operator-selection.md": "pattern"}
        result = SkillProposer(model).run(wiki, {}, traces, 1)
        self.assertEqual(result.output["action"], "create")
        self.assertEqual(len(model.errors), 3)
        self.assertTrue(any("read 3" in error for error in model.errors))
        self.assertTrue(any("outside" in error for error in model.errors))

    def test_sampling_quotas_truncation_and_determinism(self):
        traces = [{"task_id": str(i), "score": 0 if i < 10 else 1, "messages": "x" * 20000} for i in range(15)]
        sampled = sample_traces(traces, 42)
        self.assertEqual(sampled, sample_traces(traces, 42))
        self.assertEqual(sum(x["score"] == 0 for x in sampled), 5)
        self.assertEqual(sum(x["score"] == 1 for x in sampled), 3)
        self.assertTrue(all(x["truncated"] and len(x["execution_log"]) == 15000 for x in sampled))
        self.assertEqual(len(sample_traces(traces[:10], 42)), 5)

    def test_file_view_pagination_and_path_boundaries(self):
        view = FileView({"doc.txt": "0123456789"})
        first = view.read("doc.txt", limit=4)
        self.assertEqual(first["content"], "0123")
        self.assertEqual(first["next_offset"], 4)
        self.assertEqual(view.read("doc.txt", offset=4)["content"], "456789")
        for path in ("../doc.txt", "/doc.txt", "folder/../doc.txt", "unknown.txt"):
            with self.assertRaises(ValueError):
                view.read(path)

    def test_document_search_returns_read_offsets(self):
        task = TaskInput("t", "Read.", {"one.txt": "first\nneedle here\nend"})
        tools = {tool.name: tool for tool in document_tools(task)}
        matches = tools["search_files"].handler(query="needle")
        found = tools["read_file"].handler(path=matches[0]["path"], offset=matches[0]["offset"])
        self.assertTrue(found["content"].startswith("needle here"))


if __name__ == "__main__":
    unittest.main()
