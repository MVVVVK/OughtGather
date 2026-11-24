import json
from datetime import datetime, timezone

import httpx
import pytest

from src.ai.client import DeepSeekClient
from src.ai.editor import StageFailure
from scripts.editorial_v2.semantic.relations import cluster, compare
from scripts.editorial_v2.semantic.editor import run
from scripts.editorial_v2.semantic.deepseek import DeepSeekEditorial

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)


def candidate(cid, title, date=None, language='en', excerpt=None):
    return {'candidate_id': cid, 'title': title, 'source': 'Synthetic', 'language': language,
            'published_at': date, 'excerpt': excerpt, 'excerpt_permitted': bool(excerpt),
            'media_type': 'unknown', 'media_evidence': []}


class FakeEncoder:
    """Test double only: never used as a real-model result."""
    def encode(self, texts): return [[1.0, 0.0] for _ in texts]


def test_missing_time_is_distinct_from_conflict_and_not_automatic_abstention():
    a = candidate('a', 'Neptune Benghazi vessel attack')
    b = candidate('b', 'Attaque Neptune Benghazi', language='fr')
    result = compare(a, b, [1., 0.], [1., 0.], NOW, 0.9)
    assert result['relation'] == 'unknown' and result['time'] == 'unknown'
    a['published_at'] = '2026-11-20T00:00:00Z'; b['published_at'] = '2026-11-24T00:00:00Z'
    result = compare(a, b, [1., 0.], [1., 0.], NOW, 0.9, lambda *_: 'same_event')
    assert result['relation'] == 'same_event' and result['time'] == 'conflict'


def test_high_similarity_alone_is_not_same_event_and_reviewer_is_injectable():
    a = candidate('a', 'France election')
    b = candidate('b', 'Nigeria election')
    assert compare(a, b, [1., 0.], [1., 0.], NOW, 0.9)['relation'] == 'unknown'
    assert compare(a, b, [1., 0.], [1., 0.], NOW, 0.9, lambda *_: 'different')['relation'] == 'different'
    with pytest.raises(ValueError): compare(a, b, [1., 0.], [1., 0.], NOW, 0.9, lambda *_: 'invented')


def test_complete_link_disallows_review_bridge():
    rows = [candidate(x, 'Unrelated ' + x) for x in 'abc']
    def reviewer(a, b, _):
        return 'different' if {a['candidate_id'], b['candidate_id']} == {'a', 'c'} else 'same_event'
    result = cluster(rows, FakeEncoder(), NOW, 0.9, reviewer)
    assert max(map(len, result)) == 2
    assert [[c['candidate_id'] for c in g] for g in result] == [['a', 'b'], ['c']]


def test_representative_fallback_budget_and_no_forced_filling():
    rows = [candidate('a', 'Neptune Benghazi attack', excerpt='longer excerpt'),
            candidate('b', 'Attaque Neptune Benghazi', language='fr'),
            candidate('c', 'Other local item')]
    def importance(group, facts):
        return {'level': 'A' if len(group) == 2 else 'D', 'evidence_ids': [group[0]['candidate_id']],
                'fact_ids': [], 'uncertainty': 'low'}
    tested = []
    def verify(c):
        tested.append(c['candidate_id']); return 'failed' if c['candidate_id'] == 'a' else 'full_text'
    result = run(rows, NOW.isoformat(), FakeEncoder(), 0.9, importance, verify, reviewer=lambda *_: 'same_event' if _[0]['candidate_id'] in ('a', 'b') and _[1]['candidate_id'] in ('a', 'b') else 'unknown')
    assert result['selected'] == ['b'] and tested == ['a', 'b'] and result['verification_calls'] == 2
    assert result['quality']['a'] == 'failed' and result['quality']['b'] == 'full_text'
    result = run(rows, NOW.isoformat(), FakeEncoder(), 0.9, importance, verify, reviewer=lambda *_: 'same_event' if {_[0]['candidate_id'], _[1]['candidate_id']} == {'a', 'b'} else 'unknown', verification_budget=1)
    assert not result['selected'] and result['verification_calls'] == 1
    with pytest.raises(ValueError): run(rows, NOW.isoformat(), FakeEncoder(), 0.9, importance, verify, verification_budget=41)


