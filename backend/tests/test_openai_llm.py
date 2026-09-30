import json
import httpx
import pytest
from app.config import settings
from app.openai_asr import ProviderError
from app.openai_llm import extract_openai
from app.providers import OpenAIExtraction, generate
from app.schemas import Consultation


def test_openai_structured_output_and_evidence_validation(monkeypatch):
    monkeypatch.setattr(settings(), 'llm_provider', 'openai')
    monkeypatch.setattr(settings(), 'openai_api_key', 'synthetic-key')
    fields = Consultation(complaints='Тестовая жалоба', ai_test_recommendations='Тестовая рекомендация', ai_diagnosis_variants='Тестовый вариант',
        sources=[{'field':'complaints','segments':[0,99]}], reviewed_fields=['complaints'],
        diagnosis_suggestions=[{'code':'j02.9','name':'Неточное имя','reason':'Для проверки'},
                               {'code':'ZZ99','name':'Несуществующий код','reason':'Ошибка'}]).model_dump()
    def post(self, url, **kwargs):
        assert url == 'https://api.openai.com/v1/responses'
        body = kwargs['json']
        assert body['store'] is False
        schema = body['text']['format']['schema']
        assert schema['additionalProperties'] is False
        for node in schema['$defs'].values():
            if node.get('type') == 'object':
                assert set(node['required']) == set(node['properties'])
                assert node['additionalProperties'] is False
        result = {'fields':fields,'speaker_roles':[{'speaker':'SPEAKER_00','role':'patient'}, {'speaker':'SPEAKER_99','role':'doctor'}]}
        return httpx.Response(200,request=httpx.Request('POST',url),json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(result)}]}]})
    monkeypatch.setattr(httpx.Client,'post',post)
    result = generate([{'speaker':'SPEAKER_00','text':'Тестовая жалоба','start':0,'end':1}])
    assert result['fields']['sources'][0]['segments'] == [0]
    assert result['fields']['reviewed_fields'] == []
    assert len(result['fields']['diagnosis_suggestions']) == 1
    assert result['fields']['diagnosis_suggestions'][0]['code'] == 'J02.9'
    assert result['speaker_roles'] == {'SPEAKER_00':'patient'}


def test_incomplete_openai_response_never_becomes_a_draft(monkeypatch):
    monkeypatch.setattr(settings(),'openai_api_key','synthetic-key')
    monkeypatch.setattr(httpx.Client,'post', lambda self,url,**kw:httpx.Response(200,request=httpx.Request('POST',url),json={'status':'incomplete','output':[]}))
    with pytest.raises(ProviderError,match='не завершил'):
        extract_openai([], OpenAIExtraction.model_json_schema())
