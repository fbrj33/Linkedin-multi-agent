"""Manual live Wimbee workflow runner.

Starts the real plan and post graphs, sends real approval emails, polls the
real Gmail inbox, and uses the real scheduler slot sweep for publishing.

Usage:
    python test_system.py 2026-10 --clear-db

Reply to each email with exactly APPROVE or REJECT <reason>.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from database.models import Base, MonthlyPlan, Post, SessionLocal, init_db, engine


def _plan(month: str):
    db = SessionLocal()
    try:
        return (
            db.query(MonthlyPlan)
            .filter(MonthlyPlan.month == month)
            .order_by(MonthlyPlan.id.desc())
            .first()
        )
    finally:
        db.close()


def print_state(month: str) -> None:
    from orchestrator import thread_registry

    plan = _plan(month)
    db = SessionLocal()
    try:
        posts = db.query(Post).filter(Post.plan_id == plan.id).order_by(Post.id).all() if plan else []
        print("\nDATABASE STATE")
        print(f"  plan {month}: {plan.status if plan else 'not created'}")
        for post in posts:
            thread = thread_registry.get_thread(f"post-{post.id}")
            print(
                f"  post {post.id}: {post.format or 'text'} | {post.status} | "
                f"thread={thread['status'] if thread else 'missing'} | "
                f"image={'yes' if post.image_path else 'no'}"
            )
    finally:
        db.close()


def wait_for_plan(month: str, poll_seconds: int) -> None:
    from orchestrator import thread_registry
    from orchestrator.inbox import check_and_resume

    print(f"\nReply APPROVE or REJECT <reason> to the plan email. Polling every {poll_seconds}s...")
    while True:
        check_and_resume()
        print_state(month)
        thread = thread_registry.get_thread(f"plan-{month}")
        if thread and thread["status"] == "done":
            return
        if thread and thread["status"] == "failed":
            raise RuntimeError(f"Plan thread failed: {thread}")
        time.sleep(poll_seconds)


def wait_for_posts(month: str, poll_seconds: int) -> None:
    from orchestrator import thread_registry
    from orchestrator.inbox import check_and_resume

    print("\nReply to each post email with APPROVE or REJECT <reason>.")
    print("Rejected posts regenerate through the post graph and send a threaded reply.")
    while True:
        check_and_resume()
        print_state(month)
        plan = _plan(month)
        db = SessionLocal()
        try:
            posts = db.query(Post).filter(Post.plan_id == plan.id).all() if plan else []
        finally:
            db.close()
        if posts:
            threads = [thread_registry.get_thread(f"post-{post.id}") for post in posts]
            if all(
                thread
                and (
                    thread["status"] == "done"
                    or (
                        thread["status"] == "interrupted"
                        and thread.get("current_node") == "wait_for_slot"
                    )
                )
                for thread in threads
            ):
                return
        time.sleep(poll_seconds)


def publish_due_posts(month: str, poll_seconds: int) -> None:
    from scheduling.scheduler import job_slot_sweep

    print("\nRunning the real scheduler slot sweep. Posts publish at their scheduled time.")
    while True:
        job_slot_sweep()
        print_state(month)
        plan = _plan(month)
        db = SessionLocal()
        try:
            posts = db.query(Post).filter(Post.plan_id == plan.id).all() if plan else []
            terminal = posts and all(
                post.status in {"published", "publish_failed", "manual_publish_required", "rejected", "expired"}
                for post in posts
            )
        finally:
            db.close()
        if terminal:
            return
        time.sleep(poll_seconds)


def clear_database() -> None:
    print("Clearing database and checkpoints...")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    checkpoint_path = os.getenv("WIMBEE_CHECKPOINT_DB", "wimbee_checkpoints.db")
    if checkpoint_path.startswith("sqlite:///"):
        checkpoint_path = checkpoint_path.removeprefix("sqlite:///")
    if os.path.exists(checkpoint_path):
        os.remove(checkpoint_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Wimbee's real human-in-the-loop workflow")
    parser.add_argument("month", help="Fresh month in YYYY-MM, for example 2026-10")
    parser.add_argument("--clear-db", action="store_true", help="Delete local DB data first")
    parser.add_argument("--poll-seconds", type=int, default=15)
    args = parser.parse_args()

    datetime.datetime.strptime(args.month, "%Y-%m")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if args.clear_db:
        clear_database()
    init_db()

    from orchestrator.runner import start_plan_thread

    print(f"Starting the real plan graph for {args.month}...")
    start_plan_thread(args.month)
    wait_for_plan(args.month, args.poll_seconds)
    wait_for_posts(args.month, args.poll_seconds)
    publish_due_posts(args.month, args.poll_seconds)
    print("\nLive workflow finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
