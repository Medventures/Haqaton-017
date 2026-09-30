"""Пример клиента условной МИС. Только синтетические данные для демонстрации."""
import argparse
import os
import httpx


def transfer(encounter_id, medhub_url, medhub_key, mis_url, mis_key):
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        r = client.get(f'{medhub_url.rstrip("/")}/api/v1/integration/encounters/{encounter_id}', headers={'Authorization': f'Bearer {medhub_key}'})
        r.raise_for_status()
        record = r.json()
        version = record['encounter']['version']
        # Подтверждение прежней доставки не меняет версию клинического документа.
        # В API sent_at доступен для мониторинга; в повторный документ его не включаем.
        record['encounter'].pop('sent_at', None)
        response = client.post(mis_url, json=record, headers={
            'Authorization': f'Bearer {mis_key}',
            'Idempotency-Key': f'{encounter_id}:{version}',
        })
        response.raise_for_status()
        if not 200 <= response.status_code < 300:
            raise RuntimeError('МИС не подтвердила приём данных')
        return response.status_code


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('encounter_id')
    args = parser.parse_args()
    status = transfer(args.encounter_id, os.environ.get('MEDHUB_URL', 'http://localhost:8010'), os.environ['MEDHUB_API_KEY'], os.environ.get('MIS_URL', 'http://localhost:8020/v1/consultations'), os.environ['MIS_TOKEN'])
    print(f'МИС приняла документ: HTTP {status}')
