"""
Wimbee LinkedIn Automation — Full System Test
Usage:
    python tests/test_system.py              # uses current month
    python tests/test_system.py 2026-09      # specify month
    python tests/test_system.py --clear-db   # clear DB then run
"""

import os
import sys
import datetime
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_month_from_args() -> str:
    """
    Reads month from command line argument.
    Falls back to current month if none provided.
    """
    for arg in sys.argv[1:]:
        if len(arg) == 7 and arg[4] == "-":
            try:
                datetime.datetime.strptime(arg, "%Y-%m")
                return arg
            except ValueError:
                pass
    return datetime.date.today().strftime("%Y-%m")


def clear_db():
    """Drops and recreates all tables — fresh start."""
    from database.models import engine, Base
    print("  Clearing database...")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print(" Database cleared and recreated.\n")


def print_db_state():
    """Prints current state of plans and posts in the DB."""
    from database.models import SessionLocal, MonthlyPlan, Post
    db = SessionLocal()

    plans = db.query(MonthlyPlan).order_by(MonthlyPlan.id.desc()).all()
    print(f"\n{'='*60}")
    print(f"PLANS IN DB ({len(plans)} total)")
    print(f"{'='*60}")
    for p in plans:
        print(f"  Plan #{p.id} | {p.month} | status: {p.status} | deadline: {p.deadline}")

    posts = db.query(Post).order_by(Post.id.desc()).all()
    print(f"\n{'='*60}")
    print(f"POSTS IN DB ({len(posts)} total)")
    print(f"{'='*60}")
    for p in posts:
        tag = f" {p.special_day}" if p.special_day else "📈 trend"
        print(f"  Post #{p.id} | {p.scheduled_date} {getattr(p, 'scheduled_time', '')} | {tag}")
        print(f"           Theme  : {p.theme[:70]}")
        print(f"           Status : {p.status}")
        if p.content:
            print(f"           Content: {p.content[:80]}...")
        print()

    db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Main test
# ══════════════════════════════════════════════════════════════════════════════

def test_real_system_workflow(month: str):
    
    from database.models import init_db, SessionLocal, MonthlyPlan
    from orchestrator.run_monthly_plan import generate_and_send_monthly_plan
    from api.inbox_checker import process_plan_replies

    init_db()

    print(f"\n{'='*60}")
    print(f"  WIMBEE LINKEDIN AUTOMATION — SYSTEM TEST")
    print(f"  Month : {month}")
    print(f"{'='*60}\n")

    # ── Step 1 : Generate and send the plan ──────────────────────────────────
    print("[1/4] Generating monthly plan and sending to admin...")
    db_plan = generate_and_send_monthly_plan(month)

    if not db_plan:
        print(" Plan generation failed. Aborting.")
        return

    # Show the plan that was generated
    plan_data = json.loads(db_plan.plan_json)
    posts = plan_data.get("posts", [])
    print(f"\n Plan #{db_plan.id} generated — {len(posts)} posts :")
    for p in posts:
        tag = f"⭐ {p['special_day']}" if p.get("special_day") else "📈 trend"
        print(f"   {p['scheduled_date']} {p.get('scheduled_time', '')} | {tag} | {p['theme'][:60]}")

    print(f"\n Plan email sent. Deadline : {db_plan.deadline}")

    # ── Step 2 : Wait for admin reply ────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("[2/4] Go to your email and reply OUI or NON to the plan email.")
    print("      When done, press ENTER to continue...")
    input()

    # ── Step 3 : Check inbox ─────────────────────────────────────────────────
    print("\n[3/4] Checking inbox for your reply...")
    process_plan_replies()

    # ── Step 4 : Show results ─────────────────────────────────────────────────
    print("\n[4/4] Checking results...")

    db = SessionLocal()
    updated_plan = db.query(MonthlyPlan).filter(
        MonthlyPlan.id == db_plan.id
    ).first()
    db.close()

    print(f"\n Plan #{db_plan.id} status : {updated_plan.status}")

    if updated_plan.status == "approved":
        print("   → Posts are being generated and approval emails sent.")
    elif updated_plan.status == "rejected":
        print("   → Trend posts cancelled. Special day posts still generated.")
    else:
        print(f"   → Status unchanged : {updated_plan.status}")
    # Only check post replies
if "--post-inbox" in sys.argv:
    from approval.inbox_checker import process_post_replies
    init_db()
    print("\n[Post inbox check] Checking for post OUI/NON replies...")
    process_post_replies()
    print_db_state()
    sys.exit(0)

  


def test_check_inbox_only():
    
    from database.models import init_db
    from api.inbox_checker import process_plan_replies

    init_db()
    print("\n[Inbox check only] Checking for OUI/NON replies...")
    process_plan_replies()
    print_db_state()


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":

    # Clear DB if requested
    if "--clear-db" in sys.argv:
        clear_db()

    # Check inbox only if requested
    if "--inbox" in sys.argv:
        test_check_inbox_only()
        sys.exit(0)

    # Get month
    month = get_month_from_args()

    # Confirm before running
    print(f"\n Wimbee System Test")
    print(f"   Month    : {month}")
    print(f"   Clear DB : {'yes' if '--clear-db' in sys.argv else 'no'}")
    print(f"\nPress ENTER to start or Ctrl+C to cancel...")
    input()

    test_real_system_workflow(month)