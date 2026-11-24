"""CrossNews-UA E5 experiment. Explicit local model/data; prints only aggregates.

The corpus has pair-level proxy labels, not event IDs; its held-out subset is
exploratory and cannot be certified as an event-independent acceptance set.
"""
import csv
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from scripts.editorial_v2.crossnews_eval import EXPECTED_SHA256
from scripts.editorial_v2 import b2, b3
from .e5 import E5Encoder, MODEL, REVISION
from .relations import compare, cosine


def split(rows):
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        if parent[x] != x: parent[x] = find(parent[x])
        return parent[x]
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[max(ra, rb)] = min(ra, rb)
    for row in rows:
        if not row.get('source1') or not row.get('source2'): raise ValueError('missing article identity')
        union(row['source1'], row['source2'])
    return ['dev' if int(hashlib.sha256(find(r['source1']).encode()).hexdigest()[:8], 16) % 5 else 'heldout' for r in rows]


def metrics(samples, threshold):
    selected = [s for s in samples if s['label'] in ('similar', 'different')]
    tp = sum(s['score'] >= threshold and s['label'] == 'similar' for s in selected)
    fp = sum(s['score'] >= threshold and s['label'] == 'different' for s in selected)
    fn = sum(s['score'] < threshold and s['label'] == 'similar' for s in selected)
    tn = sum(s['score'] < threshold and s['label'] == 'different' for s in selected)
    denominator = 2*tp + fp + fn
    positives = sum(s['label'] == 'similar' for s in selected)
    # Average precision: precision/recall step integration, grouped for score ties.
    true = false = 0; ap = 0.0
    for score in sorted({s['score'] for s in selected}, reverse=True):
        group = [s for s in selected if s['score'] == score]
        new_true = sum(s['label'] == 'similar' for s in group)
        true += new_true; false += len(group) - new_true
        if positives: ap += (new_true / positives) * (true / (true + false))
    return {'pairs': len(selected), 'positives': positives, 'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
            'precision': tp/(tp+fp) if tp+fp else None, 'recall': tp/(tp+fn) if tp+fn else None,
            'f1': 2*tp/denominator if denominator else None, 'pr_auc_average_precision': ap if positives else None}


def run(path, encoder):
    with open(path, 'rb') as f: digest = hashlib.sha256(f.read()).hexdigest()
    if digest != EXPECTED_SHA256: raise ValueError('dataset version mismatch')
    with open(path, encoding='utf-8-sig', newline='') as f: rows = list(csv.DictReader(f))
    assignments = split(rows)
    examples = []
    for index, (r, subset) in enumerate(zip(rows, assignments)):
        if r['url1_lang'] == r['url2_lang'] or not r['text1'] or not r['text2']: continue
        examples.append({'index': index, 'subset': subset, 'label': r['aggregated_what'],
                         'languages': r['language_of_pair'], 'when': r['aggregated_when'], 'who': r['aggregated_who'],
                         'texts': [r['text1'][:800], r['text2'][:800]], 'langs': [r['url1_lang'], r['url2_lang']]})
    started = time.perf_counter()
    vectors = encoder.encode([text for example in examples for text in example['texts']])
    seconds = time.perf_counter() - started
    if len(vectors) != 2*len(examples): raise ValueError('invalid embedding count')
    cutoff = datetime(2026, 11, 24, tzinfo=timezone.utc)
    for i, example in enumerate(examples):
        example['score'] = cosine(vectors[2*i], vectors[2*i+1])
        pair = [{'candidate_id': str(k), 'title': example['texts'][k], 'excerpt': None,
                 'excerpt_permitted': False, 'language': example['langs'][k], 'published_at': None, 'url': None} for k in range(2)]
        example['B2'] = b2.same_story(*pair, cutoff)
        example['B3_heuristic'] = b3.relation(*pair, cutoff) in ('same_item', 'same_event')
    dev = [x for x in examples if x['subset'] == 'dev']
    held = [x for x in examples if x['subset'] == 'heldout']
    grid = [x/100 for x in range(50, 96)]
    threshold = max(grid, key=lambda t: ((metrics(dev, t)['f1'] or 0), t))
    for i, example in enumerate(examples):
        pair = [{'candidate_id': str(k), 'title': example['texts'][k], 'excerpt': None,
                 'excerpt_permitted': False, 'language': example['langs'][k], 'published_at': None, 'url': None} for k in range(2)]
        example['semantic_relation'] = compare(*pair, vectors[2*i], vectors[2*i+1], cutoff, threshold)['relation']
    out = {'dataset_sha256': digest, 'model': MODEL, 'revision': REVISION, 'encoding': 'query prefix, 512 tokens, mean pooling, L2 normalized',
           'model_seconds': round(seconds, 3), 'embedding_count': len(vectors), 'threshold': threshold,
           'threshold_policy': 'dev F1 over predeclared .50-.95 grid; ties higher',
           'dev': metrics(dev, threshold), 'heldout_exploratory': metrics(held, threshold),
           'split': 'connected source URL components, SHA-256 modulo 5; exact article disjoint; event-level leakage unverified',
           'independent_acceptance_status': 'unavailable_no_event_ids',
           'missing_date_pairs': len(held),
           'related_proxy': {'pairs': sum(x['label'] == 'somewhat_related' for x in held),
                             'matched': sum(x['label'] == 'somewhat_related' and x['score'] >= threshold for x in held)},
           'hard_negative_proxy': metrics([x for x in held if x['label'] == 'different' and x['who'] in ('yes', 'partly')], threshold),
           'language_pairs': {lang: metrics([x for x in held if x['languages'] == lang], threshold) for lang in sorted({x['languages'] for x in held})}}
    semantic_scored = [{**x, 'score': 1.0 if x['semantic_relation'] in ('same_item', 'same_event') else 0.0} for x in held]
    out['semantic_with_auxiliary_evidence'] = metrics(semantic_scored, 0.5)
    out['semantic_with_auxiliary_evidence']['abstentions'] = sum(x['semantic_relation'] == 'unknown' for x in held)
    out['semantic_with_auxiliary_evidence']['abstention_denominator'] = len(held)
    out['semantic_with_auxiliary_evidence']['abstention_rate'] = (out['semantic_with_auxiliary_evidence']['abstentions'] / len(held)) if held else None
    for name in ('B2', 'B3_heuristic'):
        scored = [{**x, 'score': 1.0 if x[name] else 0.0} for x in held]
        out[name] = metrics(scored, 0.5)
    # No article text, source URL or raw model output is emitted.
    out['error_indices'] = [x['index'] for x in held if x['label'] in ('similar', 'different')
                            and (x['score'] >= threshold) != (x['label'] == 'similar')][:10]
    return out


def main():
    if len(sys.argv) != 2: raise SystemExit('Usage: python -m scripts.editorial_v2.semantic.crossnews <pinned-public-csv>')
    try: print(json.dumps(run(sys.argv[1], E5Encoder()), sort_keys=True))
    except (RuntimeError, ValueError, OSError, ImportError): raise SystemExit('Experiment unavailable; model/data not accessible.') from None


if __name__ == '__main__': main()
