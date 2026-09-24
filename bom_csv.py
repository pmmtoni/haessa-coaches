"""BOM CSV validation shared by preview and confirmed imports (no database writes)."""
import csv
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation

MAX_CSV_BYTES = 256 * 1024
MAX_CSV_ROWS = 2000
STANDARD_SECTIONS = (
    'BUP', 'Electrical', 'Coach Build', 'Paint', 'Plumbing', 'Structural',
    'ET - COACH BUILD', 'ET - ELECTRICAL', 'ET - ART WORK', 'Quality',
    'ET - PLUMBING', 'ET - OTHER',
)
ALIASES = {
    'coach': ('coach_number', 'coach_no', 'coach'),
    'component': ('component', 'material', 'item'),
    'section': ('section',), 'quantity': ('quantity', 'qty'),
    'uom': ('uom', 'unit', 'unit_of_measure'),
    'delivered': ('delivered', 'status'),
    'expected_delivery_date': ('expected_delivery_date', 'expected_date', 'expected'),
    'supplier_date': ('supplier_date', 'supplier date', 'supplierdate'),
    'actual_delivery_date': ('actual_delivery_date', 'actual_date', 'actual'),
    'notes': ('notes', 'note', 'comment'),
}


def section_key(value):
    return ' '.join((value or '').split()).casefold()


def section_catalog(existing=()):
    catalog = {section_key(s): s for s in STANDARD_SECTIONS}
    # Preserve previously used custom sections; use a deterministic spelling.
    for value in sorted({s for s in existing if s}, key=lambda s: (section_key(s), s)):
        clean = ' '.join(value.split())
        if clean:
            catalog.setdefault(section_key(clean), clean)
    return catalog


def canonical_section(value, catalog):
    key = section_key(value)
    if not key:
        return None
    if key not in catalog:
        raise ValueError(f"Unknown section '{value}'. Choose a section from the BOM dropdown.")
    return catalog[key]


def read_csv_upload(file_storage):
    raw = file_storage.read(MAX_CSV_BYTES + 1)
    if isinstance(raw, bytes):
        if len(raw) > MAX_CSV_BYTES:
            raise ValueError('CSV is too large. Split it into files of at most 256 KB.')
        try:
            return raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('Save the file as CSV UTF-8 and upload it again.') from None
    if len(raw.encode('utf-8')) > MAX_CSV_BYTES:
        raise ValueError('CSV is too large. Split it into files of at most 256 KB.')
    return raw.lstrip('\ufeff')


def build_plan(raw, catalog, existing_keys=(), coach_id=None, coaches=None, as_of=None):
    """Return JSON-compatible reviewed values; dates use ISO strings."""
    as_of = as_of or date.today()
    result = {'rows': [], 'errors': [], 'ready': 0, 'duplicates': 0,
              'invalid': 0, 'blank': 0, 'corrected': 0}
    seen = set(existing_keys)
    reader = csv.DictReader(io.StringIO(raw), strict=True)
    try:
        headers = reader.fieldnames
        if not headers:
            raise ValueError('CSV has no header row.')
        normalized = [(h or '').strip().lower() for h in headers]
        if any(not h for h in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError('CSV headers must be non-empty and unique.')
        columns = {}
        for field, aliases in ALIASES.items():
            matches = [headers[i] for i, h in enumerate(normalized) if h in aliases]
            if len(matches) > 1:
                raise ValueError(f"Use only one column for '{field}' (found {', '.join(matches)}).")
            columns[field] = matches[0] if matches else None
        if not columns['component']:
            raise ValueError("CSV must include a 'component' column.")
        if coach_id is None and not columns['coach']:
            raise ValueError("CSV must include a 'coach_number' column.")

        for index, source in enumerate(reader, start=2):
            if index - 1 > MAX_CSV_ROWS:
                raise ValueError('CSV has more than 2,000 rows. Split it into smaller files.')
            row = {'line': index, 'status': 'ready', 'messages': [], 'values': {}}
            result['rows'].append(row)
            def get(field):
                return (source.get(columns[field]) or '').strip() if columns[field] else ''
            values = row['values']
            values.update(component=get('component'), section=get('section'))
            if None in source or any(v is None for v in source.values()):
                row['messages'].append('Column count does not match the header. Check CSV quoting.')
            elif not any(str(v or '').strip() for v in source.values()):
                row['status'] = 'blank'
                result['blank'] += 1
                continue
            elif not values['component']:
                row['messages'].append('Component is required.')
            target = coach_id if coach_id is not None else (coaches or {}).get(get('coach'))
            values['coach_id'] = target
            if target is None:
                row['messages'].append(f"Coach '{get('coach')}' was not found.")
            try:
                values['section'] = canonical_section(get('section'), catalog)
                if get('section') != (values['section'] or ''):
                    row['correction'] = f"Section: {get('section')} → {values['section']}"
            except ValueError as exc:
                row['messages'].append(str(exc))
            key = (target, values['component'].lower(), section_key(values['section']))
            if not row['messages'] and key in seen:
                row['status'] = 'duplicate'
                row['messages'].append('Already exists or repeats an earlier row; will be skipped.')
                result['duplicates'] += 1
                continue

            quantity = get('quantity') or '1'
            try:
                number = Decimal(quantity)
                if not number.is_finite() or number < 1 or number > 2147483647 or number != number.to_integral_value():
                    raise ValueError()
                values['quantity'] = int(number)
            except (InvalidOperation, ValueError):
                row['messages'].append(f"Quantity '{quantity}' must be a positive whole number (blank defaults to 1).")
            truth = get('delivered').lower()
            if truth in ('1', 'true', 'yes', 'y', 'delivered'):
                values['delivered'] = True
            elif truth in ('', '0', 'false', 'no', 'n', 'outstanding', 'pending', 'not delivered'):
                values['delivered'] = False
            else:
                row['messages'].append(f"Delivered/status '{get('delivered')}' must be yes/no, true/false, 1/0, delivered or outstanding.")
            for field in ('expected_delivery_date', 'supplier_date', 'actual_delivery_date'):
                text = get(field)
                values[field] = None
                if text:
                    try:
                        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
                            raise ValueError()
                        values[field] = date.fromisoformat(text).isoformat()
                    except ValueError:
                        row['messages'].append(f"{field} '{text}': use a valid YYYY-MM-DD date.")
            if values.get('delivered') and not get('actual_delivery_date'):
                values['actual_delivery_date'] = as_of.isoformat()
                row['default_note'] = 'Actual delivery defaults to the preview date for a delivered item.'
            values['uom'] = get('uom') or None
            values['notes'] = get('notes') or None
            for field, limit in (('component', 200), ('section', 120), ('uom', 50)):
                if len(values.get(field) or '') > limit:
                    row['messages'].append(f'{field} exceeds {limit} characters.')
            if row['messages']:
                row['status'] = 'invalid'
                result['invalid'] += 1
            else:
                seen.add(key)
                result['ready'] += 1
                if row.get('correction'):
                    result['corrected'] += 1
    except (ValueError, csv.Error) as exc:
        result['errors'].append(str(exc))
    return result
