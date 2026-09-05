from __future__ import annotations



import datetime
import logging
import os
import time
import uuid
from zoneinfo import ZoneInfo

from langgraph.types import interrupt

from agents.content_agent import generate_carousel_for_post, generate_image_for_post, run_content
from api.email_service import load_template, render_template, send_email
from database.models import Post, SessionLocal
from llm import get_llm
from orchestrator.state import PostState
from publishing.factory import get_publisher

log = logging.getLogger(__name__)


# Read fresh on each call rather than frozen at import — a module-level
# constant sourced from os.getenv() would silently ignore monkeypatch.setenv()
# in tests (same trap noted in publishing/browser_publisher.py). Also lets
# tests shrink the publish backoff to well under a second instead of eating
# 2s/8s/30s of real sleep to exercise the retry path.
def refine_score_threshold() -> float:
    return float(os.getenv("WIMBEE_REFINE_SCORE_THRESHOLD", "6"))


def refine_max_loops() -> int:
    return int(os.getenv("WIMBEE_REFINE_MAX_LOOPS", "2"))


def rejection_retry_limit() -> int:
    return int(os.getenv("WIMBEE_REJECTION_RETRY_LIMIT", "2"))


def publish_retry_limit() -> int:
    return int(os.getenv("WIMBEE_PUBLISH_RETRY_LIMIT", "3"))


def publish_backoff_seconds() -> tuple[float, ...]:
    raw = os.getenv("WIMBEE_PUBLISH_BACKOFF_SECONDS", "2,8,30")
    return tuple(float(part) for part in raw.split(","))


def _local_tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("WIMBEE_TIMEZONE", "Africa/Tunis"))


def _post_brief_from_state(state: PostState) -> dict:
    return {
        "theme": state.get("theme"),
        "format": state.get("format"),
        "scheduled_date": state.get("scheduled_date"),
        "scheduled_time": state.get("scheduled_time"),
        "special_day": state.get("special_day"),
        "trend_source": state.get("trend_source"),
        "trend_article_title": state.get("trend_article_title"),
        "trend_article_url": state.get("trend_article_url"),
        "brief": state.get("brief"),
    }


def _hashtags_str(hashtags) -> str | None:
    if isinstance(hashtags, list):
        return " ".join(hashtags)
    return hashtags


def _generate_image_if_requested(
    post_id: int,
    post_format: str | None,
    post_content: str | None = None,
    image_prompt: str | None = None,
) -> None:
    """Generate a visual as part of the existing content path.
    
    Builds a descriptive prompt from the post content to guide FLUX.1-schnell.
    """
    if (post_format or "").strip().lower() not in {"image", "photo", "carousel", "carrousel"}:
        return
    
    format_name = (post_format or "").strip().lower()
    if format_name in {"carousel", "carrousel"}:
        slides = generate_carousel_for_post(post_id, content=post_content)
        if not slides:
            raise RuntimeError(f"Carousel image generation failed for post {post_id}")
        return
    prompt = image_prompt or (f"Professional LinkedIn post visual for: {post_content[:200]}" if post_content else None)
    if generate_image_for_post(post_id, prompt=prompt) is None:
        raise RuntimeError(f"Image generation failed for post {post_id}")


def load(state: PostState) -> dict:
    """Claims the row before slow work — flips the projection status so a
    2am SQL query shows this post as actively being handled, and pulls the
    plan-time fields into state so later nodes never need to hit the DB
    for them again."""
    post_id = state["post_id"]
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if post is None:
            raise ValueError(f"load: Post {post_id} not found")
        post.status = "generating"
        db.commit()
        return {
            "theme": post.theme,
            "format": post.format,
            "scheduled_date": post.scheduled_date,
            "scheduled_time": post.scheduled_time,
            "special_day": post.special_day,
            "trend_source": post.trend_source,
            "trend_article_title": post.trend_article_title,
            "trend_article_url": post.trend_article_url,
            "brief": post.brief,
            "approval_token": post.approval_token,
            "deadline": post.approval_deadline.isoformat() if post.approval_deadline else None,
            "retry_count": post.retry_count or 0,
            "refine_count": 0,
            "publish_attempt": 0,
        }
    finally:
        db.close()


