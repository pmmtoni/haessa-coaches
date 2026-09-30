"""Daily full CoachAudit snapshot. No app import, schema writes, or retention deletes."""
import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import uuid
import zipfile

from audit_archive_core import ArchiveError, FIELDS

PART_LIMIT = 4 * 1024 * 1024
SELECT = 'SELECT ' + ', '.join(FIELDS) + ' FROM coach_audit ORDER BY id'


@contextmanager
def audit_rows(database_url=None, sqlite_path=None):
    """Hold a consistent, read-only snapshot while spooling local files."""
    if sqlite_path:
        with sqlite3.connect(Path(sqlite_path).resolve().as_uri() + '?mode=ro', uri=True) as conn:
            conn.execute('PRAGMA query_only = ON')
            conn.execute('BEGIN')
            cursor = conn.execute(SELECT)
            yield cursor
        conn.close()
    else:
        if not database_url:
            raise ArchiveError('Set AUDIT_DATABASE_URL, or explicitly use --sqlite for local testing.')
        url = database_url.strip().replace('postgresql+psycopg://', 'postgresql://', 1)
        if not url.startswith(('postgres://', 'postgresql://')):
            raise ArchiveError('AUDIT_DATABASE_URL must be a PostgreSQL connection URL.')
        import psycopg
        with psycopg.connect(url, connect_timeout=30) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute("SET LOCAL search_path = public")
            conn.execute("SET LOCAL statement_timeout = '120s'")
            with conn.cursor(name='daily_audit_snapshot') as cursor:
                cursor.execute(SELECT)
                yield cursor


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def row_values(row):
    return [v.isoformat(timespec='microseconds') if isinstance(v, datetime) else v for v in row]


def create_snapshot(rows, output_root):
    """Each invocation gets a new directory; exclusive creation prevents overwrite."""
    started = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + uuid.uuid4().hex
    folder = Path(output_root) / ('coach-audit-' + run_id)
    folder.mkdir(parents=True, exist_ok=False)
    manifest = dict(version=1, run_id=run_id, started_at_utc=started,
                    source_table='coach_audit', timestamp_convention='UTC',
                    fields=list(FIELDS), rows=0, parts=[])

    def save_part(batch):
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(FIELDS)
        writer.writerows(batch)
        data = zip_bytes({'coach_audit.csv': output.getvalue(),
                          'coach_audit.json': json.dumps(batch, ensure_ascii=False),
                          'run.json': json.dumps({'run_id': run_id, 'fields': list(FIELDS)})})
        if len(data) > PART_LIMIT:
            if len(batch) < 2:
                raise ArchiveError('One audit record exceeds the 4 MiB compressed part limit; backup incomplete.')
            middle = len(batch) // 2
            save_part(batch[:middle])
            save_part(batch[middle:])
            return
        name = f'coach-audit-{run_id}-part-{len(manifest["parts"]) + 1:06d}.zip'
        with (folder / name).open('xb') as file:
            file.write(data)
        manifest['parts'].append(dict(name=name, rows=len(batch), bytes=len(data),
                                      sha256=hashlib.sha256(data).hexdigest()))
        manifest['rows'] += len(batch)

    batch = []
    for row in rows:
        batch.append(row_values(row))
        if len(batch) == 1000:
            save_part(batch)
            batch = []
    if batch or not manifest['parts']:
        save_part(batch)
    manifest['snapshot_finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    # Written only after the entire source query and every part succeeded.
    with (folder / 'manifest.json').open('x', encoding='utf-8') as file:
        json.dump(manifest, file, indent=2)
    return folder


def verify_local(folder):
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    total = 0
    for part in manifest['parts']:
        if Path(part['name']).name != part['name']:
            raise ArchiveError('Invalid part filename.')
        data = (folder / part['name']).read_bytes()
        if len(data) != part['bytes'] or hashlib.sha256(data).hexdigest() != part['sha256']:
            raise ArchiveError('Local part checksum mismatch.')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            values = json.loads(archive.read('coach_audit.json'))
            run = json.loads(archive.read('run.json'))
            if run['run_id'] != manifest['run_id'] or run['fields'] != list(FIELDS):
                raise ArchiveError('Part belongs to another run or schema.')
            if len(values) != part['rows'] or any(len(row) != len(FIELDS) for row in values):
                raise ArchiveError('Part row count or schema mismatch.')
        total += len(values)
    if not manifest['parts'] or total != manifest['rows']:
        raise ArchiveError('Manifest row count mismatch.')
    return manifest


def upload_snapshot(folder, store):
    manifest = verify_local(folder)
    store.check_folder()
    uploaded = []
    for part in manifest['parts']:
        data = (Path(folder) / part['name']).read_bytes()
        file_id = store.put_verified(part['name'], data, part['sha256'])
        uploaded.append(dict(part, drive_file_id=file_id))
    complete = dict(manifest, parts=uploaded, verified_at_utc=datetime.now(timezone.utc).isoformat())
    data = zip_bytes({'manifest.json': json.dumps(complete, indent=2)})
    if len(data) > PART_LIMIT:
        raise ArchiveError('Completion manifest exceeds upload limit; no completion marker uploaded.')
    name = f'coach-audit-{manifest["run_id"]}-COMPLETE.zip'
    file_id = store.put_verified(name, data, hashlib.sha256(data).hexdigest())
    return dict(run_id=manifest['run_id'], rows=manifest['rows'], parts=len(uploaded),
                completion_file_id=file_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', choices=['local', 'drive'], default='drive')
    parser.add_argument('--output-dir', help='Required for local backups; never use ephemeral Render storage as the final destination.')
    parser.add_argument('--sqlite', help='Explicit local SQLite file for testing; never inferred.')
    parser.add_argument('--verify-local', metavar='RUN_FOLDER', help='Verify an existing local run, without connecting to a database.')
    args = parser.parse_args()
    if args.verify_local:
        result = verify_local(args.verify_local)
        print(json.dumps(dict(verified=True, run_id=result['run_id'], rows=result['rows'])))
        return
    if args.destination == 'local' and not args.output_dir:
        parser.error('--output-dir is required with --destination local')
    if args.destination == 'local' and os.environ.get('RENDER'):
        parser.error('Use --destination drive on Render: local cron storage is ephemeral.')
    store = None
    if args.destination == 'drive':
        from audit_drive import DriveStore
        store = DriveStore.from_env()
        store.check_folder()
    with tempfile.TemporaryDirectory(prefix='cte-audit-') as temporary:
        with audit_rows(os.environ.get('AUDIT_DATABASE_URL'), args.sqlite) as rows:
            folder = create_snapshot(rows, args.output_dir if args.destination == 'local' else temporary)
        # Database transaction closes before any uploads.
        if store:
            result = upload_snapshot(folder, store)
        else:
            manifest = verify_local(folder)
            result = dict(run_id=manifest['run_id'], rows=manifest['rows'], folder=str(folder))
        print(json.dumps(dict(status='complete', **result)))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # DB/HTTP exception messages may contain credentials or audit data.
        print(f'Audit backup FAILED ({type(exc).__name__}). Check configuration, connectivity, '
              'schema, storage and permissions. A run requires its COMPLETE marker.', file=sys.stderr)
        sys.exit(1)
