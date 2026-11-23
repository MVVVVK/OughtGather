"""Offline Responses API contract, failure isolation and usage accounting."""
import json
import httpx
import pytest
from src.ai.client import (ARTICLE_ANALYSIS_SCHEMA, COMPOSITION_SCHEMA, DeepSeekClient,
                           ProviderError, URL)
from src.ai.editor import run
from src.ai.schemas import DIMENSIONS, REGIONS, TOPICS
from tests.test_ai_shadow import candidate, item, response_text


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv('AI_EDITOR_MODE', 'shadow')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake-secret')
    monkeypatch.setattr('src.ai.editor.load_profile', lambda: ({}, None, 'degraded'))


def official_item(cid):
    row = item(cid)
    row.update(topic='economy_finance', region='global')
    row.pop('reason', None)
    return row


def assert_request(payload, name, output_max):
    assert payload['model'] == 'deepseek-flash'
    assert payload['reasoning'] == {'effort': 'none'}
    assert payload['tools'] == [] and payload['store'] is False and payload['background'] is False
    assert payload['max_output_tokens'] == output_max
    assert payload['text']['format']['type'] == 'json_schema'
    assert payload['text']['format']['name'] == name
    assert 'UNTRUSTED DATA' in payload['instructions']
    assert not ({'messages', 'response_format', 'max_tokens', 'previous_response_id', 'conversation'} & payload.keys())


def test_explicit_schemas_are_closed_and_complete():
    root = ARTICLE_ANALYSIS_SCHEMA
    row = root['properties']['items']['items']
    required = {'candidate_id','topic','region','news_type',*DIMENSIONS,'impact_scope','confidence','event_cluster','cluster_type'}
    assert root['additionalProperties'] is False and row['additionalProperties'] is False
    assert set(row['required']) == required == set(row['properties']) and 'reason' not in row['properties']
    assert row['properties']['topic']['enum'] == list(TOPICS)
    assert row['properties']['region']['enum'] == list(REGIONS)
    for dim in DIMENSIONS:
        assert row['properties'][dim] == {'type':'number','minimum':0,'maximum':100}
    root = COMPOSITION_SCHEMA
    event = root['properties']['events']['items']
    member = event['properties']['members']['items']
    assert not root['additionalProperties'] and not event['additionalProperties'] and not member['additionalProperties']
    assert event['properties']['members']['minItems'] == 2
    assert set(member['required']) == {'candidate_id','relation'}


