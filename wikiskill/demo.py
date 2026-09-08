"""Scripted fixtures exercise the real loop; their scores are not model research results."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .agents import InferenceAgent, SkillProposer, WikiMaintainer
from .data import Dataset, Task, json_text
from .evolution import EvolutionConfig, EvolutionEngine
from .model import ChatResponse
from .storage import Workspace


SKILL = """---
name: arithmetic_rules
description: Apply the operation requested by a small arithmetic task.
---

## When to Apply
Use for explicit integer arithmetic questions.

## When NOT to Apply
Questions involving units or ambiguous operators need additional interpretation.

## Instructions
Addition is already supported. Enable multiplication.
"""

PURPOSE = """# Origin
Training traces showed that operation selection affected correctness.

# Patterns Addressed
[Operator selection](wiki/patterns/operator-selection.md)

# Evolution History
- Created from the first training iteration.
"""


def demo_dataset() -> Dataset:
    def split(name, start, count):
        operations = ("add", "multiply", "subtract", "power")
        tasks = []
        for i in range(count):
            operation, a, b = operations[i % 4], start + i, 2
            answers = {"add": a + b, "multiply": a * b, "subtract": a - b, "power": a ** b}
            tasks.append(Task(f"{name}-{i}", f"Fixture task: {operation} {a} {b}", str(answers[operation])))
        return tuple(tasks)
    return Dataset(split("train", 3, 8), split("validation", 20, 4), split("test", 40, 4))


def write_demo_dataset(directory: Path) -> None:
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Demo dataset output directory must be empty")
    directory.mkdir(parents=True, exist_ok=True)
    data = demo_dataset()
    for split in ("train", "validation", "test"):
        with (directory / f"{split}.jsonl").open("x", encoding="utf-8") as stream:
            for task in getattr(data, split):
                stream.write(json.dumps(task.record(), ensure_ascii=False) + "\n")


class ScriptedModel:
    def __init__(self, role: str):
        self.role = role

    @property
    def identity(self) -> dict:
        return {"adapter": "scripted_fixture", "role": self.role, "research_result": False}

    @staticmethod
    def call(name: str, arguments: dict, number: int) -> ChatResponse:
        return ChatResponse({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"fixture-call-{number}", "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments)}}]})

    def complete(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        context = json.loads(messages[1]["content"])
        if self.role == "inference":
            match = re.fullmatch(r"Fixture task: (\w+) (\d+) (\d+)", context["prompt"])
            if not match:
                raise ValueError("The scripted model only accepts the synthetic demo dataset")
            operation, a, b = match[1], int(match[2]), int(match[3])
            skill_text = messages[0]["content"]
            allowed = {"add"}
            for operation_name, keyword in (("multiply", "Enable multiplication."),
                                             ("subtract", "Enable subtraction."),
                                             ("power", "Enable exponentiation.")):
                if keyword in skill_text:
                    allowed.add(operation_name)
            values = {"add": a + b, "multiply": a * b, "subtract": a - b, "power": a ** b}
            answer = str(values[operation]) if operation in allowed else "unsupported"
            if "Negate all answers." in skill_text:
                answer = "incorrect"
            return ChatResponse({"role": "assistant", "content": f"<answer>{answer}</answer>"})
        if self.role == "maintainer":
            path = "wiki/patterns/operator-selection.md"
            content = ("# Operator selection\n\nSome fixture operations are unsupported.\n"
                       "Compare the requested operation to the available procedure.\n"
                       "Addition succeeds while other operators need explicit procedures.\n"
                       "Enable the missing operator without changing working operators.\n")
            update = {"create_patterns": [], "update_patterns": [],
                      "update_index": "# Pattern index\n\n- [Operator selection](wiki/patterns/operator-selection.md): Unsupported operations require a matching procedure; preserve working addition.\n",
                      "append_log": f"Analyzed training traces for iteration {context['iteration']}."}
            if path in context["wiki"]:
                update["update_patterns"] = [{"name": "operator-selection.md", "edits": [
                    {"op": "append", "content": f"\n- More training observations in iteration {context['iteration']}.\n"}]}]
            else:
                update["create_patterns"] = [{"name": "operator-selection.md", "content": content}]
            return ChatResponse({"role": "assistant", "content": json.dumps(update)})
        requested = []
        for message in messages:
            for call in message.get("tool_calls", []):
                if call["function"]["name"] == "read_file":
                    requested.append(json.loads(call["function"]["arguments"])["path"])
        paths = (["wiki/index.md", "wiki/skill-impact.md", "wiki/patterns/operator-selection.md"]
                 if context.get("wiki_access", True) else [])
        paths += [row["trace_path"] for row in context["training_outcomes"][:4]]
        if context["active_skills"]:
            paths += ["skills/arithmetic_rules/SKILL.md"]
        for path in paths:
            if path not in requested:
                return self.call("read_file", {"path": path}, len(messages))
        iteration = context["iteration"]
        if iteration == 1:
            purpose = PURPOSE if context.get("wiki_access", True) else "# Origin\nObserved training traces t0 through t3.\n# Evolution History\nCreated in iteration 1.\n"
            proposal = {"action": "create", "name": "arithmetic_rules", "skill_md": SKILL, "purpose_md": purpose}
        elif iteration in (2, 3, 4, 5):
            text = {2: "Negate all answers.", 3: "Check formatting carefully.",
                    4: "Enable subtraction.", 5: "Enable exponentiation."}[iteration]
            proposal = {"action": "patch", "name": "arithmetic_rules", "edits": [
                {"op": "append", "content": f"\n{text}\n"}]}
        else:
            proposal = {"action": "no_action"}
        return self.call("finish", {"proposal": proposal}, len(messages))


def build_demo(root: Path | str, iterations: int = 8, progress=None) -> EvolutionEngine:
    return EvolutionEngine(Workspace(root), demo_dataset(),
        InferenceAgent(ScriptedModel("inference")), WikiMaintainer(ScriptedModel("maintainer")),
        SkillProposer(ScriptedModel("proposer")), EvolutionConfig(iterations=iterations), progress=progress,
        experiment={"type": "synthetic demonstration", "research_result": False})


def run_demo(root: Path | str, progress=None) -> dict:
    engine = build_demo(root, progress=progress)
    engine.initialize()
    baseline = engine.evaluate(skills={})
    evolution = engine.evolve()
    final = engine.evaluate()
    report = {"type": "scripted_fixture", "research_result": False,
              "note": "Deterministic mechanics check; scores do not measure LLM skill evolution.",
              "evolution": evolution, "baseline_test": baseline, "final_test": final}
    engine.workspace.write("demo-report.json", json_text(report) + "\n")
    return report
