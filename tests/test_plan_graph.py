from __future__ import annotations

"""
Drives the compiled plan graph through orchestrator.runner. run_planner,
fetch_rss_trends, and both graphs' email sends are mocked; save_plan_to_db
and scheduling.plan_expander.expand_plan run for real against the isolated
test DB, since they're already covered elsewhere and running them for real
here is what actually proves expand_plan spawns genuinely independent,
separately-resumable post threads — the point of this whole rewrite.
"""

from types import SimpleNamespace

from database.models import GraphThread, MonthlyPlan, Post, SessionLocal
from orchestrator import nodes_plan, nodes_post
from orchestrator.runner import resume_thread, start_plan_thread
from publishing.base import PublishResult


def _get_plan(plan_id: int) -> MonthlyPlan:
    db = SessionLocal()
    plan = db.query(MonthlyPlan).filter(MonthlyPlan.id == plan_id).first()
    db.close()
    return plan


def _get_thread(thread_id: str) -> GraphThread | None:
    db = SessionLocal()
    row = db.query(GraphThread).filter(GraphThread.thread_id == thread_id).first()
    db.close()
    return row


def _posts_for_plan(plan_id: int) -> list[Post]:
    db = SessionLocal()
    posts = db.query(Post).filter(Post.plan_id == plan_id).order_by(Post.id).all()
    db.close()
    return posts


def _thread_plan_id(thread) -> int:
    import json
    return json.loads(thread.interrupt_payload)["plan_id"]


def _fake_plan(month: str, n: int) -> dict:
    return {
        "month": month,
        "posts": [
            {
                "id": i + 1,
                "theme": f"Theme {i + 1}",
                "format": "texte",
                "scheduled_date": f"2026-08-{10 + i:02d}",
                "scheduled_time": "09:00",
                "brief": f"brief {i + 1}",
            }
            for i in range(n)
        ],
    }


def _patch_common(monkeypatch, *, n_posts=2):
    monkeypatch.setattr(nodes_plan, "fetch_rss_trends", lambda: [])
    monkeypatch.setattr(nodes_plan, "run_planner", lambda month, analytics_report=None: _fake_plan(month, n_posts))

    plan_emails = []
    monkeypatch.setattr(
        nodes_plan, "send_email",
        lambda subject, body, to_addr=None: (plan_emails.append(subject), True)[1],
    )

    # Post threads spawned by expand_plan need their own mocks — same
    # pattern as tests/test_post_graph.py.
    monkeypatch.setattr(nodes_post, "run_content", lambda pb, retry_feedback=None: {"content": "body", "hashtags": ["#x"]})
    monkeypatch.setattr(
        nodes_post, "get_llm",
        lambda role=None: SimpleNamespace(complete_json=lambda p, **kw: ({"score": 9.0}, SimpleNamespace(ok=True, error=None))),
    )
    post_emails = []
    monkeypatch.setattr(
        nodes_post, "send_email",
        lambda subject, body, to_addr=None: (post_emails.append(subject), True)[1],
    )
    monkeypatch.setattr(
        nodes_post, "get_publisher",
        lambda: SimpleNamespace(publish=lambda post: PublishResult(ok=True), healthcheck=lambda: PublishResult(ok=True)),
    )

    return SimpleNamespace(plan_emails=plan_emails, post_emails=post_emails)


def test_plan_approval_spawns_independently_resumable_post_threads(monkeypatch):
    fakes = _patch_common(monkeypatch, n_posts=2)

    start_plan_thread("2026-08")
    thread_id = "plan-2026-08"
    thread = _get_thread(thread_id)
    assert thread.status == "interrupted"
    assert thread.current_node == "plan_approval"
    assert len(fakes.plan_emails) == 1

    plan_id = _thread_plan_id(thread)
    resume_thread(thread_id, {"decision": "approved", "reason": None})

    plan_thread = _get_thread(thread_id)
    assert plan_thread.status == "done"  # plan graph itself ends at expand_plan -> END

    posts = _posts_for_plan(plan_id)
    assert len(posts) == 2

    post_thread_ids = [f"post-{post.id}" for post in posts]
    post_threads = [_get_thread(tid) for tid in post_thread_ids]
    assert all(t is not None and t.status == "interrupted" and t.current_node == "post_approval" for t in post_threads)

    # The independence proof: resolve ONLY the first post's approval and
    # confirm the second is completely untouched by it.
    resume_thread(post_thread_ids[0], {"decision": "approved", "reason": None})

    first_after = _get_thread(post_thread_ids[0])
    second_after = _get_thread(post_thread_ids[1])
    assert first_after.current_node == "wait_for_slot"
    assert second_after.current_node == "post_approval"  # unchanged — never blocked by the first


def test_starting_a_plan_thread_twice_for_the_same_month_does_not_clobber_the_first(monkeypatch):
    """Regression test: thread_id is derived from month alone
    (f"plan-{month}"), so calling start_plan_thread for the same month
    twice used to silently overwrite the first run's checkpoint — orphaning
    its approval_token, since graph_thread only remembers the latest
    interrupt payload. start_plan_thread must now refuse the second call
    outright instead."""
    _patch_common(monkeypatch, n_posts=1)

    start_plan_thread("2026-11")
    thread_id = "plan-2026-11"
    first_thread = _get_thread(thread_id)
    first_plan_id = _thread_plan_id(first_thread)

    start_plan_thread("2026-11")  # second call for the same month — must be refused

    thread_after = _get_thread(thread_id)
    assert _thread_plan_id(thread_after) == first_plan_id  # unchanged, not clobbered
    assert thread_after.status == "interrupted"
    assert thread_after.current_node == "plan_approval"

    # Only one MonthlyPlan row exists for this month — the second call never
    # ran the graph at all, so planning_agent never created a second one.
    db = SessionLocal()
    plans_for_month = db.query(MonthlyPlan).filter(MonthlyPlan.month == "2026-11").all()
    db.close()
    assert len(plans_for_month) == 1


def test_plan_rejection_does_not_expand(monkeypatch):
    fakes = _patch_common(monkeypatch, n_posts=2)

    start_plan_thread("2026-09")
    thread_id = "plan-2026-09"
    thread = _get_thread(thread_id)
    plan_id = _thread_plan_id(thread)

    resume_thread(thread_id, {"decision": "rejected", "reason": "not this month"})

    plan = _get_plan(plan_id)
    assert plan.status == "rejected"
    assert _posts_for_plan(plan_id) == []

    thread_after = _get_thread(thread_id)
    assert thread_after.status == "done"


def test_plan_approval_email_sent_exactly_once_across_resumes(monkeypatch):
    fakes = _patch_common(monkeypatch, n_posts=1)

    start_plan_thread("2026-10")
    assert len(fakes.plan_emails) == 1

    thread_id = "plan-2026-10"
    resume_thread(thread_id, {"decision": "approved", "reason": None})
    assert len(fakes.plan_emails) == 1

    # Replay: already terminal, must be a no-op per resume_thread's guard.
    assert resume_thread(thread_id, {"decision": "approved", "reason": None}) is True
    assert len(fakes.plan_emails) == 1
