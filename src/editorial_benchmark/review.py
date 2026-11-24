"""Local-only blinded review. Never prints or uploads candidate content."""
import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path
from .crypto import decrypt, encrypt, key_from_env
from scripts.editorial_v2.replay import validate


def create_review(snapshot, encrypted_snapshot, sample=5, include_b2=False):
    validate(snapshot, allow_private=True)
    if snapshot['data_class'] != 'private' or type(sample) is not int or sample < 0: raise ValueError('invalid review')
    local_results = {}
    if include_b2:
        from scripts.editorial_v2.b2 import select
        if 'B2_local' in snapshot['recorded_results']: raise ValueError('result already exists')
        local_results['B2_local'] = select(snapshot['candidates'], snapshot['captured_at'])
    chosen = set()
    for result in snapshot['recorded_results'].values(): chosen.update(result['selected'])
    for result in local_results.values(): chosen.update(result['selected'])
    rows = snapshot['candidates']; pool = [r for r in rows if r['candidate_id'] not in chosen]
    rng = secrets.SystemRandom(); rng.shuffle(pool)
    chosen.update(r['candidate_id'] for r in pool[:sample])
    chosen_rows = [r for r in rows if r['candidate_id'] in chosen]; rng.shuffle(chosen_rows)
    mapping = {}; items = []
    for i, row in enumerate(chosen_rows, 1):
        code = f'R{i:04d}'; mapping[code] = row['candidate_id']
        items.append({'code': code, 'title': row['title'], 'source': row['source'],
                      'published_at': row['published_at'], 'event_id': None,
                      'importance': 'unknown', 'incremental_against': {}})
    return {'schema_version': 1, 'items': items}, {'snapshot_sha256': hashlib.sha256(encrypted_snapshot).hexdigest(), 'codes': mapping, 'local_results': local_results}


def apply_review(snapshot, encrypted_snapshot, mapping, review):
    if mapping.get('snapshot_sha256') != hashlib.sha256(encrypted_snapshot).hexdigest(): raise ValueError('snapshot mismatch')
    codes = mapping['codes']; items = review['items']
    if not isinstance(items, list) or {x.get('code') for x in items} != set(codes) or len(items) != len(codes):
        raise ValueError('invalid review codes')
    gold = {}; novelty = {}; unresolved = {}; disputed_relations = []
    for item in items:
        cid = codes[item['code']]; importance = item.get('importance'); event = item.get('event_id')
        if importance in ('important', 'not_important'):
            if not isinstance(event, str) or not event or len(event) > 100: raise ValueError('invalid event')
            gold[cid] = {'event_id': event, 'important': importance == 'important'}
        elif importance in ('unknown', 'disputed'):
            unresolved[cid] = importance
            if event is not None:
                if not isinstance(event, str) or not event or len(event) > 100: raise ValueError('invalid event')
                gold[cid] = {'event_id': event, 'important': None}
        else: raise ValueError('invalid importance')
        pairs = item.get('incremental_against', {})
        if not isinstance(pairs, dict): raise ValueError('invalid relation')
        for other, value in pairs.items():
            if other not in codes or other == item['code'] or value not in ('no_new_information', 'new_information', 'substantive_update', 'unknown', 'disputed'):
                raise ValueError('invalid relation')
            novelty.setdefault(cid, {})[codes[other]] = 'unknown' if value == 'disputed' else value
            if value == 'disputed': disputed_relations.append([cid, codes[other]])
    for name, result in mapping.get('local_results', {}).items():
        if name in snapshot['recorded_results']: raise ValueError('duplicate result')
        snapshot['recorded_results'][name] = result
    snapshot['gold'] = gold; snapshot['incremental_labels'] = novelty; snapshot['unresolved_labels'] = unresolved
    snapshot['disputed_relations'] = disputed_relations
    return validate(snapshot, allow_private=True)


def write_new(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream: stream.write(content)


def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create'); create.add_argument('snapshot'); create.add_argument('directory'); create.add_argument('--sample', type=int, default=5); create.add_argument('--b2', action='store_true')
    apply = sub.add_parser('apply'); apply.add_argument('snapshot'); apply.add_argument('directory'); apply.add_argument('output')
    args = parser.parse_args()
    try:
        directory = Path(args.directory).resolve(strict=True)
        if not directory.is_dir() or directory.stat().st_mode & 0o077 or directory == Path.cwd() or Path.cwd() in directory.parents:
            raise ValueError('private directory required')
        key = key_from_env(); blob = Path(args.snapshot).read_bytes(); snapshot = decrypt(blob, key)
        if args.command == 'create':
            review, mapping = create_review(snapshot, blob, args.sample, args.b2)
            # Mapping first: do not leave an unmatchable plaintext review if it fails.
            write_new(directory / 'mapping.enc', encrypt(mapping, key))
            write_new(directory / 'review.json', json.dumps(review, ensure_ascii=False, indent=2).encode())
        else:
            output = Path(args.output).resolve()
            if output.parent != directory: raise ValueError('output must remain private')
            mapping = decrypt((directory / 'mapping.enc').read_bytes(), key)
            review = json.loads((directory / 'review.json').read_text(encoding='utf-8'))
            labeled = apply_review(snapshot, blob, mapping, review)
            write_new(output, encrypt(labeled, key))
        print('Local review operation completed')
    except Exception:
        parser.exit(2, 'Local review unavailable; no content emitted.\n')


if __name__ == '__main__': main()
