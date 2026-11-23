"""Map provider-only identifiers to stable, private local candidate IDs."""


class IdentifierError(ValueError):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


def shorten(candidates, prefix, width=2):
    mapping, result, seen = {}, [], set()
    for index, candidate in enumerate(candidates, 1):
        original = candidate.get('candidate_id')
        if not isinstance(original, str) or not original or original in seen:
            raise IdentifierError('invalid_field')
        seen.add(original)
        short = f'{prefix}{index:0{width}d}'
        mapping[short] = original
        result.append({**candidate, 'candidate_id': short})
    return result, mapping
