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
        schema = payload['text']['format']['schema']['properties']['items']
        assert schema['minItems'] == schema['maxItems'] == 40
        assert schema['items']['properties']['candidate_id']['enum'] == [f'c{i:02d}' for i in range(1,41)]
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
            member = payload['text']['format']['schema']['properties']['events']['items']['properties']['members']['items']
            assert member['properties']['candidate_id']['enum'] == ['e01', 'e02']
            assert 'profile' not in data and all('rss_excerpt' not in c for c in data['candidates'])
            result = {'events': [{'event_id':'e1', 'members':[{'candidate_id':'e01','relation':'primary'}, {'candidate_id':'e02','relation':'duplicate'}]}]}
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
        valid_text = json.dumps({'items':[official_item('c01')]})
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
            data = response_text(json.dumps({'items':[official_item('c01')]}), 10, 20)
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
        data = response_text(json.dumps({'items':[official_item('c01')]}), 10, 20)
        data.update(status='incomplete', incomplete_details={'reason':'max_output_tokens'})
        return httpx.Response(200, json=data)
    report = run([candidate(1)], DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert len(calls) == report['api_calls'] == 2
    assert report['status'] == 'degraded' and report['fallback_reason'] == 'response_incomplete_max_tokens'
    assert report['analyzed_count'] == 0 and report['total_tokens'] == 60


def test_noncompleted_message_does_not_supply_output_text():
    data = response_text(json.dumps({'items':[official_item('c01')]}), 10, 20)
    data['output'][0]['status'] = 'incomplete'
    client = DeepSeekClient('fake-secret', transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data)))
    with pytest.raises(ProviderError, match='missing_output_text'):
        client.analyze_batch(client.payload([], {}, 256), 2)


def long_candidates(count):
    import hashlib
    return [{**candidate(i), 'candidate_id': hashlib.sha256(f'example-{i}'.encode()).hexdigest(),
             'title': f'Story {i}', 'traditional_selected': True} for i in range(1, count + 1)]


