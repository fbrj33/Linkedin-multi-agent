import argparse
import json
from database.models import init_db
from agents.planner_agent import run_planner, save_plan_to_db
from api.inbox_checker import process_plan_replies


def plan_command(args) -> int:
    init_db()

    analytics_report = None
    if args.analytics:
        with open(args.analytics, "r", encoding="utf-8") as f:
            analytics_report = json.load(f)

    plan = run_planner(args.month, analytics_report)
    if not plan:
        print("Planner returned no result. Check your LLM response and prompt formatting.")
        return 1

    save_plan_to_db(plan, send_email=args.email)
    print(f"Plan for {args.month} saved successfully.")
    return 0


def check_replies_command(args) -> int:
    init_db()
    print("Checking inbox for Wimbee reply emails...")
    result = process_plan_replies(generate_posts=not args.no_generate)
    if result is None:
        return 0

    if isinstance(result, dict):
        print(f"Processed {result.get('processed', 0)} replies.")
        if result.get('plan_month'):
            print(f"Pending plan month: {result['plan_month']}")
            print(f"Updated status: {result['new_status']}")
        if result.get('generate_posts') is False:
            print("Post generation is disabled for now.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Wimbee LinkedIn planner CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Generate and save a monthly LinkedIn plan")
    plan_parser.add_argument("month", help="Month to plan in YYYY-MM format")
    plan_parser.add_argument("--analytics", help="Path to a JSON analytics report file")
    plan_parser.add_argument("--email", action="store_true", help="Also send the approval email after saving")
    plan_parser.set_defaults(func=plan_command)

    reply_parser = subparsers.add_parser("check-replies", help="Check email replies for pending plans")
    reply_parser.add_argument("--no-generate", action="store_true", help="Do not generate posts when processing replies")
    reply_parser.set_defaults(func=check_replies_command)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
