"""Task-scoped tools use task assets; proposer tools use a separate read-only view."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Callable

from .data import TaskInput, valid_relative_path


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., object]

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name,
                "description": self.description, "parameters": self.parameters}}


def object_schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


class FileView:
    def __init__(self, files: dict[str, str], trace_aliases: set[str] | None = None):
        self.files = dict(files)
        self.trace_aliases = trace_aliases or set()
        self.read_traces: set[str] = set()

    def read(self, path: str, offset: int = 0, limit: int = 60000) -> dict:
        valid_relative_path(path)
        if path not in self.files:
            raise ValueError("This file is outside the agent's available resources")
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 60000:
            raise ValueError("offset must be non-negative; limit must be between 1 and 60000")
        content = self.files[path]
        if offset >= len(content) and content:
            raise ValueError("offset lies beyond the file content")
        end = min(len(content), offset + limit)
        if path in self.trace_aliases and end > offset:
            self.read_traces.add(path)
        return {"path": path, "content": content[offset:end], "total_characters": len(content),
                "next_offset": end if end < len(content) else None}

    def read_tool(self) -> Tool:
        return Tool("read_file", "Read an available text resource; use next_offset for long files.",
                    object_schema({"path": {"type": "string"}, "offset": {"type": "integer"},
                                   "limit": {"type": "integer"}}, ["path"]), self.read)


def document_tools(task: TaskInput) -> list[Tool]:
    view = FileView(task.files)

    def list_files(pattern: str = "*") -> list[str]:
        return sorted(name for name in view.files if fnmatch.fnmatchcase(name, pattern))

    def search_files(query: str, limit: int = 30) -> list[dict]:
        if not isinstance(query, str) or not query or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Supply a non-empty query and a limit from 1 to 100")
        matches = []
        for name, text in sorted(view.files.items()):
            offset = 0
            for number, line in enumerate(text.splitlines(keepends=True), 1):
                if query.casefold() in line.casefold():
                    matches.append({"path": name, "line": number, "offset": offset,
                                    "content": line[:1000]})
                    if len(matches) >= limit:
                        return matches
                offset += len(line)
        return matches

    return [view.read_tool(),
            Tool("list_files", "List task asset paths matching a shell-style pattern.",
                 object_schema({"pattern": {"type": "string"}}, []), list_files),
            Tool("search_files", "Find literal text in task assets and return read offsets.",
                 object_schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
                 search_files)]


EnvironmentFactory = Callable[[TaskInput], list[Tool]]


def no_tools(task: TaskInput) -> list[Tool]:
    return []
