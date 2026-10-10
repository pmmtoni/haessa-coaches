"""Consistent, filesystem-safe download names in South African time."""
import re
from datetime import datetime, timedelta, timezone


def filename_part(value, fallback):
    return re.sub(r'[^a-zA-Z0-9._-]+', '_', str(value or '')).strip('._-')[:80] or fallback


def download_filename(report, coach_number, username, extension, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = now.astimezone(timezone(timedelta(hours=2))).strftime('%Y%m%d_%H%M%S_SAST')
    return '_'.join([
        filename_part(report, 'report'),
        filename_part(coach_number, 'all-coaches'),
        stamp,
        filename_part(username, 'user'),
    ]) + '.' + filename_part(extension, 'csv')
