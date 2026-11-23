"""Offline regression for multi-stage reservations, retry diagnostics and capacity."""
import json
import httpx
import pytest
from src.ai.client import DeepSeekClient
from src.ai.editor import run
from tests.test_ai_topics import samples, response


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv('AI_EDITOR_MODE', 'shadow')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake-secret')
    monkeypatch.setenv('AI_MAX_INPUT_TOKENS', '120000')
    monkeypatch.setenv('AI_MAX_OUTPUT_TOKENS', '40000')
    monkeypatch.setenv('AI_MAX_TOTAL_TOKENS', '160000')
    monkeypatch.setenv('AI_MAX_CALLS_PER_RUN', '10')
    monkeypatch.setenv('AI_TOTAL_TIMEOUT_SECONDS', '240')
    monkeypatch.setattr('src.ai.editor.load_profile', lambda: ({}, 3, 'available'))


def client(handler):
    return DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler))


def test_200_realistic_multilingual_excerpts_survive_three_stage_planning():
    candidates = samples(200)
    for i, row in enumerate(candidates):
        row['title'] = (f'International leaders discuss economic and security implications of policy decision {i}'
                        if i % 2 else f'国际新闻观察：各国领导人讨论地区安全与经济政策的最新进展和后续影响 {i}')
        row['source'] = f'Independent International News Desk {i % 13}'
        row['rss_excerpt'] = (('Officials described the latest decision and its likely impact on international trade, '
                               'security and regional cooperation. Further negotiations are expected this week. ') * 3
                              if i % 2 else ('有关方面介绍了最新决定及其对国际贸易、地区安全和经济合作可能产生的影响。'
                                             '各方预计将在本周继续进行谈判，并公布更多细节。') * 4)
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload['input'])
        stage = payload['text']['format']['name']
        rows = data['candidates'] if stage == 'topic_grouping' else [c for g in data['groups'] for c in g['candidates']]
        requests.append((stage, rows))
        if stage == 'topic_grouping':
            assert len(rows) == 200
            groups, n = [], 1
            for size in [4] * 11 + [3] * 6:
                groups.append({'members': [f'c{i:03d}' for i in range(n, n + size)]})
                n += size
            result, usage = {'groups': groups}, (22000, 400)
        elif stage == 'topic_audit':
            assert len(data['groups']) == 17 and len(rows) == 62
            result, usage = {'splits': []}, (5000, 250)
        else:
            assert len(data['groups']) == 155 and len(rows) == 200
            result, usage = {'selections': []}, (20000, 300)
        return httpx.Response(200, json=response(result, *usage))
    report = run(candidates, client(handler))
    assert report['status'] == 'ok' and [stage for stage, _ in requests] == ['topic_grouping', 'topic_audit', 'topic_selection']
    assert report['api_calls'] == 3 and report['budget_limit_hit'] is None
    assert (report['prompt_tokens'], report['completion_tokens'], report['total_tokens']) == (47000, 950, 47950)
    for stage, rows in requests:
        stats = report['excerpt_retention'][stage]
        assert stats['received_nonempty'] == len(rows)
        assert len(stats['attempts']) == 1
        assert stats['attempts'][0]['sent_nonempty'] == sum(bool(row['rss_excerpt'].strip()) for row in rows)
        assert stats['attempts'][0]['average_retained_chars'] == round(sum(len(row['rss_excerpt']) for row in rows) / len(rows), 2)
    group_stats = report['excerpt_retention']['topic_grouping']['attempts'][0]
    assert group_stats['sent_nonempty'] == 200
    assert group_stats['average_retained_chars'] >= 100 and group_stats['retention_rate'] >= 0.4
    assert '有关方面介绍了最新决定' not in json.dumps(report, ensure_ascii=False)


