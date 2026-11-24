"""Conservative semantic proposals with optional explicitly authorized review."""
import math
from datetime import timedelta
from scripts.editorial_v2.b2 import normalize_url, valid_date
from scripts.editorial_v2.b3 import features


def cosine(a, b):
    if not a or len(a) != len(b) or any(not math.isfinite(x) for x in a + b):
        raise ValueError('invalid embedding')
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(x*x for x in b))
    if na == 0 or nb == 0: raise ValueError('zero embedding')
    return max(-1.0, min(1.0, sum(x*y for x, y in zip(a, b)) / (na*nb)))


def compare(a, b, vector_a, vector_b, as_of, threshold, reviewer=None):
    if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1: raise ValueError('invalid threshold')
    url_a, url_b = normalize_url(a.get('url')), normalize_url(b.get('url'))
    if url_a and url_a == url_b: return {'relation': 'same_item', 'time': 'not_required'}
    da, db = valid_date(a.get('published_at'), as_of), valid_date(b.get('published_at'), as_of)
    time = 'unknown' if da is None or db is None else 'conflict' if abs(da - db) > timedelta(hours=72) else 'compatible'
    score = cosine(vector_a, vector_b)
    if score < threshold: return {'relation': 'unknown', 'time': time, 'score': score}
    fa, fb = features(a), features(b)
    shared = fa['entities'] & fb['entities']
    action = bool(fa['cues'] & fb['cues'])
    conflict = bool(fa['entities'] - shared and fb['entities'] - shared)
    if reviewer is not None:
        # Evidence informs review; it is neither an automatic merge nor a veto.
        evidence = {'time': time, 'semantic_candidate': True,
                    'shared_entity_count': len(shared), 'shared_action': action,
                    'entity_conflict': conflict}
        try: decision = reviewer(a, b, evidence)
        except Exception: return {'relation': 'unknown', 'time': time, 'score': score}
        if decision not in ('same_event', 'substantive_update', 'related_different', 'different', 'unknown'): raise ValueError('invalid review')
        return {'relation': decision, 'time': time, 'score': score}
    return {'relation': 'unknown', 'time': time, 'score': score}


def cluster(candidates, encoder, as_of, threshold, reviewer=None):
    ordered = sorted(candidates, key=lambda c: c['candidate_id'])
    texts = [c['title'] + ('\n' + c['excerpt'] if c.get('excerpt_permitted') and c.get('excerpt') else '') for c in ordered]
    vectors = encoder.encode(texts)
    if len(vectors) != len(ordered): raise ValueError('embedding count mismatch')
    by_id = {item['candidate_id']: vector for item, vector in zip(ordered, vectors)}
    groups = []
    for candidate in ordered:
        group = next((g for g in groups if all(compare(candidate, other, by_id[candidate['candidate_id']], by_id[other['candidate_id']], as_of, threshold, reviewer)['relation'] in ('same_item', 'same_event', 'substantive_update') for other in g)), None)
        if group is None: groups.append([candidate])
        else: group.append(candidate)
    return groups
