"""Inference, wiki consolidation and interactive skill proposal have separate inputs."""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from .data import TaskInput, extract_answer, json_text
from .environment import EnvironmentFactory, FileView, Tool, no_tools, object_schema
from .model import ChatModel
from .patches import Skills, apply_proposal


def prompt(name: str) -> str:
    return files("wikiskill").joinpath(f"prompts/{name}.md").read_text(encoding="utf-8")


@dataclass
class Finish:
    value: dict


@dataclass
class Conversation:
    messages: list[dict]
    output: str | dict
    termination: str
    usage: list[dict] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    details: dict = field(default_factory=dict)


class AgentError(RuntimeError):
    def __init__(self, message: str, messages: list[dict], usage: list[dict]):
        super().__init__(message)
        self.messages, self.usage = messages, usage


def converse(model: ChatModel, messages: list[dict], tools: list[Tool], max_turns: int,
             require_finish: bool = False) -> Conversation:
    registry = {tool.name: tool for tool in tools}
    if len(registry) != len(tools):
        raise ValueError("Tool names must be unique")
    messages = list(messages)
    usage = []
    try:
        for _ in range(max_turns):
            response = model.complete(messages, [tool.schema() for tool in tools])
            message = response.message
            messages.append(message)
            usage.append(response.usage)
            if response.finish_reason == "length":
                return Conversation(messages, message.get("content") or "", "token_limit", usage)
            calls = message.get("tool_calls") or []
            if not calls:
                if require_finish:
                    messages.append({"role": "user", "content": "Submit using the finish tool. Read more resources first if needed."})
                    continue
                return Conversation(messages, message.get("content") or "", "answer", usage)
            for call in calls:
                function = call["function"]
                try:
                    tool = registry.get(function["name"])
                    if tool is None:
                        raise ValueError("Tool is not available to this agent")
                    args = json.loads(function["arguments"])
                    if not isinstance(args, dict):
                        raise ValueError("Tool arguments must be an object")
                    if tool.name == "finish" and len(calls) != 1:
                        raise ValueError("Call finish alone, after earlier reads have completed")
                    result = tool.handler(**args)
                    if isinstance(result, Finish):
                        messages.append({"role": "tool", "tool_call_id": call["id"],
                                         "content": '{"submitted":true}'})
                        return Conversation(messages, result.value, "finish", usage)
                    content = json_text(result)
                except (ValueError, TypeError, KeyError) as exc:
                    content = json_text({"error": str(exc)})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
        if require_finish:
            raise RuntimeError("Proposer exhausted its turn budget without a valid finish")
        return Conversation(messages, "", "turn_limit", usage)
    except Exception as exc:
        raise AgentError(str(exc), messages, usage) from exc


class InferenceAgent:
    def __init__(self, model: ChatModel, max_turns: int = 20,
                 environment: EnvironmentFactory = no_tools, system_prompt: str | None = None,
                 benchmark=None):
        self.model, self.max_turns, self.environment = model, max_turns, environment
        self.system_prompt = system_prompt if system_prompt is not None else prompt("inference")
        self.benchmark = benchmark

    def skill_section(self, skills: Skills) -> str:
        section = ""
        for name, bundle in sorted(skills.items()):
            section += f"\n\n<active_skill name={json.dumps(name)}>\n{bundle['SKILL.md']}\n</active_skill>"
        return section

    def system(self, template: str, skills: Skills, wiki: dict | None = None) -> str:
        section = self.skill_section(skills)
        system = template.replace("{skill_section}", section) if "{skill_section}" in template else template + section
        if wiki:
            system += "\n\n## Wiki available for this training rollout\n" + json_text(wiki)
        return system

    def run_with_tools(self, task: TaskInput, skills: Skills, tools: list[Tool], *,
                       template: str | None = None, user_content: str | None = None,
                       max_turns: int | None = None, wiki: dict | None = None) -> Conversation:
        system = self.system(template if template is not None else self.system_prompt, skills, wiki)
        user = {"id": task.id, "prompt": task.prompt, "available_files": sorted(task.files)}
        return converse(self.model, [{"role": "system", "content": system},
                        {"role": "user", "content": user_content if user_content is not None else json_text(user)}],
                        tools, max_turns if max_turns is not None else self.max_turns)

    def run(self, task: TaskInput, skills: Skills, *, wiki: dict | None = None,
            directory: Path | None = None) -> Conversation:
        if self.benchmark:
            if directory is None:
                raise ValueError("Benchmark execution requires a task output directory")
            return self.benchmark.run(self, task, skills, wiki, directory)
        return self.run_with_tools(task, skills, self.environment(task), wiki=wiki)