def test_excerpt_counts_handle_empty_and_failed_attempts():
    candidates = samples(3)
    candidates[0]['rss_excerpt'] = 'long enough excerpt'
    candidates[1]['rss_excerpt'] = ''
    candidates[2]['rss_excerpt'] = '  '
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        name = payload['text']['format']['name']
        calls.append(name)
        if name == 'topic_grouping':
            result = {'groups': [{'members': ['c001']}]} if calls.count(name) == 1 else {'groups': []}
        else:
            result = {'selections': []}
        return httpx.Response(200, json=response(result, 100, 100))
    report = run(candidates, client(handler))
    assert report['status'] == 'ok'
    stats = report['excerpt_retention']['topic_grouping']
    assert stats['received_nonempty'] == 1 and len(stats['attempts']) == 2
    assert all(attempt['sent_nonempty'] == 1 for attempt in stats['attempts'])
    assert report['excerpt_retention']['topic_audit'] == {'received_nonempty': 0, 'attempts': []}
    assert report['excerpt_retention']['topic_selection']['received_nonempty'] == 1
    assert 'long enough excerpt' not in json.dumps(report)


def test_200_invalid_first_response_retries_and_reserves_selection():
    candidates = samples(200)
    for row in candidates:
        row['rss_excerpt'] = 'X' * 600
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload['input'])
        name = payload['text']['format']['name']
        calls.append((name, data, payload))
        if name == 'topic_grouping':
            assert len(data['candidates']) == 200
            if len(calls) == 1:
                return httpx.Response(200, json=response({'groups':[{'members':['c001']}]}, 22127, 337))
            return httpx.Response(200, json=response({'groups':[]}, 21000, 300))
        assert sum(len(group['candidates']) for group in data['groups']) == 200
        group = data['groups'][0]
        return httpx.Response(200, json=response({'selections':[{'group_id':group['group_id'], 'candidate_id':group['candidates'][0]['candidate_id']}]}, 18000, 150))
    report = run(candidates, client(handler))
    assert [x[0] for x in calls] == ['topic_grouping','topic_grouping','topic_selection']
    first_len = len(calls[0][1]['candidates'][0]['rss_excerpt'])
    retry_len = len(calls[1][1]['candidates'][0]['rss_excerpt'])
    assert 0 <= retry_len <= first_len < 600
    assert report['status'] == 'ok' and report['topic_count'] == 200
    assert report['retry_attempted'] is True and report['retry_blocked_reason'] is None
    assert report['last_attempt_error_category'] == 'local_validation' and report['last_response_status'] == 'completed'
    assert report['last_error_response_status'] == 'completed'
    assert report['stage_api_calls'] == {'topic_grouping':2, 'topic_audit':0, 'topic_selection':1}
    assert (report['prompt_tokens'], report['completion_tokens'], report['total_tokens']) == (61127, 787, 61914)
    assert report['total_tokens'] <= 160000 and report['budget_limit_hit'] is None
    assert len(report['ai_recommended']) == 1 and len(report['topic_groups']) == 200
    assert 'fake-secret' not in json.dumps(report)


def test_actual_output_limit_preserves_completed_validation_failure(monkeypatch):
    monkeypatch.setenv('AI_MAX_OUTPUT_TOKENS', '4000')
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=response({'groups':[{'members':['c001']}]}, 22127, 4000))
    report = run(samples(200), client(handler))
    assert len(calls) == 1 and report['status'] == 'degraded'
    assert report['fallback_reason'] == 'budget_exhausted' and report['failed_stage'] == 'topic_grouping'
    assert report['last_response_status'] == 'completed' and report['last_attempt_error_category'] == 'local_validation'
    assert report['retry_attempted'] is False and report['retry_blocked_reason'] == 'actual_usage_limit'
    assert report['budget_limit_hit'] == {'resource':'output','basis':'actual_usage'}
    assert not report['ai_recommended']


def test_unknown_usage_is_conservatively_reserved_and_explained(monkeypatch):
    monkeypatch.setenv('AI_MAX_OUTPUT_TOKENS', '5000')
    calls = []
    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout('raw secret must not leak')
    report = run(samples(200), client(handler))
    assert len(calls) == 1 and report['fallback_reason'] == 'budget_exhausted'
    assert report['last_attempt_error_category'] == 'timeout' and report['last_response_status'] == 'not_received'
    assert report['retry_attempted'] is False and report['retry_blocked_reason'] == 'reservation_insufficient'
    assert report['budget_limit_hit'] == {'resource':'output','basis':'reservation'}
    assert report['prompt_tokens'] == report['completion_tokens'] == 0 and not report['ai_recommended']
    assert 'raw secret' not in json.dumps(report)


