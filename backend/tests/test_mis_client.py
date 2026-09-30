import importlib.util
import json
from pathlib import Path
import httpx


def test_mis_client_retries_same_document_after_delivery_timestamp_changes(monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'examples' / 'mis_client.py'
    spec = importlib.util.spec_from_file_location('example_mis_client', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pulls, deliveries = [], []
    def handle(request):
        if request.method == 'GET':
            sent_at = None if not pulls else 999
            pulls.append(sent_at)
            return httpx.Response(200, json={'encounter': {'id': 'synthetic', 'version': 4,
                'started_at': 100, 'ended_at': 200, 'reviewed_at': 300, 'sent_at': sent_at}})
        body = json.loads(request.content)
        deliveries.append((request.headers['idempotency-key'], body))
        return httpx.Response(201, json={'accepted': True})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    for _ in range(2):
        assert module.transfer('synthetic', 'http://anamio.test', 'synthetic-key', 'http://mis.test', 'synthetic-key') == 201
    assert deliveries[0] == deliveries[1]
    assert 'sent_at' not in deliveries[0][1]['encounter']
    assert deliveries[0][1]['encounter']['started_at'] == 100
    assert deliveries[0][1]['encounter']['ended_at'] == 200
