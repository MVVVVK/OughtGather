"""Offline tests for the private, provider-independent memory primitives."""
import base64
import os
import pytest
from src.ai.memory.crypto import decrypt, encrypt, parse_key
from src.ai.memory.model import apply_patch, empty_memory


def patch(pid='one', operation='add', target=None):
    return {'schema_version': 1, 'patch_id': pid, 'operation': operation, 'target_id': target,
            'entry': {'kind': 'preference', 'strength': 'soft', 'instruction': 'Prefer context',
                      'original_feedback': 'A private example',
                      'signals': {'prefer': ['history'], 'deprioritize': []}}}


def test_encrypted_memory_roundtrip_and_authentication():
    key = os.urandom(32)
    memory, _ = apply_patch(empty_memory(), patch())
    blob = encrypt(memory, key)
    assert b'A private example' not in blob
    assert decrypt(blob, key) == memory
    with pytest.raises(ValueError):
        decrypt(blob, os.urandom(32))
    with pytest.raises(ValueError):
        decrypt(blob[:-1] + bytes([blob[-1] ^ 1]), key)
    assert parse_key(base64.b64encode(key).decode()) == key
    with pytest.raises(ValueError):
        parse_key('short')


def test_append_only_and_duplicate_patch_id():
    initial = empty_memory()
    first, changed = apply_patch(initial, patch())
    assert changed and first['revision'] == 1 and not initial['ledger']
    duplicate, changed = apply_patch(first, patch())
    assert not changed and duplicate == first
    second, changed = apply_patch(first, patch('two', 'supersede', 'one'))
    assert changed and second['revision'] == 2
    assert not second['ledger'][0]['active']
    assert second['ledger'][0]['entry']['original_feedback'] == 'A private example'
    assert len(second['profile']['soft_rules']) == 1
    inactive, _ = apply_patch(second, {'schema_version': 1, 'patch_id': 'three', 'operation': 'deactivate', 'target_id': 'two'})
    assert inactive['revision'] == 3 and not inactive['profile']['soft_rules']


def test_compiler_caps_weights_and_validates_targets():
    memory = empty_memory()
    for n in range(10):
        memory, _ = apply_patch(memory, patch(str(n)))
    assert memory['profile']['prefer_signals']['history'] == 0.3
    with pytest.raises(ValueError):
        apply_patch(memory, patch('bad', 'supersede', 'missing'))


def test_modified_profile_or_revision_is_rejected():
    from src.ai.memory.model import validate_memory
    memory = empty_memory()
    memory['profile']['raw_memory'] = 'private'
    with pytest.raises(ValueError):
        validate_memory(memory)
    memory = empty_memory()
    memory['revision'] = 42
    with pytest.raises(ValueError):
        validate_memory(memory)
