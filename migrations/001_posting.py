from __future__ import annotations

"""
Additive SQLite migration for the plan -> post -> approval -> publish pipeline.

Why this exists: database/models.py already grew several of the columns this
migration was originally scoped to add — post.published_at, post.approval_token,
post.approval_deadline, approval_request.decided_at and
approval_request.rejection_reason all predate this file. Only post.plan_id,
post.external_id and approval_request.reminder_sent_at were actually missing,
plus three indexes. post.brief was added on top of that during Phase 3
(scheduling/plan_expander.py) — content generation there is deferred well
past expansion time, and without persisting the plan item's brief text there
would be nothing left to hand the content agent when generation finally runs.
post.score_reason was added later still, once orchestrator/nodes_post.py's
score_content/refine_content loop existed — the scoring model's specific
critique was being computed and then discarded, which is why refine_content
couldn't act on anything more specific than a generic "try harder" message.
This migration adds exactly those columns — nothing here duplicates or
re-adds a column that already exists.

Safe to run any number of times: every ALTER TABLE is preceded by a
PRAGMA table_info() check (SQLite has no "ADD COLUMN IF NOT EXISTS"), and
index creation uses "CREATE INDEX IF NOT EXISTS", which SQLite already treats
as a no-op on a second run.

Run directly: `python migrations/001_posting.py` (from anywhere — it puts the
project root on sys.path itself, same as scheduler/schedular.py does).
"""

import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# column_name -> SQLite column type, keyed by table.
_NEW_COLUMNS: dict[str, dict[str, str]] = {
    "posts": {
        "plan_id": "INTEGER",
        "external_id": "VARCHAR(255)",
        "brief": "TEXT",
        "score_reason": "TEXT",
    },
    "approval_requests": {
        "reminder_sent_at": "DATETIME",
    },
}

_NEW_INDEXES = [
    ("ix_posts_status", "posts", "status"),
    ("ix_posts_approval_token", "posts", "approval_token"),
    ("ix_posts_plan_id", "posts", "plan_id"),
]


def _existing_columns(conn, table: str) -> set[str]:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def run_migration(engine: Engine | None = None) -> None:
    """Apply every additive column/index this migration owns, skipping what's already there."""
    if engine is None:
        from database.models import engine as default_engine
        engine = default_engine

    with engine.connect() as conn:
        for table, columns in _NEW_COLUMNS.items():
            existing = _existing_columns(conn, table)
            for column_name, column_type in columns.items():
                if column_name in existing:
                    log.info("Skipping %s.%s — already present", table, column_name)
                    continue
                log.info("Adding column %s.%s (%s)", table, column_name, column_type)
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}"
                )

        for index_name, table, column in _NEW_INDEXES:
            log.info("Ensuring index %s on %s(%s)", index_name, table, column)
            conn.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})"
            )

        conn.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_migration()
    print("Migration 001_posting applied.")
