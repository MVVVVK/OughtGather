"""Offline audit contracts: mocked partitions do not prove model semantic accuracy."""
import hashlib
import json

import httpx
import pytest

from src.ai.client import DeepSeekClient, AUDIT_SYSTEM
from src.ai.editor import run
from src.ai.schemas import validate_audit, validate_groups


TITLES = [
    'South African election results', 'Cape Verde election results',
    'French fraud trial verdict', 'Unrelated French murder case',
    'FBI network attacked', 'UK Next retailer cyberattack',
    'French administrative reform', 'French legal ruling on asylum',
    'Sudan hospital bombed', 'Tigray hospital services report',
    'COP31 agreement reached', 'COP31 agreement draws reactions',
    'Warship attacked at sea', 'Governments respond to ship attack',
    'Independent world news',
]


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv('AI_EDITOR_MODE', 'shadow')
    monkeypatch.setenv('AI_MAX_INPUT_TOKENS', '120000')
    monkeypatch.setenv('AI_MAX_OUTPUT_TOKENS', '40000')
    monkeypatch.setenv('AI_MAX_TOTAL_TOKENS', '160000')
    monkeypatch.setenv('AI_MAX_CALLS_PER_RUN', '10')
    monkeypatch.setenv('AI_TOTAL_TIMEOUT_SECONDS', '240')
    monkeypatch.setattr('src.ai.editor.load_profile', lambda: ({'private': 'profile'}, 3, 'available'))


def candidates():
    return [{'candidate_id': hashlib.sha256(str(i).encode()).hexdigest(), 'title': title,
             'source': f'Source {i}', 'language': 'fr' if i % 2 else 'en',
             'published_at': '2026-11-24', 'rss_excerpt': 'Evidence and context',
             'traditional_selected': i < 3} for i, title in enumerate(TITLES)]


def response(data, inp=100, out=100):
    return {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': json.dumps(data)}]}],
            'usage': {'input_tokens': inp, 'output_tokens': out, 'total_tokens': inp + out}}


def test_global_audit_applies_mocked_splits_and_preserves_true_groups():
    source = candidates()
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        name = payload['text']['format']['name']
        data = json.loads(payload['input'])
        calls.append(name)
        assert all(row['candidate_id'] not in request.content.decode() for row in source)
        if name == 'topic_grouping':
            result = {'groups': [{'members': [f'c{i:03d}', f'c{i+1:03d}']} for i in range(1, 15, 2)]}
        elif name == 'topic_audit':
            assert payload['instructions'] == AUDIT_SYSTEM
            assert len(data['groups']) == 7
            schema = payload['text']['format']['schema']
            split = schema['properties']['splits']['items']
            assert split['additionalProperties'] is False
            assert split['properties']['group_id']['enum'] == [g['group_id'] for g in data['groups']]
            assert set(split['properties']['partitions']['items']['properties']['members']['items']['enum']) == {
                c['candidate_id'] for g in data['groups'] for c in g['candidates']}
            assert all(len(g['candidates']) == 2 for g in data['groups'])
            assert all(set(c) == {'candidate_id', 'title', 'source', 'language', 'published_at', 'rss_excerpt'}
                       for g in data['groups'] for c in g['candidates'])
            splits = []
            for group in data['groups']:
                cs = group['candidates']
                if int(cs[0]['candidate_id'][1:]) <= 10:
                    splits.append({'group_id': group['group_id'],
                                   'partitions': [{'members': [c['candidate_id']]} for c in cs]})
            result = {'splits': splits}
        else:
            assert name == 'topic_selection' and len(data['groups']) == 13
            result = {'selections': [{'group_id': g['group_id'], 'candidate_id': g['candidates'][0]['candidate_id']}
                                     for g in data['groups']]}
        return httpx.Response(200, json=response(result))
    client = DeepSeekClient('fake-secret', transport=httpx.MockTransport(handler))
    report = run(source, client)
    repeated = run(source, client)
    assert calls == ['topic_grouping', 'topic_audit', 'topic_selection'] * 2
    assert report['status'] == 'ok' and report['topic_audit_status'] == 'ok'
    assert (report['audited_group_count'], report['split_group_count'], report['topic_count_after_audit']) == (7, 5, 13)
    by_id = {m['candidate_id']: g for g in report['topic_groups'] for m in g['members']}
    for i in range(0, 10, 2):
        assert by_id[source[i]['candidate_id']]['group_id'] != by_id[source[i+1]['candidate_id']]['group_id']
    for i in (10, 12):
        group = by_id[source[i]['candidate_id']]
        assert group == by_id[source[i+1]['candidate_id']]
        assert len({source[i]['candidate_id'], source[i+1]['candidate_id']} & set(report['ai_recommended'])) == 1
    assert len(by_id) == len(source) and len(report['ai_recommended']) == 13
    assert report['topic_groups'] == repeated['topic_groups']
    assert 'fake-secret' not in json.dumps(report) and 'profile' not in json.dumps(report)


