"""Strict validation for untrusted global story grouping and selection."""
import hashlib
import json


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
    grouped.sort(key=lambda members: tuple(members))
    return [{'group_id': 'g-' + hashlib.sha256(json.dumps(members, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()[:24],
             'members': members} for members in grouped]


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
