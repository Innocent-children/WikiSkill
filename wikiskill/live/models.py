import json

from wikiskill.model import ModelConfig, create_model

from .codex import INSTRUCTIONS
from .generation import generation_prompt


class ApiSession:
    """Adapt configured API generation to the local batch execution interface."""

    def __init__(self, config):
        self.client = create_model(ModelConfig(base_url=config.api_url, model=config.api_model,
            api_key_env="", provider=config.api_provider, timeout_seconds=config.timeout_seconds), api_key=config.api_key)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def start(self, title):
        return None

    def resume(self, thread_id):
        pass

    def generate(self, stage, context):
        response = self.client.complete([{"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": generation_prompt(stage, context)}], [])
        if response.finish_reason == "length" or response.message.get("tool_calls"):
            raise ValueError("API response was incomplete or requested tools")
        text = response.message.get("content") or ""
        if text.strip().startswith("```"):
            text = "\n".join(text.strip().splitlines()[1:-1])
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ValueError("API response must contain the requested JSON object") from exc

    def report(self, report):
        return report["summary"]