def test_article_success_40_candidates_and_usage_mapping(monkeypatch):
    monkeypatch.setenv('AI_MAX_CALLS_PER_RUN','1')
    calls = []
    def handler(request):
        assert str(request.url) == URL == 'https://api.deepseek.com/responses'
        payload = json.loads(request.content)
        assert_request(payload, 'article_analysis', 7200)
        assert payload['text']['format']['schema'] == ARTICLE_ANALYSIS_SCHEMA
        sent = json.loads(payload['input'])['candidates']
        assert len(sent) == 40
        calls.append(1)
        return httpx.Response(200, json=response_text(json.dumps({'items':[official_item(c['candidate_id']) for c in sent]}), 200, 100))
    report = run([candidate(i) for i in range(1,41)], DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert report['status'] == 'ok' and report['analyzed_count'] == 40 and len(calls) == 1
    assert (report['prompt_tokens'], report['completion_tokens'], report['total_tokens']) == (200, 100, 300)
    assert report['composition_status'] == 'fallback_budget'


def test_composition_success_uses_independent_schema():
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload['input'])
        calls.append(payload)
        if payload['text']['format']['name'] == 'article_analysis':
            assert_request(payload, 'article_analysis', 360)
            result = {'items': [official_item(c['candidate_id']) for c in data['candidates']]}
        else:
            assert_request(payload, 'editorial_composition', 256)
            assert payload['text']['format']['schema'] == COMPOSITION_SCHEMA
            assert 'profile' not in data and all('rss_excerpt' not in c for c in data['candidates'])
            result = {'events': [{'event_id':'e1', 'members':[{'candidate_id':'1','relation':'primary'}, {'candidate_id':'2','relation':'duplicate'}]}]}
        return httpx.Response(200, json=response_text(json.dumps(result), 100, 50))
    report = run([candidate(1), candidate(2)], DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert len(calls) == 2 and report['composition_status'] == 'ok'
    assert report['event_grouping_source'] == 'composition' and report['event_count'] == 1
    assert report['total_tokens'] == 300


def test_incomplete_max_tokens_is_not_accepted_and_retries_with_usage(monkeypatch):
    monkeypatch.setenv('AI_MAX_CALLS_PER_RUN','2')
    calls = []
    def handler(request):
        calls.append(1)
        valid_text = json.dumps({'items':[official_item(1)]})
        if len(calls) == 1:
            incomplete = response_text(valid_text, 20, 30)
            incomplete.update(status='incomplete', incomplete_details={'reason':'max_output_tokens'})
            return httpx.Response(200, json=incomplete)
        return httpx.Response(200, json=response_text(valid_text, 40, 50))
    report = run([candidate(1)], DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert len(calls) == report['api_calls'] == 2 and report['status'] == 'ok'
    assert (report['prompt_tokens'], report['completion_tokens'], report['total_tokens']) == (60, 80, 140)


def test_incomplete_content_filter_and_failed_hide_provider_details():
    secret = 'raw-private-provider-message'
    for status, details, expected in [('incomplete', {'reason':'content_filter'}, 'response_incomplete_content_filter'),
                                      ('failed', None, 'response_failed')]:
        calls = []
        def handler(request):
            calls.append(1)
            data = response_text(json.dumps({'items':[official_item(1)]}), 10, 20)
            data.update(status=status, incomplete_details=details, error={'message': secret})
            return httpx.Response(200, json=data)
        report = run([candidate(1)], DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
        assert len(calls) == 1 and report['status'] == 'degraded' and report['fallback_reason'] == expected
        assert report['total_tokens'] == 30 and secret not in json.dumps(report)


def test_missing_output_retries_and_never_leaks_raw_response():
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={'status':'completed', 'output':[{'type':'reasoning','content':[{'type':'reasoning_text','text':'private-response'}]}],
                                          'usage':{'input_tokens':7,'output_tokens':3,'total_tokens':10}})
    report = run([candidate(1)], DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert len(calls) == 2 and report['status'] == 'degraded' and report['fallback_reason'] == 'invalid_model_output'
    assert report['total_tokens'] == 20 and 'private-response' not in json.dumps(report)


def test_schema_valid_but_unknown_candidate_rejected_locally():
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=response_text(json.dumps({'items':[official_item('not-in-batch')]}), 10, 10))
    report = run([candidate(1)], DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert len(calls) == 2 and report['status'] == 'degraded'
    assert report['fallback_reason'] == 'invalid_model_output' and report['analyzed_count'] == 0


def test_incomplete_composition_falls_back_without_accepting_text():
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload['text']['format']['name'])
        data = json.loads(payload['input'])
        if calls[-1] == 'article_analysis':
            return httpx.Response(200, json=response_text(json.dumps({'items':[official_item(c['candidate_id']) for c in data['candidates']]}), 10, 10))
        incomplete = response_text(json.dumps({'events':[]}), 10, 20)
        incomplete.update(status='incomplete', incomplete_details={'reason':'max_output_tokens'})
        return httpx.Response(200, json=incomplete)
    report = run([candidate(1), candidate(2)], DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert len(calls) == 2 and report['status'] == 'ok'
    assert report['composition_status'] == 'fallback_invalid_output'
    assert report['event_grouping_source'] == 'fallback_hints' and report['total_tokens'] == 50


def test_inconsistent_usage_is_rejected_safely():
    data = response_text('{}', 1, 1)
    data['usage']['total_tokens'] = 999
    client = DeepSeekClient('fake-secret', transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data)))
    with pytest.raises(ProviderError, match='invalid_usage') as exc:
        client.analyze_batch(client.payload([], {}, 256), 2)
    assert 'fake-secret' not in str(exc.value)


def test_repeated_incomplete_is_bounded_and_never_accepts_valid_looking_json():
    calls = []
    def handler(request):
        calls.append(1)
        data = response_text(json.dumps({'items':[official_item(1)]}), 10, 20)
        data.update(status='incomplete', incomplete_details={'reason':'max_output_tokens'})
        return httpx.Response(200, json=data)
    report = run([candidate(1)], DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert len(calls) == report['api_calls'] == 2
    assert report['status'] == 'degraded' and report['fallback_reason'] == 'response_incomplete_max_tokens'
    assert report['analyzed_count'] == 0 and report['total_tokens'] == 60


def test_noncompleted_message_does_not_supply_output_text():
    data = response_text(json.dumps({'items':[official_item(1)]}), 10, 20)
    data['output'][0]['status'] = 'incomplete'
    client = DeepSeekClient('fake-secret', transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data)))
    with pytest.raises(ProviderError, match='missing_output_text'):
        client.analyze_batch(client.payload([], {}, 256), 2)