@pytest.mark.parametrize('splits', [
    [{'group_id': 'a999', 'partitions': [{'members': ['c001']}, {'members': ['c002']}]}],
    [{'group_id': 'a001', 'partitions': [{'members': ['c001']}, {'members': ['c999']}]}],
    [{'group_id': 'a001', 'partitions': [{'members': ['c001']}, {'members': ['c001']}]}],
    [{'group_id': 'a001', 'partitions': [{'members': ['c001']}, {'members': ['c003']}]}],
    [{'group_id': 'a001', 'partitions': [{'members': ['c001']}, {'members': ['c002']}]},
     {'group_id': 'a001', 'partitions': [{'members': ['c001']}, {'members': ['c002']}]}],
    [{'group_id': 'a001', 'partitions': [{'members': ['c001']}]}],
])
def test_invalid_partitions_rejected(splits):
    ids = {'c001': 'x', 'c002': 'y', 'c003': 'z', 'c004': 'w'}
    original = validate_groups({'groups': [{'members': ['c001', 'c002']}, {'members': ['c003', 'c004']}]}, ids)
    audit = {'a001': next(g for g in original if 'x' in g['members']),
             'a002': next(g for g in original if 'z' in g['members'])}
    with pytest.raises(ValueError):
        validate_audit({'splits': splits}, audit, ids, original)


def test_omitted_member_rejected():
    ids = {'c001': 'x', 'c002': 'y', 'c003': 'z'}
    original = validate_groups({'groups': [{'members': list(ids)}]}, ids)
    with pytest.raises(ValueError, match='missing_candidate_id'):
        validate_audit({'splits': [{'group_id': 'a001', 'partitions': [
            {'members': ['c001']}, {'members': ['c002']}]}]}, {'a001': original[0]}, ids, original)


def test_200_candidates_retry_and_three_stage_budget():
    source = candidates()
    source.extend([{**source[-1], 'candidate_id': hashlib.sha256(f'extra{i}'.encode()).hexdigest(),
                    'title': f'Independent event {i}', 'rss_excerpt': 'x' * 600} for i in range(185)])
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload['input'])
        name = payload['text']['format']['name']
        calls.append(name)
        if name == 'topic_grouping':
            assert len(data['candidates']) == 200
            result = {'groups': [{'members': ['c001'] if calls.count(name) == 1 else ['c001', 'c002']}]}
            return httpx.Response(200, json=response(result, 22127 if calls.count(name) == 1 else 21000, 337))
        if name == 'topic_audit':
            assert len(data['groups']) == 1
            return httpx.Response(200, json=response({'splits': []}, 1500, 100))
        assert len(data['groups']) == 199
        return httpx.Response(200, json=response({'selections': []}, 18000, 150))
    report = run(source, DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert calls == ['topic_grouping', 'topic_grouping', 'topic_audit', 'topic_selection']
    assert report['status'] == 'ok' and report['retry_attempted']
    assert report['prompt_tokens'] == 62627 and report['completion_tokens'] == 924
    assert report['total_tokens'] == 63551 and report['api_calls'] == 4
    assert report['prompt_tokens'] <= 120000 and report['completion_tokens'] <= 40000 and report['total_tokens'] <= 160000


def test_off_mode_makes_no_calls(monkeypatch):
    monkeypatch.setenv('AI_EDITOR_MODE', 'off')
    def handler(request):
        pytest.fail('off mode must not call a provider')
    report = run(candidates(), DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert report['status'] == 'off' and report['api_calls'] == 0 and not report['ai_recommended']


def test_audit_retry_success_still_reaches_selection():
    calls = []
    def handler(request):
        name = json.loads(request.content)['text']['format']['name']
        calls.append(name)
        if name == 'topic_grouping':
            data = {'groups': [{'members': ['c001', 'c002']}]}
        elif name == 'topic_audit':
            data = {'splits': [{'group_id': 'a999', 'partitions': []}]} if calls.count(name) == 1 else {'splits': []}
        else:
            data = {'selections': []}
        return httpx.Response(200, json=response(data))
    report = run(candidates(), DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert calls == ['topic_grouping', 'topic_audit', 'topic_audit', 'topic_selection']
    assert report['status'] == 'ok' and report['retry_attempted']
    assert report['stage_api_calls']['topic_audit'] == 2


@pytest.mark.parametrize('kind', ['invalid', 'provider', 'budget'])
def test_audit_failure_never_recommends_or_reaches_selection(monkeypatch, kind):
    if kind == 'budget':
        monkeypatch.setenv('AI_MAX_CALLS_PER_RUN', '1')
    calls = []
    def handler(request):
        name = json.loads(request.content)['text']['format']['name']
        calls.append(name)
        if name == 'topic_grouping':
            return httpx.Response(200, json=response({'groups': [{'members': ['c001', 'c002']}]}))
        assert name == 'topic_audit'
        if kind == 'provider':
            return httpx.Response(401, text='private provider error')
        return httpx.Response(200, json=response({'splits': [{'group_id': 'a001', 'partitions': [
            {'members': ['c001']}, {'members': ['c001']}]}]}))
    report = run(candidates(), DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert report['status'] == 'degraded' and report['failed_stage'] == 'topic_audit'
    assert report['topic_audit_status'] == 'failed' and report['topic_selection_status'] == 'not_started'
    assert not report['ai_recommended'] and 'topic_selection' not in calls
    assert 'private provider error' not in json.dumps(report)
    if kind == 'invalid':
        assert calls.count('topic_audit') == 2 and report['fallback_reason'] == 'invalid_model_output'
    if kind == 'budget':
        assert report['fallback_reason'] == 'budget_exhausted' and calls == ['topic_grouping']
