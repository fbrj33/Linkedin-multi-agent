import argparse
import logging
import sys

# Windows consoles default stdout/stderr to the system codepage (cp1252),
# which can't represent an emoji or many accented characters — and content
# generation is explicitly allowed up to 3 emojis per post, plus an admin's
# rejection reason could contain anything. Without this, a perfectly normal
# post or reply crashes this CLI with a raw UnicodeEncodeError the moment a
# log line tries to print it.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from database.models import init_db


def plan_command(args) -> int:
    init_db()

    from orchestrator.runner import start_plan_thread

    start_plan_thread(args.month)
    print(f"Plan thread 'plan-{args.month}' finished this run (see the [plan-{args.month}] log lines above for what it actually did).")
    return 0


def check_replies_command(args) -> int:
    init_db()
    print("Checking inbox for Wimbee reply emails...")

    from orchestrator.inbox import check_and_resume

    resolved = check_and_resume()
    print(f"Resolved {resolved} decision(s).")
    return 0


def run_command(args) -> int:
    """One command, one terminal, continuous live output: start a plan, then
    hand off directly into the scheduler's own blocking loop in this same
    process — no separate step, nothing running elsewhere in the background.
    Ctrl+C stops it; nothing is lost, everything resumes from checkpoint."""
    init_db()

    from orchestrator.runner import start_plan_thread
    from scheduling.scheduler import main as run_scheduler_forever

    start_plan_thread(args.month)
    print(
        f"\nPlan thread 'plan-{args.month}' created — handing off to the scheduler now. "
        "Reply to approval emails as they arrive; this process polls for them and "
        "prints every step live. Ctrl+C to stop.\n"
    )
    run_scheduler_forever()
    return 0


def main() -> int:
    # Every orchestrator node logs as it runs (see orchestrator/runner.py's
    # stream-based execution) — without this, those log.info() calls are
    # silently swallowed and this CLI looks like it does nothing until the
    # final print.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Wimbee LinkedIn planner CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Start a monthly LinkedIn plan thread (generates + emails for approval)")
    plan_parser.add_argument("month", help="Month to plan in YYYY-MM format")
    plan_parser.set_defaults(func=plan_command)

    reply_parser = subparsers.add_parser("check-replies", help="Check email replies and resume whichever graph thread they belong to")
    reply_parser.set_defaults(func=check_replies_command)

    run_parser = subparsers.add_parser("run", help="Start a plan AND run the scheduler continuously in this terminal — one command, live output, until Ctrl+C")
    run_parser.add_argument("month", help="Month to plan in YYYY-MM format")
    run_parser.set_defaults(func=run_command)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
