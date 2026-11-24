"""Research-only aggregate experiment for a user-supplied CrossNews-UA CSV.

No data is downloaded, redistributed, logged, sent to a model or persisted here.
"""
import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from . import b2, b3

EXPECTED_SHA256 = '4a8fde8f4c6bcbfe23b314833acc74cba76d58448d0db03350f3b8b044688edb'


def evaluate_file(path, expected_sha256=None):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(65536), b''): digest.update(block)
    if expected_sha256 and digest.hexdigest() != expected_sha256:
        raise ValueError('dataset version mismatch')
    cutoff = datetime(2026, 11, 24, tzinfo=timezone.utc)
    counts = {name: {'tp': 0, 'fp': 0, 'fn': 0, 'tn': 0, 'abstained': 0} for name in ('B2', 'B3')}
    total = 0; excluded = 0; cross_language = 0
    with open(path, encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            label = row.get('aggregated_what')
            if label not in ('similar', 'different') or row.get('url1_lang') == row.get('url2_lang'):
                excluded += 1; continue
            texts = [row.get('text1', '')[:800], row.get('text2', '')[:800]]
            if not all(texts): excluded += 1; continue
            # Corpus does not provide article publication timestamps or headlines.
            # First 800 characters are a documented proxy, not a title or an RSS excerpt.
            candidates = [{'candidate_id': str(i), 'title': text, 'excerpt': None,
                           'excerpt_permitted': False, 'language': row[f'url{i+1}_lang'],
                           'published_at': None, 'url': None} for i, text in enumerate(texts)]
            positive = label == 'similar'; total += 1; cross_language += 1
            b2_result = b2.same_story(*candidates, cutoff)
            relation = b3.relation(*candidates, cutoff)
            for name, prediction in [('B2', b2_result), ('B3', relation in ('same_item', 'same_event'))]:
                obj = counts[name]
                if name == 'B3' and relation == 'unknown': obj['abstained'] += 1
                if prediction and positive: obj['tp'] += 1
                elif prediction: obj['fp'] += 1
                elif positive: obj['fn'] += 1
                else: obj['tn'] += 1
    for obj in counts.values():
        pden = obj['tp'] + obj['fp']; rden = obj['tp'] + obj['fn']; fden = 2*obj['tp'] + obj['fp'] + obj['fn']
        obj.update(precision=obj['tp']/pden if pden else None, recall=obj['tp']/rden if rden else None,
                   f1=2*obj['tp']/fden if fden else None)
    return {'dataset_sha256': digest.hexdigest(), 'evaluated_cross_language_pairs': cross_language,
            'excluded_pairs': excluded, 'counts': counts,
            'label_definition': 'similar vs different topic/event proxy; somewhat_related and disagreement excluded',
            'input_limitation': 'first 800 characters, no publication dates; unknown treated as split for clustering metrics'}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('csv'); args = parser.parse_args()
    try: print(json.dumps(evaluate_file(Path(args.csv), EXPECTED_SHA256), sort_keys=True))
    except (OSError, ValueError, csv.Error): parser.exit(2, 'Dataset unavailable or invalid.\n')


if __name__ == '__main__': main()
