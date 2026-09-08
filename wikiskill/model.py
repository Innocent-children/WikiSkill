"""A dependency-free adapter for HTTP chat-completions model servers."""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Protocol
from urllib.parse import urlsplit
from .http import ServiceError, request_json


class ModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    api_key_env: str = "WIKISKILL_API_KEY"
    temperature: float = 0.0
    max_tokens: int = 8192
    timeout_seconds: float = 120
    retries: int = 2
    seed: int | None = None
    provider: str = "chat_completions"
    thinking_budget: int | None = None
    thinking_level: str | None = None

    def __post_init__(self) -> None:
        url = urlsplit(self.base_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise ValueError("base_url must be an HTTP(S) server URL without embedded credentials")
        if url.query or url.fragment or not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Supply a model name and base_url without query or fragment")
        if not isinstance(self.api_key_env, str):
            raise ValueError("api_key_env must be an environment variable name, or empty")
        if not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if type(self.max_tokens) is not int or self.max_tokens < 1:
            raise ValueError("max_tokens must be a positive integer")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if type(self.retries) is not int or not 0 <= self.retries <= 5:
            raise ValueError("retries must be between 0 and 5")
        if self.seed is not None and type(self.seed) is not int:
            raise ValueError("seed must be an integer or null")
        if self.provider not in ("chat_completions", "gemini"):
            raise ValueError("provider must be chat_completions or gemini")
        if self.thinking_budget is not None and (type(self.thinking_budget) is not int or self.thinking_budget < -1):
            raise ValueError("thinking_budget must be -1 or a non-negative integer")
        if self.thinking_level is not None and self.thinking_level not in ("MINIMAL", "LOW", "MEDIUM", "HIGH"):
            raise ValueError("Unknown Gemini thinking level")
        if self.thinking_budget is not None and self.thinking_level is not None:
            raise ValueError("Choose thinking_budget or thinking_level")
        if self.provider != "gemini" and (self.thinking_budget is not None or self.thinking_level is not None):
            raise ValueError("Thinking settings apply to the native Gemini provider")


@dataclass
class ChatResponse:
    message: dict
    usage: dict = field(default_factory=dict)
    finish_reason: str = "stop"


class ChatModel(Protocol):
    @property
    def identity(self) -> dict: ...

    def complete(self, messages: list[dict], tools: list[dict]) -> ChatResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ChatCompletionsModel:
    def __init__(self, config: ModelConfig):
        self.config = config
        self.opener = urllib.request.build_opener(_NoRedirect())

    @property
    def identity(self) -> dict:
        return {"adapter": "chat_completions", **asdict(self.config)}

    def complete(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        config = self.config
        payload = {"model": config.model, "messages": messages, "temperature": config.temperature,
                   "max_tokens": config.max_tokens, "stream": False}
        if config.seed is not None:
            payload["seed"] = config.seed
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if config.api_key_env:
            key = os.environ.get(config.api_key_env)
            if not key:
                raise ModelError(f"Set the environment variable {config.api_key_env}, or use an empty api_key_env for an unauthenticated server")
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(config.base_url.rstrip("/") + "/chat/completions",
                                         data=json.dumps(payload, ensure_ascii=False).encode(), headers=headers)
        for attempt in range(config.retries + 1):
            try:
                with self.opener.open(request, timeout=config.timeout_seconds) as response:
                    result = json.loads(response.read())
                return self._decode(result)
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code <= 599
                code = exc.code
                exc.close()
                if not retryable or attempt == config.retries:
                    raise ModelError(f"Model server returned HTTP {code}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == config.retries:
                    raise ModelError("Model server connection failed or timed out") from None
            except (json.JSONDecodeError, UnicodeError, KeyError, IndexError, TypeError) as exc:
                raise ModelError("Model server returned an invalid chat-completions response") from exc
            time.sleep(min(2 ** attempt, 8))
        raise AssertionError("Retry loop must return or raise")

    @staticmethod
    def _decode(result: dict) -> ChatResponse:
        if not isinstance(result, dict) or not isinstance(result.get("choices"), list) or not result["choices"]:
            raise ModelError("Model response must contain a non-empty choices array")
        if not isinstance(result["choices"][0], dict):
            raise ModelError("Model response choice must be an object")
        source = result["choices"][0]["message"]
        if not isinstance(source, dict):
            raise ModelError("Model response message must be an object")
        if source.get("role", "assistant") != "assistant":
            raise ModelError("Model response is not an assistant message")
        content = source.get("content")
        if content is not None and not isinstance(content, str):
            raise ModelError("Model response content must be text or null")
        message = {"role": "assistant", "content": content}
        if isinstance(source.get("reasoning_content"), str):
            message["reasoning_content"] = source["reasoning_content"]
        calls = source.get("tool_calls")
        if calls:
            if not isinstance(calls, list):
                raise ModelError("tool_calls must be a list")
            seen = set()
            normalized = []
            for call in calls:
                if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                    raise ModelError("Tool call must contain a function object")
                name, arguments, call_id = call["function"]["name"], call["function"]["arguments"], call["id"]
                if not all(isinstance(x, str) and x for x in (name, arguments, call_id)) or call_id in seen:
                    raise ModelError("Malformed or duplicate tool call")
                seen.add(call_id)
                normalized.append({"id": call_id, "type": "function",
                                   "function": {"name": name, "arguments": arguments}})
            message["tool_calls"] = normalized
        if not content and not calls:
            raise ModelError("Model returned neither content nor tool calls")
        usage = result.get("usage") or {}
        return ChatResponse(message, usage if isinstance(usage, dict) else {},
                            result["choices"][0].get("finish_reason") or "stop")


class GeminiModel:
    """Native generateContent adapter preserving function-call thought signatures."""

    def __init__(self, config: ModelConfig):
        self.config = config

    @property
    def identity(self) -> dict:
        return {"adapter": "gemini_generate_content", **asdict(self.config)}

    @staticmethod
    def _contents(messages: list[dict]) -> tuple[list[dict], str]:
        contents, instructions, call_names = [], [], {}
        for message in messages:
            role = message["role"]
            if role in ("system", "developer"):
                instructions.append(message["content"])
                continue
            if role == "assistant":
                parts = message.get("gemini_parts")
                for call in message.get("tool_calls", []):
                    call_names[call["id"]] = {"name": call["function"]["name"], "id": call.get("gemini_call_id")}
                if parts is None:
                    parts = [{"text": message["content"]}] if message.get("content") else []
                    parts += [{"functionCall": {"name": call["function"]["name"],
                              "args": json.loads(call["function"]["arguments"])}}
                              for call in message.get("tool_calls", [])]
                if parts:
                    contents.append({"role": "model", "parts": parts})
            elif role == "tool":
                if message["tool_call_id"] not in call_names:
                    raise ModelError("Tool response has no matching function call")
                try:
                    result = json.loads(message["content"])
                except json.JSONDecodeError:
                    result = message["content"]
                original = call_names[message["tool_call_id"]]
                function_response = {"name": original["name"], "response": {"result": result}}
                if original["id"] is not None:
                    function_response["id"] = original["id"]
                part = {"functionResponse": function_response}
                if contents and contents[-1]["role"] == "user" and "functionResponse" in contents[-1]["parts"][0]:
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": message["content"]}]})
            else:
                raise ModelError(f"Unsupported conversation role: {role}")
        return contents, "\n\n".join(instructions)

    def complete(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        config = self.config
        contents, system = self._contents(messages)
        generation = {"temperature": config.temperature, "maxOutputTokens": config.max_tokens}
        if config.seed is not None:
            generation["seed"] = config.seed
        if config.thinking_budget is not None or config.thinking_level is not None:
            thinking = {"includeThoughts": True}
            if config.thinking_budget is not None:
                thinking["thinkingBudget"] = config.thinking_budget
            if config.thinking_level is not None:
                thinking["thinkingLevel"] = config.thinking_level
            generation["thinkingConfig"] = thinking
        payload = {"contents": contents, "generationConfig": generation}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [{"functionDeclarations": [
                {"name": tool["function"]["name"], "description": tool["function"]["description"],
                 "parametersJsonSchema": tool["function"]["parameters"]} for tool in tools]}]
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        headers = {}
        if config.api_key_env:
            key = os.environ.get(config.api_key_env)
            if not key:
                raise ModelError(f"Set the environment variable {config.api_key_env}")
            headers["x-goog-api-key"] = key
        from urllib.parse import quote
        name = config.model.removeprefix("models/")
        url = config.base_url.rstrip("/") + f"/models/{quote(name, safe='')}:generateContent"
        try:
            response = request_json(url, payload=payload, headers=headers, timeout=config.timeout_seconds,
                                    retries=config.retries, service="Gemini")
            candidate = response["candidates"][0]
            parts = candidate["content"]["parts"]
            if not isinstance(parts, list) or not parts:
                raise ModelError("Gemini returned no content parts")
            visible, thoughts, calls = [], [], []
            for index, part in enumerate(parts):
                if "text" in part:
                    (thoughts if part.get("thought") else visible).append(part["text"])
                if "functionCall" in part:
                    function = part["functionCall"]
                    call = {"id": f"gemini-{len(messages)}-{index}", "type": "function", "function": {
                        "name": function["name"], "arguments": json.dumps(function.get("args", {}))}}
                    if "id" in function:
                        call["gemini_call_id"] = function["id"]
                    calls.append(call)
            message = {"role": "assistant", "content": "\n".join(visible) or None, "gemini_parts": parts}
            if thoughts:
                message["reasoning_content"] = "\n".join(thoughts)
            if calls:
                message["tool_calls"] = calls
            finish = candidate.get("finishReason", "STOP")
            if not visible and not calls and finish != "MAX_TOKENS":
                raise ModelError(f"Gemini returned no answer or tool call ({finish})")
            return ChatResponse(message, response.get("usageMetadata", {}),
                                "length" if finish == "MAX_TOKENS" else "stop")
        except ServiceError as exc:
            raise ModelError(str(exc)) from exc
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ModelError("Gemini returned an invalid generateContent response") from exc


def create_model(config: ModelConfig) -> ChatModel:
    return GeminiModel(config) if config.provider == "gemini" else ChatCompletionsModel(config)
