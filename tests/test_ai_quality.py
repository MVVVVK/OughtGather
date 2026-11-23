"""Offline event-composition, budget and neutral-profile acceptance tests."""
import json
from unittest.mock import patch
import httpx
import pytest
from src.ai.client import DeepSeekClient, SYSTEM
from src.ai.composition import compose
from src.ai.editor import run
from src.ai.schemas import score, validate_batch, validate_composition, TOPICS, REGIONS
from src.ai.candidates import CandidateCollector
from tests.test_ai_shadow import item, candidate, response


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv('AI_EDITOR_MODE', 'shadow')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake')
    monkeypatch.setattr('src.ai.editor.load_profile', lambda: ({'anti_filter_bubble': ['world']}, None, 'degraded'))


def test_global_impact_and_preference_weight_without_category_bonus():
    a = item(1) | {'importance':90, 'global_impact':95, 'personal_fit':10}
    b = item(2) | {'importance':55, 'global_impact':25, 'personal_fit':100}
    assert score(a) > score(b) + 20
    base = item(1)
    assert score(base | {'personal_fit':100}) - score(base | {'personal_fit':0}) == 5
    assert len({score(base | {'region':x}) for x in ('africa','east_asia','europe','north_america')}) == 1
    assert score(base | {'topic':'law_justice'}) == score(base | {'topic':'economy_finance'})
    assert score(base | {'importance':82,'global_impact':92}) > score(base | {'importance':90,'global_impact':20})


def test_no_preferences_forces_neutral_and_valid_profile_preserves_fit():
    data = {'items': [item(1) | {'personal_fit':99}]}
    assert validate_batch(data, {'1'}, personalized=False)[0]['personal_fit'] == 50
    assert validate_batch(data, {'1'}, personalized=True)[0]['personal_fit'] == 99
    assert 'anti_filter_bubble' not in ('hard_rules','soft_rules','prefer_signals','deprioritize_signals')


def test_taxonomy_normalization_preserves_distinct_subjects():
    subjects = ['governance_elections','trade_industry','economy_finance','conflict_security', 'energy',
                'technology','climate_environment','health','infrastructure_transport','migration_humanitarian','law_justice']
    rows = [item(n) | {'topic':topic} for n,topic in enumerate(subjects)]
    parsed = validate_batch({'items':rows}, {str(n) for n in range(len(rows))})
    assert [r['topic'] for r in parsed] == subjects
    assert set(subjects) <= set(TOPICS) and 'africa' in REGIONS
    assert validate_batch({'items':[item(1) | {'topic':'environment'}]}, {'1'})[0]['topic'] == 'climate_environment'
    assert 'geopolitics_diplomacy' in SYSTEM and 'global_impact' in SYSTEM


def test_event_composition_benchmark_and_must_cover():
    rows, mapping = [], {}
    for i in range(25):
        row = validate_batch({'items':[item(i) | {'importance':90 if i<13 else 82,
                              'global_impact':90 if i<13 else 80,
                              'information_gain':80-i/2}]}, {str(i)})[0]
        row['source'] = 'source'+str(i % 5)
        event = 'war' if i<9 else 'cop' if i<13 else 'event'+str(i)
        mapping[str(i)] = (event, 'analysis' if i in (1,10) else 'primary' if i in (0,9) or i>=13 else 'duplicate')
        rows.append(row)
    rows[-1].update(global_impact=98, personal_fit=5, importance=85)
    rows[-1]['score'] = score(rows[-1])
    selected, stats = compose(rows, mapping, 16)
    assert len([x for x in selected if int(x)<9]) <= 2
    assert len([x for x in selected if 9<=int(x)<13]) <= 2
    assert '24' in selected and stats['unique_events_recommended'] >= 12
    assert stats['largest_event_share'] <= 2/len(selected)
    assert selected == compose(list(reversed(rows)), mapping, 16)[0]


def test_composition_validation_and_missing_candidates():
    with pytest.raises(ValueError):
        validate_composition({'events':[{'event_id':'x','members':[{'candidate_id':'1','relation':'primary'}, {'candidate_id':'1','relation':'analysis'}]}]}, {'1','2'})
    with pytest.raises(ValueError):
        validate_composition({'events':[{'event_id':'x','members':[{'candidate_id':'no','relation':'primary'}]}]}, {'1'})
    assert validate_composition({'events':[]}, {'1'}) == {}


