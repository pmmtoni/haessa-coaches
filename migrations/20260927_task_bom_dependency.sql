-- Run once against the intended Neon database before deploying the app.
-- No existing task or BOM data is changed and no dependencies are inferred.
BEGIN;
CREATE TABLE IF NOT EXISTS task_bom_dependency (
    task_id INTEGER NOT NULL REFERENCES completion_task(id) ON DELETE CASCADE,
    bom_item_id INTEGER NOT NULL REFERENCES coach_bom_item(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, bom_item_id)
);
CREATE INDEX IF NOT EXISTS ix_task_bom_dependency_bom_item_id
    ON task_bom_dependency (bom_item_id);
COMMIT;
