"""Offline, global 200-candidate topic-first acceptance and safety tests."""
import hashlib
import json
import httpx
import pytest
from src.ai.client import DeepSeekClient, grouping_schema, selection_schema
from src.ai.editor import run
from src.ai.schemas import validate_groups, validate_selection


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv('AI_EDITOR_MODE', 'shadow')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake-secret')
    monkeypatch.setenv('AI_MAX_INPUT_TOKENS', '80000')
    monkeypatch.setenv('AI_MAX_OUTPUT_TOKENS', '8000')
    monkeypatch.setenv('AI_MAX_TOTAL_TOKENS', '88000')
    monkeypatch.setattr('src.ai.editor.load_profile', lambda: ({'soft_rules':['private profile']}, 3, 'available'))


def samples(n=200):
    titles = ([f'Ukraine ship attack report {i}' for i in range(6)] +
              [f'COP31 outcome and aftermath {i}' for i in range(5)] +
              [f'Tech company listing and market reaction {i}' for i in range(3)] +
              ['Election in country Alpha', 'Election in country Beta',
               'Trade decision about wheat', 'Trade decision about semiconductors'] +
              [f'Independent world event {i}' for i in range(max(0, n-18))])[:n]
    return [{'candidate_id': hashlib.sha256(str(i).encode()).hexdigest(), 'title': title,
             'source': f'Source {i%7}', 'language': 'fr' if i%2 else 'en',
             'published_at': '2026-11-24', 'rss_excerpt': f'Context {i}',
             'traditional_selected': i<16} for i,title in enumerate(titles)]


def response(data, inp=100, out=100):
    return {'status':'completed', 'output':[{'type':'message','role':'assistant',
            'content':[{'type':'output_text','text':json.dumps(data)}]}],
            'usage':{'input_tokens':inp,'output_tokens':out,'total_tokens':inp+out}}


def client_for(handler):
    return DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler))


def test_200_global_grouping_and_unique_representatives():
    source = samples()
    original_ids = {c['candidate_id'] for c in source}
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        assert str(request.url) == 'https://api.deepseek.com/responses'
        assert payload['reasoning'] == {'effort':'none'} and payload['tools'] == [] and not payload['store']
        assert all(real not in request.content.decode() for real in original_ids)
        data = json.loads(payload['input'])
        name = payload['text']['format']['name']
        calls.append(name)
        if name == 'topic_grouping':
            sent = data['candidates']
            assert len(sent) == 200 and [x['candidate_id'] for x in sent] == [f'c{i:03}' for i in range(1,201)]
            assert all('traditional_selected' not in c and 'url' not in c for c in sent)
            assert payload['text']['format']['schema']['properties']['groups']['items']['properties']['members']['items']['enum'] == [f'c{i:03}' for i in range(1,201)]
            result = {'groups':[{'members':[f'c{i:03}' for i in range(a,b)]} for a,b in ((1,7),(7,12),(12,15))]}
        else:
            assert name == 'topic_selection' and len(data['groups']) == 189
            assert 'review the entire shortlist' in payload['instructions'].lower()
            by_cid = {c['candidate_id']: g['group_id'] for g in data['groups'] for c in g['candidates']}
            chosen = ['c002','c007','c012','c015','c016','c017','c018'] + [f'c{i:03}' for i in range(19,28)]
            result = {'selections':[{'group_id':by_cid[cid], 'candidate_id':cid} for cid in chosen]}
        return httpx.Response(200, json=response(result))
    first = run(source, client_for(handler))
    second = run(source, client_for(handler))
    assert calls == ['topic_grouping','topic_selection'] * 2
    assert first['status'] == 'ok' and first['topic_grouping_status'] == first['topic_selection_status'] == 'ok'
    assert first['topic_count'] == 189 and first['multi_article_topic_count'] == 3
    assert first['duplicate_candidates_grouped'] == 11
    assert first['selected_topic_count'] == first['selected_article_count'] == 16 and first['max_articles_per_topic'] == 1
    assert len({x['candidate_id'] for g in first['topic_groups'] for x in g['members']}) == 200
    assert {x['candidate_id'] for g in first['topic_groups'] for x in g['members']} == original_ids
    selected_groups = [g for g in first['topic_groups'] if g['representative_id']]
    assert len(selected_groups) == 16 and {g['representative_id'] for g in selected_groups} == set(first['ai_recommended'])
    for group in selected_groups:
        assert group['representative_id'] in {m['candidate_id'] for m in group['members']}
        assert group['topic_title'] == next(m['title'] for m in group['members'] if m['candidate_id'] == group['representative_id'])
    for start,end in ((0,6),(6,11),(11,14)):
        assert len({source[i]['candidate_id'] for i in range(start,end)} & set(first['ai_recommended'])) == 1
    assert {source[i]['candidate_id'] for i in range(14,18)} <= set(first['ai_recommended'])
    assert first['topic_groups'] == second['topic_groups'] and first['ai_recommended'] == second['ai_recommended']
    artifact = json.dumps(first)
    for forbidden in ('fake-secret', 'private profile', 'rss_excerpt', 'reasoning_text', 'original_feedback'):
        assert forbidden not in artifact
    assert (first['prompt_tokens'],first['completion_tokens'],first['total_tokens']) == (200,200,400)


