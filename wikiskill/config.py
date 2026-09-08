"""Build an experiment from one explicit JSON configuration."""

from __future__ import annotations

import importlib
import json
from dataclasses import replace
from pathlib import Path

from .agents import InferenceAgent, SkillProposer, WikiMaintainer
from .data import Dataset, score_answer
from .environment import document_tools, no_tools
from .evolution import EvolutionConfig, EvolutionEngine
from .model import create_model, ModelConfig
from .storage import Workspace


def load_symbol(reference: str):
    if not isinstance(reference, str) or ":" not in reference:
        raise ValueError("A Python extension must use module:callable")
    module, name = reference.split(":", 1)
    value = getattr(importlib.import_module(module), name)
    if not callable(value):
        raise ValueError(f"Extension is not callable: {reference}")
    return value


def build_engine(root: Path | str, dataset_directory: Path | str, config: dict,
                 iterations: int | None = None, progress=None) -> EvolutionEngine:
    if not isinstance(config, dict) or set(config) - {"models", "evolution", "runtime"}:
        raise ValueError("Config accepts only models, evolution and runtime")
    specs = config.get("models", {})
    roles = {"inference", "maintainer", "proposer"}
    if not isinstance(specs, dict) or set(specs) != roles:
        raise ValueError("Configure each model role: inference, maintainer, proposer")
    models = {role: create_model(ModelConfig(**specs[role])) for role in roles}
    runtime = config.get("runtime", {})
    allowed = {"inference_turns", "proposer_turns", "environment", "scorer", "system_prompt", "benchmark", "task_description"}
    if not isinstance(runtime, dict) or set(runtime) - allowed:
        raise ValueError("Unknown runtime settings")
    inference_turns, proposer_turns = runtime.get("inference_turns", 20), runtime.get("proposer_turns", 24)
    for name, value in (("inference_turns", inference_turns), ("proposer_turns", proposer_turns)):
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    system = runtime.get("system_prompt")
    if system is not None and (not isinstance(system, str) or not system.strip()):
        raise ValueError("system_prompt must be non-empty text")
    env_name, scorer_name = runtime.get("environment", "none"), runtime.get("scorer", "builtin")
    builtins = {"none": no_tools, "documents": document_tools}
    environment = builtins[env_name] if env_name in builtins else load_symbol(env_name)
    scorer = score_answer if scorer_name == "builtin" else load_symbol(scorer_name)
    evolution = EvolutionConfig(**config.get("evolution", {}))
    if iterations is not None:
        evolution = replace(evolution, iterations=iterations)
    dataset = Dataset.load(dataset_directory)
    benchmark = None
    if runtime.get("benchmark") is not None:
        if env_name != "none" or scorer_name != "builtin" or system is not None:
            raise ValueError("A paper benchmark owns its environment, scorer and system prompt; remove generic runtime overrides")
        from .benchmarks import create_benchmark
        import copy
        spec = copy.deepcopy(runtime["benchmark"])
        if spec.get("name") == "alfworld":
            spec.setdefault("options", {}).setdefault("seed", evolution.seed)
        benchmark = create_benchmark(spec, Path(dataset_directory), dataset)
    description = benchmark.description if benchmark else runtime.get("task_description", "the configured tasks")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("task_description must be non-empty text")
    return EvolutionEngine(Workspace(root), dataset,
        InferenceAgent(models["inference"], inference_turns, environment, system, benchmark),
        WikiMaintainer(models["maintainer"]), SkillProposer(models["proposer"], proposer_turns, description),
        evolution, scorer, progress,
        experiment={"environment": env_name, "scorer": scorer_name})


def load_engine(root: Path | str, iterations: int | None = None, progress=None) -> EvolutionEngine:
    root = Path(root)
    inputs = json.loads((root / "inputs.json").read_text(encoding="utf-8"))
    return build_engine(root, inputs["dataset_directory"], inputs["config"], iterations, progress)
