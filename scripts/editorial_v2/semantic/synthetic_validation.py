"""Frozen fictional English/French pair evaluation and 300-candidate CPU exercise.

The caller must supply the actual E5Encoder; mocks belong only in unit tests.
"""
import hashlib
import json
import resource
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from scripts.editorial_v2 import b2, b3
from . import editor
from .crossnews import metrics
from .relations import compare, cosine

FIXTURE = Path('tests/fixtures/editorial_v2/synthetic_event_pairs_100.json')
FIXTURE_SHA256 = '5505ad7f214ea70155afeb8c8a2a39353e99894985abae911c059c16e65ffd6c'
AS_OF = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
GRID = tuple(x / 100 for x in range(50, 96))


def load_pairs(path=FIXTURE):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256: raise ValueError('fixture hash mismatch')
    doc = json.loads(raw); pairs = doc['pairs']
    if doc['schema_version'] != 1 or len(pairs) != 100: raise ValueError('invalid fixture')
    seen_ids = set(); events = {'dev': set(), 'acceptance': set()}; titles = {'dev': set(), 'acceptance': set()}
    for p in pairs:
        split = p['split']
        if split not in events or p['pair_id'] in seen_ids: raise ValueError('invalid split')
        seen_ids.add(p['pair_id']); events[split].update((p['event_a'], p['event_b']))
        titles[split].update((p['a']['title'], p['b']['title']))
        if p['label'] not in ('same_event', 'substantive_update', 'different'): raise ValueError('invalid label')
        if (p['event_a'] == p['event_b']) != (p['label'] != 'different'): raise ValueError('inconsistent events')
    if events['dev'] & events['acceptance'] or titles['dev'] & titles['acceptance']: raise ValueError('leakage')
    if Counter(p['split'] for p in pairs) != {'dev': 50, 'acceptance': 50}: raise ValueError('invalid split size')
    return pairs


def as_candidate(data):
    return {**data, 'source': 'fictional', 'excerpt': None, 'excerpt_permitted': False,
            'media_type': 'unknown', 'media_evidence': [], 'url': None}


def pair_evaluation(encoder):
    pairs = load_pairs(); start = time.perf_counter()
    vectors = encoder.encode([p[side]['title'] for p in pairs for side in ('a', 'b')])
    duration = time.perf_counter() - start
    if len(vectors) != 2 * len(pairs): raise ValueError('embedding count mismatch')
    samples = []
    for i, pair in enumerate(pairs):
        a, b = as_candidate(pair['a']), as_candidate(pair['b'])
        samples.append({'pair': pair, 'a': a, 'b': b, 'va': vectors[2*i], 'vb': vectors[2*i+1],
                        'label': 'different' if pair['label'] == 'different' else 'similar',
                        'score': cosine(vectors[2*i], vectors[2*i+1])})
    dev = [s for s in samples if s['pair']['split'] == 'dev']
    # Threshold is chosen exclusively from development samples; tie goes to stricter value.
    threshold = max(GRID, key=lambda t: ((metrics(dev, t)['f1'] or 0), t))
    acceptance = [s for s in samples if s['pair']['split'] == 'acceptance']
    methods = {}
    for name in ('B2', 'B3_heuristic', 'E5_raw', 'E5_hybrid'):
        evaluated = []; unknown = 0
        for s in acceptance:
            if name == 'B2': prediction = b2.same_story(s['a'], s['b'], AS_OF)
            elif name == 'B3_heuristic':
                relation = b3.relation(s['a'], s['b'], AS_OF)
                unknown += relation == 'unknown'; prediction = relation in ('same_item', 'same_event')
            elif name == 'E5_raw': prediction = s['score'] >= threshold
            else:
                relation = compare(s['a'], s['b'], s['va'], s['vb'], AS_OF, threshold)['relation']
                unknown += relation == 'unknown'; prediction = relation in ('same_item', 'same_event')
            evaluated.append({'score': s['score'] if name == 'E5_raw' else float(prediction), 'label': s['label'],
                              'category': s['pair']['category'],
                              'missing_time': s['pair']['a']['published_at'] is None or s['pair']['b']['published_at'] is None})
        value = metrics(evaluated, threshold if name == 'E5_raw' else 0.5)
        value['unknown_count'] = None if name in ('B2', 'E5_raw') else unknown
        value['unknown_rate'] = None if value['unknown_count'] is None else unknown / len(evaluated)
        value['missing_time'] = metrics([x for x in evaluated if x['missing_time']], threshold if name == 'E5_raw' else .5)
        value['same_actor_different_event'] = metrics([x for x in evaluated if x['category'] == 'same_actor_different_event'], threshold if name == 'E5_raw' else .5)
        value['related_different_event'] = metrics([x for x in evaluated if x['category'] == 'related_different_event'], threshold if name == 'E5_raw' else .5)
        value['substantive_update'] = metrics([x for x in evaluated if x['category'] == 'substantive_update'], threshold if name == 'E5_raw' else .5)
        methods[name] = value
    return {'fixture_sha256': FIXTURE_SHA256, 'dev_pairs': len(dev), 'acceptance_pairs': len(acceptance),
            'threshold_from_dev_only': threshold, 'grid': '0.50 through 0.95, step 0.01; highest on tie',
            'pair_encoding_seconds': round(duration, 3), 'methods': methods,
            'scope': 'fictional authored titles; event-group split, no paid AI; unknown treated as unmerged'}


