"""Run paper roles over model transports; retain raw replies before parsing them."""
import json

from wikiskill.model import ModelConfig, create_model

from .generation import INSTRUCTIONS, WIKI_SCHEMA, TOOLS, Proposer, maintain, maintainer_prompt, maintainer_schema, parse_json
from .ollama import OllamaModel


class ApiSession:
    def __init__(self, config):
        self.config = config
        self.metrics = {}
        self.on_response = None
        self.stage = None
        if config.executor == 'ollama':
            self.client = OllamaModel(config)
        else:
            self.client = create_model(ModelConfig(base_url=config.api_url, model=config.api_model, api_key_env='',
                provider=config.api_provider, retries=0, timeout_seconds=config.timeout_seconds), api_key=config.api_key)
        self.client.on_response = self._record

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def start(self, title):
        return None

    def resume(self, thread_id):
        pass

    def _record(self, response):
        if self.on_response:
            self.on_response({'stage': self.stage, 'provider': self.config.executor if self.config.executor == 'ollama' else self.config.api_provider,
                              'call': self.metrics['calls'], 'response': response})

    def _complete(self, messages, tools, schema=None):
        self.metrics['calls'] += 1
        response = self.client.complete(messages, tools, schema=schema)
        self.metrics['provider_usage'].append(response.usage or None)
        if response.finish_reason == 'length':
            raise ValueError('模型服务截断了回复；未发布不完整结果，请查看模型响应原文')
        return response.message

    def generate(self, stage, context):
        self.stage = stage
        self.metrics.update(calls=0, reads=[], provider_usage=[])
        if stage == 'raw':
            messages = [{'role': 'system', 'content': INSTRUCTIONS}, {'role': 'user', 'content': maintainer_prompt(context)}]
            schema = maintainer_schema(context)
            reply = self._complete(messages, [], schema)
            if reply.get('tool_calls'):
                raise ValueError('Wiki Maintainer 应返回结构化对象，而不是工具调用')
            value = parse_json(reply.get('content'), schema, 'Wiki Maintainer')
            return maintain(context, value)
        proposer = Proposer(context, self.metrics)
        messages = [{'role': 'system', 'content': INSTRUCTIONS}, {'role': 'user', 'content': proposer.prompt()}]
        while proposer.result is None:
            reply = self._complete(messages, proposer.tools)
            messages.append(reply)
            calls = reply.get('tool_calls') or []
            if not calls:
                raise ValueError('Skill Proposer 未调用 finish 工具；请查看模型响应原文')
            if any(c['function']['name'] == 'finish' for c in calls) and len(calls) != 1:
                raise ValueError('finish 必须单独调用，读取完成后再提交提案')
            for call in calls:
                try:
                    arguments = json.loads(call['function']['arguments'])
                except (ValueError, TypeError) as exc:
                    raise ValueError('模型工具参数不是合法 JSON；请查看模型响应原文') from exc
                result = proposer.call(call['function']['name'], arguments)
                messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': result})
        return proposer.result

    def report(self, report):
        return report['summary']
