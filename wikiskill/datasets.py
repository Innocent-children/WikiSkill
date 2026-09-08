"""Convert benchmark records using explicitly supplied train/validation/test IDs."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from .data import Dataset, Task, json_text, valid_relative_path
from .benchmarks.base import asset_path


PAPER_SPLIT_SIZES = {"live_math": (35, 18, 124), "sealqa": (16, 10, 85),
                     "spreadsheet": (80, 40, 280), "officeqa": (50, 24, 172), "alfworld": (39, 18, 134)}


def prepare_dataset(benchmark: str, records: list[dict], splits: dict, destination: Path,
                    asset_root: Path, paper_sizes: bool = False, documents_root: str | None = None) -> dict:
    if benchmark not in PAPER_SPLIT_SIZES:
        raise ValueError("Unknown benchmark")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Prepared dataset destination must be new or empty")
    if not isinstance(records, list) or set(splits) != {"train", "validation", "test"}:
        raise ValueError("Supply source record list and explicit train/validation/test ID lists")
    source = {}
    for row in records:
        key = str(row["id"])
        if key in source:
            raise ValueError(f"Duplicate source ID: {key}")
        source[key] = row
    converted, references, mapping = {}, set(), {}
    for split in ("train", "validation", "test"):
        if not isinstance(splits[split], list):
            raise ValueError("Each split must be a list of source IDs")
        converted[split] = []
        for raw_id in splits[split]:
            key = str(raw_id)
            if key not in source:
                raise ValueError(f"Split refers to unknown source ID: {key}")
            row = source[key]
            task_id = key if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", key) else "task-" + hashlib.sha256(key.encode()).hexdigest()[:20]
            context = dict(row.get("context", {}))
            evaluation = {**row.get("evaluation", {}), "source_id": key}
            if benchmark == "spreadsheet":
                context.update(instruction_type=row["instruction_type"], answer_position=row["answer_position"])
                context["cases"] = row.get("cases", [{"input_workbook": f"spreadsheet/{key}/{n}_{key}_input.xlsx"} for n in (1, 2, 3)])
                evaluation["reference_workbooks"] = row.get("reference_workbooks", [f"spreadsheet/{key}/{n}_{key}_answer.xlsx" for n in (1, 2, 3)])
                references.update(case["input_workbook"] for case in context["cases"])
                references.update(evaluation["reference_workbooks"])
                question, answer = row["instruction"], "completed workbook"
            elif benchmark == "alfworld":
                context["game_file"] = row["game_file"]
                references.add(row["game_file"])
                question, answer = row["task_description"], "success"
            else:
                question, answer = row["question"], row["answer"]
                if benchmark == "live_math" and "choices" in row:
                    context["choices"] = row["choices"]
                if benchmark == "officeqa":
                    context["oracle_pages"] = row["oracle_pages"]
                    for page in context["oracle_pages"]:
                        if isinstance(page, dict) and page["path"] not in row.get("files", {}):
                            references.add(page["path"])
            task = Task(task_id, question, answer, row.get("files", {}),
                        row.get("metric", "exact" if benchmark == "live_math" else "normalized"),
                        row.get("tolerance", 1e-6), context, evaluation)
            converted[split].append(task)
            mapping[task_id] = key
    dataset = Dataset(**{split: tuple(tasks) for split, tasks in converted.items()})
    sizes = tuple(len(getattr(dataset, split)) for split in ("train", "validation", "test"))
    if paper_sizes and sizes != PAPER_SPLIT_SIZES[benchmark]:
        raise ValueError(f"Paper split sizes for {benchmark} are {PAPER_SPLIT_SIZES[benchmark]}, received {sizes}")
    if documents_root is not None:
        if benchmark != "officeqa":
            raise ValueError("documents_root is used by OfficeQA")
        valid_relative_path(documents_root)
        folder = asset_root / documents_root
        if not folder.is_dir() or not folder.resolve().is_relative_to(asset_root.resolve()):
            raise ValueError("documents_root must lie inside asset_root")
        references.update(path.relative_to(asset_root).as_posix() for path in folder.rglob("*")
                          if path.is_file() and path.suffix.lower() in (".txt", ".md"))
    files = {reference: asset_path(asset_root, reference) for reference in references}
    destination.mkdir(parents=True, exist_ok=True)
    for relative, path in files.items():
        target = destination / valid_relative_path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for split, tasks in converted.items():
        with (destination / f"{split}.jsonl").open("x", encoding="utf-8") as stream:
            for task in tasks:
                stream.write(json.dumps(task.record(), ensure_ascii=False) + "\n")
    report = {"benchmark": benchmark, "sizes": dict(zip(("train", "validation", "test"), sizes)),
              "dataset_digest": dataset.fingerprint(), "source_ids": mapping, "copied_assets": len(files),
              "paper_sizes_checked": paper_sizes, "documents_root": documents_root,
              "split_source": "explicit caller-supplied IDs; no inferred paper partition"}
    (destination / "preparation.json").write_text(json_text(report) + "\n", encoding="utf-8")
    return report
