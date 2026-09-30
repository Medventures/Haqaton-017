import copy
import json
import httpx
from .config import settings
from .openai_asr import ProviderError


def strict_schema(schema):
    result = copy.deepcopy(schema)
    def walk(node):
        if isinstance(node, dict):
            node.pop('default', None)
            if node.get('type') == 'object' and 'properties' in node:
                node['additionalProperties'] = False
                node['required'] = list(node['properties'])
            for value in node.values(): walk(value)
        elif isinstance(node, list):
            for value in node: walk(value)
    walk(result)
    return result


def extract_openai(messages, schema):
    s = settings()
    if not s.openai_api_key.strip():
        raise ProviderError('Для генерации нужен OPENAI_API_KEY в настройках сервера')
    with httpx.Client(timeout=300, follow_redirects=False) as client:
        try:
            r = client.post('https://api.openai.com/v1/responses', headers={'Authorization': 'Bearer ' + s.openai_api_key}, json={
                'model': s.llm_model, 'input': messages, 'store': False,
                'reasoning': {'effort': 'low'}, 'max_output_tokens': 12000,
                'text': {'format': {'type': 'json_schema', 'name': 'consultation', 'strict': True, 'schema': strict_schema(schema)}}})
            r.raise_for_status()
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429:
                raise ProviderError('OpenAI LLM: недостаточно квоты или превышен лимит. Расшифровка сохранена; повторите генерацию позже.') from None
            raise ProviderError('OpenAI LLM отклонил запрос. Проверьте ключ, доступ к модели и настройки API.') from None
        except httpx.HTTPError:
            raise ProviderError('OpenAI LLM недоступна. Расшифровка сохранена; повторите генерацию.') from None
    body = r.json()
    if body.get('status') != 'completed':
        raise ProviderError('OpenAI не завершил генерацию. Черновик не заменён; повторите попытку.')
    parts = [p['text'] for item in body.get('output', []) if item.get('type') == 'message'
             for p in item.get('content', []) if p.get('type') == 'output_text']
    if not parts:
        raise ProviderError('OpenAI не вернул лист консультации. Заполните его вручную или повторите запрос.')
    return ''.join(parts)
