from __future__ import annotations

"""
Drives the compiled post graph through orchestrator.runner (the same entry
points scheduling/scheduler.py uses), with run_content/get_llm/get_publisher
mocked exactly the way the rest of this test suite already mocks them.
Covers: happy path to finalize (including that generated content actually
persists to Post.content/hashtags, not just graph state — the bug that
shipped), rejection looping with rejection_reason reaching run_content's
retry_feedback and the regenerated content actually reaching a resent email,
rejection past the retry limit, expiry, the score/refine loop, and — the
interrupt-replay hazard specifically — that the approval email is sent
exactly once per thread even across multiple resumes.
"""

import datetime
from types import SimpleNamespace

from database.models import GraphThread, MonthlyPlan, Post, SessionLocal
from orchestrator import nodes_post, runner
from orchestrator.runner import resume_thread, start_post_thread
from publishing.base import PublishResult


def _make_post(**overrides) -> int:
    db = SessionLocal()
    plan = MonthlyPlan(month="2026-08", plan_json="{}", status="approved")
    db.add(plan)
    db.commit()
    db.refresh(plan)

    defaults = dict(
        plan_id=plan.id,
        theme="AI trends",
        format="texte",
        scheduled_date="2020-01-01",  # deliberately in the past — wait_for_slot resumes immediately
        scheduled_time="09:00",
        brief="explain the trend",
        status="planned",
        retry_count=0,
        approval_deadline=datetime.datetime.utcnow() + datetime.timedelta(hours=24),
    )
    defaults.update(overrides)
    post = Post(**defaults)
    db.add(post)
    db.commit()
    db.refresh(post)
    post_id = post.id
    db.close()
    return post_id


def _get_post(post_id: int) -> Post:
    db = SessionLocal()
    post = db.query(Post).filter(Post.id == post_id).first()
    db.close()
    return post


def _get_thread(thread_id: str) -> GraphThread | None:
    db = SessionLocal()
    row = db.query(GraphThread).filter(GraphThread.thread_id == thread_id).first()
    db.close()
    return row


class _FakeLLM:
    def __init__(self, score: float, reason: str = "Manque un chiffre concret et une source."):
        self.score = score
        self.reason = reason
        self.calls = 0

    def complete_json(self, prompt, **kwargs):
        self.calls += 1
        return {"score": self.score, "reason": self.reason}, SimpleNamespace(ok=True, error=None)


def _patch_common(monkeypatch, *, score=9.0, publish_ok=True, publish_error="boom",
                   score_reason="Manque un chiffre concret et une source."):
    generate_calls = []

    def fake_run_content(post_brief, retry_feedback=None):
        generate_calls.append(retry_feedback)
        return {"content": f"Generated body v{len(generate_calls)}", "hashtags": ["#Wimbee", "#Data"]}

    monkeypatch.setattr(nodes_post, "run_content", fake_run_content)
    monkeypatch.setattr(nodes_post, "get_llm", lambda role=None: _FakeLLM(score, score_reason))

    email_calls = []
    monkeypatch.setattr(
        nodes_post, "send_email",
        lambda subject, body, to_addr=None: (email_calls.append((subject, to_addr)), True)[1],
    )

    publish_calls = []

    class _FakePublisher:
        def publish(self, post):
            publish_calls.append(post.id)
            if publish_ok:
                return PublishResult(ok=True, external_id="urn:li:share:999")
            return PublishResult(ok=False, error=publish_error)

        def healthcheck(self):
            return PublishResult(ok=True)

    monkeypatch.setattr(nodes_post, "get_publisher", lambda: _FakePublisher())
    monkeypatch.setenv("WIMBEE_PUBLISH_BACKOFF_SECONDS", "0.01,0.01,0.01")

    return SimpleNamespace(generate_calls=generate_calls, email_calls=email_calls, publish_calls=publish_calls)


def test_happy_path_to_published(monkeypatch):
    fakes = _patch_common(monkeypatch, score=9.0, publish_ok=True)
    post_id = _make_post()
    thread_id = f"post-{post_id}"

    start_post_thread(post_id)
    thread = _get_thread(thread_id)
    assert thread.status == "interrupted"
    assert thread.current_node == "post_approval"

    # Regression: generate_content/refine_content used to return content only
    # into graph state, never persisting it to the row notify_post_approval's
    # email actually reads from — score got written per-post (proving
    # generation really ran), but Post.content stayed permanently NULL.
    post_after_generation = _get_post(post_id)
    assert post_after_generation.content == "Generated body v1"
    assert post_after_generation.hashtags == "#Wimbee #Data"

    resume_thread(thread_id, {"decision": "approved", "reason": None})
    thread = _get_thread(thread_id)
    assert thread.status == "interrupted"
    assert thread.current_node == "wait_for_slot"

    # scheduled_date is in the past, so resuming wait_for_slot with no
    # payload (the scheduler's slot-sweep signal) lets it proceed straight
    # through publish/verify_publish to finalize.
    resume_thread(thread_id, "woken")

    thread = _get_thread(thread_id)
    assert thread.status == "done"

    post = _get_post(post_id)
    assert post.status == "published"
    assert post.external_id == "urn:li:share:999"
    assert fakes.publish_calls == [post_id]
    assert len(fakes.email_calls) == 1  # interrupt-replay check, see test below too


