# -*- coding: utf-8 -*-
"""
Created on Sun Mar  8 21:04:44 2026

@author: pmmto
"""

from app import app, db
from sqlalchemy import text

with app.app_context():
    columns = [
        "serviceworthy_ncr_gc BOOLEAN DEFAULT FALSE",
        "serviceworthy_certificate BOOLEAN DEFAULT FALSE",
        "retention_ncr_gc BOOLEAN DEFAULT FALSE",
        "retention_certificate BOOLEAN DEFAULT FALSE",
        "invoice_stripping BOOLEAN DEFAULT FALSE",
        "invoice_completion BOOLEAN DEFAULT FALSE",
        "invoice_serviceworthy BOOLEAN DEFAULT FALSE",
        "invoice_retention BOOLEAN DEFAULT FALSE",
        "invoice_current_escalation BOOLEAN DEFAULT FALSE",
    ]

    with db.engine.connect() as conn:
        for col_def in columns:
            col_name = col_def.split()[0]
            try:
                conn.execute(text(f"ALTER TABLE coach ADD COLUMN {col_def}"))
                conn.commit()
                print(f"Added column: {col_name}")
            except Exception as e:
                error_str = str(e)
                if "already exists" in error_str or "duplicate column" in error_str:
                    print(f"Column {col_name} already exists — skipping")
                else:
                    print(f"Error adding {col_name}: {error_str}")