"""Google Drive archive storage via per-file OAuth. Never deletes Drive files."""
import hashlib
import json
import os
import re
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from audit_archive_core import ArchiveError

API = 'https://www.googleapis.com/drive/v3/files'
SCOPE = 'https://www.googleapis.com/auth/drive.file'
FIELDS = 'id,name,mimeType,parents,trashed,size,sha256Checksum,webViewLink'


class DriveStore:
    def __init__(self, client_id, client_secret, refresh_token, folder_id=None):
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self.folder_id = folder_id
        self.token, self.expires = None, 0
        if folder_id and not re.fullmatch(r'[A-Za-z0-9_-]+', folder_id):
            raise ArchiveError('Invalid Google Drive folder ID.')

    @classmethod
    def from_env(cls):
        names = ['AUDIT_GOOGLE_CLIENT_ID', 'AUDIT_GOOGLE_CLIENT_SECRET',
                 'AUDIT_GOOGLE_REFRESH_TOKEN', 'AUDIT_DRIVE_FOLDER_ID']
        missing = [n for n in names if not os.environ.get(n)]
        if missing:
            raise ArchiveError('Missing settings: ' + ', '.join(missing))
        return cls(*(os.environ[n].strip() for n in names))

    def _token(self):
        if self.token and time.monotonic() < self.expires:
            return self.token
        body = urlencode({'client_id': self.client_id, 'client_secret': self.client_secret,
                          'refresh_token': self.refresh_token, 'grant_type': 'refresh_token'}).encode()
        try:
            with urlopen(Request('https://oauth2.googleapis.com/token', data=body,
                         headers={'Content-Type': 'application/x-www-form-urlencoded'}), timeout=30) as response:
                result = json.load(response)
            self.token = result['access_token']
            self.expires = time.monotonic() + max(0, int(result.get('expires_in', 3600)) - 60)
            return self.token
        except (HTTPError, URLError, KeyError, ValueError) as exc:
            raise ArchiveError('Google authorization failed. Check the Render secrets or repeat Google setup.') from None

    def _request(self, url, method='GET', data=None, content_type=None, raw_limit=None):
        headers = {'Authorization': 'Bearer ' + self._token()}
        if content_type:
            headers['Content-Type'] = content_type
        try:
            with urlopen(Request(url, data=data, method=method, headers=headers), timeout=60) as response:
                if raw_limit is not None:
                    value = response.read(raw_limit + 1)
                    if len(value) > raw_limit:
                        raise ArchiveError('Remote archive is larger than the expected file.')
                    return value
                return json.load(response)
        except HTTPError as exc:
            raise ArchiveError(f'Google Drive request failed (HTTP {exc.code}); no cleanup for this batch.') from None
        except (URLError, TimeoutError, ValueError):
            raise ArchiveError('Google Drive could not be reached or returned an invalid response.') from None

    def check_folder(self):
        if not self.folder_id:
            raise ArchiveError('No archive folder configured.')
        meta = self._request(API + '/' + quote(self.folder_id) + '?' + urlencode({'fields': FIELDS}))
        if meta.get('trashed') or meta.get('mimeType') != 'application/vnd.google-apps.folder':
            raise ArchiveError('The configured Drive archive folder is missing or trashed.')
        return meta

    def create_folder(self):
        # Folder is created by the same OAuth client, so drive.file can access it.
        result = self._request(API + '?' + urlencode({'fields': 'id,name,webViewLink'}), 'POST',
            json.dumps({'name': 'CTE Durban Coaches - Audit Archives',
                        'mimeType': 'application/vnd.google-apps.folder'}).encode(), 'application/json')
        self.folder_id = result['id']
        self.check_folder()
        return result

    def put_verified(self, name, data, digest):
        if hashlib.sha256(data).hexdigest() != digest:
            raise ArchiveError('Local archive checksum mismatch.')
        # Digest, not the display name, identifies identical retry content.
        q = (f"'{self.folder_id}' in parents and trashed = false and "
             f"appProperties has {{ key='cte_audit_sha256' and value='{digest}' }}")
        result = self._request(API + '?' + urlencode({'q': q, 'fields': f'files({FIELDS})', 'pageSize': 100}))
        files = result.get('files', [])
        if files:
            file_id = files[0]['id']
        else:
            boundary = 'cte-' + uuid.uuid4().hex
            metadata = json.dumps({'name': name, 'parents': [self.folder_id],
                'mimeType': 'application/zip', 'appProperties': {'cte_audit_sha256': digest,
                'cte_audit_schema': '1'}}).encode()
            body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'.encode()
                + metadata + f'\r\n--{boundary}\r\nContent-Type: application/zip\r\n\r\n'.encode()
                + data + f'\r\n--{boundary}--\r\n'.encode())
            result = self._request('https://www.googleapis.com/upload/drive/v3/files?' +
                urlencode({'uploadType': 'multipart', 'fields': FIELDS}), 'POST', body,
                f'multipart/related; boundary={boundary}')
            file_id = result['id']
        meta = self._request(API + '/' + quote(file_id) + '?' + urlencode({'fields': FIELDS}))
        if (meta.get('trashed') or self.folder_id not in meta.get('parents', [])
                or int(meta.get('size', -1)) != len(data)):
            raise ArchiveError('Uploaded archive metadata does not match; cleanup stopped.')
        downloaded = self._request(API + '/' + quote(file_id) + '?alt=media', raw_limit=len(data))
        if len(downloaded) != len(data) or hashlib.sha256(downloaded).hexdigest() != digest:
            raise ArchiveError('Downloaded archive checksum mismatch; cleanup stopped.')
        return file_id
