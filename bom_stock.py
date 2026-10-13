"""Shared stock rules for forms, CSV imports and production calculations."""
from datetime import date
import re

IN_STOCK = 'In Stock'
OUT_OF_STOCK = 'Out of Stock'
DATE_FIELDS = ('expected_delivery_date', 'supplier_date', 'actual_delivery_date')


def stock_status(value):
    choices = {IN_STOCK.casefold(): IN_STOCK, OUT_OF_STOCK.casefold(): OUT_OF_STOCK}
    try:
        return choices[str(value).strip().casefold()]
    except KeyError:
        raise ValueError('Stock availability must be In Stock or Out of Stock.') from None


def available(item):
    return bool(getattr(item, 'actual_delivery_date', None)
                or getattr(item, 'stock_status', None) == IN_STOCK)


def form_values(form, item=None):
    """Validate before mutation; omitted/disabled dates preserve history."""
    delivered = 'delivered' in form
    fallback = ((getattr(item, 'stock_status', None) if item else None) or
                (IN_STOCK if delivered else OUT_OF_STOCK))
    status = stock_status(form.get('stock_status', fallback))
    values = {'stock_status': status, 'delivered': delivered}
    for field in DATE_FIELDS:
        old = getattr(item, field, None)
        raw = form.get(field)
        if raw is None or (not raw.strip() and status == IN_STOCK):
            values[field] = old
        elif not raw.strip():
            values[field] = None
        else:
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw.strip()):
                    raise ValueError()
                values[field] = date.fromisoformat(raw.strip())
            except ValueError:
                raise ValueError(f'{field}: use a valid YYYY-MM-DD date.') from None
    if values['actual_delivery_date']:
        values.update(stock_status=IN_STOCK, delivered=True)
    elif delivered and status == OUT_OF_STOCK:
        raise ValueError('A delivered item must be In Stock. Enter its actual delivery date or choose In Stock.')
    return values
