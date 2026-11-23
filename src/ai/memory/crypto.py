"""Versioned authenticated encryption for private editorial memory."""
import base64
import binascii
import json
import os
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .model import validate_memory

MAGIC = b'OGMEM\x01'


def parse_key(value):
    try:
        key = base64.b64decode(value, validate=True)
    except (ValueError, TypeError, binascii.Error):
        raise ValueError('invalid memory key') from None
    if len(key) != 32:
        raise ValueError('memory key must be 32 bytes in base64')
    return key


def _derive(key, salt):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b'oughtgather-editorial-memory-v1').derive(key)


def encrypt(memory, key):
    validate_memory(memory)
    salt, nonce = os.urandom(16), os.urandom(12)
    plaintext = json.dumps(memory, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    header = MAGIC + salt + nonce
    return header + AESGCM(_derive(key, salt)).encrypt(nonce, plaintext, header)


def decrypt(blob, key):
    if not isinstance(blob, bytes) or len(blob) < len(MAGIC) + 44 or not blob.startswith(MAGIC):
        raise ValueError('invalid encrypted memory')
    header, ciphertext = blob[:len(MAGIC)+28], blob[len(MAGIC)+28:]
    try:
        memory = json.loads(AESGCM(_derive(key, header[len(MAGIC):len(MAGIC)+16])).decrypt(header[-12:], ciphertext, header))
        validate_memory(memory)
        return memory
    except Exception:
        raise ValueError('memory authentication or validation failed') from None
