"""LiveMath, Google-search SealQA, and Treasury-document OfficeQA task environments."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from ..agents import prompt
from ..data import Task, TaskInput, json_text, valid_relative_path
from ..environment import FileView, Tool, object_schema
from ..http import request_json
from .base import Benchmark, asset_path


def public_files(task: TaskInput, skills) -> dict[str, str]:
    result = dict(task.files)
    result.update({f"skills/{name}/SKILL.md": bundle["SKILL.md"] for name, bundle in skills.items()})
    return result


class LiveMath(Benchmark):
    name = "live_math"
    description = "LiveMathematicianBench mathematics multiple-choice questions"

    def validate_task(self, task: Task) -> None:
        choices = task.context.get("choices")
        if choices is not None and not isinstance(choices, (dict, list)):
            raise ValueError("LiveMath choices must be an object or list")

    def run(self, agent, task, skills, wiki, directory):
        question = task.prompt
        choices = task.context.get("choices")
        if choices:
            if isinstance(choices, list):
                choices = {chr(65 + index): value for index, value in enumerate(choices)}
            question += "\n\n" + "\n".join(f"{label}. {value}" for label, value in choices.items())
        return agent.run_with_tools(task, skills, [], template=prompt("live_math"),
                                    user_content=question, max_turns=1, wiki=wiki)


class GoogleSearch:
    def __init__(self, options: dict):
        self.options = options
        self.endpoint = options.get("search_endpoint", "https://customsearch.googleapis.com/customsearch/v1")
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.username:
            raise ValueError("search_endpoint must be an HTTP(S) URL without query or credentials")

    def search(self, query: str, num_results: int = 5) -> dict:
        if not isinstance(query, str) or not query.strip() or type(num_results) is not int or not 1 <= num_results <= 10:
            raise ValueError("Supply a query and num_results from 1 to 10")
        params = {"q": query, "num": num_results}
        for parameter, name in (("key", "api_key_env"), ("cx", "search_engine_id_env")):
            variable = self.options.get(name, "GOOGLE_SEARCH_API_KEY" if parameter == "key" else "GOOGLE_SEARCH_ENGINE_ID")
            if variable:
                value = os.environ.get(variable)
                if not value:
                    from ..http import ServiceError
                    raise ServiceError(f"Set the environment variable {variable} for Google Search")
                params[parameter] = value
        response = request_json(self.endpoint + "?" + urlencode(params),
                                timeout=self.options.get("search_timeout", 30),
                                retries=self.options.get("search_retries", 2), service="Google Search")
        if "error" in response:
            from ..http import ServiceError
            raise ServiceError("Google Search returned an API error")
        items = response.get("items", [])
        if not isinstance(items, list):
            raise ValueError("Google Search items must be a list")
        return {"query": query, "results": [{"title": item.get("title", ""), "url": item.get("link", ""),
                 "snippet": item.get("snippet", "")} for item in items],
                "search_information": response.get("searchInformation", {})}


class SealQA(Benchmark):
    name = "sealqa"
    description = "SealQA factual questions using Google Search and file reading"
    allowed_options = {"api_key_env", "search_engine_id_env", "search_endpoint", "search_timeout", "search_retries", "judge_model"}

    def __init__(self, data_root, options):
        super().__init__(data_root, options)
        self.search_client = GoogleSearch(options)
        from ..model import ModelConfig, create_model
        if not isinstance(options.get("judge_model"), dict):
            raise ValueError("SealQA requires an explicit judge_model configuration for its official A/B/C auto-rater")
        self.judge = create_model(ModelConfig(**options["judge_model"]))

    def run(self, agent, task, skills, wiki, directory):
        view = FileView(public_files(task, skills))
        searches = []

        def web_search(query: str, num_results: int = 5):
            result = self.search_client.search(query, num_results)
            searches.append(result)
            path = f"search/results-{len(searches)}.json"
            view.files[path] = json_text(result)
            return {**result, "saved_as": path}

        tools = [view.read_tool(), Tool("web_search", "Search the web with Google and save readable search results.",
                 object_schema({"query": {"type": "string"}, "num_results": {"type": "integer"}}, ["query"]), web_search)]
        result = agent.run_with_tools(task, skills, tools, template=prompt("sealqa"),
                    user_content=task.prompt + "\n\nAvailable files: " + json_text(sorted(view.files)), wiki=wiki)
        result.details["searches"] = searches
        return result

    def score(self, prediction, task, conversation):
        if conversation.termination != "answer":
            return 0.0
        target = task.answer if isinstance(task.answer, str) else json_text(task.answer)
        values = {"question": task.prompt, "target": target, "predicted_answer": prediction}
        content = re.sub(r"\{(question|target|predicted_answer)\}", lambda match: values[match[1]], prompt("sealqa_grader"))
        messages = [{"role": "user", "content": content}]
        grading = {"method": "sealqa_official_auto_rater", "model": self.judge.identity, "messages": messages}
        conversation.details["grading"] = grading
        response = self.judge.complete(messages, [])
        messages.append(response.message)
        grading["usage"] = response.usage
        verdict = (response.message.get("content") or "").strip()
        if response.finish_reason == "length" or verdict not in ("A", "B", "C"):
            raise RuntimeError("SealQA judge must return exactly A, B or C; no validation score was committed")
        grading["verdict"] = verdict
        return float(verdict == "A")


class OfficeQA(Benchmark):
    name = "officeqa"
    description = "OfficeQA questions over local Treasury bulletins and initial oracle reference pages"
    allowed_options = {"documents_root", "relative_tolerance"}

    def __init__(self, data_root, options):
        super().__init__(data_root, options)
        tolerance = options.get("relative_tolerance", 0.0)
        if not isinstance(tolerance, (int, float)) or not 0 <= tolerance <= 1:
            raise ValueError("OfficeQA relative_tolerance must be between zero and one")
        self.documents = {}
        if options.get("documents_root"):
            relative = valid_relative_path(options["documents_root"])
            root = self.data_root / relative
            if not root.is_dir() or not root.resolve().is_relative_to(self.data_root):
                raise ValueError("documents_root must be a directory within the dataset directory")
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.suffix.lower() in (".txt", ".md"):
                    name = path.relative_to(self.data_root).as_posix()
                    self.documents[name] = self.check_asset(name).read_text(encoding="utf-8")

    def validate_task(self, task):
        if not isinstance(task.context.get("oracle_pages"), list) or not task.context["oracle_pages"]:
            raise ValueError(f"OfficeQA {task.id} requires oracle_pages in its public context")
        self._oracle(task.public(), {**self.documents, **task.files})

    @staticmethod
    def _oracle(task, documents):
        pages = []
        for reference in task.context["oracle_pages"]:
            if isinstance(reference, str):
                pages.append(reference)
                continue
            if not isinstance(reference, dict) or set(reference) - {"path", "start_line", "end_line"}:
                raise ValueError("An oracle page must be text or path/start_line/end_line")
            path = reference["path"]
            if path not in documents:
                raise ValueError(f"Oracle reference is outside the document collection: {path}")
            lines = documents[path].splitlines()
            first, last = reference.get("start_line", 1), reference.get("end_line", len(lines))
            if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(lines):
                raise ValueError("Invalid oracle page line range")
            pages.append(f"{path}, lines {first}-{last}\n" + "\n".join(lines[first - 1:last]))
        return "\n\n".join(pages)

    def run(self, agent, task, skills, wiki, directory):
        documents = {**self.documents, **public_files(task, skills)}

        def glob(pattern: str = "*"):
            if not isinstance(pattern, str):
                raise ValueError("glob pattern must be text")
            return sorted(name for name in documents if fnmatch.fnmatchcase(name, pattern))

        def grep(pattern: str, path_glob: str = "*", max_results: int = 50):
            if type(max_results) is not int or not 1 <= max_results <= 100:
                raise ValueError("max_results must be from 1 to 100")
            try:
                matcher = re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"Invalid search expression: {exc}") from exc
            result = []
            for name in glob(path_glob):
                for number, line in enumerate(documents[name].splitlines(), 1):
                    if matcher.search(line):
                        result.append({"path": name, "line": number, "text": line[:2000]})
                        if len(result) == max_results:
                            return result
            return result

        def read(path: str, start_line: int = 1, end_line: int | None = None):
            if path not in documents:
                raise ValueError("File is outside the available document collection")
            lines = documents[path].splitlines()
            last = min(len(lines), start_line + 199) if end_line is None else end_line
            if type(start_line) is not int or type(last) is not int or start_line < 1 or last < start_line or last - start_line >= 1000:
                raise ValueError("Read a valid line range of at most 1000 lines")
            return {"path": path, "content": "\n".join(lines[start_line - 1:last]), "total_lines": len(lines),
                    "next_line": last + 1 if last < len(lines) else None}

        tools = [Tool("glob", "Find document paths with a wildcard pattern.",
                      object_schema({"pattern": {"type": "string"}}, []), glob),
                 Tool("grep", "Search document lines with a regular expression.",
                      object_schema({"pattern": {"type": "string"}, "path_glob": {"type": "string"},
                                     "max_results": {"type": "integer"}}, ["pattern"]), grep),
                 Tool("read", "Read a bounded range of lines in a document.",
                      object_schema({"path": {"type": "string"}, "start_line": {"type": "integer"},
                                     "end_line": {"type": "integer"}}, ["path"]), read)]
        user = task.prompt + "\n\nInitial reference pages:\n" + self._oracle(task, documents)
        return agent.run_with_tools(task, skills, tools, template=prompt("officeqa"), user_content=user, wiki=wiki)

    def score(self, prediction, task, conversation):
        from .vendor.officeqa_reward import score_answer
        if conversation.termination != "answer":
            return 0.0
        tolerance = self.options.get("relative_tolerance", 0.0)
        expected = task.answer if isinstance(task.answer, str) else json_text(task.answer)
        result = score_answer(expected, prediction, tolerance)
        conversation.details["grading"] = {"method": "officeqa_official", "relative_tolerance": tolerance,
                                            "upstream_commit": "7b9a3c154ef9fb40215bb67934afc43e6799de16", "score": result}
        return result