@pytest.mark.parametrize('calls_allowed, status', [(5, 'fallback_budget'), (6, 'fallback_provider')])
def test_composition_failure_or_budget_preserves_article_results(monkeypatch, calls_allowed, status):
    monkeypatch.setenv('AI_MAX_BATCH_CANDIDATES', '1')
    monkeypatch.setenv('AI_MAX_CALLS_PER_RUN', str(calls_allowed))
    seen = []
    def handler(request):
        payload = json.loads(request.content)
        body = json.loads(payload['messages'][1]['content'])
        seen.append(body)
        if 'profile' not in body:
            assert all('rss_excerpt' not in row for row in body['candidates'])
            return httpx.Response(503)
        cid = body['candidates'][0]['candidate_id']
        return httpx.Response(200, json=response([item(cid)]))
    report = run([candidate(i) for i in range(1,6)], DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert report['analyzed_count'] == 5 and report['status'] == 'ok'
    assert report['composition_status'] == status and len(seen) == calls_allowed
    assert report['event_grouping_source'] == 'fallback_hints'
    assert report['api_calls'] == calls_allowed


def test_candidate_cap_is_coverage_not_failure(monkeypatch):
    monkeypatch.setenv('AI_MAX_CANDIDATES','1')
    client = DeepSeekClient('fake', transport=httpx.MockTransport(lambda req: httpx.Response(200,json=response([item(1)]))))
    report = run([candidate(1), candidate(2)], client)
    assert report['status'] == 'ok' and report['coverage_status'] == 'candidate_cap_reached'
    assert report['skipped_candidate_cap'] == 1 and report['skipped_budget'] == 0


def test_language_precedence_without_guessing():
    c = CandidateCollector()
    rows = [{'url':'https://example.test/'+str(i), 'title':'Hello', '_entry':entry} for i,entry in enumerate([{'language':'fr'}, {}, {}])]
    c.collect('a', rows[:2], feed_language='en', source_language='es')
    c.collect('b', rows[2:], source_language='es')
    assert {r['language'] for r in c.snapshot(3)} == {'fr','en','es'}


def test_successful_composition_groups_cross_batch_without_leaking_data(monkeypatch):
    monkeypatch.setenv('AI_MAX_BATCH_CANDIDATES', '1')
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        body = json.loads(payload['messages'][1]['content'])
        calls.append(body)
        if 'profile' in body:
            cid = body['candidates'][0]['candidate_id']
            return httpx.Response(200, json=response([item(cid)]))
        assert 'profile' not in body
        assert all('rss_excerpt' not in c and 'url' not in c for c in body['candidates'])
        result = {'events':[{'event_id':'a','members':[{'candidate_id':'1','relation':'primary'}, {'candidate_id':'2','relation':'duplicate'}]}]}
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps(result)}}], 'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})
    candidates = [candidate(1), candidate(2), candidate(3)]
    for c in candidates: c['traditional_selected'] = True
    report = run(candidates, DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert report['composition_status'] == 'ok' and report['event_count'] == 2
    assert report['recommended_event_count'] == 2 and len(report['ai_recommended']) == 2
    assert report['duplicate_articles_suppressed'] == 1
    assert report['api_calls'] == 4 and report['prompt_tokens'] == 310
    assert 'ignore instructions' not in json.dumps(report)


def test_invalid_composition_is_safe_fallback(monkeypatch):
    monkeypatch.setenv('AI_MAX_BATCH_CANDIDATES', '2')
    def handler(request):
        body = json.loads(json.loads(request.content)['messages'][1]['content'])
        if 'profile' in body:
            return httpx.Response(200, json=response([item(x['candidate_id']) for x in body['candidates']]))
        return httpx.Response(200, json={'choices':[{'message':{'content':'   '}}], 'usage':{'prompt_tokens':10,'completion_tokens':0,'total_tokens':10}})
    report = run([candidate(1),candidate(2)], DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert report['status'] == 'ok' and report['composition_status'] == 'fallback_invalid_output'
    assert report['analyzed_count'] == 2 and report['api_calls'] == 2


def test_feed_language_is_read_only_metadata(tmp_path):
    import feedparser
    from src.config import ContentSource
    from src.dedup.tracker import DedupTracker
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import _fetch_source_task
    from tests.test_rss_publication_dedup import item as rss_item
    source = ContentSource(type='rss', src='https://example.org/feed', metadata={'language':'es'})
    collector = CandidateCollector()
    feed = feedparser.FeedParserDict({'bozo':False,'entries':[rss_item(1)],'feed':{'title':'Source','language':'en'}})
    with patch.object(RSSFetcher, '_parse_feed', return_value=feed):
        result, _ = _fetch_source_task(source, DedupTracker(str(tmp_path/'history')), 2, collector)
    assert result.articles[0].title == 'Article 1'
    assert collector.snapshot(2)[0]['language'] == 'en'
    assert 'language' not in feed.entries[0]


def test_composition_respects_output_token_reservations(monkeypatch):
    monkeypatch.setenv('AI_MAX_BATCH_CANDIDATES', '1')
    monkeypatch.setenv('AI_MAX_OUTPUT_TOKENS', '500')
    requests = []
    def handler(request):
        body = json.loads(json.loads(request.content)['messages'][1]['content'])
        requests.append(body)
        assert 'profile' in body
        return httpx.Response(200, json=response([item(body['candidates'][0]['candidate_id'])]))
    report = run([candidate(i) for i in range(1,6)], DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert report['analyzed_count'] == 5 and report['api_calls'] == 5
    assert report['completion_tokens'] == 250 and report['composition_status'] == 'fallback_budget'


def test_empty_memory_profile_neutralizes_scores_in_report(monkeypatch):
    returned = item(1) | {'personal_fit':100}
    client = DeepSeekClient('fake', transport=httpx.MockTransport(lambda req: httpx.Response(200,json=response([returned]))))
    report = run([candidate(1)], client)
    assert report['ranking'][0]['personal_fit'] == 50


def test_article_reason_is_optional_and_report_reason_is_local():
    without_reason = item(1)
    del without_reason['reason']
    parsed = validate_batch({'items': [without_reason]}, {'1'})
    assert 'reason' not in parsed[0]
    for returned in (without_reason, item(1) | {'reason': 'private provider output must not be reported'}):
        client = DeepSeekClient('fake', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response([returned]))))
        report = run([candidate(1)], client)
        assert report['status'] == 'ok'
        assert report['ranking'][0]['reason'].startswith('Local score basis:')
        assert 'private provider output' not in json.dumps(report)
    from src.ai.client import SYSTEM
    assert 'reason' not in SYSTEM.lower()


@pytest.mark.parametrize('second_completion, expected_composition', [(100, 'ok'), (7000, 'fallback_budget')])
def test_batch_allowance_refund_and_composition_budget(monkeypatch, second_completion, expected_composition):
    monkeypatch.setenv('AI_MAX_BATCH_CANDIDATES', '40')
    monkeypatch.setenv('AI_MAX_OUTPUT_TOKENS', '7300')
    monkeypatch.setenv('AI_MAX_INPUT_TOKENS', '50000')
    monkeypatch.setenv('AI_MAX_TOTAL_TOKENS', '30000')
    requests, actual = [], {'input': 0, 'output': 0}

    def handler(request):
        payload = json.loads(request.content)
        body = json.loads(payload['messages'][1]['content'])
        # Before each request, prior actual usage plus the requested maximum
        # remains within the configured output and total budget.
        estimate = len(json.dumps(payload, ensure_ascii=False).encode('utf8')) + 1024
        assert actual['input'] + estimate <= 50000
        assert actual['output'] + payload['max_tokens'] <= 7300
        assert actual['input'] + actual['output'] + estimate + payload['max_tokens'] <= 30000
        requests.append(payload)
        if 'profile' in body:
            assert len(body['candidates']) == 40 and payload['max_tokens'] == 7200
            rows = []
            for c in body['candidates']:
                row = item(c['candidate_id'])
                del row['reason']
                rows.append(row)
            completion = 100 if len(requests) == 1 else second_completion
            result = {'items': rows}
        else:
            assert 'rss_excerpt' not in body['candidates'][0]
            completion, result = 100, {'events': []}
        actual['input'] += 100
        actual['output'] += completion
        return httpx.Response(200, json={
            'choices': [{'message': {'content': json.dumps(result)}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': completion, 'total_tokens': 100 + completion}})

    report = run([candidate(i) for i in range(1, 81)],
                 DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert report['status'] == 'ok' and report['analyzed_count'] == 80
    assert report['composition_status'] == expected_composition
    assert report['api_calls'] == (3 if expected_composition == 'ok' else 2)
    assert report['prompt_tokens'] == actual['input']
    assert report['completion_tokens'] == actual['output']
    assert report['total_tokens'] == actual['input'] + actual['output'] <= 30000


@pytest.mark.parametrize('invalid_type', ['json', 'schema'])
def test_invalid_third_batch_reports_upstream_degradation(monkeypatch, invalid_type):
    monkeypatch.setenv('AI_MAX_BATCH_CANDIDATES', '40')
    monkeypatch.setenv('AI_MAX_OUTPUT_TOKENS', '30000')
    monkeypatch.setenv('AI_MAX_TOTAL_TOKENS', '100000')
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        body = json.loads(payload['messages'][1]['content'])
        assert 'profile' in body  # The composition request must not be sent.
        requests.append(body)
        if len(requests) <= 2:
            rows = []
            for c in body['candidates']:
                row = item(c['candidate_id'])
                del row['reason']
                rows.append(row)
            content = json.dumps({'items': rows})
        elif invalid_type == 'json':
            content = '   ' if len(requests) == 3 else '{invalid'
        else:
            bad_rows = [item(c['candidate_id']) for c in body['candidates']]
            del bad_rows[0]['global_impact']
            content = json.dumps({'items': bad_rows})
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}],
                                         'usage': {'prompt_tokens': 100, 'completion_tokens': 100, 'total_tokens': 200}})

    report = run([candidate(i) for i in range(1, 121)],
                 DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert len(requests) == report['api_calls'] == 4
    assert report['analyzed_count'] == 80 and report['failed_count'] == 40
    assert report['status'] == 'degraded' and report['fallback_reason'] == 'invalid_model_output'
    assert report['composition_status'] == 'fallback_upstream_degraded'


def test_composition_prompt_requests_only_non_singleton_groups():
    from src.ai.client import COMPOSE_SYSTEM
    assert 'TWO OR MORE' in COMPOSE_SYSTEM
    assert 'do not create singleton events' in COMPOSE_SYSTEM
    assert 'treated locally as singleton events' in COMPOSE_SYSTEM
    sample = json.loads(COMPOSE_SYSTEM.split('JSON example: ', 1)[1])
    assert len(validate_composition(sample, {'id1', 'id2'})) == 2


def test_composition_sixty_candidates_three_groups_and_singletons(monkeypatch):
    monkeypatch.setenv('AI_MAX_BATCH_CANDIDATES', '40')
    monkeypatch.setenv('AI_MAX_OUTPUT_TOKENS', '12000')
    monkeypatch.setenv('AI_MAX_TOTAL_TOKENS', '50000')
    monkeypatch.setenv('AI_MAX_CALLS_PER_RUN', '3')
    calls, actual = [], {'input': 0, 'output': 0}
    groups = [range(1, 6), range(6, 10), range(10, 12)]
    events = [{'event_id': f'e{n}', 'members': [
        {'candidate_id': str(i), 'relation': 'primary' if pos == 0 else 'analysis' if pos == 1 else 'duplicate'}
        for pos, i in enumerate(group)]} for n, group in enumerate(groups)]
    assert len(validate_composition({'events': events}, {str(i) for i in range(1, 61)})) == 11

    def handler(request):
        payload = json.loads(request.content)
        body = json.loads(payload['messages'][1]['content'])
        estimate = len(json.dumps(payload, ensure_ascii=False).encode('utf8')) + 1024
        assert actual['output'] + payload['max_tokens'] <= 12000
        assert actual['input'] + actual['output'] + estimate + payload['max_tokens'] <= 50000
        calls.append(payload)
        if 'profile' in body:
            data = {'items': [item(c['candidate_id']) for c in body['candidates']]}
            completion = 100
        else:
            assert len(body['candidates']) == 60 and payload['max_tokens'] == 3600
            assert all('rss_excerpt' not in row for row in body['candidates'])
            assert 'do not create singleton events' in payload['messages'][0]['content']
            data, completion = {'events': events}, 100
        actual['input'] += 100
        actual['output'] += completion
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(data)}}],
                                         'usage': {'prompt_tokens': 100, 'completion_tokens': completion, 'total_tokens': 100 + completion}})

    candidates = [candidate(i) | {'traditional_selected': True} for i in range(1, 61)]
    report = run(candidates, DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert report['status'] == 'ok' and report['composition_status'] == 'ok'
    assert report['event_grouping_source'] == 'composition' and report['api_calls'] == len(calls) == 3
    assert report['composition_candidate_count'] == 60 and report['event_count'] == 52
    assert report['total_tokens'] == actual['input'] + actual['output']
    selected = set(report['ai_recommended'])
    assert {str(i) for i in range(12, 61)} <= selected
    for group in groups:
        assert len({str(i) for i in group} & selected) <= 2
    assert {frozenset(x['candidate_id'] for x in members) for members in report['event_clusters'].values()} == {
        frozenset(str(i) for i in group) for group in groups}


def test_singleton_event_is_rejected_and_falls_back(monkeypatch):
    monkeypatch.setenv('AI_MAX_BATCH_CANDIDATES', '2')
    singleton = {'events': [{'event_id': 'e1', 'members': [{'candidate_id': '1', 'relation': 'primary'}]}]}
    with pytest.raises(ValueError):
        validate_composition(singleton, {'1', '2'})
    def handler(request):
        body = json.loads(json.loads(request.content)['messages'][1]['content'])
        data = {'items': [item(c['candidate_id']) for c in body['candidates']]} if 'profile' in body else singleton
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(data)}}],
                                         'usage': {'prompt_tokens': 100, 'completion_tokens': 100, 'total_tokens': 200}})
    report = run([candidate(1), candidate(2)], DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert report['status'] == 'ok' and report['composition_status'] == 'fallback_invalid_output'
    assert report['event_grouping_source'] == 'fallback_hints'


@pytest.mark.parametrize('bad_events', [
    [{'event_id': 'a', 'members': [{'candidate_id': '1', 'relation': 'primary'}, {'candidate_id': '999', 'relation': 'duplicate'}]}],
    [{'event_id': 'a', 'members': [{'candidate_id': '1', 'relation': 'primary'}, {'candidate_id': '1', 'relation': 'duplicate'}]}],
    [{'event_id': 'a', 'members': [{'candidate_id': '1', 'relation': 'primary'}, {'candidate_id': '2', 'relation': 'invented'}]}],
    [{'event_id': 'a', 'members': [{'candidate_id': '1', 'relation': 'primary'}, {'candidate_id': '2', 'relation': 'duplicate'}]},
     {'event_id': 'a', 'members': [{'candidate_id': '3', 'relation': 'primary'}, {'candidate_id': '4', 'relation': 'duplicate'}]}],
])
def test_composition_retains_id_and_relation_guards(bad_events):
    with pytest.raises(ValueError):
        validate_composition({'events': bad_events}, {'1', '2', '3', '4'})


@pytest.mark.parametrize('limit, expected', [('output', 'fallback_budget'), ('total', 'fallback_budget'),
                                              ('calls', 'fallback_budget'), ('deadline', 'fallback_timeout')])
def test_sixty_candidate_composition_respects_budget_and_deadline(monkeypatch, limit, expected):
    monkeypatch.setenv('AI_MAX_BATCH_CANDIDATES', '40')
    monkeypatch.setenv('AI_MAX_OUTPUT_TOKENS', '7200' if limit == 'output' else '12000')
    monkeypatch.setenv('AI_MAX_TOTAL_TOKENS', '10000' if limit == 'total' else '50000')
    monkeypatch.setenv('AI_MAX_CALLS_PER_RUN', '2' if limit == 'calls' else '3')
    monkeypatch.setenv('AI_TOTAL_TIMEOUT_SECONDS', '60')
    clock = {'now': 0}
    monkeypatch.setattr('src.ai.editor.time.monotonic', lambda: clock['now'])

    class OfflineClient:
        def __init__(self):
            self.calls = []
        def payload(self, batch, profile, max_tokens):
            return {'kind': 'article', 'ids': [x['candidate_id'] for x in batch], 'max_tokens': max_tokens}
        def composition_payload(self, candidates, max_tokens):
            assert len(candidates) == 60 and max_tokens == 3600
            if limit == 'deadline':
                clock['now'] = 61  # Time expires while preparing the request.
            return {'kind': 'composition', 'padding': 'x' * (6000 if limit == 'total' else 0), 'max_tokens': max_tokens}
        def analyze_batch(self, payload, timeout):
            assert timeout > 0
            self.calls.append(payload)
            assert payload['kind'] == 'article'  # No over-budget composition call.
            completion = (3000 if len(self.calls) == 1 else 1000) if limit == 'output' else 100
            return {'items': [item(cid) for cid in payload['ids']]}, {
                'prompt_tokens': 100, 'completion_tokens': completion, 'total_tokens': 100 + completion}

    client = OfflineClient()
    report = run([candidate(i) for i in range(1, 61)], client)
    assert report['status'] == 'ok' and report['analyzed_count'] == 60
    assert report['composition_status'] == expected and report['event_grouping_source'] == 'fallback_hints'
    assert report['api_calls'] == len(client.calls) == 2
    assert report['completion_tokens'] <= (7200 if limit == 'output' else 12000)
    assert report['total_tokens'] <= (10000 if limit == 'total' else 50000)


def test_composition_rejects_cross_event_duplicate_and_long_id():
    members = lambda a, b: [{'candidate_id': a, 'relation': 'primary'}, {'candidate_id': b, 'relation': 'duplicate'}]
    with pytest.raises(ValueError):
        validate_composition({'events': [{'event_id': 'a', 'members': members('1', '2')},
                                         {'event_id': 'b', 'members': members('2', '3')}]}, {'1', '2', '3'})
    with pytest.raises(ValueError):
        validate_composition({'events': [{'event_id': 'x' * 65, 'members': members('1', '2')}]}, {'1', '2'})
