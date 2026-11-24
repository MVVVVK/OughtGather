"""Isolated offline replay; evaluation labels never enter algorithm inputs."""
import argparse
import copy
import json
import math
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

QUALITY = {'unknown', 'unattempted', 'quota_limited', 'failed', 'rejected', 'summary', 'rss_content', 'full_text'}
MEDIA = {'text', 'media', 'mixed', 'unknown'}
DATA_CLASSES = {'synthetic', 'approved_public'}
NOVELTY = {'unknown', 'no_new_information', 'substantive_update', 'new_information'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(snapshot, allow_private=False):
    _require(isinstance(snapshot, dict) and snapshot.get('schema_version') == 1, 'invalid schema version')
    _require(snapshot.get('data_class') in DATA_CLASSES or (allow_private and snapshot.get('data_class') == 'private'), 'data not permitted')
    try:
        cutoff = datetime.fromisoformat(snapshot['captured_at'].replace('Z', '+00:00'))
        _require(cutoff.tzinfo is not None and cutoff.utcoffset() is not None, 'capture cutoff requires timezone')
    except (ValueError, TypeError, AttributeError, KeyError):
        raise ValueError('invalid capture cutoff') from None
    candidates = snapshot.get('candidates')
    _require(isinstance(candidates, list), 'candidates must be a list')
    ids = set()
    fields = {'candidate_id', 'source', 'language', 'published_at', 'title', 'excerpt', 'excerpt_permitted', 'media_type', 'media_evidence'}
    for c in candidates:
        _require(isinstance(c, dict) and fields <= set(c) <= fields | {'url', 'collected_at'}, 'invalid candidate fields')
        cid = c.get('candidate_id')
        _require(isinstance(cid, str) and cid and cid not in ids, 'missing or duplicate candidate id')
        ids.add(cid)
        for field in ('source', 'language', 'title'):
            _require(isinstance(c.get(field), str) and bool(c[field]), 'missing candidate field')
        _require(c.get('url') is None or isinstance(c['url'], str), 'invalid url')
        _require(c.get('media_type') in MEDIA and isinstance(c.get('media_evidence'), list)
                 and all(isinstance(x, str) for x in c['media_evidence']), 'invalid media evidence')
        _require(c.get('excerpt') is None or isinstance(c['excerpt'], str), 'invalid excerpt')
        _require(type(c.get('excerpt_permitted')) is bool, 'excerpt permission required')
        _require(c['excerpt_permitted'] or c.get('excerpt') is None, 'unpermitted excerpt')
        for date in (c.get('published_at'), c.get('collected_at')):
            if date is not None:
                try:
                    parsed = datetime.fromisoformat(date.replace('Z', '+00:00'))
                    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None, 'date requires timezone')
                except (ValueError, TypeError, AttributeError):
                    raise ValueError('invalid date') from None
        if c.get('collected_at'):
            _require(datetime.fromisoformat(c['collected_at'].replace('Z', '+00:00')) <= cutoff, 'candidate after cutoff')
    gold = snapshot.get('gold')
    _require(isinstance(gold, dict) and set(gold) <= ids, 'invalid gold labels')
    importance = {}
    for label in gold.values():
        _require(isinstance(label, dict) and isinstance(label.get('event_id'), str)
                 and bool(label['event_id']) and (type(label.get('important')) is bool or label.get('important') is None), 'invalid event label')
        event, important = label['event_id'], label['important']
        if important is not None:
            _require(event not in importance or importance[event] == important, 'inconsistent event importance')
            importance[event] = important
    novelty = snapshot.get('incremental_labels', {})
    _require(isinstance(novelty, dict) and set(novelty) <= ids, 'invalid incremental labels')
    for cid, comparisons in novelty.items():
        _require(isinstance(comparisons, dict) and set(comparisons) <= ids - {cid}
                 and all(value in NOVELTY for value in comparisons.values()), 'invalid incremental labels')
    results = snapshot.get('recorded_results')
    _require(isinstance(results, dict), 'recorded results required')
    for result in results.values():
        validate_result(result, ids)
    return snapshot


