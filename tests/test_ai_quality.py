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