def performance_300(encoder, threshold):
    candidates = []; facts = {}
    for i in range(100):
        # Synthetic IDs/titles are never uploaded; only aggregate statistics leave the job.
        common = f'Aster{i:03} Port{i:03}'
        texts = [f'{common} vessel attack', f'Attaque {common} navire update', f'{common} vessel attack update']
        for j, text in enumerate(texts):
            cid = f'{i:03}-{j}'; event_fact = f'fact-{i:03}'
            candidates.append({'candidate_id': cid, 'title': text, 'source': 'fictional', 'language': 'fr' if j == 1 else 'en',
                               'published_at': '2026-11-23T12:00:00Z', 'excerpt': None, 'excerpt_permitted': False,
                               'media_type': 'unknown', 'media_evidence': []})
            facts[cid] = [{'id': event_fact, 'description': 'Fictional event update'}]
    def importance(group, supplied):
        return {'level': 'A', 'uncertainty': 'low', 'evidence_ids': [group[0]['candidate_id']],
                'fact_ids': [fact['id'] for fact in supplied]}
    attempts = []
    def verify(row):
        attempts.append(row['candidate_id'])
        return 'failed' if row['candidate_id'].endswith('-0') else 'full_text'
    started = time.perf_counter()
    result = editor.run(candidates, AS_OF.isoformat(), encoder, threshold, importance, verify, facts_by_event=facts)
    duration = time.perf_counter() - started
    selected = result['selected']; cluster_sizes = [len(group) for group in result['clusters']]
    attempted_failed = {cid for cid, status in result['quality'].items() if status == 'failed'}
    fallback = sum(any(member in attempted_failed for member in group) and any(member in selected for member in group)
                   for group in result['clusters'])
    return {'candidates': len(candidates), 'observed': len(result['observed_ids']),
            'cluster_count': len(cluster_sizes), 'multi_article_clusters': sum(n > 1 for n in cluster_sizes),
            'largest_cluster': max(cluster_sizes, default=0), 'selected_count': len(selected),
            'unique_synthetic_event_count': len({cid.split('-')[0] for cid in selected}),
            'verification_calls': result['verification_calls'], 'verification_budget': result['verification_budget'],
            'verified_fallback_groups': fallback, 'selected_update_representatives': sum(cid.endswith(('-1', '-2')) for cid in selected),
            'distinct_supplied_facts_selected': len({fact for item in result['editorial_diagnostics'] for fact in item['event_fact_ids']}),
            'elapsed_seconds': round(duration, 3), 'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'importance_and_verifier': 'injected synthetic fixtures, not a real AI or external validation',
            'limitation': 'Group-level fact IDs do not prove the selected representative contains each fact.'}


def run(encoder):
    pairs = pair_evaluation(encoder)
    return {'pair_evaluation': pairs, 'candidate_pool': performance_300(encoder, pairs['threshold_from_dev_only'])}
