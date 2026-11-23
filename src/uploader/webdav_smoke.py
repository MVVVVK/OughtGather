"""Explicitly authorized, isolated Jianguoyun WebDAV capability smoke test.

This module never imports the delivery pipeline, reads the configured delivery
path, or opens deduplication state. It sends only fixed non-news test bytes.
"""
import argparse
import json
import os
import sys
import uuid
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

import httpx


class SmokeFailure(Exception):
    def __init__(self, operation, reason):
        self.operation = operation
        self.reason = reason
        super().__init__(reason)


def _root_url(url):
    """Pin live probes to the public Jianguoyun DAV root, never a subfolder."""
    parsed = urlsplit(url or '')
    if (parsed.scheme != 'https' or parsed.hostname != 'dav.jianguoyun.com'
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.query or parsed.fragment or unquote(parsed.path).rstrip('/') != '/dav'):
        raise ValueError('WEBDAV_URL must be the HTTPS Jianguoyun /dav root')
    return 'https://dav.jianguoyun.com/dav'


def run_smoke(url, client, *, authorized=False):
    """Exercise methods in a freshly created random collection, then clean it up."""
    if not authorized:
        raise ValueError('Explicit authorization is required')
    root = _root_url(url)
    folder = 'oughtgather-smoke-' + uuid.uuid4().hex
    directory = root + '/' + folder
    source, moved, target = (directory + '/' + name for name in ('source.bin', 'moved.bin', 'destination.bin'))
    result = {'success': False, 'directory': folder, 'operations': [], 'cleanup_errors': []}
    created = False
    completed = False

    def request(label, method, address, allowed, **kwargs):
        try:
            response = client.request(method, address, **kwargs)
        except Exception as exc:
            raise SmokeFailure(label, 'request error: ' + type(exc).__name__) from exc
        code = response.status_code
        if code not in allowed:
            reason = f'HTTP {code}'
            if code in (405, 501):
                reason += ' (operation unsupported)'
            elif code in (401, 403):
                reason += ' (authentication or permission denied)'
            raise SmokeFailure(label, reason)
        result['operations'].append({'operation': label, 'status': code, 'ok': True})
        return response

    def expect(label, condition):
        if not condition:
            raise SmokeFailure(label, 'response verification failed')

    try:
        request('MKCOL', 'MKCOL', directory, (201,))
        created = True
        request('PUT source', 'PUT', source, (200, 201, 204), content=b'oughtgather smoke one')
        expect('GET source', request('GET source', 'GET', source, (200,)).content == b'oughtgather smoke one')
        request('MOVE new', 'MOVE', source, (201, 204), headers={'Destination': moved, 'Overwrite': 'F'})
        expect('GET moved', request('GET moved', 'GET', moved, (200,)).content == b'oughtgather smoke one')
        request('PUT replacement', 'PUT', source, (200, 201, 204), content=b'oughtgather smoke two')
        request('PUT destination', 'PUT', target, (200, 201, 204), content=b'oughtgather old value')
        listing = request('PROPFIND', 'PROPFIND', directory + '/', (207,), headers={'Depth': '1'})
        try:
            listing_root = ElementTree.fromstring(listing.content)
            names = {unquote(urlsplit(el.text or '').path).rstrip('/').split('/')[-1]
                     for el in listing_root.findall('.//{DAV:}href')}
        except ElementTree.ParseError as exc:
            raise SmokeFailure('PROPFIND', 'invalid XML response') from exc
        expect('PROPFIND entries', {'source.bin', 'moved.bin', 'destination.bin'} <= names)
        request('MOVE overwrite', 'MOVE', source, (201, 204), headers={'Destination': target, 'Overwrite': 'T'})
        expect('GET overwritten', request('GET overwritten', 'GET', target, (200,)).content == b'oughtgather smoke two')
        request('GET moved source', 'GET', source, (404,))
        request('DELETE moved', 'DELETE', moved, (200, 204))
        request('GET deleted moved', 'GET', moved, (404,))
        request('DELETE destination', 'DELETE', target, (200, 204))
        request('GET deleted destination', 'GET', target, (404,))
        request('DELETE collection', 'DELETE', directory, (200, 204))
        request('PROPFIND deleted collection', 'PROPFIND', directory + '/', (404,), headers={'Depth': '0'})
        completed = True
        result['success'] = True
    except SmokeFailure as exc:
        result['operations'].append({'operation': exc.operation, 'ok': False, 'reason': exc.reason})
    finally:
        if created and not completed:
            # Only names in the collection created with HTTP 201 in this run.
            for label, address in (('source', source), ('moved', moved), ('destination', target), ('collection', directory)):
                try:
                    response = client.request('DELETE', address)
                    if response.status_code not in (200, 204, 404):
                        result['cleanup_errors'].append({'item': label, 'status': response.status_code})
                except Exception as exc:
                    result['cleanup_errors'].append({'item': label, 'error': type(exc).__name__})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authorize-isolated', action='store_true', help='Confirm creation and deletion of a random test directory')
    args = parser.parse_args(argv)
    if not args.authorize_isolated:
        parser.error('Explicit --authorize-isolated is required')
    try:
        url = os.environ.get('WEBDAV_URL', '')
        _root_url(url)
        username, password = os.environ.get('WEBDAV_USERNAME'), os.environ.get('WEBDAV_PASSWORD')
        if not username or not password:
            raise ValueError('WEBDAV_USERNAME and WEBDAV_PASSWORD are required')
        with httpx.Client(auth=(username, password), timeout=15.0, follow_redirects=False) as client:
            report = run_smoke(url, client, authorized=True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['success'] else 1
    except Exception as exc:
        # Do not print URL, response bodies, credentials, or transport error details.
        print(json.dumps({'success': False, 'error': type(exc).__name__}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