def _save_content(post_id: int, content: str | None, hashtags: str | None) -> None:
    """generate_content/refine_content's output lives in graph state (which
    is what score_content/notify_post_approval actually need), but
    notify_post_approval's email — and the Post row generally — reads
    Post.content/hashtags from the DB, not graph state. Without this write,
    every post's content column stays permanently NULL even though
    generation genuinely ran (this is exactly the bug that shipped: score
    got written per-post, so scoring clearly saw real content, but nothing
    ever persisted that content to the row the email template reads from).
    """
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if post is not None:
            post.content = content
            post.hashtags = hashtags
            db.commit()
    finally:
        db.close()


def generate_content(state: PostState) -> dict:
    result = run_content(_post_brief_from_state(state))
    content = result.get("content")
    hashtags = _hashtags_str(result.get("hashtags"))
    _save_content(state["post_id"], content, hashtags)
    _generate_image_if_requested(state["post_id"], state.get("format"), content, result.get("image_prompt"))
    return {
        "content": content,
        "hashtags": hashtags,
    }



_SCORE_PROMPT = """Tu es un évaluateur de contenu LinkedIn B2B pour Wimbee (Data/Digital/IA).
Note ce post de 0 à 10 sur : accroche, valeur apportée, clarté, probabilité d'engagement.

POST :
{content}

Réponds en JSON strict, rien d'autre :
{{"score": <0-10>, "reason": "<1-2 phrases ACTIONABLES : quoi changer précisément pour améliorer la note — pas juste ce qui ne va pas>"}}
"""


def score_content(state: PostState) -> dict:
    
    llm = get_llm(role="scoring")
    parsed, response = llm.complete_json(_SCORE_PROMPT.format(content=state.get("content") or ""))

    score = None
    reason = None
    if isinstance(parsed, dict):
        try:
            score = float(parsed.get("score"))
        except (TypeError, ValueError):
            score = None
        reason = parsed.get("reason")

    if score is None:
        log.warning(
            "score_content: could not parse a score for post %s (%s) — passing through without refinement",
            state["post_id"], response.error,
        )
        score = refine_score_threshold()  # a scoring failure must not force a refine loop
        reason = None

    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == state["post_id"]).first()
        if post is not None:
            post.score = score
            post.score_reason = reason
            db.commit()
    finally:
        db.close()

    return {"predicted_score": score, "score_reason": reason}


def refine_content(state: PostState) -> dict:
    
    score = state.get("predicted_score")
    reason = state.get("score_reason")
    previous_content = state.get("content") or ""
    attempt = state.get("refine_count", 0) + 1
    is_last_attempt = attempt >= refine_max_loops()

    feedback_parts = [f"Score prédit : {score}/10 (seuil requis : {refine_score_threshold()}/10)."]
    if reason:
        feedback_parts.append(f"Critique précise de l'évaluateur : {reason}")
    feedback_parts.append(
        "Corrige spécifiquement ce point — ne te contente pas de reformuler légèrement. "
        "Voici la version précédente, à ne PAS reproduire à l'identique ni quasi à l'identique :\n"
        f'"""{previous_content}"""'
    )
    if is_last_attempt:
        feedback_parts.append(
            "C'est la dernière tentative avant envoi à l'admin tel quel — vise un changement "
            "net (angle, accroche ou exemple différent), pas une variation mineure."
        )
    feedback = "\n\n".join(feedback_parts)
    result = run_content(_post_brief_from_state(state), retry_feedback=feedback)
    content = result.get("content")
    hashtags = _hashtags_str(result.get("hashtags"))
    _save_content(state["post_id"], content, hashtags)
    _generate_image_if_requested(state["post_id"], state.get("format"), content, result.get("image_prompt"))
    return {
        "content": content,
        "hashtags": hashtags,
        "refine_count": attempt,
    }