@pytest.mark.parametrize('bad', [
    {'groups':[{'members':['c001','c999']}]},
    {'groups':[{'members':['c001','c001']}]},
    {'groups':[{'members':['c001','c002']},{'members':['c002','c003']}]},
    {'groups':[{'members':['c001']}]},
])
def test_group_validation_rejects_unknown_duplicate_overlap_and_singleton(bad):
    with pytest.raises(ValueError):
        validate_groups(bad, {'c001':'a','c002':'b','c003':'c'})


@pytest.mark.parametrize('bad', [
    {'selections':[{'group_id':'g999','candidate_id':'c001'}]},
    {'selections':[{'group_id':'g001','candidate_id':'c003'}]},
    {'selections':[{'group_id':'g001','candidate_id':'c001'},{'group_id':'g001','candidate_id':'c002'}]},
])
def test_selection_validation_rejects_invalid_or_duplicate_groups(bad):
    with pytest.raises(ValueError):
        validate_selection(bad, {'g001':{'c001','c002'},'g002':{'c003'}})


def test_ungrouped_candidates_remain_singletons_and_ids_stable():
    ids = {'c001':'a','c002':'b','c003':'c'}
    groups = validate_groups({'groups':[{'members':['c002','c001']}]}, ids)
    assert len(groups) == 2 and sorted(len(g['members']) for g in groups) == [1,2]
    assert groups == validate_groups({'groups':[{'members':['c001','c002']}]}, ids)


@pytest.mark.parametrize('stage', ['topic_grouping','topic_selection'])
def test_failure_degrades_without_unvalidated_recommendations(stage):
    calls=[]
    def handler(request):
        payload = json.loads(request.content)
        name = payload['text']['format']['name']
        calls.append(name)
        if name == stage:
            return httpx.Response(503, text='private provider error')
        return httpx.Response(200, json=response({'groups':[]}))
    report = run(samples(10), client_for(handler))
    assert report['status'] == 'degraded' and report[stage+'_status'] == 'failed'
    assert report['ai_recommended'] == [] and report['selected_article_count'] == 0
    assert len(calls) <= 3 and 'private provider error' not in json.dumps(report)


def test_missing_key_and_off_do_not_call_provider(monkeypatch):
    monkeypatch.setenv('AI_EDITOR_MODE','off')
    assert run(samples(1))['status'] == 'off'
    monkeypatch.setenv('AI_EDITOR_MODE','shadow')
    monkeypatch.delenv('DEEPSEEK_API_KEY')
    assert run(samples(1))['status'] == 'unavailable'


@pytest.mark.parametrize('var', ['AI_MAX_INPUT_TOKENS','AI_MAX_OUTPUT_TOKENS','AI_MAX_TOTAL_TOKENS','AI_MAX_CALLS_PER_RUN'])
def test_zero_budget_prevents_request(monkeypatch, var):
    monkeypatch.setenv(var, '0')
    report = run(samples(10), client_for(lambda request: pytest.fail('unexpected request')))
    assert report['status'] == 'degraded' and report['fallback_reason'] == 'budget_exhausted'
    assert report['api_calls'] == 0 and not report['ai_recommended']


def test_single_call_budget_never_runs_selection(monkeypatch):
    monkeypatch.setenv('AI_MAX_CALLS_PER_RUN','1')
    calls=[]
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=response({'groups':[]}))
    report = run(samples(20), client_for(handler))
    assert len(calls) == 1 and report['topic_grouping_status'] == 'ok'
    assert report['status'] == 'degraded' and report['topic_selection_status'] == 'failed'
    assert report['fallback_reason'] == 'budget_exhausted' and not report['ai_recommended']


def test_request_schema_closed_and_no_old_scores():
    group = grouping_schema(['c001','c002'])
    assert group['additionalProperties'] is False
    assert group['properties']['groups']['items']['properties']['members']['minItems'] == 2
    schema = selection_schema(['g001'],['c001','c002'])
    assert schema['additionalProperties'] is False and schema['properties']['selections']['maxItems'] == 16
    assert 'importance' not in json.dumps(group) and 'personal_fit' not in json.dumps(schema)


