"""The full WikiSkill loop: train, consolidate, propose, validate, accept or discard."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Callable

from .agents import AgentError, InferenceAgent, SkillProposer, WikiMaintainer, sample_traces, prompt
from .data import Dataset, Scorer, Task, checked_score, digest, extract_answer, score_answer
from .patches import Skills, apply_proposal
from .storage import Workspace, skill_diff


@dataclass(frozen=True)
class EvolutionConfig:
    iterations: int = 8
    seed: int = 42
    failure_samples: int = 5
    success_samples: int = 3
    trace_characters: int = 15000
    inference_wiki_access: bool = False
    proposer_wiki_access: bool = True

    def __post_init__(self) -> None:
        for name in ("iterations", "failure_samples", "success_samples", "trace_characters"):
            value = getattr(self, name)
            if type(value) is not int or value < (1 if name == "trace_characters" else 0):
                raise ValueError(f"{name} must be a valid non-negative integer")
        if type(self.seed) is not int:
            raise ValueError("seed must be an integer")
        if self.failure_samples + self.success_samples == 0:
            raise ValueError("At least one maintainer trace sample must be enabled")
        if type(self.inference_wiki_access) is not bool or type(self.proposer_wiki_access) is not bool:
            raise ValueError("Wiki access flags must be booleans")


def run_id() -> str:
    return uuid.uuid4().hex


class EvolutionEngine:
    def __init__(self, workspace: Workspace, dataset: Dataset, inference: InferenceAgent,
                 maintainer: WikiMaintainer, proposer: SkillProposer,
                 config: EvolutionConfig | None = None, scorer: Scorer = score_answer,
                 progress: Callable[[dict], None] | None = None, experiment: dict | None = None):
        self.workspace, self.dataset, self.inference = workspace, dataset, inference
        self.maintainer, self.proposer = maintainer, proposer
        self.config, self.scorer = config or EvolutionConfig(), scorer
        self.progress = progress or (lambda event: None)
        self.experiment = experiment or {}
        self.optimizer_calls = {}

    def manifest(self) -> dict:
        config = asdict(self.config)
        # The target iteration count can grow when continuing the same experiment.
        config.pop("iterations")
        return {"dataset": self.dataset.fingerprint(), "evolution": config,
                "inference": self.inference.model.identity,
                "maintainer": self.maintainer.model.identity, "proposer": self.proposer.model.identity,
                "inference_prompt": digest(self.inference.system_prompt),
                "optimizer_prompts": {name: digest(prompt(name)) for name in ("maintainer", "proposer", "proposer-no-wiki")},
                "grader_prompt": digest(prompt("sealqa_grader")),
                "task_prompts": {name: digest(prompt(name)) for name in ("live_math", "sealqa", "spreadsheet", "officeqa", "alfworld")},
                "benchmark": self.inference.benchmark.identity if self.inference.benchmark else None,
                "inference_turns": self.inference.max_turns, "proposer_turns": self.proposer.max_turns,
                "environment": f"{self.inference.environment.__module__}:{self.inference.environment.__qualname__}",
                "scorer": f"{self.scorer.__module__}:{self.scorer.__qualname__}",
                "experiment": self.experiment}

    def initialize(self) -> None:
        self.workspace.initialize(self.manifest())

    def _rollouts(self, tasks: tuple[Task, ...], skills: Skills, prefix: str,
                  phase: str) -> tuple[float, list[dict]]:
        traces = []
        for task in tasks:
            path = f"{prefix}/{task.id}.json"
            conversation = None
            try:
                wiki = self.workspace.wiki_files() if phase == "training" and self.config.inference_wiki_access else None
                directory = self.workspace.path(f"work/{prefix.removeprefix('raw/')}/{task.id}")
                directory.mkdir(parents=True, exist_ok=False)
                conversation = self.inference.run(task.public(), skills, wiki=wiki, directory=directory)
                prediction = extract_answer(str(conversation.output))
                if self.inference.benchmark:
                    score = checked_score(lambda answer, item: self.inference.benchmark.score(answer, item, conversation), prediction, task)
                else:
                    score = (checked_score(self.scorer, prediction, task)
                             if conversation.termination == "answer" else 0.0)
                artifacts = self.workspace.archive_artifacts(conversation.artifacts, directory,
                    f"raw/artifacts/{prefix.removeprefix('raw/')}/{task.id}")
                trace = {"task_id": task.id, "prompt": task.prompt, "answer": task.answer,
                         "prediction": prediction, "score": score,
                         "messages": conversation.messages, "termination": conversation.termination,
                         "usage": conversation.usage, "skills_digest": digest(skills),
                         "artifacts": artifacts, "details": conversation.details}
            except Exception as exc:
                self.workspace.immutable_record(path, {"task_id": task.id, "error": str(exc),
                    "messages": getattr(exc, "messages", conversation.messages if conversation else []),
                    "usage": getattr(exc, "usage", conversation.usage if conversation else []),
                    "details": conversation.details if conversation else {}})
                raise
            self.workspace.immutable_record(path, trace)
            traces.append(trace)
            self.progress({"phase": phase, "task": task.id, "score": score,
                           "completed": len(traces), "total": len(tasks)})
        return sum(trace["score"] for trace in traces) / len(traces), traces

    def _optimizer(self, role: str, attempt: str, callback: Callable):
        path = f"raw/optimizer/{attempt}/{role}.json"
        try:
            result = callback()
        except Exception as exc:
            self.workspace.immutable_record(path, {"error": str(exc),
                "messages": getattr(exc, "messages", []), "usage": getattr(exc, "usage", [])})
            raise
        self.workspace.immutable_record(path, asdict(result))
        self.optimizer_calls[role] = len(result.usage)
        return result.output

    def evolve(self) -> dict:
        ws = self.workspace
        with ws.lock():
            state = ws.read_state()
            if state["manifest"] != self.manifest():
                raise ValueError("Dataset, model or experiment settings differ from this workspace; initialize another workspace")
            # Checkpoint skills and decisions repair an interrupted file projection.
            ws.materialize_skills(state["skills"])
            for record in state["history"]:
                ws.append_impact(record)
            if state["baseline_score"] is None:
                score, _ = self._rollouts(self.dataset.validation, {},
                    f"raw/evaluations/baseline-{run_id()}/validation", "baseline")
                state.update(baseline_score=score, best_score=score)
                ws.save_state(state)
                self.progress({"phase": "baseline_complete", "score": score})
            while state["iteration"] < self.config.iterations and state["best_score"] < 1.0:
                iteration = state["iteration"] + 1
                attempt = f"{iteration:04d}-{run_id()}"
                proposal, candidate = None, None
                before = state["skills"]
                record = {"iteration": iteration, "attempt": attempt, "previous_best": state["best_score"]}
                self.progress({"phase": "iteration", "iteration": iteration})
                self.optimizer_calls = {}
                try:
                    _, traces = self._rollouts(self.dataset.train, before,
                        f"raw/traces/{attempt}", "training")
                    sampled = sample_traces(traces, self.config.seed + iteration,
                        self.config.failure_samples, self.config.success_samples, self.config.trace_characters)
                    if self.config.proposer_wiki_access:
                        update = self._optimizer("maintainer", attempt, lambda: self.maintainer.run(
                            ws.wiki_files(), sampled, iteration))
                        ws.apply_wiki_update(update, iteration, attempt)
                    proposal = self._optimizer("proposer", attempt, lambda: self.proposer.run(
                        ws.wiki_files(), before, traces, iteration, wiki_access=self.config.proposer_wiki_access))
                    candidate = apply_proposal(before, proposal, iteration)
                    record.update(proposal=proposal, diff=skill_diff(before, candidate),
                                  candidate_skill=candidate.get(proposal.get("name"), {}).get("SKILL.md", ""))
                    if proposal["action"] == "no_action":
                        record.update(outcome="no_action", validation_score=None)
                    else:
                        ws.materialize_skills(candidate, f"candidates/{attempt}/skills")
                        score, _ = self._rollouts(self.dataset.validation, candidate,
                            f"raw/evaluations/{attempt}/validation", "validation")
                        accepted = score > state["best_score"]
                        record.update(outcome="accepted" if accepted else "rejected", validation_score=score)
                        if accepted:
                            state["skills"], state["best_score"] = candidate, score
                    state["iteration"] = iteration
                    patterns = {path: content for path, content in ws.wiki_files().items() if path.startswith("wiki/patterns/")}
                    record["dynamics"] = {"wiki_patterns": len(patterns),
                        "wiki_pattern_characters": sum(len(content) for content in patterns.values()),
                        "skill_count": len(state["skills"]),
                        "skill_characters": sum(len(bundle["SKILL.md"]) for bundle in state["skills"].values()),
                        "optimizer_calls": dict(self.optimizer_calls)}
                    state["history"].append(record)
                    ws.save_state(state)
                    ws.materialize_skills(state["skills"])
                    ws.append_impact(record)
                    ws.immutable_record(f"raw/events/{attempt}.json", record)
                    self.progress({"phase": "decision", "iteration": iteration,
                                   "outcome": record["outcome"], "validation_score": record["validation_score"],
                                   "best_score": state["best_score"]})
                except Exception as exc:
                    failure = {**record, "outcome": "error", "error": str(exc)}
                    ws.immutable_record(f"raw/events/{attempt}-error.json", failure)
                    if proposal is not None:
                        failure["proposal"] = proposal
                        ws.append_impact(failure)
                    raise
            return {"iteration": state["iteration"], "baseline_score": state["baseline_score"],
                    "best_score": state["best_score"], "skills": sorted(state["skills"]),
                    "stop_reason": "perfect_validation" if state["best_score"] == 1.0 else "iteration_limit",
                    "history": [{key: item[key] for key in ("iteration", "outcome", "validation_score")}
                                for item in state["history"]]}

    def evaluate(self, skills: Skills | None = None, split: str = "test") -> dict:
        if split not in ("validation", "test"):
            raise ValueError("Standalone evaluation supports validation or test")
        ws = self.workspace
        with ws.lock():
            state = ws.read_state()
            if self.dataset.fingerprint() != state["manifest"]["dataset"]:
                raise ValueError("Evaluation data differs from the workspace's dataset")
            benchmark_identity = self.inference.benchmark.identity if self.inference.benchmark else None
            if benchmark_identity != state["manifest"]["benchmark"]:
                raise ValueError("Benchmark assets, tools or scoring settings differ from the workspace")
            selected = state["skills"] if skills is None else skills
            evaluation_id = f"evaluation-{run_id()}"
            from .benchmarks.base import comparison_identity
            score, traces = self._rollouts(getattr(self.dataset, split), selected,
                f"raw/evaluations/{evaluation_id}/{split}", f"evaluate_{split}")
            report = {"evaluation_id": evaluation_id, "split": split, "score": score,
                      "benchmark": self.inference.benchmark.name if self.inference.benchmark else "generic",
                      "benchmark_digest": digest(comparison_identity(benchmark_identity)),
                      "seed": self.config.seed,
                      "model": self.inference.model.identity, "skills_digest": digest(selected),
                      "dataset_digest": self.dataset.fingerprint(),
                      "tasks": [{"task_id": trace["task_id"], "score": trace["score"],
                                 "prediction": trace["prediction"], "answer": trace["answer"]} for trace in traces]}
            ws.immutable_record(f"raw/evaluations/{evaluation_id}/report.json", report)
            return report