def notify_post_approval(state: PostState) -> dict:
    """Sends the approval email — kept as its own node, separate from
    post_approval's interrupt() call, so a resume never re-sends it (see
    orchestrator/post_graph.py's interrupt-replay note)."""
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == state["post_id"]).first()
        if post is None:
            return {}
        deadline_label = post.approval_deadline.strftime("%d/%m/%Y à %H:%M") if post.approval_deadline else "N/A"
        template = load_template("post_approval_email.html")
        html = render_template(template, {
            "post_id": post.id,
            "scheduled_date": post.scheduled_date,
            "scheduled_time": post.scheduled_time or "",
            "deadline": deadline_label,
            "content": post.content or "",
            "hashtags": post.hashtags or "",
            "approval_token": post.approval_token,
        })
        subject = f"[WIMBEE] Post #{post.id} du {post.scheduled_date} à {post.scheduled_time}"
    finally:
        db.close()

    admin_email = os.getenv("ADMIN_EMAIL", os.getenv("GMAIL_USER", "")).strip()
    
    
    message_id = f"<wimbee-post-{state['post_id']}-{uuid.uuid4()}@wimbee.local>"
    
    sent = send_email(subject, html, admin_email, message_id=message_id)
    if not sent:
        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.id == state["post_id"]).first()
            if post is not None:
                post.status = "notification_failed"
                db.commit()
        finally:
            db.close()
        raise RuntimeError("Post approval email was not sent; refusing to wait for an unreachable approval")

    
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == state["post_id"]).first()
        if post is not None:
            post.email_message_id = message_id  
            post.status = "pending_approval"
            db.commit()
    finally:
        db.close()
    return {}


def post_approval(state: PostState) -> dict:
    payload = {
        "post_id": state["post_id"],
        "approval_token": state["approval_token"],
        "deadline": state["deadline"],
    }
    resume = interrupt(payload)
    decision = resume.get("decision") if isinstance(resume, dict) else resume
    reason = resume.get("reason") if isinstance(resume, dict) else None

    update: dict = {"decision": decision, "rejection_reason": reason}

    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == state["post_id"]).first()
        if post is not None:
            if decision == "approved":
                post.status = "approved"
            elif decision == "rejected":
                post.retry_count = (post.retry_count or 0) + 1
                update["retry_count"] = post.retry_count
                post.status = "planned" if post.retry_count <= rejection_retry_limit() else "rejected"
                post.content = None
                post.hashtags = None
            elif decision == "expired":
                post.status = "expired"
            db.commit()
    finally:
        db.close()

    return update


def send_rejection_reply(state: PostState) -> dict:
    regenerated = generate_content(state)

    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == state["post_id"]).first()
        if post is None:
            return {}
        
        
        db.refresh(post)
        original_message_id = post.email_message_id
        if not original_message_id:
            log.warning(f"Post {post.id}: no original Message-ID stored; cannot thread reply")
            return {}
        
        
        deadline_label = post.approval_deadline.strftime("%d/%m/%Y à %H:%M") if post.approval_deadline else "N/A"
        template = load_template("post_approval_email.html")
        html = render_template(template, {
            "post_id": post.id,
            "scheduled_date": post.scheduled_date,
            "scheduled_time": post.scheduled_time or "",
            "deadline": deadline_label,
            "content": regenerated.get("content") or post.content or "",
            "hashtags": regenerated.get("hashtags") or post.hashtags or "",
            "approval_token": post.approval_token,
            "rejection_reason": state.get("rejection_reason", ""),
        })
        
        # Original subject (without "Re: " prefix for reconstruction)
        original_subject = f"[WIMBEE] Post #{post.id} du {post.scheduled_date} à {post.scheduled_time}"
        reply_subject = f"Re: {original_subject}"
        
    finally:
        db.close()

    admin_email = os.getenv("ADMIN_EMAIL", os.getenv("GMAIL_USER", "")).strip()
    
    # Send with threading headers
    sent = send_email(
        subject=reply_subject,
        html_body=html,
        to_addr=admin_email,
        in_reply_to=original_message_id,
        references=original_message_id,
        attachment_path=post.image_path,
    )
    
    if not sent:
        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.id == state["post_id"]).first()
            if post is not None:
                post.status = "reply_send_failed"
                db.commit()
        finally:
            db.close()
        raise RuntimeError(f"Failed to send rejection reply for post {state['post_id']}")

    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == state["post_id"]).first()
        if post is not None:
            post.status = "pending_approval"  # Back to waiting for approval
            db.commit()
    finally:
        db.close()
    
    log.info(f"✓ Regenerated post {state['post_id']} sent as reply in thread")
    return regenerated


