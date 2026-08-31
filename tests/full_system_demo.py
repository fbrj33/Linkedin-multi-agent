#!/usr/bin/env python
from __future__ import annotations

"""
One-command, fully visual walkthrough of the entire pipeline: plan
generation, plan approval, per-post content generation + scoring, a
reject-with-reason -> regenerate -> resend cycle, approval, and a publish
attempt.

Not a pytest test — run directly: `python tests/full_system_demo.py [month]`

Design choices, deliberately:
- Uses REAL LLM calls (whatever WIMBEE_LLM_PROVIDER/keys are already in your
  .env) — the whole point is to prove generation genuinely works, not to
  mock it away. Switch WIMBEE_LLM_PROVIDER in .env and rerun this same
  script to compare providers.
- SIMULATES the admin's approve/reject decisions directly via
  orchestrator.runner.resume_thread() instead of waiting on real email
  round-trips — no inbox polling, no reply parsing, just the orchestrator
  itself, so this runs start-to-finish in one shot.
- Runs against an isolated, throwaway DB + checkpoint file (a fresh temp
  directory every run) — never touches wimbee.db, always starts clean, safe
  to rerun as many times as you like.
- Only drives ONE representative post through the full reject/regenerate/
  approve/publish cycle (real LLM calls cost real money/quota across every
  post in a plan otherwise) — the rest are left at their post-generation
  state and summarized, not ignored.
"""

import datetime
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Windows consoles default stdout/stderr to the system codepage (cp1252),
# which can't represent an emoji or many accented characters — and content
# generation is explicitly allowed up to 3 emojis per post (see
# agents/content_agent.py's prompt). Without this, a perfectly successful
# generation crashes this script the moment it tries to print a preview of
# it, mid-demo, with a raw UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Isolate from production data BEFORE any project module is imported — same
# reasoning as tests/conftest.py and tests/smoke_test.py.
_tmp_dir = tempfile.mkdtemp(prefix="wimbee-demo-")
os.environ["WIMBEE_DATABASE_URL"] = f"sqlite:///{os.path.join(_tmp_dir, 'demo.db')}"
os.environ["WIMBEE_CHECKPOINT_DB"] = os.path.join(_tmp_dir, "demo_checkpoints.db")
os.environ.setdefault("WIMBEE_DRY_RUN", "yes")
os.environ.setdefault("WIMBEE_ALLOW_BROWSER_PUBLISHER", "no")

from dotenv import load_dotenv  # noqa: E402

load_dotenv()  # fills in real LLM/email keys; never overrides the isolation vars above (override=False)

logging.basicConfig(level=logging.INFO, format="    ... %(message)s")

_WIDTH = 78


def banner(title: str) -> None:
    print()
    print("=" * _WIDTH)
    print(f" {title}")
    print("=" * _WIDTH)


def step(msg: str) -> None:
    print(f"\n>>> {msg}")


def ok(msg: str) -> None:
    print(f"    [OK]   {msg}")


def fail(msg: str) -> None:
    print(f"    [FAIL] {msg}")


def info(msg: str) -> None:
    print(f"    -      {msg}")


def main() -> int:
    month = sys.argv[1] if len(sys.argv) > 1 else "2026-08"
    try:
        return _run(month)
    except Exception as exc:
        # Real LLM/network calls happen throughout this script — a raw
        # traceback here would bury the actually-useful message (e.g.
        # OpenRouter's 402 "insufficient credits") under framework noise.
        banner("FAILED")
        fail(f"{type(exc).__name__}: {exc}")
        return 1


