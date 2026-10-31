#!/usr/bin/env python3
"""Database setup script — applies Alembic migrations.

Equivalent to ``uv run alembic upgrade head``: brings the database named by
DATABASE_URL (from .env or the environment) up to the latest schema revision.

The schema is owned by Alembic (``alembic/versions/``). The old hand-written
SQL files in ``migrations/legacy/`` are kept for reference only; a database
that was built from them (or from the old startup ``create_all``) must be
stamped once instead of upgraded:

    uv run alembic stamp 0001_baseline

Usage:
    cd backend/
    uv run setup-db
"""

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def main_sync() -> None:
    """Run ``alembic upgrade head`` using ``backend/alembic.ini``.

    Exits with status 1 and a hint if the upgrade fails.
    """
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    try:
        command.upgrade(cfg, "head")
    except Exception as exc:  # noqa: BLE001 — CLI wrapper: report and exit
        print(f"\n✗ Database migration failed: {exc}")
        print("\nCheck that:")
        print("  1. DATABASE_URL in .env is correct and the database is reachable")
        print("  2. If the tables already exist (pre-Alembic database), run:")
        print("       uv run alembic stamp 0001_baseline")
        sys.exit(1)
    print("Done. Database is at the latest revision.")


if __name__ == "__main__":
    main_sync()