def wait_for_slot(state: PostState) -> dict:
    """Interrupts, never sleeps — a sleeping run holds a worker and dies on
    restart; an interrupted one costs nothing and survives. Resumed only by
    the scheduler's slot-sweep once scheduled_time (converted from
    WIMBEE_TIMEZONE local to UTC — see publishing/dispatcher.py's old
    conversion, now inlined here since that module is retired) has passed.
    """
    wake_at_utc = None
    if state.get("scheduled_date"):
        try:
            local_dt = datetime.datetime.strptime(
                f"{state['scheduled_date']} {state.get('scheduled_time') or '09:00'}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=_local_tz())
            wake_at_utc = local_dt.astimezone(datetime.timezone.utc).replace(tzinfo=None).isoformat()
        except ValueError:
            log.error("wait_for_slot: unparseable schedule for post %s", state["post_id"])

    interrupt({"post_id": state["post_id"], "wake_at": wake_at_utc})
    return {}


def publish(state: PostState) -> dict:
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == state["post_id"]).first()
        if post is None:
            return {"published": False, "publish_error": "post not found", "publish_attempt": state.get("publish_attempt", 0) + 1}
        result = get_publisher().publish(post)
    finally:
        db.close()

    return {
        "published": result.ok,
        "needs_human": result.needs_human,
        "external_id": result.external_id,
        "publish_error": result.error,
        "publish_attempt": state.get("publish_attempt", 0) + 1,
    }


def verify_publish(state: PostState) -> dict:
    """Checks the PublishResult from `publish`; on failure, a short REAL
    sleep backoff (2s/8s/30s) before the routing function sends control
    back to `publish` — distinct in kind from wait_for_slot's unbounded
    interrupt-based wait: this is a bounded few-attempt retry of seconds,
    not a wait of unknown (possibly multi-day) length."""
    if state.get("published") or state.get("needs_human"):
        return {}

    retry_limit = publish_retry_limit()
    attempt = state.get("publish_attempt", 0)
    if attempt >= retry_limit:
        return {}

    backoff = publish_backoff_seconds()
    delay = backoff[min(attempt - 1, len(backoff) - 1)]
    log.warning(
        "verify_publish: post %s failed (attempt %d/%d) — retrying in %.2fs: %s",
        state["post_id"], attempt, retry_limit, delay, state.get("publish_error"),
    )
    time.sleep(delay)
    return {}


def finalize(state: PostState) -> dict:
    """Always writes the terminal projection and is the second half of the
    "load/finalize claim the row before slow work and write the
    projection" pair — load claims it, finalize is where every path
    (published, rejected, expired, permanently failed) converges."""
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == state["post_id"]).first()
        if post is None:
            return {}

        if state.get("published"):
            post.status = "published"
            post.published_at = datetime.datetime.utcnow()
            post.external_id = state.get("external_id")
        elif state.get("needs_human"):
            post.status = "manual_publish_required"
            post.publish_error = state.get("publish_error")
        elif state.get("decision") == "expired":
            post.status = "expired"
        elif state.get("decision") == "rejected":
            post.status = "rejected"
        elif state.get("publish_error"):
            post.status = "publish_failed"
            post.publish_error = state.get("publish_error")
        else:
            post.status = "rejected"

        db.commit()
    finally:
        db.close()

    return {}