def _run(month: str) -> int:
    from database.models import MonthlyPlan, Post, SessionLocal, init_db

    init_db()

    banner("WIMBEE FULL SYSTEM DEMO")
    print(f"LLM provider   : {os.getenv('WIMBEE_LLM_PROVIDER', 'openrouter')}")
    print(f"Target month   : {month}")
    print(f"Isolated DB    : {os.environ['WIMBEE_DATABASE_URL']}")
    print(f"Isolated ckpt  : {os.environ['WIMBEE_CHECKPOINT_DB']}")
    print("(wimbee.db and wimbee_checkpoints.db are never touched by this script)")

    from orchestrator import thread_registry
    from orchestrator.runner import resume_thread, start_plan_thread

    # ------------------------------------------------------------------
    banner("STEP 1 — Generate a monthly plan (real LLM call + real RSS fetch)")
    step(f"Starting plan thread 'plan-{month}'...")
    start_plan_thread(month)

    plan_thread = thread_registry.get_thread(f"plan-{month}")
    if plan_thread is None or plan_thread["status"] != "interrupted":
        fail(f"Plan thread did not reach the expected interrupted state (got: {plan_thread}).")
        return 1
    ok(f"Plan generated, thread interrupted at '{plan_thread['current_node']}'")

    plan_id = plan_thread["interrupt_payload"]["plan_id"]
    db = SessionLocal()
    plan = db.query(MonthlyPlan).filter(MonthlyPlan.id == plan_id).first()
    plan_data = json.loads(plan.plan_json)
    db.close()

    posts_planned = plan_data.get("posts", [])
    if not posts_planned:
        fail("Plan has zero posts — planner_agent.run_planner produced nothing usable.")
        return 1
    ok(f"Plan id={plan_id}, {len(posts_planned)} posts planned")
    for p in posts_planned[:3]:
        info(f"{p.get('scheduled_date')} — {p.get('theme')}")
    if len(posts_planned) > 3:
        info(f"... and {len(posts_planned) - 3} more")

    # ------------------------------------------------------------------
    banner("STEP 2 — Simulate the admin APPROVING the plan (no email needed)")
    step("resume_thread(plan, decision=approved)...")
    resume_thread(f"plan-{month}", {"decision": "approved", "reason": None})

    plan_thread_after = thread_registry.get_thread(f"plan-{month}")
    if plan_thread_after["status"] != "done":
        fail(f"Expected the plan thread to finish, got status '{plan_thread_after['status']}'.")
        return 1
    ok("Plan approved — expand_plan ran and spawned one post thread per item")

    db = SessionLocal()
    posts = db.query(Post).filter(Post.plan_id == plan_id).order_by(Post.id).all()
    post_summaries = [(p.id, p.theme, p.status, p.content, p.hashtags, p.score) for p in posts]
    db.close()

    if not post_summaries:
        fail("No Post rows were created — expand_plan did not run correctly.")
        return 1
    ok(f"{len(post_summaries)} Post rows created")

    # ------------------------------------------------------------------
    banner("STEP 3 — Verify content actually generated for every post")
    all_good = True
    for post_id, theme, status, content, hashtags, score in post_summaries:
        if content:
            preview = content.replace("\n", " ")[:70]
            ok(f"Post #{post_id} [{status}] score={score} — {len(content)} chars — \"{preview}...\"")
        else:
            fail(f"Post #{post_id} [{status}] — NO CONTENT GENERATED")
            all_good = False
    if not all_good:
        fail("Stopping — some posts have no content, see errors above.")
        return 1

    # ------------------------------------------------------------------
    first_post_id = post_summaries[0][0]
    thread_id = f"post-{first_post_id}"

    banner(f"STEP 4 — Reject Post #{first_post_id} with a reason (test the regenerate loop)")
    db = SessionLocal()
    original_content = db.query(Post).filter(Post.id == first_post_id).first().content
    db.close()

    reason = "Trop générique, ajoute un chiffre concret et une source précise."
    step(f'resume_thread(post-{first_post_id}, decision=rejected, reason="{reason}")')
    resume_thread(thread_id, {"decision": "rejected", "reason": reason})

    db = SessionLocal()
    post = db.query(Post).filter(Post.id == first_post_id).first()
    new_content, retry_count = post.content, post.retry_count
    db.close()

    if not new_content:
        fail(f"Post #{first_post_id} has no content after regeneration.")
        return 1
    if new_content == original_content:
        fail("Content is byte-identical after rejection+regeneration — regeneration did not actually run.")
        return 1
    ok(f"Post #{first_post_id} regenerated (retry_count={retry_count}) — content genuinely changed")
    info(f"New: \"{new_content.replace(chr(10), ' ')[:90]}...\"")

    thread_after_reject = thread_registry.get_thread(thread_id)
    if thread_after_reject["current_node"] != "post_approval":
        fail(f"Expected the thread back at post_approval, got '{thread_after_reject['current_node']}'.")
        return 1
    ok("A fresh approval email would go out now, with the new content, same subject line")

    # ------------------------------------------------------------------
    banner(f"STEP 5 — Approve Post #{first_post_id}")
    step(f"resume_thread(post-{first_post_id}, decision=approved)...")
    resume_thread(thread_id, {"decision": "approved", "reason": None})
    thread_after_approve = thread_registry.get_thread(thread_id)
    ok(f"Approved — now parked at '{thread_after_approve['current_node']}' (waiting for its scheduled time)")

    # ------------------------------------------------------------------
    banner("STEP 6 — Simulate the scheduled time arriving")
    step("In production the scheduler's slot-sweep does this automatically every 5 min; nudging it now instead of waiting weeks.")
    resume_thread(thread_id, "woken")

    db = SessionLocal()
    post = db.query(Post).filter(Post.id == first_post_id).first()
    final_status, publish_error, external_id = post.status, post.publish_error, post.external_id
    db.close()

    banner("STEP 7 — Publish result")
    if final_status == "published":
        ok(f"Post #{first_post_id} PUBLISHED (external_id={external_id})")
    elif final_status == "publish_failed":
        ok(f"Post #{first_post_id} publish correctly REFUSED: {publish_error}")
        info("Expected: WIMBEE_ORG_NAME/browser login aren't configured. This proves the safety gate works.")
    else:
        fail(f"Unexpected final status: {final_status!r}")
        return 1

    # ------------------------------------------------------------------
    banner("SUMMARY")
    print(f"LLM provider tested                        : {os.getenv('WIMBEE_LLM_PROVIDER', 'openrouter')}")
    print(f"Plan generation (real LLM + RSS)            : OK  ({len(post_summaries)} posts)")
    print(f"Content generation, every post               : OK  (all had real content)")
    print(f"Reject + reason -> regenerate + resend        : OK  (content verifiably changed)")
    print(f"Approve -> scheduled wait -> publish attempt  : OK  (status={final_status})")
    print()
    print("Every stage of the orchestrator is functioning correctly.")
    print(f"Demo DB left at {_tmp_dir} for inspection — safe to delete anytime, wimbee.db was never touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
