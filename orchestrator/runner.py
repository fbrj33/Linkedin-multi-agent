from __future__ import annotations

"""
The only module scheduling/scheduler.py is allowed to call into (besides
orchestrator/inbox.py, which only calls back into this one). Starts,
resumes, and reconciles graph threads. No job-level code outside this
module should touch a Post row, an agent, or a Publisher directly — that
all lives inside the graph nodes themselves.
"""

import logging
import time

from langgraph.types import Command

from database.models import Post, SessionLocal
from orchestrator import thread_registry
from orchestrator.plan_graph import get_plan_graph
from orchestrator.post_graph import get_post_graph
from orchestrator.state import PlanState, PostState

log = logging.getLogger(__name__)

# Node names too noisy/large to echo the raw state delta for (content bodies,
# hashtags) — logged as a bare "done" instead of the full return value.
_QUIET_NODES = {"generate_content", "refine_content", "load"}

# Maps the node a post thread is paused *about to run* to the projection
# status reconcile_on_startup should force Post.status to, when the two
# disagree. Anything not listed here (e.g. "load", already covered by the
# node itself) is left alone.
_NODE_TO_PROJECTED_STATUS = {
    "generate_content": "generating",
    "score_content": "generating",
    "refine_content": "generating",
    "notify_post_approval": "generating",
    "post_approval": "pending_approval",
    "wait_for_slot": "approved",
    "publish": "approved",
    "verify_publish": "approved",
}

POST_THREAD_DELAY_SECONDS = 4


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _mark_post_failed(post_id: int | None) -> None:
    if post_id is None:
        return
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if post is not None:
            post.status = "failed"
            db.commit()
    finally:
        db.close()


def _record_graph_state(thread_id: str, thread_type: str, graph, *, post_id=None, month=None) -> None:
    """Reads back get_state() right after a run and mirrors it into
    graph_thread — the one place every entry point below updates the
    operator-facing projection, so it can never drift from what just
    actually happened."""
    snapshot = graph.get_state(_config(thread_id))
    # A pending `next` node is normal work, not necessarily an external
    # LangGraph interrupt. Only an actual interrupt payload can be resumed
    # with Command(resume=...).
    if snapshot.interrupts:
        payload = snapshot.interrupts[0].value if snapshot.interrupts else None
        thread_registry.upsert_thread(
            thread_id, thread_type, "interrupted",
            current_node=snapshot.next[0], interrupt_payload=payload,
            post_id=post_id, month=month,
        )
    elif snapshot.next:
        thread_registry.upsert_thread(
            thread_id, thread_type, "running",
            current_node=snapshot.next[0],
            post_id=post_id, month=month,
        )
    else:
        thread_registry.upsert_thread(
            thread_id, thread_type, "done",
            current_node=None, post_id=post_id, month=month,
        )


def _stream_and_log(graph, thread_id: str, run_input) -> None:
    """Runs the graph via .stream(..., stream_mode="updates") instead of a
    single blocking .invoke() call, logging each node as it completes —
    real-time visibility into what the orchestrator is actually doing,
    rather than a silent block until the next interrupt. Content-bearing
    nodes (generate_content, refine_content, load) log a bare "done" instead
    of echoing their full return value.
    """
    for chunk in graph.stream(run_input, _config(thread_id), stream_mode="updates"):
        if "__interrupt__" in chunk:
            payload = chunk["__interrupt__"][0].value if chunk["__interrupt__"] else None
            log.info("[%s] INTERRUPTED — waiting on: %s", thread_id, payload)
            continue
        for node_name, result in chunk.items():
            if node_name in _QUIET_NODES:
                log.info("[%s] %s — done", thread_id, node_name)
            else:
                log.info("[%s] %s — done: %s", thread_id, node_name, result)


def start_plan_thread(month: str) -> None:
    thread_id = f"plan-{month}"
    existing = thread_registry.get_thread(thread_id)
    if existing is not None and existing["status"] != "done":
        log.warning(
            "start_plan_thread: %s is already '%s' (plan_id in payload: %s) — refusing to start a "
            "second run, which would silently overwrite the in-flight one's checkpoint state. "
            "Resolve or let the existing thread finish first.",
            thread_id, existing["status"], (existing.get("interrupt_payload") or {}).get("plan_id"),
        )
        return

    graph = get_plan_graph()
    thread_registry.upsert_thread(thread_id, "plan", "running", month=month)
    log.info("[%s] starting...", thread_id)
    try:
        _stream_and_log(graph, thread_id, PlanState(month=month))
        _record_graph_state(thread_id, "plan", graph, month=month)
    except Exception:
        log.exception("[%s] plan thread failed", thread_id)
        thread_registry.upsert_thread(thread_id, "plan", "failed", current_node="error", month=month)


def start_post_thread(post_id: int) -> None:
    thread_id = f"post-{post_id}"
    existing = thread_registry.get_thread(thread_id)
    if existing is not None and existing["status"] != "done":
        log.warning(
            "start_post_thread: %s is already '%s' — refusing to start a second run",
            thread_id, existing["status"],
        )
        return

    thread_registry.upsert_thread(thread_id, "post", "running", post_id=post_id)
    log.info("[%s] starting...", thread_id)
    try:
        graph = get_post_graph()
        _stream_and_log(graph, thread_id, PostState(post_id=post_id))
        _record_graph_state(thread_id, "post", graph, post_id=post_id)
    except Exception:
        log.exception("[%s] post thread failed", thread_id)
        _mark_post_failed(post_id)
        thread_registry.upsert_thread(thread_id, "post", "failed", current_node="error", post_id=post_id)


