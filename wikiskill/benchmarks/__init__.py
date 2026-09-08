"""Executable adapters for the five task families evaluated in WikiSkill."""

from pathlib import Path

from .base import Benchmark
from .qa import LiveMath, OfficeQA, SealQA


def create_benchmark(spec: dict, data_root: Path, dataset) -> Benchmark:
    from .spreadsheet import SpreadsheetBench
    from .alfworld import ALFWorld
    if not isinstance(spec, dict) or set(spec) - {"name", "options"}:
        raise ValueError("benchmark accepts name and options")
    classes = {cls.name: cls for cls in (LiveMath, SealQA, OfficeQA, SpreadsheetBench, ALFWorld)}
    if spec.get("name") not in classes:
        raise ValueError(f"Unknown benchmark: {spec.get('name')}")
    benchmark = classes[spec["name"]](data_root, spec.get("options", {}))
    benchmark.validate(dataset)
    return benchmark
