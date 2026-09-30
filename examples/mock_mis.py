"""Вымышленная МИС для локального теста. В памяти; не использовать с реальными данными."""
import hashlib
import json
import os
import secrets
from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title='Вымышленная МИС — демонстрационный приёмник')
received = {}


@app.post('/v1/consultations', status_code=201)
def receive(body: dict, authorization: str = Header(''), idempotency_key: str = Header(...)):
    secret = os.environ.get('MIS_TOKEN', '')
    if not secret or not secrets.compare_digest(authorization, 'Bearer ' + secret):
        raise HTTPException(401)
    if not body.get('encounter', {}).get('reviewed_at'):
        raise HTTPException(422, 'Требуется подтверждение врача')
    checksum = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if idempotency_key in received and received[idempotency_key] != checksum:
        raise HTTPException(409, 'Ключ уже использован с другим документом')
    received[idempotency_key] = checksum
    return {'accepted': True, 'receipt_id': idempotency_key}
