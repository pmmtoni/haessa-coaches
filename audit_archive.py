"""Standalone Render monthly job. Does not import or change the web application."""
import argparse
import json
import os
import sys
from urllib.parse import urlsplit
from audit_archive_core import ArchiveError, FIELDS, boundaries, month_bounds, make_archive, archive_then_cleanup
from audit_drive import DriveStore

PART_LIMIT = 4 * 1024 * 1024  # ZIP files <= 4 MiB; suitable for Drive multipart upload.


def direct_database_url(value):
    url = value.strip().replace('postgresql+psycopg://', 'postgresql://', 1)
    if not url.startswith(('postgresql://', 'postgres://')):
        raise ArchiveError('This maintenance job requires a direct PostgreSQL database URL.')
    host = urlsplit(url).hostname or ''
    if not host or '-pooler.' in host or host.split('.')[0].endswith('-pooler'):
        raise ArchiveError('Use the DIRECT Neon connection string for this job (disable connection pooling in Neon Connect). The web app URL can stay unchanged.')
    return url


class Repository:
    def __init__(self, connection):
        self.conn = connection

    def server_now(self):
        return self.conn.execute('SELECT clock_timestamp() AS now').fetchone()['now']

    def verify_schema(self):
        columns = self.conn.execute("""
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'coach_audit'
        """).fetchall()
        types = {r['column_name']: r['data_type'] for r in columns}
        if set(types) != set(FIELDS) or types.get('created_at') != 'timestamp without time zone':
            raise ArchiveError('The audit schema differs from this archiver. Update the archiver before cleanup; no records were deleted.')

    def months(self, completed_before):
        return [r['month'] for r in self.conn.execute("""
            SELECT DISTINCT to_char(created_at + interval '2 hours', 'YYYY-MM') AS month
            FROM public.coach_audit WHERE created_at < %s ORDER BY month
        """, (completed_before,)).fetchall()]

    def counts(self, completed_before, cutoff):
        return self.conn.execute("""
            SELECT count(*) AS total,
                count(*) FILTER (WHERE created_at < %s) AS completed_month_records,
                count(*) FILTER (WHERE created_at < %s) AS older_than_12_months,
                count(*) FILTER (WHERE created_at IS NULL) AS missing_timestamps
            FROM public.coach_audit
        """, (completed_before, cutoff)).fetchone()

    def batch(self, month, after_id):
        start, end = month_bounds(month)
        rows = self.conn.execute("""
            SELECT id, coach_id, coach_number, action, changed_by, details, created_at
            FROM public.coach_audit
            WHERE created_at >= %s AND created_at < %s AND id > %s
            ORDER BY id LIMIT 1000 FOR UPDATE
        """, (start, end, after_id)).fetchall()
        # Reduce the part before upload if compression still exceeds the limit.
        while rows and len(make_archive(month, rows)[1]) > PART_LIMIT:
            if len(rows) == 1:
                raise ArchiveError(f"Audit {rows[0]['id']} exceeds the archive part limit; it has not been deleted.")
            rows = rows[:max(1, len(rows) // 2)]
        return rows

    def delete_exact(self, ids, cutoff):
        if not ids:
            return 0
        return self.conn.execute("""
            DELETE FROM public.coach_audit
            WHERE id = ANY(%s) AND created_at < %s
        """, (ids, cutoff)).rowcount


def run(connection, store, mode):
    repo = Repository(connection)
    repo.verify_schema()
    completed, cutoff = boundaries(repo.server_now())
    counts = repo.counts(completed, cutoff)
    print(json.dumps({'mode': mode, 'archive_before_utc': completed.isoformat(),
        'cleanup_before_utc': cutoff.isoformat(), **counts}))
    if mode == 'dry-run':
        print('Read-only check complete. No files uploaded and no records deleted.')
        return
    if counts['missing_timestamps']:
        raise ArchiveError('Some audit records have no timestamp. Resolve them before running cleanup.')
    store.check_folder()
    totals = {'archived_or_reverified': 0, 'deleted': 0, 'parts': 0}
    for month in repo.months(completed):
        after_id = 0
        while True:
            # Locks protect the exact row values during upload and verification.
            # A failure rolls back THIS batch; completed batches remain archived.
            with connection.transaction():
                rows = repo.batch(month, after_id)
                if not rows:
                    break
                result = archive_then_cleanup(store, repo, month, rows, cutoff, mode == 'run')
            after_id = rows[-1]['id']
            totals['archived_or_reverified'] += result['archived']
            totals['deleted'] += result['deleted']
            totals['parts'] += 1
            print(json.dumps({'month': month, **result}))
    print(json.dumps({'status': 'complete', **totals}))


def main():
    parser = argparse.ArgumentParser(description='Monthly audit archives with 12-month retention.')
    parser.add_argument('mode', nargs='?', choices=['dry-run', 'archive-only', 'run'], default='dry-run')
    args = parser.parse_args()
    url = os.environ.get('DATABASE_URL', '').strip()
    if not url:
        raise ArchiveError('DATABASE_URL is required; there is no local database fallback.')
    url = direct_database_url(url)
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=15,
                         application_name='cte_audit_archiver') as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute("SET lock_timeout = '10s'")
        conn.execute("SET statement_timeout = '120s'")
        # Never run destructive maintenance concurrently, even across Render jobs.
        locked = conn.execute('SELECT pg_try_advisory_lock(2460924, 12) AS locked').fetchone()['locked']
        if not locked:
            raise ArchiveError('Another archive job is active. This run has not changed any records.')
        try:
            if args.mode == 'dry-run':
                conn.execute('SET default_transaction_read_only = on')
            store = DriveStore.from_env() if args.mode != 'dry-run' else None
            run(conn, store, args.mode)
        finally:
            conn.execute('SELECT pg_advisory_unlock(2460924, 12)')


if __name__ == '__main__':
    try:
        main()
    except ArchiveError as exc:
        print('ARCHIVE STOPPED: ' + str(exc), file=sys.stderr)
        print('The current batch was not committed. Earlier verified batches may have completed.', file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Driver and HTTP exceptions can contain credential-bearing URLs.
        print(f'ARCHIVE STOPPED ({type(exc).__name__}). Check database/network settings. '
              'The current batch was not committed; earlier verified batches may have completed.', file=sys.stderr)
        sys.exit(1)