def test_notify_email_sent_exactly_once_across_resumes(monkeypatch):
    """The interrupt-replay hazard, specifically: notify_post_approval runs
    once, post_approval's interrupt() is what's replayed on resume, and
    resuming again after the thread has moved on must not re-trigger the
    email."""
    fakes = _patch_common(monkeypatch, score=9.0, publish_ok=True)
    post_id = _make_post()
    thread_id = f"post-{post_id}"

    start_post_thread(post_id)
    assert len(fakes.email_calls) == 1

    resume_thread(thread_id, {"decision": "approved", "reason": None})
    assert len(fakes.email_calls) == 1

    # Replays: thread is no longer sitting at post_approval, so these must
    # be no-ops per resume_thread's idempotency guard.
    assert resume_thread(thread_id, {"decision": "approved", "reason": None}) is True
    assert len(fakes.email_calls) == 1

    resume_thread(thread_id, "woken")
    assert len(fakes.email_calls) == 1

    assert resume_thread(thread_id, "woken") is True  # thread now terminal — still a no-op
    assert len(fakes.email_calls) == 1


def test_rejection_feeds_reason_into_retry_feedback(monkeypatch):
    fakes = _patch_common(monkeypatch, score=9.0, publish_ok=True)
    post_id = _make_post()
    thread_id = f"post-{post_id}"

    start_post_thread(post_id)
    assert fakes.generate_calls == [None]  # first generation has no feedback yet

    resume_thread(thread_id, {"decision": "rejected", "reason": "too promotional"})

    assert fakes.generate_calls[-1] == "too promotional"
    post = _get_post(post_id)
    assert post.status in ("pending_approval",)  # back through generate->score->notify->pending_approval
    assert post.retry_count == 1
    # The regenerated content actually reached the row (not just state) and
    # a fresh approval email went out with it — this is the "reject with a
    # reason -> content agent redoes it -> resends" loop end to end.
    assert post.content == "Generated body v2"
    assert len(fakes.email_calls) == 2


def test_rejection_past_retry_limit_is_terminal(monkeypatch):
    fakes = _patch_common(monkeypatch, score=9.0, publish_ok=True)
    monkeypatch.setenv("WIMBEE_REJECTION_RETRY_LIMIT", "2")
    post_id = _make_post()
    thread_id = f"post-{post_id}"

    start_post_thread(post_id)
    resume_thread(thread_id, {"decision": "rejected", "reason": "r1"})
    resume_thread(thread_id, {"decision": "rejected", "reason": "r2"})
    resume_thread(thread_id, {"decision": "rejected", "reason": "r3"})

    post = _get_post(post_id)
    assert post.status == "rejected"
    assert post.retry_count == 3
    thread = _get_thread(thread_id)
    assert thread.status == "done"


def test_expiry_is_terminal(monkeypatch):
    _patch_common(monkeypatch, score=9.0, publish_ok=True)
    post_id = _make_post()
    thread_id = f"post-{post_id}"

    start_post_thread(post_id)
    resume_thread(thread_id, {"decision": "expired", "reason": None})

    post = _get_post(post_id)
    assert post.status == "expired"
    thread = _get_thread(thread_id)
    assert thread.status == "done"


def test_low_score_triggers_refine_loop_capped_at_two(monkeypatch):
    reason = "Manque un chiffre concret et une source."
    fakes = _patch_common(monkeypatch, score=2.0, publish_ok=True, score_reason=reason)  # always below threshold
    monkeypatch.setenv("WIMBEE_REFINE_MAX_LOOPS", "2")
    post_id = _make_post()
    thread_id = f"post-{post_id}"

    start_post_thread(post_id)

    # 1 initial generate_content + 2 refine_content calls = 3 total content calls
    assert len(fakes.generate_calls) == 3

    # Regression: refine_content used to send a fixed, generic "improve the
    # hook and value" message on every attempt — which is exactly why a post
    # could score identically across every retry, since the model never
    # learned what was actually wrong or what it had already tried. It now
    # must carry the scorer's specific critique and the literal previous
    # draft, so two consecutive attempts are never fed the same instruction.
    first_refine_feedback = fakes.generate_calls[1]
    second_refine_feedback = fakes.generate_calls[2]

    assert reason in first_refine_feedback
    assert "Generated body v1" in first_refine_feedback  # the draft being replaced, quoted verbatim

    assert reason in second_refine_feedback
    assert "Generated body v2" in second_refine_feedback  # the SECOND draft, not the first again
    assert "dernière tentative" in second_refine_feedback  # escalates on the final attempt
    assert "dernière tentative" not in first_refine_feedback  # ...but not before then

    post = _get_post(post_id)
    assert post.score_reason == reason

    thread = _get_thread(thread_id)
    assert thread.current_node == "post_approval"  # proceeded despite still-low score


