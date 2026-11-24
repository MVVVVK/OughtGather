"""Synthetic-only capture adapter. No IO, network, production state or storage."""
import copy
from datetime import datetime
from .replay import validate


def capture_synthetic(*, collector_items, captured_at, title_permitted, excerpt_permitted,
                      traditional_result, shadow_result, gold=None, incremental_labels=None):
    if title_permitted is not True or type(excerpt_permitted) is not bool:
        raise ValueError('explicit content permission required')
    timestamp = datetime.fromisoformat(captured_at.replace('Z', '+00:00'))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError('capture cutoff requires timezone')
    candidates = []
    for item in collector_items:
        published = item.get('published_at') or None
        if published:
            try:
                parsed = datetime.fromisoformat(published.replace('Z', '+00:00'))
                if parsed.tzinfo is None or parsed.utcoffset() is None: published = None
            except (ValueError, TypeError, AttributeError): published = None
        # A media hint is evidence, not a definitive media classification.
        media_evidence = ['collector_media_hint'] if item.get('media_hint') is True else []
        candidates.append({'candidate_id': item['candidate_id'], 'source': item['source'],
                           'language': item.get('language') or 'unknown', 'published_at': published,
                           'title': item['title'], 'excerpt': item.get('rss_excerpt') if excerpt_permitted else None,
                           'excerpt_permitted': excerpt_permitted, 'media_type': 'unknown', 'media_evidence': media_evidence})
    snapshot = {'schema_version': 1, 'data_class': 'synthetic', 'captured_at': captured_at,
                'candidates': candidates, 'gold': copy.deepcopy(gold or {}),
                'incremental_labels': copy.deepcopy(incremental_labels or {}),
                'recorded_results': {'B0': copy.deepcopy(traditional_result), 'B1': copy.deepcopy(shadow_result)}}
    return validate(snapshot)
