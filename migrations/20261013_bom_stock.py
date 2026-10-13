"""Explicit, repeatable stock migration. Never run by application startup."""
import os
from datetime import datetime
from sqlalchemy import create_engine, inspect, text


def migrate(engine):
    with engine.begin() as connection:
        # SQLite needs an explicit transaction for transactional DDL.
        if connection.dialect.name == 'sqlite':
            connection.exec_driver_sql('BEGIN')
        if 'stock_status' in {c['name'] for c in inspect(connection).get_columns('coach_bom_item')}:
            return 0
        connection.execute(text("""ALTER TABLE coach_bom_item ADD COLUMN stock_status
            VARCHAR(12) NOT NULL DEFAULT 'Out of Stock'
            CONSTRAINT ck_bom_stock_status CHECK (stock_status IN ('In Stock', 'Out of Stock'))"""))
        rows = connection.execute(text('''SELECT b.id, b.coach_id, b.delivered,
            b.actual_delivery_date, c.coach_number FROM coach_bom_item b
            JOIN coach c ON c.id = b.coach_id''')).mappings().all()
        for row in rows:
            status = 'In Stock' if row['delivered'] or row['actual_delivery_date'] else 'Out of Stock'
            delivered = bool(row['delivered'] or row['actual_delivery_date'])
            connection.execute(text('''UPDATE coach_bom_item
                SET stock_status=:status, delivered=:delivered WHERE id=:id'''),
                dict(status=status, delivered=delivered, id=row['id']))
            details = f"BOM item #{row['id']}: Stock status initialized -> {status}"
            if delivered != bool(row['delivered']):
                details += ' | Delivered: No -> Yes (existing actual delivery date)'
            connection.execute(text('''INSERT INTO coach_audit
                (coach_id, coach_number, action, changed_by, details, created_at)
                VALUES (:coach_id, :coach_number, :action, :actor, :details, :created_at)'''),
                dict(coach_id=row['coach_id'], coach_number=row['coach_number'],
                     action='bom_stock_migrated', actor='migration:20261013',
                     details=details, created_at=datetime.utcnow()))
        return len(rows)


if __name__ == '__main__':
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise SystemExit('Set DATABASE_URL explicitly. No default database will be modified.')
    if url.startswith(('postgres://', 'postgresql://')):
        url = 'postgresql+psycopg://' + url.split('://', 1)[1]
    print(f'Migrated {migrate(create_engine(url))} BOM items.')
