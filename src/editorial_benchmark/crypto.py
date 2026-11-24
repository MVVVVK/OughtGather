"""Authenticated encrypted benchmark envelope, independent of all other state."""
import base64
import json
import os
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b'OGBENCH\x01'


def key_from_env():
    try:
        key = base64.b64decode(os.environ['EDITORIAL_BENCHMARK_KEY'], validate=True)
        if len(key) != 32: raise ValueError()
        return key
    except Exception:
        raise ValueError('benchmark key unavailable') from None


def derive(key, salt):
    if not isinstance(key, bytes) or len(key) != 32: raise ValueError('invalid key')
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                info=b'oughtgather-benchmark-v1').derive(key)


def encrypt(value, key):
    salt, nonce = os.urandom(16), os.urandom(12)
    header = MAGIC + salt + nonce
    plain = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return header + AESGCM(derive(key, salt)).encrypt(nonce, plain, header)


def decrypt(blob, key):
    try:
        if not isinstance(blob, bytes) or not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 44: raise ValueError()
        n = len(MAGIC); header = blob[:n+28]
        result = json.loads(AESGCM(derive(key, header[n:n+16])).decrypt(header[n+16:n+28], blob[n+28:], header))
        if not isinstance(result, dict): raise ValueError()
        return result
    except Exception:
        raise ValueError('benchmark authentication failed') from None
