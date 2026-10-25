"""Optimize repeated work without changing schema or clinical interpretation."""
import asyncio
import copy
import json

import pytest
from desktop import request_intent as intent
from desktop import spelling_hints as spelling
from tests.test_velia_web_chat import fixture

@pytest.fixture(autouse=True)
def empty_cache():
    intent._DIRECT_CACHE.clear()
    yield
    intent._DIRECT_CACHE.clear()

def test_compact_schema_keeps_validation_rules():
    assert intent.TOOL['function']['parameters']['required'] == ['action','quote','candidate','query','source_scope','task_query','context']
    schema=intent.TOOL['function']['parameters']
    assert schema == intent._compact_schema(intent.FULL_TOOL['function']['parameters'])
    assert schema['additionalProperties'] is False
    assert schema['properties']['context']['maxItems'] == 6
    assert schema['properties']['context']['items']['properties']['quote']['maxLength'] == 128
    assert schema['properties']['source_scope']['enum'] == ['general','official_health']
    assert 'description' not in json.dumps(schema)

def test_only_identical_validated_plain_direct_input_reuses_interpretation(monkeypatch):
    async def run():
        args={'action':'direct','quote':'','candidate':'','query':'','source_scope':'general','task_query':'','context':[]}
        async with fixture(monkeypatch, intent=args) as (_,_,state):
            question=[{'role':'user','content':'Объясни разницу между локальным и облачным хранением.'}]
            first=await intent.understand(question)
            first['context'].append({'span':[0,1],'status':'stated'})
            second=await intent.understand(question)
            assert second['context'] == []
            assert len(state['intent_payloads']) == 1
            await intent.understand([{'role':'assistant','content':'Предыдущий контекст.'},*question])
            assert len(state['intent_payloads']) == 2
            await intent.understand(question,on_invalid=lambda _:None)
            assert len(state['intent_payloads']) == 3
    asyncio.run(run())

@pytest.mark.parametrize('decision',[
    {'action':'search','query':'medical advice','context':[]},
    {'action':'clarify','span':[0,4]},
    {'action':'direct','query':'','context':[{'span':[0,4],'status':'stated'}]},
    {'action':'direct','query':'','context':[],'candidate':'апноэ','span':[0,5]},
    {'action':'direct','query':''},
])
def test_medical_context_repair_search_and_clarification_are_not_cached(decision):
    key=b'x';intent._direct_cache_put(key,decision)
    assert intent._direct_cache_get(key) is None

def test_cache_keys_include_provider_credentials_and_exact_payload():
    p={'messages':[{'role':'user','content':'private example'}]}
    key=intent._direct_cache_key(p,'https://provider.example','secret-a')
    assert isinstance(key,bytes) and len(key)==32
    assert key != intent._direct_cache_key(p,'https://other.example','secret-a')
    assert key != intent._direct_cache_key(p,'https://provider.example','secret-b')
    changed=copy.deepcopy(p);changed['messages'][0]['content']+='!'
    assert key != intent._direct_cache_key(changed,'https://provider.example','secret-a')

def test_cache_is_bounded_and_expires(monkeypatch):
    monkeypatch.setattr(intent.time,'monotonic',lambda:100)
    decision={'action':'direct','query':'','context':[]}
    for n in range(140):intent._direct_cache_put(str(n).encode(),decision)
    assert len(intent._DIRECT_CACHE)==intent._DIRECT_CACHE_LIMIT
    assert intent._direct_cache_get(b'0') is None
    monkeypatch.setattr(intent.time,'monotonic',lambda:221)
    assert intent._direct_cache_get(b'139') is None
    assert not intent._DIRECT_CACHE

@pytest.mark.parametrize('word,language',[('гестамин','ru'),('эпное','ru'),('роутор','ru'),('termenal','en')])
def test_index_returns_the_exact_same_phonetic_bucket_as_full_scan(word,language):
    lexicon=spelling._lexicon(language);folded=word.translate(spelling._VOWELS)
    expected={v for v in lexicon if len(v)==len(word) and v.translate(spelling._VOWELS)==folded}
    assert set(spelling._phonetic_index(language,len(word)).get(folded,()))==expected
    assert spelling._phonetic_index.cache_info().maxsize == 8


