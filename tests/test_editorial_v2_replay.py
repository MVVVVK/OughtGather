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
    assert b1['quality_full_or_rss_rate'] == 0.5
    assert b1['gold_labeled_candidates'] == 5 and b1['candidate_count'] == 6


def test_false_merge_and_split_are_measured_and_undefined_is_null():
    s = snapshot()
    result = {'selected': [], 'clusters': [['a', 'c'], ['b'], ['d'], ['e'], ['f']]}
    metrics = evaluate(s, result)
    assert metrics['cluster_precision'] == metrics['cluster_recall'] == metrics['cluster_f1'] == 0
    assert metrics['duplicate_rate'] is None and metrics['quality_full_or_rss_rate'] is None
    result['clusters'] = [[c['candidate_id']] for c in s['candidates']]
    assert evaluate(s, result)['cluster_precision'] is None


def test_engines_receive_no_gold_and_inputs_are_detached_and_orderable():
    s = snapshot()
    seen = []
    def engine(items):
        seen.append([c['candidate_id'] for c in items])
        assert all('event_id' not in c and 'important' not in c for c in items)
        items[0]['title'] = 'changed'
        return {'selected': [items[0]['candidate_id']], 'clusters': None}
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