def test_publish_failure_retries_then_marks_publish_failed(monkeypatch):
    fakes = _patch_common(monkeypatch, score=9.0, publish_ok=False, publish_error="composer stuck")
    monkeypatch.setenv("WIMBEE_PUBLISH_RETRY_LIMIT", "3")
    post_id = _make_post()
    thread_id = f"post-{post_id}"

    start_post_thread(post_id)
    resume_thread(thread_id, {"decision": "approved", "reason": None})
    resume_thread(thread_id, "woken")  # wakes wait_for_slot -> publish loop runs to exhaustion

    assert len(fakes.publish_calls) == 3
    post = _get_post(post_id)
    assert post.status == "publish_failed"
    assert post.publish_error == "composer stuck"
    thread = _get_thread(thread_id)
    assert thread.status == "done"


def test_reconcile_on_startup_repairs_content_from_checkpoint(monkeypatch):
    """This is the actual mechanism used to repair the live DB after the
    content-persistence bug was found: the checkpoint already had the real
    generated content (score_content needs it to build its prompt, so it
    provably existed), it just never made it into Post.content. Simulates
    that exact divergence directly, without needing the bug itself present,
    so this stays a real regression test rather than one that only fails
    while the original bug exists."""
    from orchestrator.runner import reconcile_on_startup

    _patch_common(monkeypatch, score=9.0, publish_ok=True)
    post_id = _make_post()
    start_post_thread(post_id)

    # Simulate the row having fallen out of sync with checkpoint truth.
    db = SessionLocal()
    post = db.query(Post).filter(Post.id == post_id).first()
    post.content = None
    post.hashtags = None
    db.commit()
    db.close()

    repaired = reconcile_on_startup()
    assert repaired >= 2  # content + hashtags for this post

    post = _get_post(post_id)
    assert post.content == "Generated body v1"
    assert post.hashtags == "#Wimbee #Data"


def test_failed_approval_email_never_leaves_a_post_waiting(monkeypatch):
    """A failed SMTP send used to leave the row pending_approval even though
    nobody had received an email to approve. The graph must fail visibly
    instead of creating an unreachable wait."""
    _patch_common(monkeypatch, score=9.0, publish_ok=True)
    monkeypatch.setattr(nodes_post, "send_email", lambda *args, **kwargs: False)
    post_id = _make_post()

    start_post_thread(post_id)

    post = _get_post(post_id)
    thread = _get_thread(f"post-{post_id}")
    assert post.status == "notification_failed"
    assert thread.status == "failed"


def test_pending_normal_node_is_running_not_interrupted():
    """`StateSnapshot.next` means runnable work; only `interrupts` means
    external input is required. Keeping these distinct makes recovery safe."""
    post_id = _make_post()

    class _Graph:
        def get_state(self, _config):
            return SimpleNamespace(next=("generate_content",), interrupts=())

    runner._record_graph_state(f"post-{post_id}", "post", _Graph(), post_id=post_id)
    thread = _get_thread(f"post-{post_id}")
    assert thread.status == "running"
    assert thread.current_node == "generate_content"
    assert thread.interrupt_payload is None


def test_manual_publisher_stops_at_human_handoff(monkeypatch):
    fakes = _patch_common(monkeypatch, score=9.0, publish_ok=True)
    manual_calls = []
    monkeypatch.setattr(
        nodes_post,
        "get_publisher",
        lambda: SimpleNamespace(
            publish=lambda post: (
                manual_calls.append(post.id),
                PublishResult(ok=False, needs_human=True, error="Post this manually."),
            )[1],
            healthcheck=lambda: PublishResult(ok=True),
        ),
    )
    post_id = _make_post()
    thread_id = f"post-{post_id}"

    start_post_thread(post_id)
    resume_thread(thread_id, {"decision": "approved", "reason": None})
    resume_thread(thread_id, "woken")

    post = _get_post(post_id)
    thread = _get_thread(thread_id)
    assert post.status == "manual_publish_required"
    assert thread.status == "done"
    assert manual_calls == [post_id]
    assert fakes.publish_calls == []  # replaced publisher handled the handoff