def sample_traces(traces: list[dict], seed: int, max_failures: int = 5,
                  max_successes: int = 3, max_characters: int = 15000) -> list[dict]:
    rng = random.Random(seed)
    failures = [trace for trace in traces if trace["score"] < 1.0]
    successes = [trace for trace in traces if trace["score"] == 1.0]
    selected = (rng.sample(failures, min(max_failures, len(failures))) +
                rng.sample(successes, min(max_successes, len(successes))))
    sampled = []
    for trace in selected:
        text = json_text(trace)
        sampled.append({"task_id": trace["task_id"], "score": trace["score"],
                        "truncated": len(text) > max_characters,
                        "total_characters": len(text), "execution_log": text[:max_characters]})
    return sampled


def parse_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        if not match:
            raise ValueError("Malformed JSON code fence")
        text = match[1]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Agent output must be a JSON object")
    return value


class WikiMaintainer:
    def __init__(self, model: ChatModel):
        self.model = model

    def run(self, wiki: dict[str, str], sampled: list[dict], iteration: int) -> Conversation:
        context = {"iteration": iteration, "wiki": wiki, "sampled_traces": sampled,
                   "wiki_path_note": "wiki/log.md in the prompt names the evolution log stored as wiki/logs.md."}
        conversation = converse(self.model, [{"role": "system", "content": prompt("maintainer")},
                                {"role": "user", "content": json_text(context)}], [], 1)
        try:
            conversation.output = parse_json_object(str(conversation.output))
        except (ValueError, TypeError) as exc:
            raise AgentError(str(exc), conversation.messages, conversation.usage) from exc
        return conversation


class SkillProposer:
    def __init__(self, model: ChatModel, max_turns: int = 24, task_description: str = "the configured tasks"):
        self.model, self.max_turns = model, max_turns
        self.task_description = task_description

    def run(self, wiki: dict[str, str], skills: Skills, traces: list[dict], iteration: int,
            wiki_access: bool = True) -> Conversation:
        resources = dict(wiki) if wiki_access else {}
        if "wiki/logs.md" in resources:
            resources["wiki/log.md"] = resources["wiki/logs.md"]
        for name, bundle in skills.items():
            for filename, content in bundle.items():
                resources[f"skills/{name}/{filename}"] = content
        aliases = {f"traces/{trace['task_id']}" for trace in traces}
        resources.update({f"traces/{trace['task_id']}": json_text(trace) for trace in traces})
        view = FileView(resources, aliases)

        def finish(proposal: dict) -> Finish:
            candidate = apply_proposal(skills, proposal, iteration)
            if proposal["action"] != "no_action":
                if len(view.read_traces) < 4:
                    raise ValueError(f"Read at least four distinct training traces before proposing a change; read {len(view.read_traces)}")
                if proposal["action"] == "create" and wiki_access:
                    purpose = candidate[proposal["name"]]["PURPOSE.md"]
                    links = set(re.findall(r"wiki/patterns/[a-z0-9_-]+\.md", purpose))
                    if not links or not links <= set(wiki):
                        raise ValueError("PURPOSE.md must link existing motivating pattern pages")
            return Finish(proposal)

        tools = [view.read_tool(), Tool("finish", "Submit a create, patch or no_action proposal.",
                 object_schema({"proposal": {"type": "object"}}, ["proposal"]), finish)]
        summaries = [{"task_id": trace["task_id"], "score": trace["score"],
                      "prediction": trace["prediction"], "answer": trace["answer"],
                      "trace_path": f"traces/{trace['task_id']}"} for trace in traces]
        context = {"iteration": iteration, "active_skills": sorted(skills),
                   "training_outcomes": summaries, "wiki_access": wiki_access}
        if wiki_access:
            context.update(wiki_index=wiki["wiki/index.md"], skill_impact=wiki["wiki/skill-impact.md"])
        system = prompt("proposer").replace("{task_desc}", self.task_description)
        if not wiki_access:
            system = prompt("proposer-no-wiki").replace("{task_desc}", self.task_description)
        return converse(self.model, [{"role": "system", "content": system},
                        {"role": "user", "content": json_text(context)}], tools,
                        self.max_turns, require_finish=True)