def test_excerpt_budget_truncation_keeps_every_candidate(monkeypatch):
    source = samples()
    for row in source:
        row['rss_excerpt'] = 'A' * 600
    lengths = []
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload['input'])
        if payload['text']['format']['name'] == 'topic_grouping':
            assert len(data['candidates']) == 200
            lengths.append(len(data['candidates'][0]['rss_excerpt']))
            return httpx.Response(200, json=response({'groups':[]}))
        assert sum(len(group['candidates']) for group in data['groups']) == 200
        lengths.append(len(data['groups'][0]['candidates'][0]['rss_excerpt']))
        return httpx.Response(200, json=response({'selections':[]}))
    report = run(source, client_for(handler))
    assert report['status'] == 'ok' and report['topic_count'] == 200
    assert len(lengths) == 2 and any(length < 600 for length in lengths)
    assert sum(len(group['members']) for group in report['topic_groups']) == 200


def test_usage_refund_enables_second_stage(monkeypatch):
    monkeypatch.setenv('AI_MAX_OUTPUT_TOKENS', '4500')
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload['max_output_tokens'] == (4000 if len(requests) == 1 else 800)
        return httpx.Response(200, json=response({'groups':[]} if len(requests) == 1 else {'selections':[]}, 100, 100))
    report = run(samples(), client_for(handler))
    assert report['status'] == 'ok' and report['api_calls'] == 2
    assert report['completion_tokens'] == 200 and report['total_tokens'] == 400


def test_candidate_cap_is_explicit_and_not_silent(monkeypatch):
    monkeypatch.setenv('AI_MAX_CANDIDATES', '2')
    calls=[]
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload['input'])
        calls.append(data)
        if payload['text']['format']['name'] == 'topic_grouping':
            assert len(data['candidates']) == 2
            return httpx.Response(200, json=response({'groups':[]}))
        assert sum(len(group['candidates']) for group in data['groups']) == 2
        return httpx.Response(200, json=response({'selections':[]}))
    report = run(samples(5), client_for(handler))
    assert report['status'] == 'ok' and report['candidate_count'] == 5
    assert report['coverage_status'] == 'candidate_cap_reached' and report['skipped_candidate_cap'] == 3


@pytest.mark.parametrize('status, count', [(400,1),(401,1),(403,1),(429,2),(500,2),(503,2)])
def test_provider_http_failures_are_bounded_and_sanitized(status, count):
    calls=[]
    def handler(request):
        calls.append(1)
        return httpx.Response(status, text='SECRET-provider-details')
    report = run(samples(2), client_for(handler))
    assert len(calls) == count and report['status'] == 'degraded'
    assert not report['ai_recommended'] and 'SECRET-provider-details' not in json.dumps(report)


@pytest.mark.parametrize('content', ['', '   ', '{bad'])
def test_invalid_provider_json_has_bounded_retry(content):
    calls=[]
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={'status':'completed','output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':content}]}], 'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20}})
    report = run(samples(2), client_for(handler))
    assert len(calls) == 2 and report['status'] == 'degraded'
    assert report['total_tokens'] == 40 and not report['ai_recommended']


def test_incomplete_response_refunds_known_usage_and_retries():
    calls=[]
    def handler(request):
        payload=json.loads(request.content)
        calls.append(payload['text']['format']['name'])
        if len(calls) == 1:
            data=response({'groups':[]},20,30)
            data.update(status='incomplete', incomplete_details={'reason':'max_output_tokens'})
            return httpx.Response(200,json=data)
        return httpx.Response(200,json=response({'groups':[]} if len(calls)==2 else {'selections':[]}, 10, 10))
    report=run(samples(3),client_for(handler))
    assert calls == ['topic_grouping','topic_grouping','topic_selection']
    assert report['status']=='ok' and report['api_calls']==3
    assert report['last_response_status']=='completed' and report['last_error_response_status']=='incomplete'
    assert (report['prompt_tokens'],report['completion_tokens'],report['total_tokens']) == (40,50,90)


def test_timeout_and_deadline_do_not_publish_partial_result(monkeypatch):
    monkeypatch.setenv('AI_TOTAL_TIMEOUT_SECONDS','0')
    report=run(samples(2),client_for(lambda request: pytest.fail('request after deadline')))
    assert report['status']=='degraded' and report['fallback_reason']=='timeout'
    assert report['api_calls']==0 and not report['ai_recommended']


