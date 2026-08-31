from __future__ import annotations

"""
Two low-effort publishing backends: a webhook relay (Make/Zapier/Ayrshare —
anything that accepts a JSON POST and makes the actual LinkedIn call itself)
and a manual fallback that never posts anything, only tells a human to.
Both exist so WIMBEE_PUBLISHER always resolves to *something* runnable even
with no LinkedIn automation configured at all — silence would mean a post
quietly never goes out, which is worse than an explicit "do this by hand".
"""

import logging
import os
from typing import Any

import requests

from publishing.base import Publisher, PublishResult, build_post_text

log = logging.getLogger(__name__)

# Read fresh on each call rather than frozen at import — see the same note
# in browser_publisher.py about why these can't be module-level constants.


def _relay_webhook_url() -> str:
    return os.getenv("WIMBEE_RELAY_WEBHOOK_URL", "").strip()


def _relay_timeout_seconds() -> float:
    return float(os.getenv("WIMBEE_RELAY_TIMEOUT", "15"))


class RelayPublisher(Publisher):
    """Posts the payload to a Make/Zapier/Ayrshare-style webhook and trusts
    its response — this backend has no way to independently verify LinkedIn
    delivery beyond what the relay itself reports back."""

    def publish(self, post: Any) -> PublishResult:
        webhook_url = _relay_webhook_url()
        if not webhook_url:
            return PublishResult(ok=False, error="WIMBEE_RELAY_WEBHOOK_URL is not set")

        payload = {
            "post_id": post.id,
            "text": build_post_text(post.content, post.hashtags),
            "scheduled_date": post.scheduled_date,
            "scheduled_time": post.scheduled_time,
        }

        try:
            response = requests.post(webhook_url, json=payload, timeout=_relay_timeout_seconds())
        except requests.RequestException as exc:
            log.exception("RelayPublisher.publish: request failed for post %s", post.id)
            return PublishResult(ok=False, error=str(exc))

        if not (200 <= response.status_code < 300):
            return PublishResult(
                ok=False, error=f"Relay returned {response.status_code}: {response.text[:300]}"
            )

        external_id = None
        url = None
        try:
            body = response.json()
            external_id = body.get("external_id") or body.get("id")
            url = body.get("url")
        except ValueError:
            pass

        return PublishResult(ok=True, external_id=external_id, url=url)

    def healthcheck(self) -> PublishResult:
        if not _relay_webhook_url():
            return PublishResult(ok=False, error="WIMBEE_RELAY_WEBHOOK_URL is not set")
        return PublishResult(ok=True)


class ManualFallbackPublisher(Publisher):
    """Never posts anything — surfaces the post to a human instead. The safe
    default when no real backend is configured."""

    def publish(self, post: Any) -> PublishResult:
        log.warning(
            "ManualFallbackPublisher: post %s needs to be published by hand "
            "(no WIMBEE_PUBLISHER backend configured)",
            post.id,
        )
        return PublishResult(
            ok=False, needs_human=True,
            error="No automated publisher configured — post this manually.",
        )

    def healthcheck(self) -> PublishResult:
        return PublishResult(ok=True, needs_human=True, error="Manual fallback is active — nothing is automated")
