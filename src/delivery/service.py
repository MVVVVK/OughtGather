"""Authenticated temporary backup and independent delivery status."""
import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.uploader.webdav_uploader import WebDavUploader
from src.utils.helpers import get_now

MAGIC = b'OGDELIVERY1'
BACKUP_PATH = 'output/delivery-retry.enc'
STATUS_PATH = 'output/delivery-status.json'


def backup_key():
    key = os.getenv('DELIVERY_BACKUP_KEY', '')
    if len(key.encode('utf-8')) < 32:
        raise ValueError('DELIVERY_BACKUP_KEY must contain at least 32 bytes')
    return key


def _key(secret, salt):
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000).derive(secret.encode('utf-8'))


def _write_private(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.delivery-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def make_backup(epub_path, secret):
    content = Path(epub_path).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    edition = get_now().strftime('%Y-%m-%dT%H%M%S') + '-' + digest + '.epub'
    payload = json.dumps({'edition': edition, 'sha256': digest, 'content': base64.b64encode(content).decode('ascii')}).encode('utf-8')
    salt, nonce = os.urandom(16), os.urandom(12)
    encrypted = MAGIC + salt + nonce + AESGCM(_key(secret, salt)).encrypt(nonce, payload, MAGIC)
    _write_private(BACKUP_PATH, encrypted)
    return edition


def decrypt_backup(path, secret):
    data = Path(path).read_bytes()
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 44:
        raise ValueError('Invalid encrypted backup')
    offset = len(MAGIC)
    salt, nonce, encrypted = data[offset:offset + 16], data[offset + 16:offset + 28], data[offset + 28:]
    payload = json.loads(AESGCM(_key(secret, salt)).decrypt(nonce, encrypted, MAGIC))
    content = base64.b64decode(payload['content'], validate=True)
    if hashlib.sha256(content).hexdigest() != payload['sha256']:
        raise ValueError('Backup checksum mismatch')
    return payload['edition'], content


def write_status(status):
    _write_private(STATUS_PATH, json.dumps(status, ensure_ascii=False, indent=2).encode('utf-8'))


def deliver(epub_path, edition, uploader=None, sender_factory=None, cleanup_backup=None):
    try:
        uploader = uploader or WebDavUploader()
        if not uploader.config or not uploader.config.enabled:
            ok, error, details = False, 'disabled', {}
        else:
            ok = uploader.upload_epub(epub_path, edition=edition)
            error, details = uploader.last_error, uploader.last_result
    except Exception:
        ok, error, details = False, 'configuration_or_unexpected', {}
    status = {'webdav': 'success' if ok else 'failed', 'error': error,
              'details': details, 'email': 'not_sent', 'edition': edition,
              'attempted_at': get_now().isoformat()}
    if not ok and sender_factory:
        try:
            sender = sender_factory()
            if os.getenv('EMAIL_FALLBACK_ATTACHMENT', 'false').lower() == 'true':
                sent = sender.send_epub(epub_path, 'OughtGather WebDAV delivery failed')
            else:
                sent = sender.send_notification('OughtGather WebDAV delivery failed',
                                                'WebDAV delivery failed. Use the encrypted backup to retry this edition.')
            status['email'] = 'sent' if sent else 'failed'
        except Exception:
            status['email'] = 'failed'
    if ok and cleanup_backup and os.path.exists(cleanup_backup):
        os.unlink(cleanup_backup)
    write_status(status)
    return status


def retry_encrypted(path):
    try:
        edition, content = decrypt_backup(path, backup_key())
        with tempfile.TemporaryDirectory(prefix='oughtgather-delivery-') as directory:
            epub = Path(directory) / 'edition.epub'
            epub.write_bytes(content)
            from src.mailer.smtp_sender import SMTPSender
            return deliver(str(epub), edition, sender_factory=SMTPSender)['webdav'] == 'success'
    except Exception:
        write_status({'webdav': 'failed', 'error': 'backup_or_configuration', 'email': 'not_sent'})
        return False
