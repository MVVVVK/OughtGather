"""Prompt/contract regressions, not evidence of model semantic performance."""
import hashlib
import json

import httpx
import pytest

from src.ai.client import DeepSeekClient, GROUP_SYSTEM, SELECT_SYSTEM
from src.ai.editor import run
from tests.test_ai_topics import response


# Fictional examples mirror the classes of error reported in real review.
STORIES = [
    ('US election results', 'US vote count'),
    ('Nigeria election results', 'Nigerian vote count'),
    ('Cyberattack against company A', 'Company A investigates its breach'),
    ('Cyberattack against institution B', 'Institution B investigates a separate breach'),
    ('HIV treatment research', 'HIV health research'),
    ('Ebola outbreak response', 'Ebola response update'),
    ('French court case involving person A', 'Decision in French case A'),
    ('French court case involving person B', 'Decision in unrelated French case B'),
    ('Company C announces an IPO', 'A listing announcement'),
    ('Company C reports an unrelated factory fire', 'A separate fire at the same company'),
    ('COP31 negotiations reach an agreement', 'Negotiators announce an agreement'),
    ('Officials react directly to COP31 agreement', 'Direct reaction to that agreement'),
    ('Ship struck in a single attack', 'Report on a ship attack'),
    ('Diplomats react to that same ship attack', 'Direct reaction to the ship attack'),
]


def test_grouping_prompt_guards_specific_event_vs_broad_category():
    prompt = GROUP_SYSTEM.lower()
    for phrase in ('one specific real-world', 'direct reactions and consequences',
                   'us and nigerian elections', 'cyberattacks on different companies or institutions',
                   'hiv and ebola', 'unrelated french court cases',
                   'two unrelated events involving the same company', 'when uncertain', 'singletons',
                   'cop31 negotiation outcome', 'same ship attack', 'untrusted data'):
        assert phrase in prompt


def test_selection_prompt_requires_evidence_based_independent_coverage():
    prompt = SELECT_SYSTEM.lower()
    for phrase in ('important independent news stories', 'actual international importance',
                   'global politics', 'security', 'economy', 'technology and society',
                   'timeliness and new information', 'one report best supported', 'reliable sourcing',
                   'useful background', 'no mechanical', 'no automatic bonus for law',
                   'clearly low-value', 'return fewer', 'do not claim to have read full articles'):
        assert phrase in prompt


def test_predefined_groups_exercise_contract_not_model_semantics(monkeypatch):
    monkeypatch.setenv('AI_EDITOR_MODE', 'shadow')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake')
    monkeypatch.setattr('src.ai.editor.load_profile', lambda: ({}, None, 'degraded'))
    candidates = [{'candidate_id': hashlib.sha256(str(i).encode()).hexdigest(),
                   'title': title, 'source': f'Source {i}', 'language': 'en' if i % 2 else 'fr',
                   'published_at': '', 'rss_excerpt': excerpt, 'traditional_selected': True}
                  for i, (title, excerpt) in enumerate(STORIES)]
    # This predetermined response tests how known groups and singletons flow
    # through local validation; it does not evaluate a model's classification.
    responses = []
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload['input'])
        name = payload['text']['format']['name']
        responses.append(name)
        if name == 'topic_grouping':
            assert payload['instructions'] == GROUP_SYSTEM
            assert [row['title'] for row in data['candidates']] == [title for title, _ in STORIES]
            result = {'groups': [{'members': ['c011', 'c012']}, {'members': ['c013', 'c014']}]}
        else:
            assert payload['instructions'] == SELECT_SYSTEM
            assert len(data['groups']) == 12
            result = {'selections': [{'group_id': group['group_id'],
                                      'candidate_id': group['candidates'][0]['candidate_id']}
                                     for group in data['groups']]}
        return httpx.Response(200, json={'status': 'completed',
            'output': [{'type': 'message', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': json.dumps(result)}]}],
            'usage': {'input_tokens': 100, 'output_tokens': 50, 'total_tokens': 150}})

    report = run(candidates, DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert responses == ['topic_grouping', 'topic_selection']
    assert report['status'] == 'ok' and report['topic_count'] == 12
    assert report['multi_article_topic_count'] == 2 and report['selected_article_count'] == 12
    groups_by_id = {m['candidate_id']: group for group in report['topic_groups'] for m in group['members']}
    ids = [c['candidate_id'] for c in candidates]
    for left, right in ((0, 1), (2, 3), (4, 5), (6, 7), (8, 9)):
        assert groups_by_id[ids[left]]['group_id'] != groups_by_id[ids[right]]['group_id']
    for left, right in ((10, 11), (12, 13)):
        assert groups_by_id[ids[left]]['group_id'] == groups_by_id[ids[right]]['group_id']
        assert len({ids[left], ids[right]} & set(report['ai_recommended'])) == 1
    assert all(group['representative_id'] in {m['candidate_id'] for m in group['members']}
               for group in report['topic_groups'] if group['representative_id'])
