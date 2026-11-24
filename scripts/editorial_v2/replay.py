"""Isolated, offline replay of explicitly distributable editorial snapshots.

No production adapters, network access or provider clients are used here.
"""
import argparse
import copy
import json
import math
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

QUALITY = {'unknown', 'unattempted', 'quota_limited', 'failed', 'rejected',
           'summary', 'rss_content', 'full_text'}
MEDIA = {'text', 'media', 'mixed', 'unknown'}
DATA_CLASSES = {'synthetic', 'approved_public'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(snapshot):
    _require(isinstance(snapshot, dict) and snapshot.get('schema_version') == 1, 'invalid schema version')
    _require(snapshot.get('data_class') in DATA_CLASSES, 'only synthetic or approved public data allowed')
    candidates = snapshot.get('candidates')
    _require(isinstance(candidates, list), 'candidates must be a list')
    ids = set()
    for c in candidates:
        _require(isinstance(c, dict), 'invalid candidate')
        _require(set(c) == {'candidate_id', 'source', 'language', 'published_at', 'title', 'excerpt',
                            'excerpt_permitted', 'media_type', 'media_evidence', 'quality_status'},
                 'invalid candidate fields')
        cid = c.get('candidate_id')
        _require(isinstance(cid, str) and cid and cid not in ids, 'missing or duplicate candidate id')
        ids.add(cid)
        for field in ('source', 'language', 'title'):
            _require(isinstance(c.get(field), str) and bool(c[field]), 'missing candidate field')
        _require(c.get('quality_status') in QUALITY, 'invalid quality status')
        _require(c.get('media_type') in MEDIA and isinstance(c.get('media_evidence'), list)
                 and all(isinstance(x, str) for x in c['media_evidence']), 'invalid media evidence')
        _require(c.get('excerpt') is None or isinstance(c['excerpt'], str), 'invalid excerpt')
        _require(type(c.get('excerpt_permitted')) is bool, 'excerpt permission required')
        _require(c['excerpt_permitted'] or c.get('excerpt') is None, 'unpermitted excerpt')
        date = c.get('published_at')
        if date is not None:
            try:
                parsed = datetime.fromisoformat(date.replace('Z', '+00:00'))
                _require(parsed.tzinfo is not None and parsed.utcoffset() is not None, 'date requires timezone')
            except (ValueError, TypeError, AttributeError):
                raise ValueError('invalid published_at') from None
    gold = snapshot.get('gold')
    _require(isinstance(gold, dict) and set(gold) <= ids, 'invalid gold labels')
    for label in gold.values():
        _require(isinstance(label, dict) and isinstance(label.get('event_id'), str)
                 and bool(label['event_id']) and type(label.get('important')) is bool, 'invalid event label')
    importance = {}
    for label in gold.values():
        event, important = label['event_id'], label['important']
        _require(event not in importance or importance[event] == important, 'inconsistent event importance')
        importance[event] = important
    results = snapshot.get('recorded_results')
    _require(isinstance(results, dict), 'recorded results required')
    for result in results.values():
        validate_result(result, ids)
    return snapshot


def validate_result(result, ids):
    _require(isinstance(result, dict), 'invalid result')
    selected = result.get('selected')
    _require(isinstance(selected, list) and all(isinstance(x, str) for x in selected)
             and len(set(selected)) == len(selected) and set(selected) <= ids, 'invalid selection')
    clusters = result.get('clusters')
    if clusters is not None:
        _require(isinstance(clusters, list) and all(isinstance(g, list) and g for g in clusters), 'invalid clusters')
        flat = [x for group in clusters for x in group]
        _require(all(isinstance(x, str) for x in flat) and len(flat) == len(set(flat))
                 and set(flat) == ids, 'clusters must partition candidates')
    for key in ('elapsed_seconds', 'cost', 'input_tokens', 'output_tokens'):
        value = result.get(key)
        _require(value is None or (type(value) in (int, float) and math.isfinite(value) and value >= 0), 'invalid usage')


def ratio(a, b):
    return a / b if b else None


def evaluate(snapshot, result):
    candidates = {c['candidate_id']: c for c in snapshot['candidates']}
    gold = snapshot['gold']
    selected = result['selected']
    selected_events = [gold[x]['event_id'] for x in selected if x in gold]
    important = {v['event_id'] for v in gold.values() if v['important']}
    covered = important & set(selected_events)
    sizes = {}
    for v in gold.values():
        sizes[v['event_id']] = sizes.get(v['event_id'], 0) + 1
    singletons = {event for event in important if sizes[event] == 1}
    precision = recall = f1 = None
    if result.get('clusters') is not None:
        predicted = {frozenset(pair) for group in result['clusters'] for pair in combinations([x for x in group if x in gold], 2)}
        actual = {frozenset(pair) for pair in combinations(gold, 2) if gold[pair[0]]['event_id'] == gold[pair[1]]['event_id']}
        tp = len(predicted & actual)
        precision, recall = ratio(tp, len(predicted)), ratio(tp, len(actual))
        f1 = (2 * precision * recall / (precision + recall)
              if precision is not None and recall is not None and precision + recall else
              0.0 if precision is not None and recall is not None else None)
    counts = {status: sum(candidates[x]['quality_status'] == status for x in selected) for status in sorted(QUALITY)}
    return {'important_event_coverage': ratio(len(covered), len(important)),
            'important_singleton_omission': ratio(len(singletons - covered), len(singletons)),
            'cluster_precision': precision, 'cluster_recall': recall, 'cluster_f1': f1,
            'duplicate_rate': ratio(len(selected_events) - len(set(selected_events)), len(selected_events)),
            'selected_quality_counts': counts,
            'quality_full_or_rss_rate': ratio(counts['full_text'] + counts['rss_content'], len(selected)),
            'selected_count': len(selected), 'selected_labeled_count': len(selected_events),
            'gold_labeled_candidates': len(gold), 'candidate_count': len(candidates),
            'elapsed_seconds': result.get('elapsed_seconds'), 'cost': result.get('cost'),
            'input_tokens': result.get('input_tokens'), 'output_tokens': result.get('output_tokens')}


def replay(snapshot, engines, ordering='original'):
    """Engines get detached candidates only; gold labels never reach engines."""
    validate(snapshot)
    _require(ordering in ('original', 'id', 'newest'), 'invalid ordering')
    candidates = copy.deepcopy(snapshot['candidates'])
    if ordering == 'id':
        candidates.sort(key=lambda c: c['candidate_id'])
    elif ordering == 'newest':
        def order(c):
            date = c['published_at']
            return (date is None, -datetime.fromisoformat(date.replace('Z', '+00:00')).astimezone(timezone.utc).timestamp() if date else 0, c['candidate_id'])
        candidates.sort(key=order)
    ids = {c['candidate_id'] for c in candidates}
    output = {}
    for name, engine in engines.items():
        result = engine(copy.deepcopy(candidates))
        validate_result(result, ids)
        output[name] = evaluate(snapshot, result)
    return output


def main():
    parser = argparse.ArgumentParser(description='Offline replay of distributable synthetic or approved-public data')
    parser.add_argument('snapshot')
    parser.add_argument('--ordering', choices=('original', 'id', 'newest'), default='original')
    args = parser.parse_args()
    try:
        data = validate(json.loads(Path(args.snapshot).read_text(encoding='utf-8')))
        engines = {name: (lambda _c, result=result: copy.deepcopy(result))
                   for name, result in data['recorded_results'].items()}
        print(json.dumps(replay(data, engines, args.ordering), sort_keys=True, ensure_ascii=False))
    except (ValueError, KeyError, OSError, TypeError):
        parser.exit(2, 'Invalid or inaccessible snapshot. No content was emitted.\n')


if __name__ == '__main__':
    main()
