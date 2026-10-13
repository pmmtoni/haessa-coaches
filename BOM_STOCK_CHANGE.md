# BOM stock workflow

In Stock makes a component available for production without requiring a delivery
date or marking it as a recorded delivery. Out of Stock enables Expected,
Supplier and Actual date inputs. Entering an Actual Delivery Date selects In
Stock and checks Delivered; the server applies the same rule. Dates must be
valid YYYY-MM-DD values. No receipt dates are invented.

Disabled date fields retain their values. To correct a recorded receipt, select
Out of Stock, edit/clear Actual and adjust Delivered, then save. A remaining
Actual date always makes the saved item In Stock and Delivered. Changes are
recorded by the existing before/after coach audit, including stock changes.

The BOM countdown and analytics availability totals include in-stock items.
Delivered remains a separate checkbox and CSV field. Analytics retain their
existing `delivered` filter/key for compatibility, now labelled Available.
Monthly receipt charts still count actual dates only. Existing actual receipt
delays remain visible in dependency scenarios; stock without a receipt date has
zero delivery delay and does not remain pending or unknown.

CSV imports accept optional `stock_status` values In Stock/Out of Stock, show the
result in preview and include the field in export. Older CSVs without the column
infer In Stock from Delivered or Actual, otherwise Out of Stock. Existing records
with neither receipt evidence nor a stock marker cannot be reliably inferred as
in stock; users can mark those as In Stock without entering a receipt date.

## Deployment preparation — requires confirmation

No deployment or production migration has been performed. Back up the intended
database, test this migration on a copy, stop application writes, then run
`python migrations/20261013_bom_stock.py` with the intended `DATABASE_URL` before
starting the updated app. `db.create_all()` does not upgrade existing tables.
The migration supports PostgreSQL and SQLite, adds a constrained stock column,
infers stock from existing Delivered/Actual values, normalizes Delivered where
an Actual date already exists, and audits each initialization. All dates, notes,
quantities and dependency links remain unchanged. Rerunning it is a no-op.

Rollback the code only if necessary; the additive column may stay in the database.
Retain the audit history and backup. New stock decisions cannot be represented
by old code, so restore this version before allowing further stock edits.

## Verification

Run `python -m unittest discover -s tests -v` with the application dependencies.
To repeat the browser checks, install Playwright and its Chromium browser, run
`python tests/render_bom_stock_fixture.py /path/to/stock_fixture.html`, then
`node tests/test_bom_stock_ui.cjs /path/to/stock_fixture.html`. Optionally set
`BROWSER_EXECUTABLE` to an installed Chrome executable. The fixture uses only
an isolated in-memory SQLite database and does not import application startup.
