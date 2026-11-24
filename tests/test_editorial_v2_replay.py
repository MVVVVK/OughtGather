import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.editorial_v2.replay import evaluate, replay, validate, validate_result

FIXTURE = Path(__file__).parent / 'fixtures/editorial_v2/synthetic_snapshot.json'


def snapshot():
    return json.loads(FIXTURE.read_text())


def test_evaluation_distinguishes_coverage_duplicates_quality_and_unknown():
    s = validate(snapshot())
    b0 = evaluate(s, s['recorded_results']['B0_fixture'])
    assert b0['important_event_coverage'] == pytest.approx(1 / 3)
    assert b0['duplicate_rate'] == pytest.approx(1 / 3)
    assert b0['important_singleton_omission'] == 1
    assert b0['cluster_f1'] is None
    assert b0['selected_quality_counts']['summary'] == 1
    b1 = evaluate(s, s['recorded_results']['B1_fixture'])
    assert b1['important_event_coverage'] == pytest.approx(2 / 3)
    assert b1['important_singleton_omission'] == pytest.approx(1 / 2)
    assert b1['cluster_precision'] == b1['cluster_recall'] == b1['cluster_f1'] == 1
    assert b1['selected_quality_counts']['unknown'] == 1
    assert b1['quality_full_or_rss_rate'] is None and b1['quality_complete'] is False
    assert b1['gold_labeled_candidates'] == 5 and b1['candidate_count'] == 6


def test_false_merge_and_split_are_measured_and_undefined_is_null():
    s = snapshot()
    result = {'status': 'ok', 'observed_ids': list('abcdef'), 'selected': [], 'clusters': [['a', 'c'], ['b'], ['d'], ['e'], ['f']]}
    metrics = evaluate(s, result)
    assert metrics['cluster_precision'] == metrics['cluster_recall'] == metrics['cluster_f1'] == 0
    assert metrics['duplicate_rate'] is None and metrics['quality_full_or_rss_rate'] is None
    result['clusters'] = [[c['candidate_id']] for c in s['candidates']]
    assert evaluate(s, result)['cluster_precision'] is None
    assert evaluate(s, result)['cluster_f1'] == 0


def test_engines_receive_no_gold_and_inputs_are_detached_and_orderable():
    s = snapshot()
    seen = []
    def engine(items):
        seen.append([c['candidate_id'] for c in items])
        assert all('event_id' not in c and 'important' not in c and 'quality_status' not in c for c in items)
        items[0]['title'] = 'changed'
        return {'status': 'ok', 'observed_ids': [c['candidate_id'] for c in items], 'selected': [items[0]['candidate_id']], 'clusters': None}
    replay(s, {'x': engine}, 'newest')
    assert seen[0] == ['f', 'e', 'c', 'b', 'a', 'd']
    assert s['candidates'][0]['title'] != 'changed'
    first = replay(s, {'x': engine}, 'id')
    assert first == replay(s, {'x': engine}, 'id')


def test_invalid_selection_clusters_and_gold_cannot_be_silently_accepted():
    s = snapshot(); ids = {c['candidate_id'] for c in s['candidates']}
    for result in ({'selected': ['missing'], 'clusters': None},
                   {'selected': ['a', 'a'], 'clusters': None},
                   {'selected': [], 'clusters': [['a', 'b'], ['b']]}):
        with pytest.raises(ValueError): validate_result(result, ids)
    s['gold']['b']['important'] = False
    with pytest.raises(ValueError): validate(s)


def test_non_public_and_unpermitted_excerpt_are_rejected_without_leak():
    s = snapshot(); s['data_class'] = 'private'
    with pytest.raises(ValueError): validate(s)
    s = snapshot(); s['candidates'][0]['excerpt_permitted'] = False
    with pytest.raises(ValueError): validate(s)
    s = snapshot(); s['candidates'][0]['event_id'] = 'gold'
    with pytest.raises(ValueError): validate(s)


