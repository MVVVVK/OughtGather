import copy
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import pytest

from scripts.editorial_v2.b3 import relation, select, explicit_date
from scripts.editorial_v2.crossnews_eval import evaluate_file
from scripts.editorial_v2.replay import replay

NOW = '2026-11-24T12:00:00Z'
AS_OF = datetime.fromisoformat(NOW.replace('Z', '+00:00'))


def row(cid, title, language='en', date='2026-11-24T10:00:00Z', excerpt=None, url=None):
    return {'candidate_id': cid, 'title': title, 'source': 'Synthetic', 'language': language,
            'published_at': date, 'excerpt': excerpt, 'excerpt_permitted': excerpt is not None,
            'url': url, 'media_type': 'unknown', 'media_evidence': []}


def test_cross_language_same_event_and_independent_elections():
    a = row('a', 'Neptune Benghazi vessel attack')
    b = row('b', 'Attaque du navire Neptune à Benghazi', 'fr')
    c = row('c', 'South Africa election commission announces vote')
    d = row('d', 'Cape Verde election commission announces vote')
    assert relation(a, b, AS_OF) == 'same_event'
    assert relation(c, d, AS_OF) == 'unknown'
    result = select([d, b, c, a], NOW)
    assert {frozenset(x) for x in result['clusters']} == {frozenset({'a', 'b'}), frozenset({'c'}), frozenset({'d'})}
    assert len({'a', 'b'} & set(result['selected'])) == 1
    assert result == select([a, b, c, d], NOW)


def test_unknown_temporal_data_remains_unmerged_without_fabricated_dates():
    a = row('a', 'Neptune Benghazi vessel attack', date=None)
    b = row('b', 'Attaque Neptune Benghazi', 'fr', date=None)
    assert relation(a, b, AS_OF) == 'unknown'
    a['title'] += ' 2026-11-23'; b['title'] += ' 2026-11-23'
    assert relation(a, b, AS_OF) == 'same_event'
    b['title'] += ' 2026-11-22'
    assert relation(a, b, AS_OF) == 'unknown'
    assert explicit_date('2026-01-01 2026-01-02') is None


def test_complete_link_prevents_weak_bridge_and_preserves_future_unknowns():
    a = row('a', 'Norway Berlin attack', url='https://example.org/same')
    b = row('b', 'Alpha Beta vessel attack', url='https://example.org/same')
    c = row('c', 'Alpha Beta vessel attack today')
    assert relation(a, b, AS_OF) == 'same_item' and relation(b, c, AS_OF) == 'same_event'
    assert relation(a, c, AS_OF) == 'unknown'
    result = select([a, b, c], NOW)
    assert max(map(len, result['clusters'])) == 2
    assert relation(a, row('d', a['title'], date='2026-11-25T12:00:00Z'), AS_OF) == 'unknown'


def test_same_actors_different_targets_abstain():
    hospital = row('a', 'Gaza Israel Hospital attack')
    school = row('b', 'Gaza Israel School attack')
    assert relation(hospital, school, AS_OF) == 'unknown'


def test_importance_marginal_selection_and_representative_preselection_evidence():
    a = row('a', 'Neptune Benghazi vessel attack', excerpt='brief')
    b = row('b', 'Attaque du navire Neptune à Benghazi', 'fr', excerpt='Detailed coverage of public details.')
    c = row('c', 'War escalation threatens multiple countries')
    d = row('d', 'Local fair opens in village', date=None)
    result = select([d, a, c, b], NOW, capacity=2)
    assert 'c' in result['selected'] and 'b' in result['selected'] and 'a' not in result['selected']
    assert result['quality'] == {} and result['input_tokens'] == 0
    assert select([a, b, c, d], NOW, capacity=0)['selected'] == []
    with pytest.raises(ValueError): select([a], NOW, capacity=-1)


def test_unpermitted_excerpt_does_not_affect_ranking_or_event_relation():
    a = row('a', 'Alpha Beta update', excerpt=None)
    b = row('b', 'Gamma Delta update', excerpt=None)
    baseline = select([a, b], NOW, capacity=1)
    a['excerpt'] = 'war war war climate pandemic'; a['excerpt_permitted'] = False
    assert select([a, b], NOW, capacity=1) == baseline


def test_replay_integrates_b3_without_labels_or_post_fetch_quality():
    path = Path('tests/fixtures/editorial_v2/synthetic_snapshot.json')
    snapshot = json.loads(path.read_text())
    r = replay(snapshot, {'B3': lambda candidates: select(candidates, snapshot['captured_at'], 2)})
    assert r['B3']['selected_count'] == 2 and r['B3']['observed_count'] == len(snapshot['candidates'])
    assert r['B3']['quality_full_or_rss_rate'] is None
    cp = subprocess.run([sys.executable, '-m', 'scripts.editorial_v2.replay', str(path), '--b2', '--b3'], capture_output=True, text=True, check=True)
    out = json.loads(cp.stdout)
    assert {'B2', 'B3'} <= set(out) and 'Synthetic English A' not in cp.stdout


def test_public_csv_experiment_emits_only_aggregates(tmp_path):
    path = tmp_path / 'data.csv'
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['aggregated_what','url1_lang','url2_lang','text1','text2'])
        w.writeheader(); w.writerow({'aggregated_what':'similar','url1_lang':'en','url2_lang':'fr',
            'text1':'SECRET SAMPLE Neptune Benghazi attack', 'text2':'Attaque Neptune Benghazi'})
        w.writerow({'aggregated_what':'disagreement','url1_lang':'en','url2_lang':'fr','text1':'x','text2':'y'})
    result = evaluate_file(path)
    assert result['evaluated_cross_language_pairs'] == 1 and result['excluded_pairs'] == 1
    assert result['counts']['B3']['abstained'] == 1 and result['counts']['B3']['f1'] == 0
    assert 'SECRET SAMPLE' not in json.dumps(result)