def start_post_threads(post_ids: list[int]) -> None:
    for index, post_id in enumerate(post_ids):
        if index:
            log.info("Waiting %ss before starting post %s", POST_THREAD_DELAY_SECONDS, post_id)
            time.sleep(POST_THREAD_DELAY_SECONDS)
        start_post_thread(post_id)


def resume_thread(thread_id: str, resume_value) -> bool:
    """Idempotent: resuming a thread that isn't currently interrupted (a
    replayed email, a double-fired sweep) is a no-op that returns True —
    the same "replay must be a no-op, not a failure" guarantee the retired
    resolve_decision() made, now enforced by checking graph_thread before
    ever touching the graph."""
    thread = thread_registry.get_thread(thread_id)
    if thread is None:
        log.warning("resume_thread: unknown thread %s", thread_id)
        return False
    if thread["status"] == "done":
        log.info(
            "resume_thread: %s is '%s', not interrupted — treating as already-handled",
            thread_id, thread["status"],
        )
        return True

    if thread["status"] != "interrupted":
        log.warning("resume_thread: %s is '%s', not waiting for external input", thread_id, thread["status"])
        return False

    thread_type = thread["thread_type"]
    graph = get_plan_graph() if thread_type == "plan" else get_post_graph()

    log.info("[%s] resuming with %s...", thread_id, resume_value)
    try:
        _stream_and_log(graph, thread_id, Command(resume=resume_value))
        _record_graph_state(
            thread_id, thread_type, graph,
            post_id=thread.get("post_id"), month=thread.get("month"),
        )
        return True
    except Exception:
        log.exception("[%s] resume failed", thread_id)
        _mark_post_failed(thread.get("post_id"))
        thread_registry.upsert_thread(
            thread_id, thread_type, "failed", current_node="error",
            post_id=thread.get("post_id"), month=thread.get("month"),
        )
        return False


def recover_running_threads() -> int:
    """Continue work checkpointed between ordinary graph nodes.

    Unlike approval and schedule waits, these threads have no interrupt, so
    they must be invoked with ``None`` rather than ``Command(resume=...)``.
    """
    recovered = 0
    for thread in thread_registry.list_non_terminal():
        if thread["status"] != "running":
            continue
        thread_id = thread["thread_id"]
        graph = get_plan_graph() if thread["thread_type"] == "plan" else get_post_graph()
        try:
            log.info("[%s] recovering normal work at %s...", thread_id, thread.get("current_node"))
            _stream_and_log(graph, thread_id, None)
            _record_graph_state(
                thread_id, thread["thread_type"], graph,
                post_id=thread.get("post_id"), month=thread.get("month"),
            )
            recovered += 1
        except Exception:
            log.exception("[%s] recovery failed", thread_id)
            _mark_post_failed(thread.get("post_id"))
            thread_registry.upsert_thread(
                thread_id, thread["thread_type"], "failed", current_node="error",
                post_id=thread.get("post_id"), month=thread.get("month"),
            )
    return recovered


# Post fields, besides status, that the checkpoint's state dict is
# authoritative over — if a node ever returns one of these into state
# without another node persisting it to the row (exactly the bug that
# shipped: generate_content/refine_content returned "content" into state,
# nothing wrote it to Post.content), reconcile repairs it here instead of
# leaving that class of bug permanently invisible until the next crash.
_STATE_KEY_TO_POST_COLUMN = {
    "content": "content",
    "hashtags": "hashtags",
    "predicted_score": "score",
    "score_reason": "score_reason",
}


def reconcile_on_startup() -> int:
    """For every non-terminal graph_thread row, get_state() and repair the
    Post projection (status, plus content/hashtags/score) to match.
    Returns the count of repairs made and logs a warning for each — every
    one means a node wrote checkpoint state that never made it into the row
    an operator or an email template actually reads."""
    divergences = 0
    for thread in thread_registry.list_non_terminal():
        thread_id = thread["thread_id"]
        graph = get_plan_graph() if thread["thread_type"] == "plan" else get_post_graph()
        try:
            snapshot = graph.get_state(_config(thread_id))
        except Exception:
            log.exception("reconcile_on_startup: get_state failed for %s", thread_id)
            continue

        _record_graph_state(
            thread_id, thread["thread_type"], graph,
            post_id=thread.get("post_id"), month=thread.get("month"),
        )

        if thread["thread_type"] != "post" or thread.get("post_id") is None:
            continue

        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.id == thread["post_id"]).first()
            if post is None:
                continue

            expected_status = _NODE_TO_PROJECTED_STATUS.get(snapshot.next[0]) if snapshot.next else None
            if expected_status is not None and post.status != expected_status:
                log.warning(
                    "reconcile_on_startup: post %s status projection was '%s', checkpoint says '%s' — repairing",
                    post.id, post.status, expected_status,
                )
                post.status = expected_status
                divergences += 1

            for state_key, column in _STATE_KEY_TO_POST_COLUMN.items():
                checkpoint_value = snapshot.values.get(state_key)
                if checkpoint_value is None:
                    continue
                if getattr(post, column) != checkpoint_value:
                    log.warning(
                        "reconcile_on_startup: post %s .%s was out of sync with checkpoint — repairing",
                        post.id, column,
                    )
                    setattr(post, column, checkpoint_value)
                    divergences += 1

            db.commit()
        finally:
            db.close()

    return divergences
