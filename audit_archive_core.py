"""Pure archive/retention logic. No network or database side effects."""
import calendar
import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

SAST = timezone(timedelta(hours=2))
FIELDS = ('id', 'coach_id', 'coach_number', 'action', 'changed_by', 'details', 'created_at')


class ArchiveError(RuntimeError):
    pass


def boundaries(now):
    """Use calendar months and South African time; DB timestamps are naive UTC."""
    if now.tzinfo is None:
        raise ValueError('An aware server timestamp is required.')
    local = now.astimezone(SAST)
    completed = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year = local.year - 1
    cutoff = local.replace(year=year, day=min(local.day, calendar.monthrange(year, local.month)[1]))
    utc_naive = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)
    return utc_naive(completed), utc_naive(cutoff)


def month_bounds(month):
    start = datetime.strptime(month, '%Y-%m').replace(tzinfo=SAST)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return (start.astimezone(timezone.utc).replace(tzinfo=None),
            end.astimezone(timezone.utc).replace(tzinfo=None))


def normalized_row(row):
    result = {key: row[key] for key in FIELDS}
    timestamp = result['created_at']
    if timestamp is None:
        raise ArchiveError(f"Audit {row['id']} has no timestamp; it cannot be archived automatically.")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    result['created_at'] = timestamp.astimezone(timezone.utc).isoformat(timespec='microseconds')
    return result


def json_line(row):
    return (json.dumps(normalized_row(row), ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')


def safe_cell(value):
    value = '' if value is None else str(value)
    if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


def make_archive(month, rows):
    if not rows:
        raise ArchiveError('Refusing to create an empty archive.')
    ids = [r['id'] for r in rows]
    if ids != sorted(set(ids)):
        raise ArchiveError('Archive rows must have distinct, increasing IDs.')
    start, end = month_bounds(month)
    for r in rows:
        if r['created_at'] is None or not start <= r['created_at'] < end:
            raise ArchiveError('Row is outside the archive month.')
    raw = b''.join(json_line(r) for r in rows)
    csv_buffer = io.StringIO(newline='')
    writer = csv.writer(csv_buffer)
    writer.writerow(['Audit ID', 'Date (SAST / UTC+2)', 'Date (UTC)', 'Coach ID',
                     'Coach Number', 'Action', 'Changed By', 'Details'])
    for row in rows:
        timestamp = row['created_at'].replace(tzinfo=timezone.utc)
        writer.writerow([safe_cell(v) for v in (
            row['id'], timestamp.astimezone(SAST).isoformat(timespec='microseconds'),
            timestamp.isoformat(timespec='microseconds'), row['coach_id'], row['coach_number'],
            row['action'], row['changed_by'], row['details'])])
    csv_bytes = ('\ufeff' + csv_buffer.getvalue()).encode('utf-8')
    manifest = {
        'schema_version': 1, 'table': 'public.coach_audit', 'month_sast': month,
        'row_count': len(rows), 'first_id': ids[0], 'last_id': ids[-1],
        'csv_sha256': hashlib.sha256(csv_bytes).hexdigest(),
        'jsonl_sha256': hashlib.sha256(raw).hexdigest(),
        'retention_months': 12,
        'notes': 'CSV is spreadsheet-safe. audit.jsonl preserves exact values and nulls for recovery.',
    }
    buf = io.BytesIO()
    # Stable ZIP metadata makes retries produce an identical content hash.
    with zipfile.ZipFile(buf, 'w') as archive:
        for name, content in [('audit.csv', csv_bytes), ('audit.jsonl', raw),
                              ('manifest.json', json.dumps(manifest, sort_keys=True, indent=2).encode())]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    data = buf.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    name = f'coach-audit-{month}-{ids[0]}-{ids[-1]}-{digest[:16]}.zip'
    return name, data, digest


def archive_then_cleanup(store, repository, month, rows, cutoff, cleanup):
    """Caller holds row locks and owns the DB transaction until this returns."""
    name, data, digest = make_archive(month, rows)
    file_id = store.put_verified(name, data, digest)
    # put_verified must download and hash the remote bytes before returning.
    if not file_id:
        raise ArchiveError('Archive verification did not return a file ID.')
    eligible = [r['id'] for r in rows if r['created_at'] < cutoff]
    deleted = repository.delete_exact(eligible, cutoff) if cleanup and eligible else 0
    if cleanup and deleted != len(eligible):
        raise ArchiveError('Deletion count differed from the verified batch; roll back this batch.')
    return {'file_id': file_id, 'file_name': name, 'archived': len(rows), 'deleted': deleted}