def test_invalid_compact_output_retries_with_original_complete_schema(monkeypatch):
    async def run():
        invalid={'action':'direct','quote':'invented fragment','candidate':'correction','query':'','context':[]}
        valid={'action':'direct','quote':'','query':'','context':[]}
        async with fixture(monkeypatch,intent_results=[invalid,valid]) as (_,_,state):
            result=await intent.understand([{'role':'user','content':'Объясни, что такое браузер.'}])
            assert result['action']=='direct'
            assert len(state['intent_payloads'])==2
            assert state['intent_payloads'][0]['tools']==[intent.TOOL]
            assert state['intent_payloads'][1]['tools']==[intent.FULL_TOOL]
            assert not intent._DIRECT_CACHE
    asyncio.run(run())


@pytest.mark.parametrize('question',['Привет!','Hello','Merhaba','2 + 2','12*3='])
def test_clear_greetings_and_literal_arithmetic_do_not_need_a_planner(question):
    from desktop.direct_tasks import direct_task
    assert asyncio.run(direct_task([{'role':'user','content':question}])) == {'action':'direct','query':'','context':[]}

@pytest.mark.parametrize('question',[
    'Что нового сегодня?','Найди новости','Как похудеть при астме?',
    'У меня гестамин эпное','Что значит 120/80?','120/80','2026-10-13',
    'Объясни разницу между браузером и облаком','Может это болезнь?',
    'Переведи это в дозировку: 2 мг','Сократи его','Привет, как лечить астму?',
])
def test_ambiguous_medical_current_and_general_questions_keep_the_full_planner(question):
    from desktop.direct_tasks import direct_task
    assert asyncio.run(direct_task([{'role':'user','content':question}])) is None

@pytest.mark.parametrize('question',['Привет!','Кто ты?','Привет, что ты умеешь?','Who are you?','Merhaba, sen kimsin?','2+2'])
def test_self_contained_turn_skips_planner_without_discarding_conversation_context(question):
    from desktop.direct_tasks import direct_task
    messages=[{'role':'user','content':'У меня астма.'},{'role':'assistant','content':'Поняла.'},{'role':'user','content':question}]
    before=copy.deepcopy(messages)
    assert asyncio.run(direct_task(messages)) == {'action':'direct','query':'','context':[]}
    assert messages == before

@pytest.mark.parametrize('question',['Привет, как лечить астму?','Кто ты и какая сейчас погода?','Сократи его','Как мне похудеть?'])
def test_context_dependent_and_mixed_requests_retain_full_interpretation(question):
    from desktop.direct_tasks import direct_task
    messages=[{'role':'user','content':'У меня астма.'},{'role':'assistant','content':'Поняла.'},{'role':'user','content':question}]
    assert asyncio.run(direct_task(messages)) is None

def test_explicit_text_task_keeps_unknown_spelling_on_full_path(monkeypatch):
    from desktop.direct_tasks import direct_task
    monkeypatch.setattr('desktop.direct_tasks.spelling_hints',lambda q:[])
    q='Переведи на английский: Я хочу прочитать книгу.'
    before=copy.deepcopy(q)
    assert asyncio.run(direct_task([{'role':'user','content':q}]))['action']=='direct'
    assert q==before
    monkeypatch.setattr('desktop.direct_tasks.spelling_hints',lambda q:[{'word':'unknown'}])
    assert asyncio.run(direct_task([{'role':'user','content':q}])) is None


@pytest.mark.parametrize('question,expected_calls',[('Привет!',0),('2+2',0),('Как безопасно снизить вес при астме?',1),('Гестамин эпное, как похудеть?',1)])
def test_direct_route_keeps_medical_and_typo_interpretation_over_real_http(monkeypatch,question,expected_calls):
    from desktop.web_search import WebSearch
    async def run():
        args={'action':'direct','quote':'','candidate':'','query':'','source_scope':'general','task_query':'','context':[]}
        async with fixture(monkeypatch,intent=args) as (_,_,state):
            search=WebSearch(provider='tavily',api_key='synthetic')
            result=await search._understand([{'role':'user','content':question}])
            assert result['action']=='direct'
            assert len(state['intent_payloads'])==expected_calls
    asyncio.run(run())
