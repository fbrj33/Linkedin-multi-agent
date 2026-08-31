from __future__ import annotations

"""
Provides the LangGraph SQLite checkpointer, in its own database file.

Why a separate file from wimbee.db: SQLite allows exactly one writer per
file at a time. The checkpointer writes on every node transition (far more
often than a Post row changes), and this pipeline runs a scheduler process
driving multiple threads concurrently alongside an operator running ad hoc
queries against wimbee.db — sharing one file would mean an in-flight
publish could block an analytics read, or vice versa. WAL mode (set here,
and on wimbee.db via database.models.init_db()) matters for the same
reason: it lets readers and a writer proceed concurrently instead of
serializing on a single lock.

The import path and constructor API below were verified against the
actually-installed langgraph-checkpoint-sqlite 3.1.0
(langgraph.checkpoint.sqlite.SqliteSaver) via `inspect` on the real source,
and exercised live against a throwaway graph — not taken from memory or
docs. This surface has changed across LangGraph versions; a stale import
path fails loudly at startup, not subtly, which is exactly why it isn't
worth guessing.
"""

import logging
import os
import sqlite3
import threading

from langgraph.checkpoint.sqlite import SqliteSaver

log = logging.getLogger(__name__)

_lock = threading.Lock()
_checkpointer: SqliteSaver | None = None
_checkpointer_db_path: str | None = None


def checkpoint_db_path() -> str:
    return os.getenv("WIMBEE_CHECKPOINT_DB", "wimbee_checkpoints.db")


def get_checkpointer() -> SqliteSaver:
    """Returns a process-wide cached SqliteSaver, opening it (and running
    .setup()) on first use. Cached the same way llm/factory.py and
    publishing/factory.py cache their instances — a fresh sqlite3
    connection per call would defeat WAL's whole point.
    """
    global _checkpointer, _checkpointer_db_path

    db_path = checkpoint_db_path()
    with _lock:
        if _checkpointer is not None and _checkpointer_db_path == db_path:
            return _checkpointer

        # check_same_thread=False: APScheduler resumes threads from its own
        # worker pool, not necessarily the thread that first opened this
        # connection.
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        checkpointer = SqliteSaver(conn)
        checkpointer.setup()

        _checkpointer = checkpointer
        _checkpointer_db_path = db_path
        log.info("Checkpointer ready at %s (WAL)", db_path)
        return _checkpointer


def reset_checkpointer() -> None:
    """Test-only: drop the cached checkpointer so a changed WIMBEE_CHECKPOINT_DB takes effect."""
    global _checkpointer, _checkpointer_db_path
    with _lock:
        _checkpointer = None
        _checkpointer_db_path = None
