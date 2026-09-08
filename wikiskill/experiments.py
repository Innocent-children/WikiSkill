"""Independent runs, four Wiki ablations, cross-model transfer and result analysis."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .config import build_engine, load_engine
from .data import Dataset, json_text, digest
from .model import ModelConfig, create_model
from .statistics import average_reports, compare_methods
from .storage import Workspace, atomic_text


def initialize(root: Path, data: Path, config: dict, progress=None):
    engine = build_engine(root, data, config, progress=progress)
    engine.initialize()
    engine.workspace.write("inputs.json", json_text({"dataset_directory": str(data.resolve()), "config": config}) + "\n")
    return engine


def empty_destination(directory: Path) -> None:
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValueError("Experiment output directory must be new or empty")


def name_component(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("Experiment labels must use letters, numbers, underscores, dots or hyphens")
    return name


def run_experiment(directory: Path, data: Path, config: dict, seeds: list[int], progress=None) -> dict:
    if not seeds or any(type(seed) is not int for seed in seeds) or len(seeds) != len(set(seeds)):
        raise ValueError("Independent run seeds must be non-empty, integer and distinct")
    empty_destination(directory)
    runs = []
    for seed in seeds:
        run_config = copy.deepcopy(config)
        run_config.setdefault("evolution", {})["seed"] = seed
        for model in run_config["models"].values():
            model["seed"] = seed
        engine = initialize(directory / f"seed-{seed}", data, run_config, progress)
        baseline = engine.evaluate(skills={})
        evolution = engine.evolve()
        final = engine.evaluate()
        item = {"seed": seed, "evolution": evolution, "baseline_test": baseline, "final_test": final}
        engine.workspace.write("experiment-report.json", json_text(item) + "\n")
        runs.append(item)
    baseline = average_reports([row["baseline_test"] for row in runs])
    final = average_reports([row["final_test"] for row in runs])
    report = {"runs": runs, "mean_baseline_test": baseline["score"], "mean_final_test": final["score"],
              "baseline_average": baseline, "final_average": final}
    atomic_text(directory / "experiment-report.json", json_text(report) + "\n")
    return report


def run_ablation(directory: Path, data: Path, config: dict, seeds: list[int], progress=None) -> dict:
    empty_destination(directory)
    variants = {}
    for inference_access, proposer_access in ((False, True), (True, True), (False, False), (True, False)):
        name = f"inference-{int(inference_access)}_proposer-{int(proposer_access)}"
        variant = copy.deepcopy(config)
        variant.setdefault("evolution", {}).update(inference_wiki_access=inference_access,
                                                    proposer_wiki_access=proposer_access)
        variants[name] = run_experiment(directory / name, data, variant, seeds, progress)
    methods = {name: {report["final_average"]["benchmark"]: [row["final_test"] for row in report["runs"]]}
               for name, report in variants.items()}
    primary = variants["inference-0_proposer-1"]
    methods["no_skill"] = {primary["baseline_average"]["benchmark"]: [row["baseline_test"] for row in primary["runs"]]}
    report = {"variants": variants, "comparison": compare_methods(methods),
              "inference_wiki_scope": "training_only", "maintainer_enabled": "same as proposer_wiki_access"}
    atomic_text(directory / "ablation-report.json", json_text(report) + "\n")
    return report


def load_comparison(plan: dict, plan_directory: Path) -> dict:
    methods = {}
    if set(plan) - {"methods"} or not isinstance(plan.get("methods"), dict):
        raise ValueError("Comparison plan requires methods -> benchmark -> report path list")
    for method, benchmarks in plan["methods"].items():
        methods[method] = {}
        for benchmark, paths in benchmarks.items():
            if not isinstance(paths, list) or not paths:
                raise ValueError("Each comparison entry requires a non-empty list of evaluation reports")
            methods[method][benchmark] = [json.loads((plan_directory / path).read_text(encoding="utf-8")) for path in paths]
    return methods


def import_results(data: Path, rows: list[dict], benchmark: str, method: str, model: str, seed: int,
                   config: dict | None = None) -> dict:
    dataset = Dataset.load(data)
    expected = {task.id for task in dataset.test}
    from .statistics import task_scores
    identity = None
    if config is None and benchmark != "generic":
        raise ValueError("Native benchmark result import requires its configuration")
    if config is not None:
        engine = build_engine(data / ".import-metadata-only", data, config)
        identity = engine.inference.benchmark.identity if engine.inference.benchmark else None
        configured_name = engine.inference.benchmark.name if engine.inference.benchmark else "generic"
        if benchmark != configured_name:
            raise ValueError("Imported benchmark label differs from the supplied configuration")
    from .benchmarks.base import comparison_identity
    report = {"split": "test", "benchmark": benchmark, "dataset_digest": dataset.fingerprint(),
              "benchmark_digest": digest(comparison_identity(identity)),
              "method": method, "model": {"model": model}, "seed": seed,
              "tasks": rows, "origin": "external_evaluation"}
    scores = task_scores(report)
    if scores.keys() != expected:
        raise ValueError("Imported scores must contain every test task exactly once")
    report["score"] = sum(scores.values()) / len(scores)
    return report


def transfer_matrix(directory: Path, plan: dict, plan_directory: Path, progress=None) -> dict:
    empty_destination(directory)
    if set(plan) != {"benchmarks"} or not plan["benchmarks"]:
        raise ValueError("Transfer plan requires a non-empty benchmarks mapping")
    matrices = {}
    for benchmark_label, specification in plan["benchmarks"].items():
        name_component(benchmark_label)
        if set(specification) != {"sources", "targets"} or not specification["sources"] or not specification["targets"]:
            raise ValueError("Each transfer benchmark needs sources and targets")
        engines = {}
        data_digest = None
        benchmark_contract = None
        for source_label, workspaces in specification["sources"].items():
            name_component(source_label)
            if not isinstance(workspaces, list) or not workspaces:
                raise ValueError("A skill source requires a list of evolution workspaces")
            engines[source_label] = []
            for workspace in workspaces:
                engine = load_engine(plan_directory / workspace, progress=progress)
                current = engine.dataset.fingerprint()
                if data_digest is not None and current != data_digest:
                    raise ValueError("Transfer sources for one benchmark must share the same dataset")
                data_digest = current
                from .benchmarks.base import comparison_identity
                contract = comparison_identity(engine.inference.benchmark.identity) if engine.inference.benchmark else {"name": "generic", "system_prompt": engine.inference.system_prompt}
                if benchmark_contract is not None and contract != benchmark_contract:
                    raise ValueError("Transfer sources must use the same benchmark tools and scoring settings")
                benchmark_contract = contract
                engines[source_label].append(engine)
        matrix = {}
        for target_label, target_config in specification["targets"].items():
            name_component(target_label)
            model = create_model(ModelConfig(**target_config))
            target = {}
            reference = next(iter(engines.values()))
            baseline_reports = []
            for index, engine in enumerate(reference):
                engine.inference.model = model
                evaluation = engine.evaluate(skills={})
                baseline_reports.append(evaluation)
                atomic_text(directory / benchmark_label / target_label / "no_skill" / f"run-{index + 1}.json", json_text(evaluation) + "\n")
            target["no_skill"] = {"available": True, "report": average_reports(baseline_reports)}
            for source_label, sources in engines.items():
                if not any(engine.workspace.read_state()["skills"] for engine in sources):
                    target[source_label] = {"available": False, "reason": "no_skill_evolved", "score": None}
                    continue
                evaluations = []
                for index, engine in enumerate(sources):
                    engine.inference.model = model
                    evaluation = engine.evaluate()
                    evaluations.append(evaluation)
                    atomic_text(directory / benchmark_label / target_label / source_label / f"run-{index + 1}.json", json_text(evaluation) + "\n")
                target[source_label] = {"available": True, "report": average_reports(evaluations)}
            matrix[target_label] = target
        matrices[benchmark_label] = matrix
    result = {"benchmarks": matrices}
    atomic_text(directory / "transfer-matrix.json", json_text(result) + "\n")
    return result


def analyze_history(workspace: Path) -> dict:
    state = Workspace(workspace).read_state()
    stages = {"early_iterations_1_2": 0, "middle_iterations_3_5": 0, "late_iterations_6_onward": 0}
    rows = []
    for record in state["history"]:
        if record["outcome"] == "accepted":
            key = ("early_iterations_1_2" if record["iteration"] <= 2 else
                   "middle_iterations_3_5" if record["iteration"] <= 5 else "late_iterations_6_onward")
            stages[key] += 1
        rows.append({key: record.get(key) for key in ("iteration", "outcome", "validation_score", "dynamics")})
    accepted = sum(stages.values())
    return {"iterations": rows, "accepted_updates": accepted, "accepted_by_stage": stages,
            "accepted_share_by_stage": {key: value / accepted if accepted else 0 for key, value in stages.items()},
            "iteration_numbering": "one-based; paper table's 0-1 corresponds to 1-2 here"}
