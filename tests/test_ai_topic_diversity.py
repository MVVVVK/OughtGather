"""Prompt and structure tests: mocked choices do not measure editorial quality."""
import hashlib
import json

import httpx
import pytest

from src.ai.client import DeepSeekClient, GROUP_SYSTEM, SELECT_SYSTEM
from src.ai.editor import run
from src.ai.schemas import validate_selection
from tests.test_ai_topics import response


def test_selection_prompt_describes_soft_broad_topic_balance():
    prompt = SELECT_SYSTEM.lower()
    for phrase in (
        'a story is a specific event and gets at most one article',
        'a broad topic can contain multiple distinct stories',
        'when a broad topic already occupies several slots',
        'marginal information value',
        'important independent stories not yet selected',
        'highly similar or adds little',
        'equal or greater importance',
        'multiple stories on one broad topic are appropriate',
        'genuinely major global event',
        'without fixed quotas',
        'never select clearly unimportant news for diversity',
        'favor a country or research field',
        'global importance, news value, information gain or reporting quality',
        'up to 16',
    ):
        assert phrase in prompt


@pytest.mark.parametrize('chosen', [(0, 1, 6, 7), (0, 1, 2, 3, 4, 5)])
def test_mocked_shortlists_preserve_one_story_and_allow_multiple_same_broad_topic(monkeypatch, chosen):
    """Predetermined mock choices test the contract, not the model's judgment."""
    monkeypatch.setenv('AI_EDITOR_MODE', 'shadow')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake')
    monkeypatch.setattr('src.ai.editor.load_profile', lambda: ({}, None, 'degraded'))
    titles = [f'Distinct conflict story {i}' for i in range(6)] + [
        'Independent global technology development', 'Independent global trade agreement',
        'Independent energy development', 'Independent health development']
    candidates = [{'candidate_id': hashlib.sha256(str(i).encode()).hexdigest(),
                   'title': title, 'source': f'Source {i}', 'language': 'en', 'published_at': '',
                   'rss_excerpt': 'Distinct development', 'traditional_selected': True}
                  for i, title in enumerate(titles)]
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload['input'])
        name = payload['text']['format']['name']
        calls.append(name)
        if name == 'topic_grouping':
            assert payload['instructions'] == GROUP_SYSTEM
            assert len(data['candidates']) == len(candidates)
            result = {'groups': []}  # Mocked distinct stories, not a semantic test.
        else:
            assert payload['instructions'] == SELECT_SYSTEM
            assert payload['text']['format']['schema']['properties']['selections']['maxItems'] == 16
            by_title = {c['title']: (group['group_id'], c['candidate_id'])
                        for group in data['groups'] for c in group['candidates']}
            result = {'selections': [{'group_id': by_title[titles[i]][0],
                                      'candidate_id': by_title[titles[i]][1]} for i in chosen]}
        return httpx.Response(200, json=response(result))

    report = run(candidates, DeepSeekClient('fake', transport=httpx.MockTransport(handler)))
    assert calls == ['topic_grouping', 'topic_selection']
    assert report['status'] == 'ok' and report['selected_article_count'] == len(chosen)
    assert set(report['ai_recommended']) == {candidates[i]['candidate_id'] for i in chosen}
    assert report['max_articles_per_topic'] == 1


def test_local_validation_still_rejects_two_articles_from_one_story():
    with pytest.raises(ValueError, match='duplicate_selection'):
        validate_selection({'selections': [{'group_id': 'g001', 'candidate_id': 'c001'},
                                           {'group_id': 'g001', 'candidate_id': 'c002'}]},
                           {'g001': {'c001', 'c002'}})
