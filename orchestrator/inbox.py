from __future__ import annotations

"""
Reads Gmail via IMAP, parses APPROVE/REJECT replies, and resumes the owning
graph thread. Kept separate from orchestrator/runner.py so
scheduling/scheduler.py's inbox-poll job has exactly one thing to import.

Which thread a reply belongs to is resolved from the email's SUBJECT line,
not a token in the body — post approval emails always carry "Post #<id>"
and plan approval emails always carry "Plan LinkedIn <month>" (see
nodes_post.py::notify_post_approval / nodes_plan.py::notify_plan_approval),
which map directly onto this codebase's thread_id scheme ("post-{id}" /
"plan-{month}"). This replaces an earlier token-based design: a token in the
body works but is needless friction for a human replying by hand, and the
subject already uniquely identifies the thread every bit as well — arguably
better, since thread_id is ALSO keyed by post_id/month, so the two can never
disagree the way a stored-then-copied token could.
"""

import email
import imaplib
import logging
import os
import re

from orchestrator.runner import resume_thread

log = logging.getLogger(__name__)

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# Matches the two quoted-reply boundary styles this parser has to ignore —
# the instructional email we sent contains the literal words "APPROVE" and
# "REJECT", and if the admin's client quotes it below their reply, a naive
# whole-body search would match that boilerplate instead of the actual
# decision.
_QUOTE_BOUNDARY_RE = re.compile(
    r"\n\s*On\s.+?\swrote:|\n?-{2,}\s*Original Message\s*-{2,}",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(r"\b(APPROVE|REJECT)\b\s*(.*)", re.IGNORECASE | re.DOTALL)

_POST_SUBJECT_RE = re.compile(r"Post\s*#(\d+)", re.IGNORECASE)
_PLAN_SUBJECT_RE = re.compile(r"Plan LinkedIn\s+(\d{4}-\d{2})", re.IGNORECASE)


def parse_decision(body: str) -> tuple[str, str | None] | None:
    """Parse "APPROVE" / "REJECT <reason>" from a reply body.

    Only looks above the first quoted-reply boundary — see module docstring.
    Returns (decision, reason); reason is always None for an approval.
    """
    if not body:
        return None

    top = _QUOTE_BOUNDARY_RE.split(body, maxsplit=1)[0]
    match = _DECISION_RE.search(top)
    if not match:
        return None

    verb, rest = match.groups()
    decision = "approved" if verb.upper() == "APPROVE" else "rejected"
    reason = rest.strip() or None if decision == "rejected" else None
    return decision, reason


def thread_id_from_subject(subject: str) -> str | None:
    """Post subjects take priority — a post subject never collides with the
    plan pattern, but checking order matters if that ever changes."""
    if not subject:
        return None
    post_match = _POST_SUBJECT_RE.search(subject)
    if post_match:
        return f"post-{post_match.group(1)}"
    plan_match = _PLAN_SUBJECT_RE.search(subject)
    if plan_match:
        return f"plan-{plan_match.group(1)}"
    return None


def _connect_inbox():
    if not GMAIL_USER or not GMAIL_PASSWORD:
        log.warning("check_and_resume: Gmail credentials are missing — skipping")
        return None
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_PASSWORD)
    mail.select("inbox")
    return mail


def _extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return (part.get_payload(decode=True) or b"").decode(errors="ignore")
        return ""
    return (msg.get_payload(decode=True) or b"").decode(errors="ignore")


def check_and_resume() -> int:
    """Reads unread replies (plan or post approvals — resuming is generic
    now, both are just a thread_id) and resumes whichever thread the
    subject line identifies. A reply that doesn't parse, or whose subject
    doesn't map to a real thread, is left unread rather than silently
    marked seen.
    """
    mail = _connect_inbox()
    if mail is None:
        return 0

    resolved = 0
    try:
        _, message_numbers = mail.search(None, "UNSEEN")
        for num in message_numbers[0].split():
            _, data = mail.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])
            subject = msg.get("Subject", "") or ""
            body = _extract_body(msg)

            parsed = parse_decision(body)
            if parsed is None:
                continue

            thread_id = thread_id_from_subject(subject)
            if thread_id is None:
                log.warning("check_and_resume: subject %r doesn't identify a thread — leaving unread", subject)
                continue

            decision, reason = parsed
            if resume_thread(thread_id, {"decision": decision, "reason": reason}):
                mail.store(num, "+FLAGS", "\\Seen")
                resolved += 1
    finally:
        mail.logout()

    return resolved
