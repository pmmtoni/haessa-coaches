"""Read-only local check of a downloaded archive: python verify_audit_archive.py FILE.zip"""
import argparse
import hashlib
import json
import zipfile
from audit_archive_core import ArchiveError


def verify(path):
    with zipfile.ZipFile(path) as z:
        if set(z.namelist()) != {'audit.csv', 'audit.jsonl', 'manifest.json'}:
            raise ArchiveError('Unexpected archive contents.')
        manifest = json.loads(z.read('manifest.json'))
        if manifest.get('schema_version') != 1 or manifest.get('table') != 'public.coach_audit':
            raise ArchiveError('Unexpected archive version or source table.')
        for name, key in [('audit.csv', 'csv_sha256'), ('audit.jsonl', 'jsonl_sha256')]:
            with z.open(name) as f:
                actual = hashlib.file_digest(f, 'sha256').hexdigest()
            if actual != manifest[key]:
                raise ArchiveError(f'Checksum mismatch for {name}.')
        count, first, last = 0, None, None
        with z.open('audit.jsonl') as f:
            for line in f:
                record = json.loads(line)
                if last is not None and record['id'] <= last:
                    raise ArchiveError('Duplicate or out-of-order audit IDs.')
                if first is None:
                    first = record['id']
                last = record['id']
                count += 1
        if (count, first, last) != (manifest['row_count'], manifest['first_id'], manifest['last_id']):
            raise ArchiveError('Archive record count or ID range does not match.')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archive')
    args = parser.parse_args()
    result = verify(args.archive)
    print(f"Verified {result['row_count']} audit records for {result['month_sast']}.")
