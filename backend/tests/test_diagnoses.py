from app.diagnoses import search_diagnoses, catalog


def test_dictionary_code_prefix_synonym_and_typo():
    assert len(catalog()['items']) > 14000
    assert search_diagnoses('i10')[0]['code'] == 'I10'
    assert search_diagnoses('J 02')[0]['code'] == 'J02'
    assert search_diagnoses('ОРВИ')[0]['code'] == 'J06'
    assert any('фарингит' in x['name'].lower() for x in search_diagnoses('фаренгит'))
    assert search_diagnoses('инфекция верхних дыхательных')[0]['code'].startswith('J06')
