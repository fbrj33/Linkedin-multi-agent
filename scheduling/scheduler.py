from __future__ import annotations

"""
APScheduler as a pure resumption driver. This module owns no business
logic: every job body is exactly one call into orchestrator.runner or
orchestrator.inbox — resume a thread, or send an email. It never imports an
agent or a Publisher, and never touches a Post row directly; all of that
lives inside the graph nodes themselves (see orchestrator/nodes_plan.py,
orchestrator/nodes_post.py) — "posting lives inside the orchestrator."

Starting a brand-new plan thread is deliberately NOT a job here — it's
neither "resume a thread" nor "send an email," so it stays a manual/CLI
action (`python main.py plan <month>`), same as before this rewrite.

Supersedes the deleted scheduler/ package (schedular.py, monthly_scheduler.py)
and the function-call version of this file from the original Phase 6.
"""

import datetime
import logging
import os
import sys

# Windows consoles default stdout/stderr to the system codepage (cp1252),
# which can't represent an emoji or many accented characters — and content
# generation is explicitly allowed up to 3 emojis per post, plus an admin's
# rejection reason could contain anything. Without this, a perfectly normal
# post or reply crashes this long-running process with a raw
# UnicodeEncodeError the moment a log line tries to print it.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler

from database.models import init_db
from orchestrator import thread_registry
from orchestrator.inbox import check_and_resume
from orchestrator.runner import reconcile_on_startup, recover_running_threads, resume_thread

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("wimbee-scheduler")

INBOX_POLL_MINUTES = int(os.getenv("WIMBEE_INBOX_POLL_MINUTES", "15"))
SLOT_SWEEP_MINUTES = int(os.getenv("WIMBEE_SLOT_SWEEP_MINUTES", "5"))
REMINDER_SWEEP_MINUTES = int(os.getenv("WIMBEE_REMINDER_SWEEP_MINUTES", "60"))
REMINDER_HOURS_BEFORE_DEADLINE = float(os.getenv("WIMBEE_REMINDER_HOURS_BEFORE_DEADLINE", "6"))


def job_inbox_poll() -> None:
    log.info("Checking inbox for approval replies...")
    resolved = check_and_resume()
    log.info("Resumed %d thread(s) from inbox replies", resolved)


def job_slot_sweep() -> None:
    """Resumes post threads parked at wait_for_slot once their scheduled
    local time (already converted to UTC in the interrupt payload — see
    orchestrator/nodes_post.py::wait_for_slot) has arrived."""
    log.info("Checking for posts whose scheduled slot has arrived...")
    now = datetime.datetime.utcnow()
    woken = 0
    for thread in thread_registry.list_interrupted("post"):
        payload = thread.get("interrupt_payload") or {}
        wake_at = payload.get("wake_at")
        if not wake_at:
            continue
        try:
            wake_dt = datetime.datetime.fromisoformat(wake_at)
        except ValueError:
            log.error("job_slot_sweep: unparseable wake_at %r for %s", wake_at, thread["thread_id"])
            continue
      
        if wake_dt <= now and resume_thread(thread["thread_id"], "woken"):
            woken += 1
    log.info("Woke %d post(s) at their scheduled slot", woken)


def job_reminders_and_expiry() -> None:
    """Sends one reminder per interrupted thread nearing its deadline
    (guarded by graph_thread.reminder_sent_at), and resumes with
    decision="expired" any interrupted thread whose deadline has already
    passed with no reply."""
    from api.email_service import send_email

    log.info("Sweeping for deadline reminders and expired approvals...")
    now = datetime.datetime.utcnow()
    window_end = now + datetime.timedelta(hours=REMINDER_HOURS_BEFORE_DEADLINE)
    admin_email = os.getenv("ADMIN_EMAIL", os.getenv("GMAIL_USER", "")).strip()

    reminded, expired = 0, 0
    for thread in thread_registry.list_interrupted():
        payload = thread.get("interrupt_payload") or {}
        deadline_str = payload.get("deadline")
        if not deadline_str:
            continue  # e.g. wait_for_slot, which has no approval deadline
        try:
            deadline = datetime.datetime.fromisoformat(deadline_str)
        except ValueError:
            log.error("job_reminders_and_expiry: unparseable deadline %r for %s", deadline_str, thread["thread_id"])
            continue

        if deadline < now:
            if resume_thread(thread["thread_id"], {"decision": "expired", "reason": None}):
                expired += 1
            continue

        if deadline <= window_end and thread.get("reminder_sent_at") is None:
            # Subject must carry the same "Post #<id>" / "Plan LinkedIn <month>"
            # marker the original approval email did — orchestrator/inbox.py
            # resolves a reply's thread from the subject line, not a body
            # token, so a reminder with a different subject shape would be
            # unresolvable if replied to.
            if thread["thread_type"] == "post":
                subject = f"[WIMBEE] Reminder — Post #{thread.get('post_id')} needs your APPROVE/REJECT"
            else:
                subject = f"[WIMBEE] Reminder — Plan LinkedIn {thread.get('month')} needs your APPROVE/REJECT"
            body = (
                f"Reminder: {thread['thread_id']} is still waiting on your decision.\n\n"
                f"Deadline: {deadline_str}\n\n"
                f"Reply APPROVE to approve, or REJECT <reason> to reject."
            )
            if send_email(subject, body, admin_email):
                thread_registry.mark_reminder_sent(thread["thread_id"])
                reminded += 1

    log.info("Reminders sent: %d, expired: %d", reminded, expired)


def _run_startup_diagnostics() -> None:
    
    log.info("Reconciling graph_thread against checkpoint state...")
    divergences = reconcile_on_startup()
    log.info("Reconcile complete — %d projection(s) repaired", divergences)

    recovered = recover_running_threads()
    log.info("Recovered %d thread(s) that were between normal graph nodes", recovered)

    log.info("Running publisher healthcheck...")
    from publishing.factory import get_publisher

    try:
        result = get_publisher().healthcheck()
    except Exception:
        log.exception("Publisher healthcheck raised unexpectedly")
        return

    if result.ok:
        log.info("Publisher healthcheck: OK")
    elif result.needs_human:
        log.critical("Publisher healthcheck FAILED (needs human): %s", result.error)
    else:
        log.error("Publisher healthcheck FAILED: %s", result.error)


def main() -> None:
    init_db()
    _run_startup_diagnostics()

    scheduler = BlockingScheduler(
        executors={"default": ThreadPoolExecutor(6)},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    )

    scheduler.add_job(job_inbox_poll, "interval", minutes=INBOX_POLL_MINUTES, id="inbox_poll")
    scheduler.add_job(job_slot_sweep, "interval", minutes=SLOT_SWEEP_MINUTES, id="slot_sweep")
    scheduler.add_job(job_reminders_and_expiry, "interval", minutes=REMINDER_SWEEP_MINUTES, id="reminders_and_expiry")

    log.info(
        "Scheduler started — inbox poll every %dmin, slot sweep every %dmin, "
        "reminders/expiry every %dmin",
        INBOX_POLL_MINUTES, SLOT_SWEEP_MINUTES, REMINDER_SWEEP_MINUTES,
    )

    job_inbox_poll()
    job_slot_sweep()
    job_reminders_and_expiry()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
