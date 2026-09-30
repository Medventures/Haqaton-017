import json
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path


@lru_cache
def catalog():
    return json.loads((Path(__file__).parent / 'catalogs/icd10.ru.json').read_text(encoding='utf-8'))


@lru_cache
def by_code():
    return {x['code']: x for x in catalog()['items']}


def normalize(value):
    return re.sub(r'[^\w.]+', ' ', value.casefold().replace('ё', 'е')).strip()


SYNONYMS = {'орви': 'J06', 'гипертония': 'I10', 'аг': 'I10', 'сахарный диабет': 'E11', 'ибс': 'I25', 'онмк': 'I64', 'орз': 'J06'}


def search_diagnoses(query, limit=20):
    query = normalize(query)
    if not query:
        return []
    code = re.sub(r'\s', '', query).upper().translate(str.maketrans('АВЕКМНОРСТХ', 'ABEKMHOPCTX'))
    alias = SYNONYMS.get(query)
    tokens = query.split()
    ranked = []
    for item in catalog()['items']:
        name = normalize(item['name'])
        rank = 0
        if item['code'] == code: rank = 100
        elif item['code'].startswith(code): rank = 90
        elif alias and item['code'].startswith(alias): rank = 85
        elif query in name: rank = 80
        elif all(t in name for t in tokens): rank = 70
        elif len(query) >= 5:
            similarity = max((SequenceMatcher(None, query, w).ratio() for w in name.split() if abs(len(w)-len(query)) < 4), default=0)
            if similarity >= .79: rank = 50 * similarity
        if rank: ranked.append((rank, item))
    ranked.sort(key=lambda x: (-x[0], len(x[1]['code']), x[1]['code']))
    return [x[1] for x in ranked[:limit]]
