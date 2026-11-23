"""Validate untrusted model output and calculate provider-independent scores."""
TOPICS = ('geopolitics_diplomacy','conflict_security','governance_elections','economy_finance',
          'trade_industry','technology','energy','climate_environment','science','health',
          'migration_humanitarian','society_demography','law_justice','infrastructure_transport','culture_media','other')
REGIONS = ('east_asia','southeast_asia','south_asia','europe','north_america','latin_america',
           'middle_east','africa','central_asia','oceania','global','multiregional','other')
DIMENSIONS = ('importance','global_impact','expected_depth','information_gain','timeliness','perspective_value','personal_fit')
WEIGHTS = (.25,.25,.15,.10,.10,.10,.05)
TOPIC_ALIASES = {'politics':'governance_elections','diplomacy':'geopolitics_diplomacy','geopolitics':'geopolitics_diplomacy',
                 'war':'conflict_security','conflict':'conflict_security','elections':'governance_elections',
                 'economy':'economy_finance','finance':'economy_finance','trade':'trade_industry',
                 'industry':'trade_industry','environment':'climate_environment','climate':'climate_environment',
                 'law':'law_justice','justice':'law_justice','society':'society_demography',
                 'migration':'migration_humanitarian','transport':'infrastructure_transport',
                 'shipping':'infrastructure_transport','culture':'culture_media'}
REGION_ALIASES = {'world':'global','international':'global','middle east':'middle_east','americas':'north_america'}


def category(value, choices, aliases):
    value = str(value).strip().lower().replace('-', '_')
    value = aliases.get(value, value)
    return value if value in choices else 'other'


def score(item):
    return round(sum(item[k] * w for k, w in zip(DIMENSIONS, WEIGHTS)), 2)


def validate_batch(data, ids, personalized=True):
    if not isinstance(data, dict) or not isinstance(data.get('items'), list) or len(data['items']) != len(ids):
        raise ValueError('incomplete output')
    seen, result = set(), []
    for item in data['items']:
        if not isinstance(item, dict) or item.get('candidate_id') not in ids or item['candidate_id'] in seen:
            raise ValueError('invalid id')
        seen.add(item['candidate_id'])
        for field in DIMENSIONS:
            if type(item.get(field)) not in (int, float) or not 0 <= item[field] <= 100:
                raise ValueError('invalid score')
        for field, maximum in (('topic',100),('region',100),('news_type',100),('event_cluster',100)):
            if not isinstance(item.get(field), str) or len(item[field]) > maximum:
                raise ValueError('invalid field')
        if item.get('confidence') not in ('high','medium','low') or item.get('cluster_type') not in ('duplicate','update','different_angle','analysis','none') or item.get('impact_scope') not in ('local','national','regional','global'):
            raise ValueError('invalid enum')
        row = {k: item[k] for k in ('candidate_id','topic','region','news_type',*DIMENSIONS,'impact_scope','confidence','event_cluster','cluster_type')}
        row['topic'] = category(row['topic'], TOPICS, TOPIC_ALIASES)
        row['region'] = category(row['region'], REGIONS, REGION_ALIASES)
        if not personalized:
            row['personal_fit'] = 50
        row['score'] = score(row)
        result.append(row)
    return result


def validate_composition(data, ids):
    if not isinstance(data, dict) or not isinstance(data.get('events'), list) or len(data['events']) > len(ids):
        raise ValueError('invalid events')
    seen, events, event_ids = set(), {}, set()
    for event in data['events']:
        if not isinstance(event, dict) or not isinstance(event.get('event_id'), str) or not 1 <= len(event['event_id']) <= 64 or event['event_id'] in event_ids:
            raise ValueError('invalid event')
        event_ids.add(event['event_id'])
        members = event.get('members')
        if not isinstance(members, list) or not members:
            raise ValueError('invalid members')
        for member in members:
            if not isinstance(member, dict) or member.get('candidate_id') not in ids or member['candidate_id'] in seen or member.get('relation') not in ('primary','duplicate','update','different_angle','analysis'):
                raise ValueError('invalid member')
            seen.add(member['candidate_id'])
            events[member['candidate_id']] = (event['event_id'], member['relation'])
    return events