def test_cli_only_emits_aggregate_and_rejects_invalid_without_content(tmp_path):
    proc = subprocess.run([sys.executable, '-m', 'scripts.editorial_v2.replay', str(FIXTURE)], capture_output=True, text=True, check=True)
    assert 'Synthetic English A' not in proc.stdout
    assert 'candidate_id' not in proc.stdout
    bad = snapshot(); bad['data_class'] = 'private'; bad['candidates'][0]['title'] = 'SENSITIVE_SENTINEL'
    path = tmp_path / 'private.json'; path.write_text(json.dumps(bad))
    proc = subprocess.run([sys.executable, '-m', 'scripts.editorial_v2.replay', str(path)], capture_output=True, text=True)
    assert proc.returncode == 2 and 'SENSITIVE_SENTINEL' not in proc.stdout + proc.stderr


def test_pairwise_f1_false_negatives_and_explicit_counts():
    s = snapshot()
    result = {'status': 'ok', 'observed_ids': list('abcdef'), 'selected': [],
              'clusters': [[c] for c in 'abcdef']}
    m = evaluate(s, result)
    assert m['cluster_precision'] is None and m['cluster_recall'] == 0 and m['cluster_f1'] == 0
    assert m['cluster_pair_counts'] == {'tp': 0, 'fp': 0, 'fn': 1, 'precision_denominator': 0,
                                        'recall_denominator': 1, 'f1_numerator': 0, 'f1_denominator': 1,
                                        'gold_pairs_total': 1, 'gold_pairs_observed': 1}


def test_novelty_requires_ordered_pair_labels_and_updates_are_not_duplicates():
    s = snapshot(); result = s['recorded_results']['B0_fixture']
    assert evaluate(s, result)['duplicate_rate'] == pytest.approx(1/3)
    assert evaluate(s, result)['no_increment_duplicate_rate'] is None
    s['incremental_labels'] = {'b': {'a': 'substantive_update'}}
    assert evaluate(s, result)['no_increment_duplicate_rate'] == 0
    s['incremental_labels']['b']['a'] = 'no_new_information'
    m = evaluate(s, result)
    assert m['no_increment_duplicate_counts'] == {'numerator': 1, 'denominator': 3}
    result['selected'] = ['b', 'a']
    assert evaluate(s, result)['no_increment_duplicate_rate'] is None


def test_partial_cap_and_failed_run_never_fabricate_singletons():
    s = snapshot()
    partial = {'status': 'partial', 'observed_ids': ['a', 'b'], 'selected': ['a'], 'clusters': [['a', 'b']]}
    validate_result(partial, set('abcdef'))
    m = evaluate(s, partial)
    assert m['observed_count'] == 2 and m['unobserved_count'] == 4
    assert m['cluster_pair_counts']['tp'] == 1 and m['cluster_labeled_observed_count'] == 2
    broken = dict(partial, clusters=[['a'], ['b'], ['c']])
    with pytest.raises(ValueError): validate_result(broken, set('abcdef'))
    failed = {'status': 'failed', 'observed_ids': ['a'], 'selected': [], 'clusters': None}
    m = evaluate(s, failed)
    assert m['important_event_coverage'] is None and m['duplicate_rate'] is None and m['cluster_f1'] is None
    assert m['observed_count'] == 1 and m['unobserved_count'] == 5
    with pytest.raises(ValueError): validate_result(dict(failed, selected=['a']), set('abcdef'))


def test_synthetic_capture_does_not_infer_quality_or_turn_media_hint_into_rejection():
    from scripts.editorial_v2.capture import capture_synthetic
    entry = {'candidate_id': 'x', 'source': 'Synthetic', 'title': 'Example', 'language': 'en',
             'published_at': 'bad-date', 'rss_excerpt': 'safe', 'media_hint': True}
    b0 = {'status': 'ok', 'observed_ids': ['x'], 'selected': ['x'], 'clusters': None, 'quality': {'x': 'summary'}}
    b1 = {'status': 'partial', 'observed_ids': [], 'selected': [], 'clusters': []}
    s = capture_synthetic(collector_items=[entry], captured_at='2026-11-24T06:00:00Z', title_permitted=True,
                          excerpt_permitted=False, traditional_result=b0, shadow_result=b1)
    assert s['candidates'][0]['excerpt'] is None and s['candidates'][0]['published_at'] is None
    assert s['candidates'][0]['media_type'] == 'unknown' and s['candidates'][0]['media_evidence'] == ['collector_media_hint']
    assert 'quality_status' not in s['candidates'][0] and s['recorded_results']['B0']['quality']['x'] == 'summary'
