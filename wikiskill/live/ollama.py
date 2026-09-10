"""Ollama native chat with explicit thinking, structured output and native tools."""
import json

from wikiskill.http import request_json
from wikiskill.model import ChatResponse, ModelError


class OllamaModel:
    def __init__(self, config):
        if not config.ollama_model.strip():
            raise ValueError('请配置本机 Ollama 模型名称')
        self.config = config
        self.on_response = None

    def complete(self, messages, tools, schema=None):
        names = {}
        native = []
        for message in messages:
            role = message['role']
            if role == 'assistant':
                for call in message.get('tool_calls', []):
                    names[call['id']] = call['function']['name']
                native.append(message.get('ollama_message', {'role': role, 'content': message.get('content') or ''}))
            elif role == 'tool':
                if message['tool_call_id'] not in names:
                    raise ModelError('Ollama tool result has no matching call')
                native.append({'role': 'tool', 'tool_name': names[message['tool_call_id']], 'content': message['content']})
            else:
                native.append({'role': role, 'content': message['content']})
        payload = {'model': self.config.ollama_model, 'messages': native, 'stream': False,
                   'think': False, 'options': {'temperature': 0}}
        if schema is not None:
            payload['format'] = schema
        if tools:
            payload['tools'] = tools
        result = request_json('http://127.0.0.1:11434/api/chat', payload=payload,
                              timeout=self.config.timeout_seconds, retries=0, service='Ollama')
        if self.on_response:
            self.on_response(result)
        source = result.get('message')
        if not result.get('done') or not isinstance(source, dict):
            raise ModelError('Ollama returned an incomplete chat response')
        content = source.get('content')
        if content is not None and not isinstance(content, str):
            raise ModelError('Ollama content must be text')
        message = {'role': 'assistant', 'content': content or '', 'ollama_message': source}
        if source.get('thinking'):
            message['reasoning_content'] = source['thinking']
        calls = source.get('tool_calls') or []
        if not isinstance(calls, list):
            raise ModelError('Ollama tool_calls must be an array')
        normalized = []
        for index, call in enumerate(calls):
            function = call.get('function', {}) if isinstance(call, dict) else {}
            name, args = function.get('name'), function.get('arguments')
            if not isinstance(name, str) or not isinstance(args, dict):
                raise ModelError('Ollama tool call requires a name and object arguments')
            normalized.append({'id': f'ollama-{len(messages)}-{index}', 'type': 'function',
                               'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}})
        if normalized:
            message['tool_calls'] = normalized
        usage = {k: result[k] for k in ('prompt_eval_count', 'eval_count', 'total_duration', 'load_duration') if k in result}
        return ChatResponse(message, usage, 'length' if result.get('done_reason') == 'length' else 'stop')