def test_invalid_selection_cannot_be_published():
    calls=[]
    def handler(request):
        payload=json.loads(request.content)
        calls.append(payload['text']['format']['name'])
        data={'groups':[]} if len(calls)==1 else {'selections':[{'group_id':'g999','candidate_id':'c001'}]}
        return httpx.Response(200,json=response(data))
    report=run(samples(3),client_for(handler))
    assert calls == ['topic_grouping','topic_selection','topic_selection']
    assert report['status']=='degraded' and report['topic_selection_status']=='failed'
    assert report['fallback_reason']=='invalid_model_output' and not report['ai_recommended']


def test_failed_provider_output_does_not_fabricate_grouping():
    def handler(request):
        data = response({'groups': [{'members':['c001','c002']}]}, 10, 10)
        data.update(status='failed', error={'message':'PRIVATE_PROVIDER_ERROR'})
        return httpx.Response(200,json=data)
    report = run(samples(2),client_for(handler))
    assert report['status']=='degraded' and report['fallback_reason']=='response_failed'
    assert report['topic_grouping_status']=='failed' and not report['topic_groups']
    assert 'PRIVATE_PROVIDER_ERROR' not in json.dumps(report)


def test_incomplete_valid_looking_json_is_not_accepted():
    calls=[]
    def handler(request):
        calls.append(1)
        data=response({'groups':[]},10,20)
        data.update(status='incomplete', incomplete_details={'reason':'max_output_tokens'})
        return httpx.Response(200,json=data)
    report=run(samples(2),client_for(handler))
    assert len(calls)==2 and report['status']=='degraded'
    assert report['fallback_reason']=='response_incomplete_max_tokens' and not report['ai_recommended']
    assert report['total_tokens']==60


def test_inconsistent_usage_rejected_without_key_disclosure():
    from src.ai.client import ProviderError
    data=response({'groups':[]},10,10)
    data['usage']['total_tokens']=999
    client=client_for(lambda request:httpx.Response(200,json=data))
    with pytest.raises(ProviderError, match='invalid_usage') as exc:
        client.analyze_batch(client.grouping_payload([{'candidate_id':'c001'}],256),2)
    assert 'fake-secret' not in str(exc.value)


def test_provider_missing_output_is_bounded():
    calls=[]
    def handler(request):
        calls.append(1)
        return httpx.Response(200,json={'status':'completed','output':[],
                                      'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20}})
    report=run(samples(2),client_for(handler))
    assert len(calls)==2 and report['fallback_reason']=='missing_output_text'
    assert report['status']=='degraded' and not report['ai_recommended']


def test_retry_after_beyond_deadline_prevents_retry(monkeypatch):
    monkeypatch.setenv('AI_TOTAL_TIMEOUT_SECONDS','1')
    calls=[]
    def handler(request):
        calls.append(1)
        return httpx.Response(429,headers={'Retry-After':'60'})
    report=run(samples(2),client_for(handler))
    assert len(calls)==1 and report['status']=='degraded' and not report['ai_recommended']


def test_provider_timeout_is_bounded_and_no_output_leaks():
    calls=[]
    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout('fake-secret')
    report=run(samples(2),client_for(handler))
    assert len(calls)==2 and report['status']=='degraded'
    assert report['fallback_reason']=='timeout' and 'fake-secret' not in json.dumps(report)


def test_unexpected_json_fields_are_rejected():
    with pytest.raises(ValueError):
        validate_groups({'groups':[], 'extra':'not allowed'}, {'c001':'a'})
    with pytest.raises(ValueError):
        validate_selection({'selections':[], 'extra':'not allowed'}, {'g001':{'c001'}})


def test_workflow_permissions_remain_read_only():
    from pathlib import Path
    for name in ('daily-gather.yml','apply-ai-memory.yml'):
        assert '\npermissions:\n  contents: read\n' in Path('.github/workflows',name).read_text()


def test_provider_input_scrubs_urls_and_hashes_from_snippets_and_profile(monkeypatch):
    source=samples(2)
    forbidden_hash=source[0]['candidate_id']
    source[0]['title']='News https://example.test/private ' + forbidden_hash
    source[0]['rss_excerpt']='Details https://example.test/story?id=secret ' + forbidden_hash
    monkeypatch.setattr('src.ai.editor.load_profile', lambda: ({'soft_rules':['Read https://example.test/private ' + forbidden_hash]}, 1, 'available'))
    def handler(request):
        body=request.content.decode()
        assert 'https://example.test' not in body and forbidden_hash not in body
        payload=json.loads(body)
        data={'groups':[]} if payload['text']['format']['name']=='topic_grouping' else {'selections':[]}
        return httpx.Response(200,json=response(data))
    report=run(source,client_for(handler))
    assert report['status']=='ok' and report['api_calls']==2
