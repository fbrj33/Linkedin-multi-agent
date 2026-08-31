from __future__ import annotations



import datetime
import json
import logging
import os
import re
import uuid
from zoneinfo import ZoneInfo

from database.models import MonthlyPlan, Post, SessionLocal
from llm.base import sanitize_nullish

log = logging.getLogger(__name__)

DEFAULT_TIME = os.getenv("WIMBEE_DEFAULT_POST_TIME", "09:00")
APPROVAL_LEAD_HOURS = float(os.getenv("WIMBEE_APPROVAL_LEAD_HOURS", "24"))


def _local_tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("WIMBEE_TIMEZONE", "Africa/Tunis"))

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_LIST_KEYS = ("posts", "items", "schedule", "plan", "entries")

# Columns worth checking for the pre-Phase-1 "literal string null" bug —
# every user-facing text field a planner/content prompt could have populated.
_BACKFILL_COLUMNS = (
    "theme", "format", "special_day", "trend_source", "brief", "content", "hashtags",
)


def expand_plan(plan_id: int) -> list[int]:
    """Create one Post row per item in an approved plan's plan_json.

    Returns the list of newly created Post ids, or [] if the plan isn't
    approved yet, doesn't exist, or has already been expanded.
    """
    db = SessionLocal()
    try:
        plan = db.query(MonthlyPlan).filter(MonthlyPlan.id == plan_id).first()
        if plan is None:
            log.error("expand_plan: MonthlyPlan %s not found", plan_id)
            return []

        if plan.status != "approved":
            log.warning(
                "expand_plan: plan %s has status '%s', not 'approved' — refusing to expand",
                plan_id, plan.status,
            )
            return []

        already_expanded = db.query(Post.id).filter(Post.plan_id == plan_id).first()
        if already_expanded is not None:
            log.info("expand_plan: plan %s already has post(s) — skipping (idempotent)", plan_id)
            return []

        items = _extract_items(plan.plan_json)
        if not items:
            log.warning("expand_plan: plan %s has no expandable items in plan_json", plan_id)
            return []

        year, month_num = _parse_plan_month(plan.month)

        created_ids: list[int] = []
        for raw_item in items:
            item = sanitize_nullish(raw_item)
            if not isinstance(item, dict):
                log.warning("expand_plan: skipping non-dict item in plan %s: %r", plan_id, item)
                continue

            scheduled_date, scheduled_time = _resolve_datetime(item, year, month_num)
            if scheduled_date is None:
                log.warning(
                    "expand_plan: could not resolve a date for item in plan %s: %r",
                    plan_id, item,
                )
                continue

            publish_dt_local = datetime.datetime.strptime(
                f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=_local_tz())
            publish_dt_utc = publish_dt_local.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            approval_deadline = publish_dt_utc - datetime.timedelta(hours=APPROVAL_LEAD_HOURS)

            post = Post(
                plan_id=plan_id,
                theme=item.get("theme"),
                format=item.get("format"),
                scheduled_date=scheduled_date,
                scheduled_time=scheduled_time,
                special_day=item.get("special_day"),
                trend_source=item.get("trend_source"),
                brief=item.get("brief"),
                retry_count=0,
                status="planned",
                approval_token=uuid.uuid4().hex,
                approval_deadline=approval_deadline,
            )
            db.add(post)
            db.flush()  # assign post.id without committing yet — one all-or-nothing commit below
            created_ids.append(post.id)

        db.commit()
        log.info("expand_plan: created %d post(s) for plan %s", len(created_ids), plan_id)
        return created_ids
    finally:
        db.close()


def _extract_items(plan_json_raw) -> list:
    """plan_json may be a JSON string, a bare list, or a dict wrapping a list."""
    if plan_json_raw is None:
        return []

    if isinstance(plan_json_raw, str):
        try:
            parsed = json.loads(plan_json_raw)
        except json.JSONDecodeError as exc:
            log.error("expand_plan: plan_json is not valid JSON: %s", exc)
            return []
    else:
        parsed = plan_json_raw

    if isinstance(parsed, list):
        return parsed

    if isinstance(parsed, dict):
        for key in _LIST_KEYS:
            value = parsed.get(key)
            if isinstance(value, list):
                return value

    log.error(
        "expand_plan: could not find a list of posts in plan_json (got %s)",
        type(parsed).__name__,
    )
    return []


def _parse_plan_month(month_str: str) -> tuple[int, int]:
    year_str, month_num_str = month_str.split("-")
    return int(year_str), int(month_num_str)


def _resolve_datetime(item: dict, year: int, month_num: int) -> tuple[str | None, str]:
    """Accepts YYYY-MM-DD, DD/MM/YYYY, or a bare day number against the plan's month."""
    scheduled_time = _normalize_time(item.get("scheduled_time") or item.get("time"))

    raw_date = item.get("scheduled_date") or item.get("date")
    if raw_date is None:
        return None, scheduled_time

    raw_date_str = str(raw_date).strip()

    for fmt in _DATE_FORMATS:
        try:
            parsed_date = datetime.datetime.strptime(raw_date_str, fmt).date()
            return parsed_date.isoformat(), scheduled_time
        except ValueError:
            continue

    if raw_date_str.isdigit():
        day = int(raw_date_str)
        try:
            return datetime.date(year, month_num, day).isoformat(), scheduled_time
        except ValueError:
            log.warning(
                "expand_plan: day %s is not valid for %04d-%02d", day, year, month_num
            )
            return None, scheduled_time

    log.warning("expand_plan: unrecognized date format %r", raw_date_str)
    return None, scheduled_time


def _normalize_time(raw_time) -> str:
    if raw_time is None:
        return DEFAULT_TIME
    match = _TIME_RE.match(str(raw_time).strip())
    if not match:
        return DEFAULT_TIME
    hour, minute = match.groups()
    return f"{int(hour):02d}:{minute}"


def backfill_special_day_nulls() -> int:
    """One-time repair for rows written before sanitize_nullish existed.

    Despite the name (kept as specified), this scans every text column a
    pre-Phase-1 prompt could have poisoned with the literal string "null" (or
    "None", "N/A", ...) — not just special_day — since the same bug could hit
    any of them. Intended to run once, right after Phase 1 ships; safe to run
    again since a row with nothing left to fix is simply skipped.
    """
    db = SessionLocal()
    try:
        posts = db.query(Post).all()
        fixed = 0
        for post in posts:
            changed = False
            for column in _BACKFILL_COLUMNS:
                value = getattr(post, column, None)
                sanitized = sanitize_nullish(value)
                if sanitized != value:
                    setattr(post, column, sanitized)
                    changed = True
            if changed:
                fixed += 1
        db.commit()
        log.info("backfill_special_day_nulls: repaired %d post row(s)", fixed)
        return fixed
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    repaired = backfill_special_day_nulls()
    print(f"Backfill complete — {repaired} post row(s) repaired.")
