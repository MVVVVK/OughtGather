"""Explicitly authorized, isolated Jianguoyun WebDAV capability smoke test.

This module never imports the delivery pipeline, reads the configured delivery
path, or opens deduplication state. It sends only fixed non-news test bytes.
"""
import argparse
import json
import os
import re
import sys
import uuid
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

import httpx


class SmokeFailure(Exception):
    def __init__(self, operation, reason, status=None):
        self.operation = operation
        self.reason = reason
        self.status = status
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
    parent = root + '/OughtGatherSmoke'
    folder = 't-' + uuid.uuid4().hex[:12]
    relative_path = 'OughtGatherSmoke/' + folder
    directory = parent + '/' + folder
    source, moved, target = (directory + '/' + name for name in ('source.bin', 'moved.bin', 'destination.bin'))
    result = {'success': False, 'directory': relative_path, 'operations': [],
              'cleanup_actions': [], 'cleanup_errors': [], 'residual_path': None}
    created = False
    creation_attempted = False
    completed = False

    def request(label, method, address, allowed, **kwargs):
        try:
            response = client.request(method, address, **kwargs)
        except Exception as exc:
            raise SmokeFailure(label, 'request error: ' + type(exc).__name__) from exc
        code = response.status_code
        if code not in allowed:
            reason = f'HTTP {code}'
            if code == 405:
                reason += ' (operation not allowed or unsupported'
                if method == 'MKCOL':
                    reason += '; collection may already exist'
                reason += ')'
            elif code == 501:
                reason += ' (operation unsupported)'
            elif code in (401, 403):
                reason += ' (authentication or permission denied)'
            raise SmokeFailure(label, reason, status=code)
        result['operations'].append({'operation': label, 'status': code, 'ok': True})
        return response

    def expect(label, condition):
        if not condition:
            last_status = result['operations'][-1]['status'] if result['operations'] else None
            raise SmokeFailure(label, 'response verification failed', status=last_status)

    try:
        listing = request('PROPFIND test parent', 'PROPFIND', parent + '/', (207,),
                          headers={'Depth': '0', 'Content-Type': 'application/xml'},
                          content=b'<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>')
        try:
            root_xml = ElementTree.fromstring(listing.content)
            expected = urlsplit(parent).path.rstrip('/')
            parent_exists = any(
                unquote(urlsplit(node.findtext('{DAV:}href', '')).path).rstrip('/') == expected
                and any(re.search(r'\s2\d\d(?:\s|$)', prop.findtext('{DAV:}status', ''))
                        and prop.find('{DAV:}prop/{DAV:}resourcetype/{DAV:}collection') is not None
                        for prop in node.findall('{DAV:}propstat'))
                for node in root_xml.findall('{DAV:}response')
            )
        except ElementTree.ParseError as exc:
            raise SmokeFailure('PROPFIND test parent', 'invalid XML response') from exc
        expect('PROPFIND test parent verification', parent_exists)
        creation_attempted = True
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
        result['operations'].append({'operation': exc.operation, 'status': exc.status,
                                     'ok': False, 'reason': exc.reason})
    finally:
        if created and not completed:
            # Only names in the collection created with HTTP 201 in this run.
            for label, address in (('source', source), ('moved', moved), ('destination', target), ('collection', directory)):
                try:
                    response = client.request('DELETE', address)
                    action = {'item': label, 'status': response.status_code}
                    result['cleanup_actions'].append(action)
                    if response.status_code not in (200, 204, 404):
                        result['cleanup_errors'].append(action)
                except Exception as exc:
                    action = {'item': label, 'status': None, 'error': type(exc).__name__}
                    result['cleanup_actions'].append(action)
                    result['cleanup_errors'].append(action)
            try:
                response = client.request('PROPFIND', directory + '/', headers={'Depth': '0'})
                action = {'item': 'verify_collection_absent', 'status': response.status_code}
                result['cleanup_actions'].append(action)
                if response.status_code != 404:
                    result['cleanup_errors'].append(action)
            except Exception as exc:
                action = {'item': 'verify_collection_absent', 'status': None, 'error': type(exc).__name__}
                result['cleanup_actions'].append(action)
                result['cleanup_errors'].append(action)
            if result['cleanup_errors']:
                result['residual_path'] = relative_path
        elif creation_attempted and not created:
            # A failed/ambiguous MKCOL does not establish ownership; do not delete it.
            result['residual_path'] = relative_path
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authorize-isolated', action='store_true', help='Confirm creation and deletion of a random test directory')
    args = parser.parse_args(argv)
    if not args.authorize_isolated:
        parser.error('Explicit --authorize-isolated is required')
    url = os.environ.get('WEBDAV_URL', '')
    try:
        _root_url(url)
    except ValueError:
        print(json.dumps({'success': False, 'error': 'configuration',
                          'reason': 'WEBDAV_URL must be the HTTPS Jianguoyun /dav root'}))
        return 1
    username, password = os.environ.get('WEBDAV_USERNAME'), os.environ.get('WEBDAV_PASSWORD')
    if not username or not password:
        print(json.dumps({'success': False, 'error': 'configuration',
                          'reason': 'WEBDAV_USERNAME and WEBDAV_PASSWORD are required'}))
        return 1
    try:
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
