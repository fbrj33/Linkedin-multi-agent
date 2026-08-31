from __future__ import annotations



import os
import tempfile

import pytest

_tmp_dir = tempfile.mkdtemp(prefix="wimbee-test-db-")
os.environ.setdefault("WIMBEE_DATABASE_URL", f"sqlite:///{os.path.join(_tmp_dir, 'test.db')}")
os.environ.setdefault("WIMBEE_CHECKPOINT_DB", os.path.join(_tmp_dir, "test_checkpoints.db"))

from database.models import ApprovalRequest, GraphThread, MonthlyPlan, Post, SessionLocal, init_db  # noqa: E402

init_db()


def _wipe_tables() -> None:
    db = SessionLocal()
    try:
        db.query(ApprovalRequest).delete()
        db.query(GraphThread).delete()
        db.query(Post).delete()
        db.query(MonthlyPlan).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _isolated_database(monkeypatch, tmp_path):
    """Every test starts and ends with empty tables, AND its own fresh
    checkpoint DB. The latter matters more than it looks: SQLite reuses
    rowids after a DELETE when a table has no AUTOINCREMENT column (which
    Post/GraphThread don't), so post_id=1 in one test is post_id=1 again in
    the next — and orchestrator/checkpointer.py, plan_graph.py, and
    post_graph.py all cache their checkpointer/compiled-graph objects at
    module level. Without resetting those too, a later test's
    thread_id="post-1" would silently resume the previous test's leftover
    checkpoint state instead of starting fresh.
    """
    _wipe_tables()

    monkeypatch.setenv("WIMBEE_CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))
    from orchestrator.checkpointer import reset_checkpointer
    from orchestrator.plan_graph import reset_plan_graph
    from orchestrator.post_graph import reset_post_graph

    reset_checkpointer()
    reset_plan_graph()
    reset_post_graph()

    yield

    _wipe_tables()
