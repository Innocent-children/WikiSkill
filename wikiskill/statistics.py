"""Paired task-level bootstrap comparisons for saved evaluation reports."""

from __future__ import annotations

import math
import random
from collections import Counter


def task_scores(report: dict) -> dict[str, float]:
    scores = {}
    for row in report["tasks"]:
        task_id, score = row["task_id"], float(row["score"])
        if task_id in scores or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Reports need unique task IDs and finite scores between zero and one")
        scores[task_id] = score
    if not scores:
        raise ValueError("Report contains no tasks")
    return scores


def average_reports(reports: list[dict]) -> dict:
    if not reports:
        raise ValueError("At least one independent run is required")
    first = reports[0]
    maps = []
    for report in reports:
        if (report["split"] != first["split"] or report["dataset_digest"] != first["dataset_digest"]
                or report.get("benchmark_digest") != first.get("benchmark_digest")):
            raise ValueError("Independent runs must use the same dataset and split")
        values = task_scores(report)
        if maps and values.keys() != maps[0].keys():
            raise ValueError("Independent runs contain different task IDs")
        maps.append(values)
    tasks = [{"task_id": task_id, "score": sum(values[task_id] for values in maps) / len(maps)}
             for task_id in sorted(maps[0])]
    return {"split": first["split"], "dataset_digest": first["dataset_digest"],
            "benchmark_digest": first.get("benchmark_digest"),
            "benchmark": first.get("benchmark", "generic"), "independent_runs": len(reports),
            "score": sum(row["score"] for row in tasks) / len(tasks), "tasks": tasks}


def bootstrap_summary(distribution: list[float], observed: float, alpha: float = 0.05) -> dict:
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between zero and one")
    ordered = sorted(distribution)
    count = len(ordered)
    # Center paired bootstrap margins to estimate the null distribution. The +1
    # correction keeps a finite Monte Carlo run from reporting p=0.
    extreme = sum(abs(margin - observed) + 1e-12 >= abs(observed) for margin in ordered)
    p_value = (extreme + 1) / (count + 1)
    return {"mean_delta": observed,
            "delta_interval_95": [ordered[int(count * 0.025)], ordered[min(count - 1, int(count * 0.975))]],
            "p_value": p_value, "significant": p_value < alpha, "alpha": alpha,
            "p_value_method": "two-sided centered paired bootstrap with Monte Carlo +1 correction"}


def compare_reports(baseline: dict, candidate: dict, samples: int = 1000, seed: int = 42) -> dict:
    if type(samples) is not int or samples < 100:
        raise ValueError("Use at least 100 bootstrap samples")
    if baseline["split"] != candidate["split"] or baseline["dataset_digest"] != candidate["dataset_digest"]:
        raise ValueError("Reports must use the same split and dataset")
    if baseline.get("benchmark_digest") != candidate.get("benchmark_digest"):
        raise ValueError("Reports must use the same benchmark assets and scoring settings")
    left = {row["task_id"]: float(row["score"]) for row in baseline["tasks"]}
    right = {row["task_id"]: float(row["score"]) for row in candidate["tasks"]}
    if not left or left.keys() != right.keys():
        raise ValueError("Reports must contain the same non-empty set of task IDs")
    if len(left) != len(baseline["tasks"]) or len(right) != len(candidate["tasks"]):
        raise ValueError("Evaluation reports contain duplicate task IDs")
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in list(left.values()) + list(right.values())):
        raise ValueError("Report scores must be finite and between zero and one")
    deltas = [right[key] - left[key] for key in sorted(left)]
    rng = random.Random(seed)
    distribution = sorted(sum(rng.choices(deltas, k=len(deltas))) / len(deltas) for _ in range(samples))
    counts = Counter("win" if delta > 0 else "loss" if delta < 0 else "tie" for delta in deltas)
    return {"tasks": len(deltas), "baseline_score": sum(left.values()) / len(left),
            "candidate_score": sum(right.values()) / len(right),
            "mean_delta": sum(deltas) / len(deltas),
            "delta_interval_95": [distribution[int(samples * 0.025)],
                                  distribution[min(samples - 1, int(samples * 0.975))]],
            "wins": counts["win"], "losses": counts["loss"], "ties": counts["tie"],
            "bootstrap_samples": samples, "seed": seed,
            "method": "paired task bootstrap, percentile interval",
            **bootstrap_summary(distribution, sum(deltas) / len(deltas))}


def compare_methods(methods: dict[str, dict[str, list[dict]]], samples: int = 1000,
                    seed: int = 42, alpha: float = 0.05) -> dict:
    if len(methods) < 2 or type(samples) is not int or samples < 100:
        raise ValueError("Comparison needs at least two methods and 100 bootstrap draws")
    names = sorted(methods)
    benchmarks = sorted(methods[names[0]])
    if not benchmarks or any(set(methods[name]) != set(benchmarks) for name in names):
        raise ValueError("Every method must report the same non-empty set of benchmarks")
    reports = {name: {benchmark: average_reports(methods[name][benchmark]) for benchmark in benchmarks}
               for name in names}
    arrays = {}
    for benchmark in benchmarks:
        baseline = reports[names[0]][benchmark]
        expected = task_scores(baseline)
        arrays[benchmark] = {}
        for name in names:
            report = reports[name][benchmark]
            current = task_scores(report)
            if (report["dataset_digest"] != baseline["dataset_digest"] or report["split"] != baseline["split"]
                    or report.get("benchmark_digest") != baseline.get("benchmark_digest") or current.keys() != expected.keys()):
                raise ValueError(f"Methods do not use matching tasks for {benchmark}")
            arrays[benchmark][name] = [current[key] for key in sorted(expected)]
    scores = {benchmark: {name: sum(values) / len(values) for name, values in arrays[benchmark].items()}
              for benchmark in benchmarks}
    macro = {name: sum(scores[benchmark][name] for benchmark in benchmarks) / len(benchmarks) for name in names}
    rng = random.Random(seed)
    draws = {benchmark: {name: [] for name in names} for benchmark in benchmarks}
    macro_draws = {name: [] for name in names}
    for _ in range(samples):
        totals = {name: 0.0 for name in names}
        for benchmark in benchmarks:
            size = len(arrays[benchmark][names[0]])
            indexes = rng.choices(range(size), k=size)
            for name in names:
                value = sum(arrays[benchmark][name][index] for index in indexes) / size
                draws[benchmark][name].append(value)
                totals[name] += value / len(benchmarks)
        for name in names:
            macro_draws[name].append(totals[name])

    def ranking(observed, sampled):
        order = sorted(names, key=lambda name: (-observed[name], name))
        best = order[0]
        pairwise = {}
        top_tier = [best]
        for name in order[1:]:
            delta = observed[best] - observed[name]
            margins = [left - right for left, right in zip(sampled[best], sampled[name])]
            comparison = bootstrap_summary(margins, delta, alpha)
            pairwise[name] = comparison
            if not comparison["significant"]:
                top_tier.append(name)
        return {"scores": observed, "ranking": order, "top_tier": top_tier,
                "sole_best": best if len(top_tier) == 1 else None, "best_vs_others": pairwise}

    return {"benchmarks": {benchmark: ranking(scores[benchmark], draws[benchmark]) for benchmark in benchmarks},
            "macro_average": ranking(macro, macro_draws), "bootstrap_samples": samples, "seed": seed,
            "benchmark_weighting": "equal weight per benchmark; task resampling independently within each benchmark",
            "run_aggregation": "average each task across independent runs before paired task resampling"}