def validate_result(result, ids):
    _require(isinstance(result, dict) and result.get('status') in {'ok', 'partial', 'failed', 'not_run'}, 'invalid result status')
    observed = result.get('observed_ids')
    _require(isinstance(observed, list) and all(isinstance(x, str) for x in observed)
             and len(set(observed)) == len(observed) and set(observed) <= ids, 'invalid coverage')
    observed = set(observed)
    if result['status'] == 'ok':
        _require(observed == ids, 'complete result must observe all candidates')
    selected = result.get('selected')
    _require(isinstance(selected, list) and all(isinstance(x, str) for x in selected)
             and len(set(selected)) == len(selected) and set(selected) <= observed, 'invalid selection')
    clusters = result.get('clusters')
    if clusters is not None:
        _require(isinstance(clusters, list) and all(isinstance(g, list) and g for g in clusters), 'invalid clusters')
        flat = [x for group in clusters for x in group]
        _require(all(isinstance(x, str) for x in flat) and len(flat) == len(set(flat))
                 and set(flat) == observed, 'clusters must partition observed candidates')
    if result['status'] in ('failed', 'not_run'):
        _require(not selected and clusters is None, 'failed result cannot claim selection or clusters')
    quality = result.get('quality', {})
    _require(isinstance(quality, dict) and set(quality) <= observed and all(v in QUALITY for v in quality.values()), 'invalid result quality')
    for key in ('elapsed_seconds', 'cost', 'input_tokens', 'output_tokens'):
        value = result.get(key)
        _require(value is None or (type(value) in (int, float) and math.isfinite(value) and value >= 0), 'invalid usage')


def ratio(a, b):
    return a / b if b else None


