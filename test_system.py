import os
import sys
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database.models import init_db
from scheduler.monthly_scheduler import main as generate_plan
from approval.inbox_checker import process_plan_replies


def test_real_system_workflow():
    init_db()
    print("Generating monthly plan...")
    plan = generate_plan("2026-09")
    assert plan is not None, "The monthly plan should be created successfully."

    print("Waiting for approval through Gmail...")
    time.sleep(5)

    print("Checking Gmail replies...")
    result = process_plan_replies(generate_posts=True)
    print("Inbox processing result:", result)


if __name__ == "__main__":
    test_real_system_workflow()
