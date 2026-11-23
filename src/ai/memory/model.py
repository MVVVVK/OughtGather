"""Deterministic, append-only editorial feedback ledger.

This module never invokes a model. Patch text is private and must not be logged.
"""
from copy import deepcopy
from datetime import datetime, timezone

KINDS = {'preference', 'requirement', 'positive_example', 'negative_example', 'correction'}
OPERATIONS = {'add', 'supersede', 'deactivate'}


def _now():
    return datetime.now(timezone.utc).isoformat()


def empty_memory():
    now = _now()
    return {'schema_version': 1, 'revision': 0, 'created_at': now,
            'updated_at': now, 'ledger': [], 'profile': compile_profile([])}


def validate_patch(patch):
    if not isinstance(patch, dict) or (type(patch.get('schema_version')) is not int or patch['schema_version'] != 1):
        raise ValueError('invalid patch schema')
    patch_id = patch.get('patch_id')
    if not isinstance(patch_id, str) or not 1 <= len(patch_id) <= 128:
        raise ValueError('invalid patch id')
    operation = patch.get('operation')
    if operation not in OPERATIONS:
        raise ValueError('invalid patch operation')
    if operation != 'add' and (not isinstance(patch.get('target_id'), str) or not patch['target_id']):
        raise ValueError('missing target id')
    entry = patch.get('entry')
    if operation != 'deactivate':
        if not isinstance(entry, dict) or entry.get('kind') not in KINDS:
            raise ValueError('invalid entry kind')
        if entry.get('strength') not in ('hard', 'soft'):
            raise ValueError('invalid strength')
        for key in ('instruction', 'original_feedback'):
            if not isinstance(entry.get(key), str) or not 1 <= len(entry[key]) <= 4000:
                raise ValueError('invalid entry text')
        scope = entry.get('scope', [])
        if not isinstance(scope, list) or len(scope) > 50 or any(not isinstance(x, str) or len(x) > 100 for x in scope):
            raise ValueError('invalid scope')
        references = entry.get('reference_articles', [])
        if not isinstance(references, list) or len(references) > 50 or any(not isinstance(x, dict) or any(not isinstance(v, str) or len(v) > 2000 for v in x.values()) for x in references):
            raise ValueError('invalid references')
        signals = entry.get('signals', {})
        if not isinstance(signals, dict):
            raise ValueError('invalid signals')
        for key in ('prefer', 'deprioritize'):
            items = signals.get(key, [])
            if not isinstance(items, list) or len(items) > 50 or any(not isinstance(x, str) or len(x) > 100 for x in items):
                raise ValueError('invalid signals')
    return True


def compile_profile(ledger):
    profile = {'hard_rules': [], 'soft_rules': [], 'prefer_signals': {},
               'deprioritize_signals': {}, 'anti_filter_bubble': [
                   'Preserve major world news regardless of personal preferences.',
                   'Include diverse regional and explanatory perspectives.']}
    for record in ledger:
        if not record.get('active') or not record.get('entry'):
            continue
        entry = record['entry']
        profile[entry['strength'] + '_rules'].append(entry['instruction'])
        for direction in ('prefer', 'deprioritize'):
            weights = profile[direction + '_signals']
            for signal in entry.get('signals', {}).get(direction, []):
                weights[signal] = min(0.3, round(weights.get(signal, 0) + 0.1, 3))
    return profile


def validate_memory(memory):
    if not isinstance(memory, dict) or (type(memory.get('schema_version')) is not int or memory['schema_version'] != 1):
        raise ValueError('invalid memory schema')
    if type(memory.get('revision')) is not int or memory['revision'] < 0:
        raise ValueError('invalid memory revision')
    if not isinstance(memory.get('ledger'), list) or not isinstance(memory.get('profile'), dict):
        raise ValueError('invalid memory data')
    ids = set()
    for item in memory['ledger']:
        if not isinstance(item, dict) or not isinstance(item.get('patch_id'), str) or item['patch_id'] in ids:
            raise ValueError('invalid ledger')
        ids.add(item['patch_id'])
        if not isinstance(item.get('active'), bool):
            raise ValueError('invalid ledger')
        if item.get('entry') is not None:
            validate_patch({'schema_version': 1, 'patch_id': item['patch_id'], 'operation': 'add', 'entry': item['entry']})
    if memory['revision'] != len(memory['ledger']) or memory['profile'] != compile_profile(memory['ledger']):
        raise ValueError('invalid memory revision or profile')
    if not isinstance(memory.get('created_at'), str) or not isinstance(memory.get('updated_at'), str):
        raise ValueError('invalid memory timestamps')
    return True


def apply_patch(memory, patch):
    validate_memory(memory)
    validate_patch(patch)
    if any(row['patch_id'] == patch['patch_id'] for row in memory['ledger']):
        return deepcopy(memory), False
    updated = deepcopy(memory)
    operation = patch['operation']
    if operation != 'add':
        target = next((row for row in updated['ledger'] if row['patch_id'] == patch['target_id']), None)
        if not target or not target['active']:
            raise ValueError('target not active')
        target['active'] = False
    updated['ledger'].append({'patch_id': patch['patch_id'], 'operation': operation,
                              'target_id': patch.get('target_id'), 'active': operation != 'deactivate',
                              'entry': deepcopy(patch.get('entry')) if operation != 'deactivate' else None, 'created_at': _now()})
    updated['revision'] += 1
    updated['updated_at'] = _now()
    updated['profile'] = compile_profile(updated['ledger'])
    return updated, True