def evaluate(snapshot, result):
    ids = {c['candidate_id'] for c in snapshot['candidates']}
    validate_result(result, ids)
    gold = snapshot['gold']
    observed = set(result['observed_ids'])
    selected = result['selected']
    success = result['status'] not in ('failed', 'not_run')
    selected_events = [gold[x]['event_id'] for x in selected if x in gold]
    important = {v['event_id'] for v in gold.values() if v['important'] is True}
    covered = important & set(selected_events)
    sizes = {}
    for v in gold.values():
        sizes[v['event_id']] = sizes.get(v['event_id'], 0) + 1
    singletons = {event for event in important if sizes[event] == 1}
    tp = fp = fn = precision = recall = f1 = None
    gold_pairs_total = sum(gold[x]['event_id'] == gold[y]['event_id'] for x, y in combinations(sorted(gold), 2))
    if result.get('clusters') is not None:
        eligible = observed & set(gold)
        predicted = {frozenset(pair) for group in result['clusters'] for pair in combinations([x for x in group if x in eligible], 2)}
        actual = {frozenset(pair) for pair in combinations(sorted(eligible), 2) if gold[pair[0]]['event_id'] == gold[pair[1]]['event_id']}
        tp, fp, fn = len(predicted & actual), len(predicted - actual), len(actual - predicted)
        precision, recall, f1 = ratio(tp, tp + fp), ratio(tp, tp + fn), ratio(2 * tp, 2 * tp + fp + fn)
    quality = result.get('quality', {})
    counts = {status: sum(quality.get(x, 'unknown') == status for x in selected) for status in sorted(QUALITY)}
    quality_complete = not any(counts[state] for state in ('unknown', 'unattempted', 'quota_limited'))
    # Novelty is relative to preceding selected articles, not an intrinsic duplicate label.
    seen = {}; redundant = 0; novelty_known = bool(snapshot.get('incremental_labels'))
    for cid in selected:
        label = gold.get(cid)
        if not label:
            novelty_known = False; continue
        event = label['event_id']
        if event in seen:
            labels = [snapshot.get('incremental_labels', {}).get(cid, {}).get(previous, 'unknown') for previous in seen[event]]
            if 'unknown' in labels: novelty_known = False
            elif all(value == 'no_new_information' for value in labels): redundant += 1
        seen.setdefault(event, []).append(cid)
    duplicate_n = len(selected_events) - len(set(selected_events))
    return {'status': result['status'], 'observed_count': len(observed), 'unobserved_count': len(ids - observed),
            'important_event_coverage': ratio(len(covered), len(important)) if success else None,
            'important_event_coverage_counts': {'numerator': len(covered) if success else None, 'denominator': len(important)},
            'important_singleton_omission': ratio(len(singletons - covered), len(singletons)) if success else None,
            'important_singleton_omission_counts': {'numerator': len(singletons - covered) if success else None, 'denominator': len(singletons)},
            'cluster_precision': precision, 'cluster_recall': recall, 'cluster_f1': f1,
            'cluster_pair_counts': {'tp': tp, 'fp': fp, 'fn': fn, 'precision_denominator': None if tp is None else tp + fp,
                                    'recall_denominator': None if tp is None else tp + fn,
                                    'f1_numerator': None if tp is None else 2 * tp,
                                    'f1_denominator': None if tp is None else 2 * tp + fp + fn,
                                    'gold_pairs_total': gold_pairs_total,
                                    'gold_pairs_observed': None if tp is None else tp + fn},
            'duplicate_rate': ratio(duplicate_n, len(selected_events)) if success else None,
            'event_concentration_counts': {'numerator': duplicate_n if success else None, 'denominator': len(selected_events) if success else None},
            'no_increment_duplicate_rate': ratio(redundant, len(selected)) if success and novelty_known else None,
            'no_increment_duplicate_counts': {'numerator': redundant if success and novelty_known else None, 'denominator': len(selected) if success else None},
            'selected_quality_counts': counts if success else None,
            'quality_full_or_rss_rate': ratio(counts['full_text'] + counts['rss_content'], len(selected)) if success and quality_complete else None,
            'quality_full_or_rss_counts': {'numerator': counts['full_text'] + counts['rss_content'] if success else None, 'denominator': len(selected) if success else None},
            'quality_complete': quality_complete if success else None,
            'selected_count': len(selected) if success else None, 'selected_labeled_count': len(selected_events) if success else None,
            'gold_labeled_candidates': len(gold), 'cluster_labeled_observed_count': len(observed & set(gold)), 'candidate_count': len(ids),
            'elapsed_seconds': result.get('elapsed_seconds'), 'cost': result.get('cost'),
            'input_tokens': result.get('input_tokens'), 'output_tokens': result.get('output_tokens')}


def replay(snapshot, engines, ordering='original', allow_private=False):
    """Engines get detached pre-selection candidates only; no gold or quality."""
    validate(snapshot, allow_private=allow_private)
    _require(ordering in ('original', 'id', 'newest'), 'invalid ordering')
    candidates = copy.deepcopy(snapshot['candidates'])
    if ordering == 'id': candidates.sort(key=lambda c: c['candidate_id'])
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
    parser.add_argument('--b2', action='store_true', help='also run the synthetic offline deterministic baseline')
    parser.add_argument('--encrypted-private', action='store_true', help='decrypt private snapshot in memory using EDITORIAL_BENCHMARK_KEY')
    args = parser.parse_args()
    try:
        if args.encrypted_private:
            from src.editorial_benchmark.crypto import decrypt, key_from_env
            data = validate(decrypt(Path(args.snapshot).read_bytes(), key_from_env()), allow_private=True)
            _require(data['data_class'] == 'private', 'private snapshot required')
        else:
            data = validate(json.loads(Path(args.snapshot).read_text(encoding='utf-8')))
        engines = {name: (lambda _c, result=result: copy.deepcopy(result)) for name, result in data['recorded_results'].items()}
        if args.b2:
            from .b2 import select
            engines['B2'] = lambda candidates: select(candidates, data['captured_at'], capacity=16)
        print(json.dumps(replay(data, engines, args.ordering, allow_private=args.encrypted_private), sort_keys=True, ensure_ascii=False))
    except (ValueError, KeyError, OSError, TypeError):
        parser.exit(2, 'Invalid or inaccessible snapshot. No content was emitted.\n')


if __name__ == '__main__': main()
