"""Strict validation for untrusted global story grouping and selection."""
import hashlib
import json


def make_groups(grouped):
    grouped = sorted((sorted(members) for members in grouped), key=tuple)
    return [{'group_id': 'g-' + hashlib.sha256(json.dumps(members, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()[:24],
             'members': members} for members in grouped]


def validate_groups(data, short_to_real):
    if not isinstance(data, dict) or set(data) != {'groups'} or not isinstance(data.get('groups'), list):
        raise ValueError('invalid_groups')
    seen, grouped = set(), []
    if len(data['groups']) > len(short_to_real) // 2:
        raise ValueError('invalid_groups')
    for group in data['groups']:
        if not isinstance(group, dict) or set(group) != {'members'}:
            raise ValueError('invalid_group')
        members = group['members']
        if not isinstance(members, list) or len(members) < 2:
            raise ValueError('invalid_group')
        restored = []
        for short in members:
            if not isinstance(short, str) or short not in short_to_real:
                raise ValueError('unknown_candidate_id')
            if short in seen:
                raise ValueError('duplicate_candidate_id')
            seen.add(short)
            restored.append(short_to_real[short])
        grouped.append(sorted(restored))
    grouped.extend([[real] for short, real in short_to_real.items() if short not in seen])
    return make_groups(grouped)


def validate_audit(data, audit_groups, short_to_real, original_groups):
    """Apply complete, disjoint partitions only to their original multi-member groups."""
    if not isinstance(data, dict) or set(data) != {'splits'} or not isinstance(data['splits'], list):
        raise ValueError('invalid_audit')
    replacements, seen_groups = {}, set()
    for split in data['splits']:
        if not isinstance(split, dict) or set(split) != {'group_id', 'partitions'}:
            raise ValueError('invalid_split')
        group_id, parts = split['group_id'], split['partitions']
        if not isinstance(group_id, str) or group_id not in audit_groups:
            raise ValueError('unknown_group_id')
        if group_id in seen_groups:
            raise ValueError('duplicate_group_id')
        seen_groups.add(group_id)
        if not isinstance(parts, list) or len(parts) < 2:
            raise ValueError('invalid_partition')
        original = audit_groups[group_id]
        expected, seen, restored = set(original['members']), set(), []
        for part in parts:
            if not isinstance(part, dict) or set(part) != {'members'} or not isinstance(part['members'], list) or not part['members']:
                raise ValueError('invalid_partition')
            members = []
            for short in part['members']:
                if not isinstance(short, str) or short not in short_to_real:
                    raise ValueError('unknown_candidate_id')
                real = short_to_real[short]
                if real not in expected:
                    raise ValueError('candidate_not_in_group')
                if real in seen:
                    raise ValueError('duplicate_candidate_id')
                seen.add(real)
                members.append(real)
            restored.append(members)
        if seen != expected:
            raise ValueError('missing_candidate_id')
        replacements[original['group_id']] = restored
    grouped = []
    for group in original_groups:
        grouped.extend(replacements.get(group['group_id'], [group['members']]))
    return make_groups(grouped), len(replacements)


def validate_selection(data, provider_groups):
    if not isinstance(data, dict) or set(data) != {'selections'} or not isinstance(data.get('selections'), list) or len(data['selections']) > 16:
        raise ValueError('invalid_selections')
    seen_groups, seen_candidates, selected = set(), set(), []
    for row in data['selections']:
        if not isinstance(row, dict) or set(row) != {'group_id', 'candidate_id'}:
            raise ValueError('invalid_selection')
        group, candidate = row['group_id'], row['candidate_id']
        if not isinstance(group, str) or group not in provider_groups:
            raise ValueError('unknown_group_id')
        if not isinstance(candidate, str) or candidate not in provider_groups[group]:
            raise ValueError('candidate_not_in_group')
        if group in seen_groups or candidate in seen_candidates:
            raise ValueError('duplicate_selection')
        seen_groups.add(group)
        seen_candidates.add(candidate)
        selected.append((group, candidate))
    return selected
