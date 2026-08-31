from __future__ import annotations



import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS graph_thread (
    thread_id         VARCHAR(100) PRIMARY KEY,
    thread_type       VARCHAR(20),
    status            VARCHAR(20),
    current_node      VARCHAR(100),
    post_id           INTEGER,
    month             VARCHAR(7),
    interrupt_payload TEXT,
    reminder_sent_at  DATETIME,
    created_at        DATETIME,
    updated_at        DATETIME
)
"""

# Additive columns for when graph_thread already exists from an earlier run
# of this same migration (CREATE TABLE IF NOT EXISTS won't add a column to
# an existing table) — same PRAGMA table_info pattern as 001.
_NEW_COLUMNS = {
    "reminder_sent_at": "DATETIME",
}

_NEW_INDEXES = [
    ("ix_graph_thread_thread_type", "graph_thread", "thread_type"),
    ("ix_graph_thread_status", "graph_thread", "status"),
    ("ix_graph_thread_post_id", "graph_thread", "post_id"),
]


def run_migration(engine: Engine | None = None) -> None:
    if engine is None:
        from database.models import engine as default_engine
        engine = default_engine

    with engine.connect() as conn:
        log.info("Ensuring table graph_thread exists")
        conn.exec_driver_sql(_CREATE_TABLE)

        existing_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(graph_thread)").fetchall()}
        for column_name, column_type in _NEW_COLUMNS.items():
            if column_name in existing_cols:
                log.info("Skipping graph_thread.%s — already present", column_name)
                continue
            log.info("Adding column graph_thread.%s (%s)", column_name, column_type)
            conn.exec_driver_sql(f"ALTER TABLE graph_thread ADD COLUMN {column_name} {column_type}")

        for index_name, table, column in _NEW_INDEXES:
            log.info("Ensuring index %s on %s(%s)", index_name, table, column)
            conn.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})"
            )

        conn.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_migration()
    print("Migration 002_graph_thread applied.")
