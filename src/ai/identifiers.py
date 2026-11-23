"""Map provider-only identifiers to stable, private local candidate IDs."""


class IdentifierError(ValueError):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


def shorten(candidates, prefix):
    mapping, result, seen = {}, [], set()
    for index, candidate in enumerate(candidates, 1):
        original = candidate.get('candidate_id')
        if not isinstance(original, str) or not original or original in seen:
            raise IdentifierError('invalid_field')
        seen.add(original)
        short = f'{prefix}{index:02d}'
        mapping[short] = original
        result.append({**candidate, 'candidate_id': short})
    return result, mapping


def _restore_member(member, mapping, seen):
    if not isinstance(member, dict):
        raise IdentifierError('invalid_field')
    short = member.get('candidate_id')
    if not isinstance(short, str) or short not in mapping:
        raise IdentifierError('unknown_candidate_id')
    if short in seen:
        raise IdentifierError('duplicate_candidate_id')
    seen.add(short)
    return {**member, 'candidate_id': mapping[short]}


def restore_articles(data, mapping):
    if not isinstance(data, dict) or not isinstance(data.get('items'), list) or len(data['items']) != len(mapping):
        raise IdentifierError('item_count')
    seen = set()
    return {**data, 'items': [_restore_member(item, mapping, seen) for item in data['items']]}


def restore_composition(data, mapping):
    if not isinstance(data, dict) or not isinstance(data.get('events'), list):
        raise IdentifierError('invalid_field')
    seen, events = set(), []
    for event in data['events']:
        if not isinstance(event, dict) or not isinstance(event.get('members'), list):
            raise IdentifierError('invalid_field')
        events.append({**event, 'members': [_restore_member(member, mapping, seen) for member in event['members']]})
    return {**data, 'events': events}