@pytest.mark.parametrize('count', [200,500,1000,2000])
def test_capacity_and_cap_coverage_are_explicit(count):
    candidates = samples(count)
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload['input'])
        name = payload['text']['format']['name']
        calls.append(name)
        if name == 'topic_grouping':
            assert len(data['candidates']) == 200
            return httpx.Response(200, json=response({'groups':[]}, 100, 100))
        assert sum(len(group['candidates']) for group in data['groups']) == 200
        return httpx.Response(200, json=response({'selections':[]}, 100, 100))
    report = run(candidates, client(handler))
    assert report['candidate_count'] == count and report['candidate_cap'] == 200
    assert report['skipped_candidate_cap'] == count - 200
    assert report['coverage_status'] == ('complete' if count == 200 else 'candidate_cap_reached')
    assert report['topic_count'] == 200 and report['status'] == 'ok'
    assert report['stage_api_calls'] == {'topic_grouping':1, 'topic_audit':0, 'topic_selection':1} and len(calls) == 2
    assert report['prompt_tokens'] <= 120000 and report['completion_tokens'] <= 40000 and report['total_tokens'] <= 160000


@pytest.mark.parametrize('variable, resource', [('AI_MAX_INPUT_TOKENS','input'), ('AI_MAX_OUTPUT_TOKENS','output'),
                                                ('AI_MAX_TOTAL_TOKENS','total'), ('AI_MAX_CALLS_PER_RUN','calls')])
def test_budget_diagnostics_identify_hard_limit(monkeypatch, variable, resource):
    monkeypatch.setenv(variable, '0')
    report = run(samples(200), client(lambda request: pytest.fail('unexpected request')))
    assert report['status'] == 'degraded' and report['fallback_reason'] == 'budget_exhausted'
    assert report['budget_limit_hit']['resource'] == resource
    assert report['failed_stage'] == 'topic_grouping' and not report['ai_recommended']


def test_invalid_json_reports_completed_parse_failure_without_raw_content():
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={'status':'completed', 'output':[{'type':'message','role':'assistant',
            'content':[{'type':'output_text','text':'{SECRET-invalid-json'}]}],
            'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20}})
    report = run(samples(2), client(handler))
    assert len(calls) == 2 and report['status'] == 'degraded'
    assert report['failed_stage'] == 'topic_grouping' and report['last_response_status'] == 'completed'
    assert report['last_attempt_error_category'] == 'json_parse' and report['retry_attempted'] is True
    assert 'SECRET-invalid-json' not in json.dumps(report)


def test_call_limit_preserves_first_validation_failure(monkeypatch):
    monkeypatch.setenv('AI_MAX_CALLS_PER_RUN', '1')
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=response({'groups':[{'members':['c001']}]}, 100, 100))
    report = run(samples(2), client(handler))
    assert len(calls) == 1 and report['fallback_reason'] == 'budget_exhausted'
    assert report['last_attempt_error_category'] == 'local_validation' and report['last_response_status'] == 'completed'
    assert report['last_error_response_status'] == 'completed'
    assert report['retry_attempted'] is False and report['retry_blocked_reason'] == 'call_limit'
    assert report['budget_limit_hit'] == {'resource':'calls','basis':'actual_usage'}


def test_completed_response_with_invalid_usage_has_safe_status():
    def handler(request):
        data = response({'groups':[]}, 10, 10)
        data['usage']['total_tokens'] = 999
        return httpx.Response(200, json=data)
    report = run(samples(2), client(handler))
    assert report['status'] == 'degraded' and report['fallback_reason'] == 'invalid_usage'
    assert report['last_response_status'] == 'completed' and report['last_attempt_error_category'] == 'invalid_usage'
    assert report['stage_api_calls'] == {'topic_grouping':1,'topic_audit':0,'topic_selection':0}


def test_non_retryable_provider_error_explains_why_no_retry():
    calls=[]
    def handler(request):
        calls.append(1)
        return httpx.Response(403, text='private error')
    report=run(samples(2), client(handler))
    assert len(calls)==1 and report['status']=='degraded'
    assert report['last_response_status']=='http_error' and report['last_attempt_error_category']=='provider_http'
    assert report['retry_attempted'] is False and report['retry_blocked_reason']=='non_retryable'
    assert 'private error' not in json.dumps(report)