def test_forty_hashes_become_short_ids_and_are_restored(monkeypatch):
    monkeypatch.setenv('AI_MAX_CALLS_PER_RUN', '1')
    source = long_candidates(40)
    originals = [row['candidate_id'] for row in source]
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        sent = json.loads(payload['input'])['candidates']
        ids = [f'c{i:02d}' for i in range(1, 41)]
        assert [c['candidate_id'] for c in sent] == ids
        assert all(original not in request.content.decode() for original in originals)
        items = payload['text']['format']['schema']['properties']['items']
        assert items['minItems'] == items['maxItems'] == 40
        assert items['items']['properties']['candidate_id']['enum'] == ids
        assert items['items']['additionalProperties'] is False
        assert items['items']['properties']['event_cluster']['maxLength'] == 100
        assert items['items']['properties']['news_type']['maxLength'] == 100
        assert 'exactly one analysis item' in payload['instructions']
        calls.append(1)
        return httpx.Response(200, json=response_text(json.dumps({'items': [official_item(cid) for cid in ids]})))
    report = run(source, DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert len(calls) == 1 and report['status'] == 'ok'
    assert {row['candidate_id'] for row in report['ranking']} == set(originals)
    assert set(report['traditional_selected']) == set(originals)


def test_last_short_batch_has_exact_seventeen_id_schema(monkeypatch):
    monkeypatch.setenv('AI_MAX_CALLS_PER_RUN', '2')
    source = long_candidates(57)
    batches = []
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload['input'])
        ids = [row['candidate_id'] for row in data['candidates']]
        schema = payload['text']['format']['schema']['properties']['items']
        assert ids == [f'c{i:02d}' for i in range(1, len(ids) + 1)]
        assert schema['minItems'] == schema['maxItems'] == len(ids)
        assert schema['items']['properties']['candidate_id']['enum'] == ids
        batches.append(ids)
        return httpx.Response(200, json=response_text(json.dumps({'items': [official_item(cid) for cid in ids]})))
    report = run(source, DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert [len(batch) for batch in batches] == [40, 17]
    assert report['status'] == 'ok' and report['analyzed_count'] == 57
    assert {row['candidate_id'] for row in report['ranking']} == {c['candidate_id'] for c in source}


@pytest.mark.parametrize('bad_ids, expected', [(['c01', 'c99'], 'unknown_candidate_id'),
                                               (['c01', 'c01'], 'duplicate_candidate_id'),
                                               (['c01'], 'item_count')])
def test_article_bad_short_ids_are_rejected_without_raw_details(bad_ids, expected):
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=response_text(json.dumps({'items':[official_item(cid) for cid in bad_ids]}), 10, 10))
    report = run(long_candidates(2), DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert report['status'] == 'degraded' and report['analyzed_count'] == 0 and len(calls) == 2
    assert report['fallback_reason'] == 'invalid_model_output'
    assert report['local_validation_reason'] == expected
    assert 'c99' not in json.dumps(report)


def test_composition_sixty_short_ids_restore_real_ids_and_keep_singletons(monkeypatch):
    monkeypatch.setenv('AI_MAX_OUTPUT_TOKENS', '12000')
    monkeypatch.setenv('AI_MAX_TOTAL_TOKENS', '50000')
    monkeypatch.setenv('AI_MAX_CALLS_PER_RUN', '3')
    source = long_candidates(60)
    real_ids = {c['candidate_id'] for c in source}
    by_title = {c['title']: c['candidate_id'] for c in source}
    seen = []
    actual_groups = []
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload['input'])
        assert all(original not in request.content.decode() for original in real_ids)
        sent = data['candidates']
        seen.append(payload['text']['format']['name'])
        if 'profile' in data:
            ids = [c['candidate_id'] for c in sent]
            result = {'items': [official_item(cid) for cid in ids]}
        else:
            ids = [f'e{i:02d}' for i in range(1, 61)]
            assert [c['candidate_id'] for c in sent] == ids
            member = payload['text']['format']['schema']['properties']['events']['items']['properties']['members']['items']
            assert member['properties']['candidate_id']['enum'] == ids
            groups = [sent[:5], sent[5:9], sent[9:11]]
            actual_groups.extend({by_title[c['title']] for c in group} for group in groups)
            result = {'events': [{'event_id': f'group{i}', 'members': [
                {'candidate_id': c['candidate_id'], 'relation': 'primary' if n == 0 else 'analysis' if n == 1 else 'duplicate'}
                for n, c in enumerate(group)]} for i, group in enumerate(groups)]}
        return httpx.Response(200, json=response_text(json.dumps(result), 100, 100))
    report = run(source, DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert seen == ['article_analysis', 'article_analysis', 'editorial_composition']
    assert report['composition_status'] == 'ok' and report['event_grouping_source'] == 'composition'
    assert report['event_count'] == 52 and report['analyzed_count'] == 60
    assert {row['candidate_id'] for row in report['ranking']} == real_ids
    selected = set(report['ai_recommended'])
    assert all(len(group & selected) <= 2 for group in actual_groups)
    assert real_ids - set().union(*actual_groups) <= selected
    assert {frozenset(x['candidate_id'] for x in members) for members in report['event_clusters'].values()} == {frozenset(group) for group in actual_groups}


@pytest.mark.parametrize('bad_field, bad_value, expected', [('importance', -1, 'invalid_field'),
                                                             ('confidence', 'invented', 'invalid_enum')])
def test_local_field_validation_reason_is_safe(bad_field, bad_value, expected):
    def handler(request):
        payload = json.loads(request.content)
        ids = [c['candidate_id'] for c in json.loads(payload['input'])['candidates']]
        rows = [official_item(cid) for cid in ids]
        rows[0][bad_field] = bad_value
        return httpx.Response(200, json=response_text(json.dumps({'items': rows}), 10, 10))
    report = run(long_candidates(1), DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert report['status'] == 'degraded' and report['local_validation_reason'] == expected
    assert 'invented' not in json.dumps(report)


@pytest.mark.parametrize('returned_ids', [('e01', 'e99'), ('e01', 'e01')])
def test_composition_bad_short_ids_fall_back_without_raw_output(returned_ids):
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload['input'])
        if 'profile' in data:
            result = {'items': [official_item(c['candidate_id']) for c in data['candidates']]}
        else:
            result = {'events': [{'event_id': 'group', 'members': [
                {'candidate_id': returned_ids[0], 'relation': 'primary'},
                {'candidate_id': returned_ids[1], 'relation': 'duplicate'}]}]}
        return httpx.Response(200, json=response_text(json.dumps(result), 10, 10))
    report = run(long_candidates(2), DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler)))
    assert report['status'] == 'ok' and report['composition_status'] == 'fallback_invalid_output'
    assert report['event_grouping_source'] == 'fallback_hints' and 'e99' not in json.dumps(report)
