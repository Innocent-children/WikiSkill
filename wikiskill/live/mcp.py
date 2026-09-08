from __future__ import annotations

import json
import sys

from .runtime import Runtime


def schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TEXT = {"type": "string"}
PROJECT = {"project": {"type": "string", "description": "Absolute project directory; Git worktrees share accumulated knowledge."}}
TOOLS = {
    "wikiskill_context": ("Load the managed Skill contents for the current business task.", schema(PROJECT, ["project"])),
    "wikiskill_wiki": ("Write named knowledge pages; metadata-only or identical-body updates do not trigger evolution.",
        schema({**PROJECT, "pages": {"type": "array", "items": schema({"name": TEXT, "body": TEXT}, ["name", "body"])},
                "metadata": {"type": "object"}}, ["project", "pages"])),
    "wikiskill_status": ("Query three-layer pending counts, thresholds, managed Skills and background jobs.", schema(PROJECT, [])),
    "wikiskill_query": ("Read paginated Raw originals, Wiki bodies or optimization reports and session ids.",
        schema({**PROJECT, "layer": {"enum": ["raw", "wiki", "reports"], "type": "string"}, "key": TEXT,
                "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
               ["project", "layer"])),
    "wikiskill_history": ("Read Skill version diffs, or full before/after bundles including binary assets when version_id is supplied.",
        schema({"skill_id": TEXT, "version_id": TEXT}, ["skill_id"])),
    "wikiskill_rollback": ("Restore a selected full Skill directory version and retain the replaced directory as another version.",
        schema({"skill_id": TEXT, "version_id": TEXT, "side": {"type": "string", "enum": ["before", "after"]}}, ["skill_id", "version_id"])),
    "wikiskill_retry": ("Retry a failed batch, or resend a missing report without republishing; regenerate discards an unpublished proposal.",
        schema({"job_id": TEXT, "regenerate": {"type": "boolean"}}, ["job_id"])),
}


def validate(value, spec):
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    kind = spec.get("type")
    if kind and type(value) is not types[kind]:
        raise ValueError(f"Expected {kind}")
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError("Value is outside the supported choices")
    if kind == "object":
        if not set(spec.get("required", [])) <= set(value):
            raise ValueError("Missing required arguments")
        if spec.get("additionalProperties") is False and set(value) - set(spec["properties"]):
            raise ValueError("Unknown arguments")
        for key, item in value.items():
            if key in spec.get("properties", {}):
                validate(item, spec["properties"][key])
    elif kind == "array":
        for item in value:
            validate(item, spec["items"])
    elif kind == "integer":
        if value < spec.get("minimum", value) or value > spec.get("maximum", value):
            raise ValueError("Integer outside supported range")


def call(runtime: Runtime, name: str, arguments: dict):
    if name not in TOOLS:
        raise ValueError("Unknown WikiSkill tool")
    validate(arguments, TOOLS[name][1])
    args = dict(arguments)
    if name == "wikiskill_context":
        return runtime.context(**args)
    if name == "wikiskill_wiki":
        result = runtime.put_wiki(**args)
    elif name == "wikiskill_retry":
        result = runtime.retry(**args)
    elif name == "wikiskill_rollback":
        return runtime.skills.rollback(**args)
    elif name == "wikiskill_history":
        return {"versions": runtime.skills.history(**args)}
    elif name == "wikiskill_status":
        key = runtime.store.project(args["project"]) if args.get("project") else None
        return runtime.store.status(key)
    else:
        project = runtime.store.project(args.pop("project"))
        if args.get("layer") == "raw":
            from .traces import TraceView
            return TraceView(runtime.config.root).records(project, offset=args.get("offset", 0), limit=args.get("limit", 50), record_id=args.get("key"))
        return runtime.store.query(project, **args)
    try:
        result.update(runtime.wake())
    except OSError as exc:
        result["worker_error"] = str(exc)
    return result


def serve(config, incoming=None, outgoing=None):
    """MCP stdio JSON-RPC, with stdout reserved for protocol messages."""
    incoming, outgoing = incoming or sys.stdin, outgoing or sys.stdout
    initialized = False
    versions = {"2024-11-05", "2025-03-26", "2025-06-18"}
    for line in incoming:
        request_id = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
                raise ValueError("Invalid JSON-RPC request")
            request_id = request.get("id")
            if "id" not in request:
                continue
            method, params = request["method"], request.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            if method == "initialize":
                version = params.get("protocolVersion")
                result = {"protocolVersion": version if version in versions else "2025-06-18",
                          "serverInfo": {"name": "wikiskill", "version": "0.1.0"}, "capabilities": {"tools": {}}}
                initialized = True
            elif method == "ping":
                result = {}
            elif not initialized:
                raise ValueError("Initialize the MCP connection first")
            elif method == "tools/list":
                result = {"tools": [{"name": name, "description": text, "inputSchema": spec}
                                    for name, (text, spec) in TOOLS.items()]}
            elif method == "tools/call":
                try:
                    runtime = Runtime(type(config).load(config.root))
                    value = call(runtime, params.get("name"), params.get("arguments", {}))
                    result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}
                except Exception as exc:
                    result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            else:
                response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
                outgoing.write(json.dumps(response) + "\n")
                outgoing.flush()
                continue
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        except (ValueError, TypeError) as exc:
            response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}}
        outgoing.write(json.dumps(response, ensure_ascii=False) + "\n")
        outgoing.flush()
