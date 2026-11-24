"""Explicit, local-only grouping trial for small sets of self-authored descriptions."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .relations import cluster

RELATIONS = {'same_event', 'substantive_update', 'related_different', 'unknown'}


def trial(data, encoder):
    if not isinstance(data, dict) or data.get('content_origin') != 'self_authored':
        raise ValueError('self-authored descriptions required')
    rows = data.get('candidates')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
        raise ValueError('provide 1 to 20 candidates')
    ids = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) - {'candidate_id', 'title', 'published_at'}
                or not isinstance(row.get('candidate_id'), str) or not row['candidate_id']
                or not isinstance(row.get('title'), str) or not 1 <= len(row['title']) <= 500
                or row['candidate_id'] in ids):
            raise ValueError('invalid candidate')
        ids.add(row['candidate_id'])
    cutoff = datetime.fromisoformat(data['captured_at'].replace('Z', '+00:00'))
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError('timezone required')
    reviews = {}
    for item in data.get('manual_reviews', []):
        if not isinstance(item, dict) or set(item) != {'a', 'b', 'relation'}:
            raise ValueError('invalid review')
        a, b, relation = item['a'], item['b'], item['relation']
        if a not in ids or b not in ids or a == b or relation not in RELATIONS:
            raise ValueError('invalid review')
        key = frozenset((a, b))
        if key in reviews: raise ValueError('duplicate review')
        reviews[key] = relation
    def reviewer(a, b, evidence):
        return reviews.get(frozenset((a['candidate_id'], b['candidate_id'])), 'unknown')
    groups = cluster(rows, encoder, cutoff.astimezone(timezone.utc), .88, reviewer)
    return {'scope': 'self_authored_local_trial', 'threshold': .88,
            'groups': [[row['candidate_id'] for row in group] for group in groups],
            'note': 'Unreviewed pairs remain separate; this is not publication or verified editorial quality.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='store_true', help='explicitly enable local trial')
    parser.add_argument('--input', help='local JSON with self-authored descriptions')
    args = parser.parse_args()
    if not args.enable:
        parser.exit(2, 'Trial disabled; use --enable with a local input file.\n')
    if not args.input: parser.error('--input is required')
    path = Path(args.input)
    if path.stat().st_size > 65536: parser.error('input exceeds 64 KiB')
    data = json.loads(path.read_text(encoding='utf-8'))
    from .e5 import E5Encoder
    print(json.dumps(trial(data, E5Encoder()), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
