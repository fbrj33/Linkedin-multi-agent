from __future__ import annotations

"""
CRUD over the graph_thread table — the operator-queryable projection of
LangGraph thread state.

Why this exists: the checkpointer is the actual source of truth for a
thread's state, but it's not something a reminder sweep or a dashboard
should query directly (see database/models.py::GraphThread's docstring).
This module is the only thing that writes graph_thread — orchestrator/
runner.py calls it right after every graph.invoke()/get_state() so the
projection can never drift further than "one run behind" the checkpoint.
"""

import datetime
import json
import logging
from typing import Any, Optional

from database.models import GraphThread, SessionLocal

log = logging.getLogger(__name__)


def upsert_thread(
    thread_id: str,
    thread_type: str,
    status: str,
    *,
    current_node: str | None = None,
    interrupt_payload: dict | None = None,
    post_id: int | None = None,
    month: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        row = db.query(GraphThread).filter(GraphThread.thread_id == thread_id).first()
        now = datetime.datetime.utcnow()

        if row is None:
            row = GraphThread(
                thread_id=thread_id,
                thread_type=thread_type,
                status=status,
                current_node=current_node,
                interrupt_payload=json.dumps(interrupt_payload) if interrupt_payload is not None else None,
                post_id=post_id,
                month=month,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        else:
            row.thread_type = thread_type
            row.status = status
            row.current_node = current_node
            # interrupt_payload is only ever meaningful while status ==
            # "interrupted" — clear it on any other status so a resumed/
            # terminal thread doesn't keep showing a stale approval_token.
            row.interrupt_payload = (
                json.dumps(interrupt_payload) if interrupt_payload is not None else None
            )
            # upsert_thread is only called at real state transitions (see
            # orchestrator/runner.py::_record_graph_state) — every call
            # here means the thread just moved, so any reminder sent for
            # whatever it was previously interrupted at is now stale.
            row.reminder_sent_at = None
            if post_id is not None:
                row.post_id = post_id
            if month is not None:
                row.month = month
            row.updated_at = now

        db.commit()
    finally:
        db.close()


def get_thread(thread_id: str) -> Optional[dict]:
    db = SessionLocal()
    try:
        row = db.query(GraphThread).filter(GraphThread.thread_id == thread_id).first()
        return _to_dict(row) if row else None
    finally:
        db.close()


def list_non_terminal() -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.query(GraphThread).filter(GraphThread.status != "done").all()
        return [_to_dict(row) for row in rows]
    finally:
        db.close()


def list_interrupted(thread_type: str | None = None) -> list[dict]:
    db = SessionLocal()
    try:
        query = db.query(GraphThread).filter(GraphThread.status == "interrupted")
        if thread_type:
            query = query.filter(GraphThread.thread_type == thread_type)
        return [_to_dict(row) for row in query.all()]
    finally:
        db.close()


def mark_reminder_sent(thread_id: str) -> None:
    db = SessionLocal()
    try:
        row = db.query(GraphThread).filter(GraphThread.thread_id == thread_id).first()
        if row is not None:
            row.reminder_sent_at = datetime.datetime.utcnow()
            db.commit()
    finally:
        db.close()


def _to_dict(row: GraphThread) -> dict[str, Any]:
    payload = None
    if row.interrupt_payload:
        try:
            payload = json.loads(row.interrupt_payload)
        except json.JSONDecodeError:
            payload = None
    return {
        "thread_id": row.thread_id,
        "thread_type": row.thread_type,
        "status": row.status,
        "current_node": row.current_node,
        "post_id": row.post_id,
        "month": row.month,
        "interrupt_payload": payload,
        "reminder_sent_at": row.reminder_sent_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
