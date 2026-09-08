"""Command-line entry points for experiments, evaluation and skill export."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_engine
from .data import Dataset, json_text
from .demo import run_demo, write_demo_dataset
from .model import create_model, ModelConfig
from .statistics import compare_reports
from .storage import Workspace, atomic_text, read_skill_directory
from .experiments import (initialize, run_experiment, run_ablation, transfer_matrix,
                          load_comparison, import_results, analyze_history)
from .statistics import compare_methods


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def progress(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="wikiskill", description="WikiSkill: persistent wiki and validation-gated skill evolution")
    sub = root.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Run the offline, scripted mechanics demonstration")
    demo.add_argument("workspace", type=Path)
    demo.add_argument("--quiet", action="store_true")
    sample = sub.add_parser("demo-data", help="Write synthetic JSONL examples")
    sample.add_argument("directory", type=Path)
    check = sub.add_parser("check-data", help="Validate dataset structure and split overlap")
    check.add_argument("directory", type=Path)
    init = sub.add_parser("init", help="Create an experiment workspace without calling a model")
    init.add_argument("workspace", type=Path)
    init.add_argument("--data", required=True, type=Path)
    init.add_argument("--config", required=True, type=Path)
    run = sub.add_parser("run", help="Run or continue evolution up to the target iteration count")
    run.add_argument("workspace", type=Path)
    run.add_argument("--iterations", type=int)
    run.add_argument("--quiet", action="store_true")
    evaluate = sub.add_parser("evaluate", help="Evaluate a skill snapshot without updating it or the wiki")
    evaluate.add_argument("workspace", type=Path)
    evaluate.add_argument("--split", choices=["validation", "test"], default="test")
    selection = evaluate.add_mutually_exclusive_group()
    selection.add_argument("--no-skills", action="store_true")
    selection.add_argument("--skills", type=Path)
    evaluate.add_argument("--inference-config", type=Path, help="ModelConfig JSON for cross-model skill transfer")
    evaluate.add_argument("--output", type=Path)
    evaluate.add_argument("--quiet", action="store_true")
    status = sub.add_parser("status", help="Inspect scores and accepted skills without model calls")
    status.add_argument("workspace", type=Path)
    export = sub.add_parser("export", help="Export accepted SKILL.md and PURPOSE.md packages")
    export.add_argument("workspace", type=Path)
    export.add_argument("destination", type=Path)
    compare = sub.add_parser("compare", help="Pair task scores from two saved evaluation reports")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--samples", type=int, default=1000)
    compare.add_argument("--seed", type=int, default=42)
    compare.add_argument("--output", type=Path)
    experiment = sub.add_parser("experiment", help="Run independent seeded evolutions and held-out tests")
    experiment.add_argument("directory", type=Path)
    experiment.add_argument("--data", type=Path, required=True)
    experiment.add_argument("--config", type=Path, required=True)
    experiment.add_argument("--seeds", type=int, nargs="+", default=[41, 42, 43])
    experiment.add_argument("--quiet", action="store_true")
    ablate = sub.add_parser("ablate", help="Run all four Wiki access combinations and a no-skill comparison")
    ablate.add_argument("directory", type=Path)
    ablate.add_argument("--data", type=Path, required=True)
    ablate.add_argument("--config", type=Path, required=True)
    ablate.add_argument("--seeds", type=int, nargs="+", default=[41, 42, 43])
    ablate.add_argument("--quiet", action="store_true")
    matrix = sub.add_parser("transfer-matrix", help="Evaluate source skills across models and benchmarks")
    matrix.add_argument("directory", type=Path)
    matrix.add_argument("--plan", type=Path, required=True)
    matrix.add_argument("--quiet", action="store_true")
    analyze = sub.add_parser("analyze", help="Paired per-benchmark and macro-average bootstrap with top-tier grouping")
    analyze.add_argument("--plan", type=Path, required=True)
    analyze.add_argument("--samples", type=int, default=1000)
    analyze.add_argument("--seed", type=int, default=42)
    analyze.add_argument("--alpha", type=float, default=0.05)
    analyze.add_argument("--output", type=Path)
    history = sub.add_parser("history", help="Inspect skill/wiki growth and acceptance distribution")
    history.add_argument("workspace", type=Path)
    history.add_argument("--output", type=Path)
    imported = sub.add_parser("import-results", help="Normalize externally produced comparator scores")
    imported.add_argument("--scores", type=Path, required=True)
    imported.add_argument("--data", type=Path, required=True)
    imported.add_argument("--config", type=Path, required=True, help="Matching task environment and scoring configuration")
    imported.add_argument("--benchmark", required=True)
    imported.add_argument("--method", required=True)
    imported.add_argument("--model", required=True)
    imported.add_argument("--seed", type=int, required=True)
    imported.add_argument("--output", type=Path, required=True)
    prepare = sub.add_parser("prepare", help="Convert benchmark records using explicit split IDs")
    prepare.add_argument("directory", type=Path)
    prepare.add_argument("--benchmark", choices=["live_math", "sealqa", "spreadsheet", "officeqa", "alfworld"], required=True)
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--splits", type=Path, required=True)
    prepare.add_argument("--asset-root", type=Path)
    prepare.add_argument("--documents-root")
    prepare.add_argument("--paper-sizes", action="store_true")
    sandbox = sub.add_parser("sandbox-build", help="Build the isolated Python/LibreOffice task image")
    sandbox.add_argument("--tag", default="wikiskill-spreadsheet:local")
    return root


def dispatch(args) -> dict:
    callback = None if getattr(args, "quiet", False) else progress
    if args.command == "demo":
        report = run_demo(args.workspace, callback)
        return {"type": report["type"], "research_result": False, **report["evolution"],
                "report": str(args.workspace.resolve() / "demo-report.json")}
    if args.command == "demo-data":
        write_demo_dataset(args.directory)
        return {"directory": str(args.directory.resolve()), "type": "synthetic fixture"}
    if args.command == "check-data":
        dataset = Dataset.load(args.directory)
        return {"dataset_digest": dataset.fingerprint(),
                "sizes": {split: len(getattr(dataset, split)) for split in ("train", "validation", "test")}}
    if args.command == "init":
        initialize(args.workspace, args.data, read_json(args.config))
        return {"workspace": str(args.workspace.resolve()), "initialized": True, "model_calls": 0}
    if args.command == "run":
        engine = load_engine(args.workspace, args.iterations, callback)
        report = engine.evolve()
        engine.workspace.write("run-report.json", json_text(report) + "\n")
        return report
    if args.command == "evaluate":
        engine = load_engine(args.workspace, progress=callback)
        if args.inference_config:
            engine.inference.model = create_model(ModelConfig(**read_json(args.inference_config)))
        selected = {} if args.no_skills else read_skill_directory(args.skills) if args.skills else None
        if args.skills and not selected:
            raise ValueError("The supplied skill directory contains no skills")
        report = engine.evaluate(selected, args.split)
        if args.output:
            atomic_text(args.output, json_text(report) + "\n")
        return report
    if args.command == "status":
        state = Workspace(args.workspace).read_state()
        return {"iteration": state["iteration"], "baseline_score": state["baseline_score"],
                "best_score": state["best_score"], "skills": sorted(state["skills"]),
                "history": [{key: row[key] for key in ("iteration", "outcome", "validation_score")}
                            for row in state["history"]]}
    if args.command == "export":
        skills = Workspace(args.workspace).read_state()["skills"]
        if args.destination.exists() and any(args.destination.iterdir()):
            raise ValueError("Export destination must be new or empty")
        args.destination.mkdir(parents=True, exist_ok=True)
        for name, bundle in skills.items():
            for filename, content in bundle.items():
                atomic_text(args.destination / name / filename, content)
        return {"destination": str(args.destination.resolve()), "skills": sorted(skills)}
    if args.command == "compare":
        report = compare_reports(read_json(args.baseline), read_json(args.candidate), args.samples, args.seed)
        if args.output:
            atomic_text(args.output, json_text(report) + "\n")
        return report
    if args.command == "experiment":
        return run_experiment(args.directory, args.data, read_json(args.config), args.seeds, callback)
    if args.command == "ablate":
        return run_ablation(args.directory, args.data, read_json(args.config), args.seeds, callback)
    if args.command == "transfer-matrix":
        return transfer_matrix(args.directory, read_json(args.plan), args.plan.resolve().parent, callback)
    if args.command == "analyze":
        report = compare_methods(load_comparison(read_json(args.plan), args.plan.resolve().parent),
                                 args.samples, args.seed, args.alpha)
        if args.output:
            atomic_text(args.output, json_text(report) + "\n")
        return report
    if args.command == "history":
        report = analyze_history(args.workspace)
        if args.output:
            atomic_text(args.output, json_text(report) + "\n")
        return report
    if args.command == "import-results":
        report = import_results(args.data, read_json(args.scores), args.benchmark, args.method, args.model, args.seed, read_json(args.config))
        atomic_text(args.output, json_text(report) + "\n")
        return report
    if args.command == "prepare":
        from .datasets import prepare_dataset
        return prepare_dataset(args.benchmark, read_json(args.source), read_json(args.splits), args.directory,
                               args.asset_root or args.source.resolve().parent, args.paper_sizes, args.documents_root)
    if args.command == "sandbox-build":
        import subprocess
        from importlib.resources import files
        dockerfile = files("wikiskill.benchmarks").joinpath("Dockerfile").read_text(encoding="utf-8")
        try:
            subprocess.run(["docker", "build", "--tag", args.tag, "-"], input=dockerfile, text=True,
                           stdout=sys.stderr, check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Docker sandbox build failed with exit code {exc.returncode}") from exc
        return {"image": args.tag, "built": True}
    raise AssertionError(args.command)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = dispatch(args)
    except (ValueError, TypeError, KeyError, AttributeError, ImportError, OSError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json_text(report))
    return 0
