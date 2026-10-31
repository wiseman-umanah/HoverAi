# Legacy SQL migrations (read-only)

These hand-numbered files were the schema history before Alembic. They are kept
for reference only — do not apply them to new databases. Note the clashing
numbers (two `002_*`, two `003_*`), which is why they were replaced (B23).

The schema now lives in `backend/alembic/versions/`. Revision `0001_baseline`
is equivalent to all of these files applied in order.

**Existing databases** that were built from these files (or from the old
`create_all()` at app startup) already have the tables, so do not upgrade them
from scratch. Mark them as being at the baseline once:

```bash
cd backend
uv run alembic stamp 0001_baseline
uv run alembic upgrade head   # applies anything newer than the baseline
```

Databases created by the old startup `create_all()` differ slightly from the
baseline: FKs have no `ON DELETE`, the `query_logs` JSON columns are `json`
rather than `jsonb`, there is no `ix_refresh_tokens_user_id` index, and the
CHECK constraints are missing. `uv run alembic check` lists the differences.
