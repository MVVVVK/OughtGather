"""Synthetic plumbing tests only. No fake vectors are research results."""
import json
from collections import Counter

from scripts.editorial_v2.semantic.synthetic_validation import load_pairs, pair_evaluation, performance_300


class FakeEncoder:
    def __init__(self, flip=None): self.flip = flip or set()
    def encode(self, texts): return [[-1.0, 0.0] if text in self.flip else [1.0, 0.0] for text in texts]


def test_frozen_100_pair_fixture_has_disjoint_events_and_both_classes():
    pairs = load_pairs()
    assert len(pairs) == 100
    assert Counter(p['split'] for p in pairs) == {'dev': 50, 'acceptance': 50}
    assert Counter(p['label'] for p in pairs) == {'same_event': 40, 'substantive_update': 20, 'different': 40}
    sets = {split: {event for p in pairs if p['split'] == split for event in (p['event_a'], p['event_b'])}
            for split in ('dev', 'acceptance')}
    assert not sets['dev'] & sets['acceptance']
    assert any(p['a']['published_at'] is None and p['label'] != 'different' for p in pairs)
    assert any(p['a']['published_at'] is None and p['label'] == 'different' for p in pairs)


def test_threshold_uses_only_dev_and_no_fictional_title_is_emitted():
    pairs = load_pairs()
    acceptance_a = {p['a']['title'] for p in pairs if p['split'] == 'acceptance'}
    first = pair_evaluation(FakeEncoder())
    second = pair_evaluation(FakeEncoder(acceptance_a))
    assert first['threshold_from_dev_only'] == second['threshold_from_dev_only']
    assert first['dev_pairs'] == first['acceptance_pairs'] == 50
    assert {'B2', 'B3_heuristic', 'E5_raw', 'E5_hybrid'} == set(first['methods'])
    assert not any(p['a']['title'] in json.dumps(first) for p in pairs)


def test_300_candidate_plumbing_observes_capacity_and_validation_budget():
    result = performance_300(FakeEncoder(), threshold=.95)
    assert result['candidates'] == result['observed'] == 300
    assert result['selected_count'] <= 16 and result['verification_calls'] <= 40
    assert result['peak_rss_kib'] > 0
    assert 'not a real AI' in result['importance_and_verifier']
