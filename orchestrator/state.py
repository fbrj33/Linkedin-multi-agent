from __future__ import annotations

"""
TypedDict state shapes for the plan and post graphs.

Kept flat and JSON-serializable — no ORM objects — since this is exactly
what gets written to the checkpointer on every node transition.
"""

from typing import Optional, TypedDict


class PlanState(TypedDict, total=False):
    month: str
    trends: list
    performance_brief: dict
    plan_id: Optional[int]
    approval_token: Optional[str]
    deadline: Optional[str]  # ISO string
    decision: Optional[str]  # "approved" | "rejected" | "expired"
    post_ids: list


class PostState(TypedDict, total=False):
    post_id: int
    theme: Optional[str]
    format: Optional[str]
    scheduled_date: Optional[str]
    scheduled_time: Optional[str]
    special_day: Optional[str]
    trend_source: Optional[str]
    brief: Optional[str]
    content: Optional[str]
    hashtags: Optional[str]
    predicted_score: Optional[float]
    score_reason: Optional[str]
    refine_count: int
    retry_count: int
    rejection_reason: Optional[str]
    approval_token: Optional[str]
    deadline: Optional[str]
    decision: Optional[str]
    published: bool
    external_id: Optional[str]
    publish_error: Optional[str]
    publish_attempt: int
    needs_human: bool