def test_fact_marginal_gain_and_untrusted_assessment():
    rows = [candidate('a', 'Alpha'), candidate('b', 'Beta'), candidate('c', 'Gamma')]
    facts = {'a': [{'id': 'f1', 'description': 'one'}], 'b': [{'id': 'f1', 'description': 'one'}],
             'c': [{'id': 'f2', 'description': 'two'}]}
    def importance(group, items):
        return {'level':'A', 'uncertainty':'low', 'evidence_ids':[group[0]['candidate_id']],
                'fact_ids':[item['id'] for item in items]}
    result = run(rows, NOW.isoformat(), FakeEncoder(), 0.9, importance, lambda c: {'status': 'full_text', 'verified_fact_ids': ['f2' if c['candidate_id'] == 'c' else 'f1']}, facts_by_event=facts, capacity=2)
    assert result['selected'] == ['a', 'c']
    def bad(group, items): return {'level':'A', 'uncertainty':'low', 'evidence_ids':['unknown'], 'fact_ids':[]}
    result = run(rows, NOW.isoformat(), FakeEncoder(), 0.9, bad, lambda _: 'full_text')
    assert result['selected'] == [] and result['unresolved_events'] == 3


def test_deepseek_real_client_adapter_uses_explicit_approval_and_existing_budget(monkeypatch):
    monkeypatch.setenv('AI_MAX_CALLS_PER_RUN', '1')
    calls = []
    def transport(request):
        payload = json.loads(request.content); calls.append(payload)
        assert payload['store'] is False and payload['tools'] == [] and payload['reasoning'] == {'effort': 'none'}
        assert 'real-hash' not in payload['input'] and 'UNPERMITTED_EXCERPT' not in payload['input']
        data = {'level':'A', 'evidence_ids':['c001'], 'fact_ids':['f001'], 'uncertainty':'medium'}
        return httpx.Response(200, json={'status':'completed', 'usage':{'input_tokens':10,'output_tokens':4,'total_tokens':14},
            'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps(data)}]}]})
    client = DeepSeekClient('synthetic-key', transport=httpx.MockTransport(transport))
    with pytest.raises(ValueError): DeepSeekEditorial(client, {'real-hash'})
    adapter = DeepSeekEditorial(client, {'real-hash'}, approved_fact_ids={'fact-1'}, allow_external=True)
    row = candidate('real-hash', 'Synthetic article'); row['excerpt'] = 'UNPERMITTED_EXCERPT'; row['excerpt_permitted'] = False
    result = adapter.importance([row], [{'id':'fact-1', 'description':'Synthetic evidence'}])
    assert result == {'level':'A', 'evidence_ids':['real-hash'], 'fact_ids':['fact-1'], 'uncertainty':'medium'}
    assert len(calls) == 1 and adapter.report['total_tokens'] == 14
    with pytest.raises(StageFailure): adapter.importance([row], [])
    with pytest.raises(ValueError): adapter._prepare([candidate('unauthorized','title')], 100)


def test_public_proxy_split_prevents_exact_article_overlap_and_pr_auc():
    from scripts.editorial_v2.semantic.crossnews import split, metrics
    rows = [{'source1':'a','source2':'b'}, {'source1':'b','source2':'c'},
            {'source1':'d','source2':'e'}, {'source1':'f','source2':'g'}]
    assignments = split(rows)
    assert assignments[0] == assignments[1]
    a = {r[k] for r, group in zip(rows, assignments) if group == 'dev' for k in ('source1','source2')}
    b = {r[k] for r, group in zip(rows, assignments) if group == 'heldout' for k in ('source1','source2')}
    assert not a & b and assignments == split(rows)
    measured = metrics([{'score':.9,'label':'similar'}, {'score':.8,'label':'different'},
                        {'score':.4,'label':'similar'}, {'score':.1,'label':'different'}], .5)
    assert measured['tp'] == 1 and measured['fp'] == 1 and measured['fn'] == 1
    assert measured['f1'] == 0.5 and measured['pr_auc_average_precision'] == pytest.approx((1 + 2/3)/2)


def test_blocked_model_path_does_not_automatically_download(monkeypatch):
    from scripts.editorial_v2.semantic import e5
    import builtins
    original = builtins.__import__
    def reject(name, *args, **kwargs):
        if name == 'torch': raise ImportError('unavailable')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', reject)
    with pytest.raises(RuntimeError, match='unavailable'): e5.E5Encoder()


def test_verification_budget_is_hard_and_no_forced_selection():
    rows = [candidate(f'{i:02}', f'Unclassified report {i}') for i in range(45)]
    def importance(group, facts):
        return {'level': 'A', 'uncertainty': 'low', 'evidence_ids': [group[0]['candidate_id']], 'fact_ids': []}
    calls = []
    def unavailable(c):
        calls.append(c['candidate_id']); raise RuntimeError('offline')
    result = run(rows, NOW.isoformat(), FakeEncoder(), .9, importance, unavailable)
    assert len(calls) == result['verification_calls'] == 40 and result['selected'] == []


def test_reviewer_failure_abstains_without_exposing_error():
    a = candidate('a', 'France election'); b = candidate('b', 'Nigeria election')
    def failed(*args): raise RuntimeError('private provider text')
    result = compare(a, b, [1., 0.], [1., 0.], NOW, .9, failed)
    assert result['relation'] == 'unknown' and 'private provider text' not in str(result)


def test_synthetic_substantive_update_can_survive_representative_failure():
    original = candidate('a', 'Neptune Benghazi vessel attack', excerpt='Original longer RSS excerpt')
    update = candidate('b', 'Attaque Neptune Benghazi mise à jour', language='fr', excerpt='new')
    facts = {'a': [{'id': 'update-fact', 'description': 'Synthetic substantive update'}]}
    def importance(group, supplied):
        return {'level': 'A', 'uncertainty': 'low', 'evidence_ids': ['b'],
                'fact_ids': [fact['id'] for fact in supplied]}
    def verify(c): return 'failed' if c['candidate_id'] == 'a' else 'full_text'
    result = run([original, update], NOW.isoformat(), FakeEncoder(), .9, importance, verify, reviewer=lambda *_: 'substantive_update', facts_by_event=facts)
    assert result['selected'] == ['b']
    assert result['quality'] == {'a': 'failed', 'b': 'full_text'}
    assert result['editorial_diagnostics'][0]['unverified_event_fact_ids'] == ['update-fact']
    assert result['editorial_diagnostics'][0]['representative_verified_fact_ids'] == []
    # Group-level fact IDs are supplied evidence, not proof that the selected text contains them.


def test_auxiliary_conflict_does_not_prevent_manual_review():
    a = candidate('a', 'One organization attack', '2026-11-20T00:00:00Z')
    b = candidate('b', 'Different organization direct response', '2026-11-24T00:00:00Z')
    seen = []
    def reviewer(a, b, evidence):
        seen.append(evidence)
        return 'substantive_update'
    assert compare(a, b, [1., 0.], [1., 0.], NOW, .9, reviewer)['relation'] == 'substantive_update'
    assert seen[0]['time'] == 'conflict'
    assert compare(a, b, [1., 0.], [1., 0.], NOW, .9)['relation'] == 'unknown'
    assert compare(a, b, [1., 0.], [1., 0.], NOW, .9, lambda *_: 'related_different')['relation'] == 'related_different'


def test_event_facts_are_not_attributed_to_representative_without_verification():
    rows = [candidate('a', 'Original report', excerpt='long original'), candidate('b', 'Direct update')]
    facts = {'a': [{'id': 'update', 'description': 'new information'}]}
    def importance(group, facts):
        return {'level': 'A', 'uncertainty': 'low', 'evidence_ids': ['b'], 'fact_ids': ['update']}
    result = run(rows, NOW.isoformat(), FakeEncoder(), .9, importance, lambda _: 'full_text',
                 reviewer=lambda *_: 'substantive_update', facts_by_event=facts)
    item = result['editorial_diagnostics'][0]
    assert item['candidate_id'] == 'a' and item['event_fact_ids'] == ['update']
    assert item['representative_verified_fact_ids'] == [] and item['unverified_event_fact_ids'] == ['update']
    result = run(rows, NOW.isoformat(), FakeEncoder(), .9, importance,
                 lambda _: {'status': 'full_text', 'verified_fact_ids': ['invented']},
                 reviewer=lambda *_: 'substantive_update', facts_by_event=facts)
    assert not result['selected'] and result['review_queue']


def test_uncertain_major_event_is_visible_for_manual_review():
    rows = [candidate('major', 'Major event, incomplete evidence')]
    def importance(group, facts):
        return {'level': 'A', 'uncertainty': 'high', 'evidence_ids': ['major'], 'fact_ids': []}
    result = run(rows, NOW.isoformat(), FakeEncoder(), .9, importance, lambda _: 'full_text')
    assert result['status'] == 'partial' and result['selected'] == []
    assert result['review_queue'] == [{'event_id': 'major', 'candidate_ids': ['major'], 'reason': 'importance_uncertain', 'level': 'A'}]


def test_read_only_trial_uses_only_explicit_manual_review():
    from scripts.editorial_v2.semantic.trial import trial
    data = {'content_origin': 'self_authored', 'captured_at': NOW.isoformat(),
            'candidates': [{'candidate_id': x, 'title': x} for x in 'abc'],
            'manual_reviews': [{'a': 'a', 'b': 'b', 'relation': 'same_event'}]}
    assert trial(data, FakeEncoder())['groups'] == [['a', 'b'], ['c']]
    data['manual_reviews'].append({'a': 'a', 'b': 'missing', 'relation': 'same_event'})
    with pytest.raises(ValueError): trial(data, FakeEncoder())
    data['content_origin'] = 'media'
    with pytest.raises(ValueError): trial(data, FakeEncoder())
