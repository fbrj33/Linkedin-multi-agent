from __future__ import annotations

"""
Tests scheduling/plan_expander.py against the isolated test DB wired up by
tests/conftest.py — never touches the real wimbee.db.
"""

import datetime
import json

from database.models import MonthlyPlan, Post, SessionLocal
from scheduling import plan_expander as pe


def _make_plan(month="2026-08", status="approved", plan_json=None):
    db = SessionLocal()
    stored_json = plan_json if isinstance(plan_json, str) else json.dumps(plan_json)
    plan = MonthlyPlan(month=month, plan_json=stored_json, status=status)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    plan_id = plan.id
    db.close()
    return plan_id


def _get_posts_for_plan(plan_id):
    db = SessionLocal()
    posts = db.query(Post).filter(Post.plan_id == plan_id).order_by(Post.id).all()
    db.close()
    return posts


def test_expand_plan_returns_empty_for_missing_plan():
    assert pe.expand_plan(999_999) == []


def test_refuses_to_expand_unapproved_plan():
    plan_id = _make_plan(
        status="pending",
        plan_json={"posts": [{"theme": "x", "scheduled_date": "2026-08-05"}]},
    )
    assert pe.expand_plan(plan_id) == []
    assert _get_posts_for_plan(plan_id) == []


def test_expand_plan_handles_invalid_json_string():
    plan_id = _make_plan(plan_json="not valid json {")
    assert pe.expand_plan(plan_id) == []


def test_expand_plan_creates_one_post_per_item():
    plan_json = {
        "posts": [
            {
                "theme": "AI trends", "format": "texte",
                "scheduled_date": "2026-08-05", "scheduled_time": "08:30",
                "brief": "explain X",
            },
            {
                "theme": "Special day", "format": "carrousel",
                "scheduled_date": "13/08/2026", "special_day": "Fete",
                "brief": "explain Y",
            },
        ]
    }
    plan_id = _make_plan(plan_json=plan_json)
    created_ids = pe.expand_plan(plan_id)
    assert len(created_ids) == 2

    posts = _get_posts_for_plan(plan_id)
    assert posts[0].theme == "AI trends"
    assert posts[0].scheduled_date == "2026-08-05"
    assert posts[0].scheduled_time == "08:30"
    assert posts[0].brief == "explain X"
    assert posts[0].status == "planned"
    assert posts[0].approval_token  # uuid4 hex, non-empty

    assert posts[1].scheduled_date == "2026-08-13"  # DD/MM/YYYY normalized to ISO
    assert posts[1].special_day == "Fete"


def test_expand_plan_is_idempotent():
    plan_json = {"posts": [{"theme": "x", "scheduled_date": "2026-08-05"}]}
    plan_id = _make_plan(plan_json=plan_json)
    first = pe.expand_plan(plan_id)
    second = pe.expand_plan(plan_id)
    assert len(first) == 1
    assert second == []
    assert len(_get_posts_for_plan(plan_id)) == 1


def test_expand_plan_handles_bare_list_plan_json():
    plan_id = _make_plan(plan_json=[{"theme": "bare list item", "scheduled_date": "2026-08-06"}])
    assert len(pe.expand_plan(plan_id)) == 1


def test_expand_plan_handles_wrapped_dict_shapes():
    for key in ("items", "schedule", "plan", "entries"):
        plan_id = _make_plan(
            month="2026-09",
            plan_json={key: [{"theme": f"via {key}", "scheduled_date": "2026-09-10"}]},
        )
        assert len(pe.expand_plan(plan_id)) == 1, f"failed for wrapper key {key!r}"


def test_expand_plan_resolves_bare_day_number_against_plan_month():
    plan_id = _make_plan(month="2026-08", plan_json={"posts": [{"theme": "bare day", "scheduled_date": "15"}]})
    pe.expand_plan(plan_id)
    posts = _get_posts_for_plan(plan_id)
    assert posts[0].scheduled_date == "2026-08-15"
    assert posts[0].scheduled_time == pe.DEFAULT_TIME


def test_expand_plan_skips_item_with_unresolvable_date():
    plan_id = _make_plan(plan_json={"posts": [
        {"theme": "bad date", "scheduled_date": "not-a-date"},
        {"theme": "good date", "scheduled_date": "2026-08-08"},
    ]})
    created_ids = pe.expand_plan(plan_id)
    assert len(created_ids) == 1
    posts = _get_posts_for_plan(plan_id)
    assert posts[0].theme == "good date"


def test_expand_plan_sanitizes_nullish_fields():
    plan_id = _make_plan(plan_json={"posts": [{
        "theme": "AI",
        "scheduled_date": "2026-08-07",
        "special_day": "null",
        "trend_source": "N/A",
    }]})
    pe.expand_plan(plan_id)
    posts = _get_posts_for_plan(plan_id)
    assert posts[0].special_day is None
    assert posts[0].trend_source is None


def test_expand_plan_sets_approval_deadline_lead_time_before_publish():
    plan_id = _make_plan(plan_json={"posts": [{
        "theme": "AI", "scheduled_date": "2026-08-07", "scheduled_time": "10:00",
    }]})
    pe.expand_plan(plan_id)
    posts = _get_posts_for_plan(plan_id)

    # scheduled_date/time is Africa/Tunis local; approval_deadline is stored
    # as true UTC (see plan_expander's module docstring on why).
    publish_dt_local = datetime.datetime(2026, 8, 7, 10, 0).replace(tzinfo=pe._local_tz())
    publish_dt_utc = publish_dt_local.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    expected_deadline = publish_dt_utc - datetime.timedelta(hours=pe.APPROVAL_LEAD_HOURS)
    assert posts[0].approval_deadline == expected_deadline
    # Must comfortably clear the 2h post-approval inbox poll from Phase 6.
    assert pe.APPROVAL_LEAD_HOURS >= 4


def test_backfill_special_day_nulls_repairs_poisoned_rows():
    db = SessionLocal()
    plan = MonthlyPlan(month="2026-08", plan_json="{}", status="approved")
    db.add(plan)
    db.commit()
    db.refresh(plan)

    poisoned = Post(
        plan_id=plan.id, theme="x", special_day="null",
        trend_source="N/A", brief="   ", status="planned",
    )
    db.add(poisoned)
    db.commit()
    post_id = poisoned.id
    db.close()

    assert pe.backfill_special_day_nulls() >= 1

    db = SessionLocal()
    fixed = db.query(Post).filter(Post.id == post_id).first()
    assert fixed.special_day is None
    assert fixed.trend_source is None
    assert fixed.brief is None
    db.close()


def test_backfill_special_day_nulls_leaves_clean_rows_alone():
    db = SessionLocal()
    plan = MonthlyPlan(month="2026-08", plan_json="{}", status="approved")
    db.add(plan)
    db.commit()
    db.refresh(plan)

    clean = Post(plan_id=plan.id, theme="Real theme", special_day="Fete", status="planned")
    db.add(clean)
    db.commit()
    post_id = clean.id
    db.close()

    pe.backfill_special_day_nulls()

    db = SessionLocal()
    unchanged = db.query(Post).filter(Post.id == post_id).first()
    assert unchanged.theme == "Real theme"
    assert unchanged.special_day == "Fete"
    db.close()
