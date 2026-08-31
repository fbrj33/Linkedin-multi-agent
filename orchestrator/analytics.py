from __future__ import annotations

"""
Minimal placeholder for the "analytics" thread named in the Phase 6 spec's
thread-naming scheme (plan-{month} / post-{post_id} / analytics).

Deliberately small: there is no existing feature anywhere in this codebase
that collects real LinkedIn engagement data — Analytics/MonthlyReport are
schema that nothing populates yet. Building that collector is a separate
feature, out of scope here. This module only proves the thread exists and
feeds performance_brief (see orchestrator/nodes_plan.py) something real,
even if that something is currently empty.
"""

import datetime
import json
import logging

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from database.models import Analytics, MonthlyReport, SessionLocal
from orchestrator.checkpointer import get_checkpointer

log = logging.getLogger(__name__)

THREAD_ID = "analytics"


class AnalyticsState(TypedDict, total=False):
    month: str
    snapshot: dict


def snapshot_performance(state: AnalyticsState) -> dict:
    db = SessionLocal()
    try:
        month = state.get("month") or datetime.datetime.utcnow().strftime("%Y-%m")
        rows = db.query(Analytics).all()

        if not rows:
            snapshot = {"top_themes": [], "best_format": "texte", "avg_engagement": 0}
        else:
            avg_engagement = sum(
                (row.likes or 0) + (row.comments or 0) + (row.shares or 0) for row in rows
            ) / len(rows)
            snapshot = {"top_themes": [], "best_format": "texte", "avg_engagement": round(avg_engagement, 1)}

        db.add(MonthlyReport(month=month, report_json=json.dumps(snapshot), created_at=datetime.datetime.utcnow()))
        db.commit()
        return {"snapshot": snapshot}
    finally:
        db.close()


def build_analytics_graph():
    builder = StateGraph(AnalyticsState)
    builder.add_node("snapshot_performance", snapshot_performance)
    builder.add_edge(START, "snapshot_performance")
    builder.add_edge("snapshot_performance", END)
    return builder.compile(checkpointer=get_checkpointer())


_graph = None


def get_analytics_graph():
    global _graph
    if _graph is None:
        _graph = build_analytics_graph()
    return _graph
