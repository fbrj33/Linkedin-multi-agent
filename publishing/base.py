from __future__ import annotations

"""
Shared contract every publishing backend (browser, official API, relay) implements.

Why this module exists: publishing is the one step in this pipeline with no
"retry and it's fine" safety net the way a failed email or LLM call has — a
false "posted" here means content is either silently missing from LinkedIn,
or worse, silently posted somewhere it shouldn't have (a personal feed
instead of the company page). ok=True must mean independently verified as
live, never just "no exception was raised."
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class PublishResult:
    ok: bool
    external_id: str | None = None
    url: str | None = None
    error: str | None = None
    needs_human: bool = False


class Publisher(ABC):
    """post is expected to be a database.models.Post (or anything with the
    same .id/.content/.hashtags attributes) — kept as Any here so this module
    has no import-time dependency on the database layer."""

    @abstractmethod
    def publish(self, post: Any) -> PublishResult:
        ...

    @abstractmethod
    def healthcheck(self) -> PublishResult:
        ...


def build_post_text(content: str | None, hashtags: str | list[str] | None) -> str:
    """Combine a post's body and hashtags into exactly what gets typed/submitted."""
    body = (content or "").strip()

    if not hashtags:
        return body

    tags = hashtags.strip() if isinstance(hashtags, str) else " ".join(hashtags)
    if not tags:
        return body
    if not body:
        return tags
    return f"{body}\n\n{tags}"
