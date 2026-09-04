from __future__ import annotations

"""
Plan-graph node functions: collect trends and past performance, run the
planner, wait for a human decision on the plan, then spawn one independent
post thread per planned item.
"""

import datetime
import json
import logging

from langgraph.types import interrupt

from agents.planner_agent import fetch_rss_trends, run_planner, save_plan_to_db
from api.email_service import load_template, render_template, send_email
from database.models import Analytics, MonthlyPlan, MonthlyReport, SessionLocal
from orchestrator.state import PlanState

log = logging.getLogger(__name__)


def collect_trends(state: PlanState) -> dict:
    return {"trends": fetch_rss_trends()}


def performance_brief(state: PlanState) -> dict:
    """Minimal placeholder: reads whatever Analytics/MonthlyReport data
    already exists (there is none yet — nothing in this codebase collects
    real LinkedIn engagement data, that's a separate unbuilt feature) into
    the {top_themes, best_format, avg_engagement} shape run_planner()
    already accepts. Proves the wiring without inventing data collection.
    """
    db = SessionLocal()
    try:
        report = (
            db.query(MonthlyReport)
            .filter(MonthlyReport.month == state["month"])
            .order_by(MonthlyReport.id.desc())
            .first()
        )
        if report and report.report_json:
            try:
                return {"performance_brief": json.loads(report.report_json)}
            except json.JSONDecodeError:
                log.warning("performance_brief: MonthlyReport %s has invalid report_json", report.id)

        analytics_rows = db.query(Analytics).all()
        if not analytics_rows:
            return {"performance_brief": {}}

        avg_engagement = sum(
            (row.likes or 0) + (row.comments or 0) + (row.shares or 0) for row in analytics_rows
        ) / len(analytics_rows)
        return {
            "performance_brief": {
                "top_themes": [],
                "best_format": "texte",
                "avg_engagement": round(avg_engagement, 1),
            }
        }
    finally:
        db.close()


def planning_agent(state: PlanState) -> dict:
    plan = run_planner(state["month"], analytics_report=state.get("performance_brief") or None)
    db_plan = save_plan_to_db(plan)
    return {
        "plan_id": db_plan.id,
        "approval_token": db_plan.approval_token,
        "deadline": db_plan.deadline.isoformat() if db_plan.deadline else None,
    }


def notify_plan_approval(state: PlanState) -> dict:
    db = SessionLocal()
    try:
        plan = db.query(MonthlyPlan).filter(MonthlyPlan.id == state["plan_id"]).first()
        if plan is None:
            return {}
        plan_dict = json.loads(plan.plan_json)
        deadline_label = plan.deadline.strftime("%d/%m/%Y à %H:%M") if plan.deadline else "N/A"
    finally:
        db.close()

    posts_rows = ""
    for post in plan_dict.get("posts", []):
        special = f"⭐ {post.get('special_day')}" if post.get("special_day") else ""
        posts_rows += (
            f"<tr><td>{post.get('scheduled_date')} {post.get('scheduled_time', '')}</td>"
            f"<td>{post.get('theme')} {special}</td><td>{post.get('format')}</td>"
            f"<td>{(post.get('brief') or '')[:80]}...</td></tr>"
        )
    template = load_template("plan_email.html")
    html = render_template(template, {
        "month": plan_dict.get("month", state["month"]),
        "deadline": deadline_label,
        "posts_rows": posts_rows,
    })
    sent = send_email(f"[WIMBEE] Plan LinkedIn {plan_dict.get('month', state['month'])}", html)
    if not sent:
        db = SessionLocal()
        try:
            plan = db.query(MonthlyPlan).filter(MonthlyPlan.id == state["plan_id"]).first()
            if plan is not None:
                plan.status = "notification_failed"
                db.commit()
        finally:
            db.close()
        raise RuntimeError("Plan approval email was not sent; refusing to wait for an unreachable approval")
    return {}


def plan_approval(state: PlanState) -> dict:
    """Its only job is to ask for (and route on) a decision — the email was
    already sent by notify_plan_approval. Re-running this node on resume
    just re-asks interrupt() for the already-recorded value; no side effect
    repeats.
    """
    payload = {
        "plan_id": state["plan_id"],
        "approval_token": state["approval_token"],
        "deadline": state["deadline"],
    }
    resume = interrupt(payload)
    decision = resume.get("decision") if isinstance(resume, dict) else resume

    db = SessionLocal()
    try:
        plan = db.query(MonthlyPlan).filter(MonthlyPlan.id == state["plan_id"]).first()
        if plan is not None:
            plan.status = decision
            plan.decided_at = datetime.datetime.utcnow()
            db.commit()
    finally:
        db.close()

    return {"decision": decision}


def expand_plan(state: PlanState) -> dict:
    """Creates the Post rows (reuses scheduling/plan_expander.py's
    expand_plan() as-is — item-shape handling, date/timezone resolution,
    sanitize_nullish, idempotency all already live there) and spawns one
    independent post thread per row. "Spawns" here means each post thread
    runs synchronously up to its own first interrupt before this node
    returns — after that, each is independently resumable, so one stalled
    approval never blocks another.
    """
    from scheduling.plan_expander import expand_plan as expand_plan_rows

    from orchestrator.runner import start_post_threads

    post_ids = expand_plan_rows(state["plan_id"])
    start_post_threads(post_ids)
    return {"post_ids": post_ids}
