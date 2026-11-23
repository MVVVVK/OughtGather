"""Strict validation of untrusted model output."""
DIMENSIONS = ('importance', 'expected_depth', 'novelty', 'personal_fit', 'timeliness', 'perspective_value')
WEIGHTS = (.30, .20, .15, .15, .10, .10)


def validate_batch(data, ids):
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ValueError('invalid output')
    items = data['items']
    if len(items) != len(ids):
        raise ValueError('incomplete output')
    seen = set()
    for item in items:
        if not isinstance(item, dict) or item.get('candidate_id') not in ids or item['candidate_id'] in seen:
            raise ValueError('invalid id')
        seen.add(item['candidate_id'])
        for field in DIMENSIONS:
            if type(item.get(field)) not in (int, float) or not 0 <= item[field] <= 100:
                raise ValueError('invalid score')
        for field, maxlen in (('topic', 100), ('region', 100), ('news_type', 100), ('event_cluster', 100), ('reason', 300)):
            if not isinstance(item.get(field), str) or len(item[field]) > maxlen:
                raise ValueError('invalid field')
        if item.get('confidence') not in ('high', 'medium', 'low') or item.get('cluster_type') not in ('duplicate', 'update', 'different_angle', 'analysis', 'none'):
            raise ValueError('invalid enum')
    return [{k: item[k] for k in ('candidate_id', 'topic', 'region', 'news_type', *DIMENSIONS, 'confidence', 'event_cluster', 'cluster_type', 'reason')} | {'score': round(max(sum(item[k] * w for k, w in zip(DIMENSIONS, WEIGHTS)), item['importance'] * .75), 2)} for item in items]
