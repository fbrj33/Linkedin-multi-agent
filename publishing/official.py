from __future__ import annotations

"""
Publishes via LinkedIn's own REST API to a personal profile — the self-serve
path when a company page isn't available or browser automation isn't wanted.

Why this exists separately from browser_publisher.py: the official API
returns a real post URN in its response, so unlike the browser path this
backend CAN populate Post.external_id, and a 2xx response IS the
verification (unlike a browser click, which has no clean success signal).
It requires a LinkedIn Developer app and an OAuth access token for a member
(LINKEDIN_PERSON_URN + a token in linkedin_token.json) — obtaining that token
is a manual, one-time OAuth consent flow outside this module's scope; this
file only ever calls the API with a token that already exists on disk.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import requests

from publishing.base import Publisher, PublishResult, build_post_text

log = logging.getLogger(__name__)

# Read fresh on each call rather than frozen at import — see the same note
# in browser_publisher.py about why these can't be module-level constants.


def _token_file() -> Path:
    return Path(os.getenv("WIMBEE_LINKEDIN_TOKEN_FILE", "linkedin_token.json"))


def _person_urn() -> str:
    return os.getenv("LINKEDIN_PERSON_URN", "").strip()


def _api_base() -> str:
    return os.getenv("WIMBEE_LINKEDIN_API_BASE", "https://api.linkedin.com/v2")


def _request_timeout_seconds() -> float:
    return float(os.getenv("WIMBEE_LINKEDIN_API_TIMEOUT", "15"))


def _load_access_token() -> str | None:
    token_file = _token_file()
    if not token_file.exists():
        return None
    try:
        data = json.loads(token_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.error("official.py: could not read %s: %s", token_file, exc)
        return None
    return data.get("access_token")


class OfficialAPIPublisher(Publisher):
    def publish(self, post: Any) -> PublishResult:
        person_urn = _person_urn()
        if not person_urn:
            return PublishResult(ok=False, error="LINKEDIN_PERSON_URN is not set")

        token = _load_access_token()
        if not token:
            return PublishResult(
                ok=False, needs_human=True,
                error=f"No access token at {_token_file()} — complete the OAuth consent flow first",
            )

        text = build_post_text(post.content, post.hashtags)
        payload = {
            "author": person_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }

        try:
            response = requests.post(
                f"{_api_base()}/ugcPosts",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                json=payload,
                timeout=_request_timeout_seconds(),
            )
        except requests.RequestException as exc:
            log.exception("OfficialAPIPublisher.publish: request failed for post %s", post.id)
            return PublishResult(ok=False, error=str(exc))

        if response.status_code not in (200, 201):
            log.error(
                "OfficialAPIPublisher.publish: LinkedIn returned %s for post %s: %s",
                response.status_code, post.id, response.text[:500],
            )
            needs_human = response.status_code in (401, 403)
            return PublishResult(
                ok=False, needs_human=needs_human,
                error=f"LinkedIn API returned {response.status_code}: {response.text[:300]}",
            )

        # A 2xx here is the verification — LinkedIn's own server accepted
        # the post. Missing the URN just means external_id can't be filled
        # in, not that the publish itself is unverified.
        post_urn = response.headers.get("x-restli-id") or response.headers.get("X-RestLi-Id")
        if not post_urn:
            try:
                post_urn = response.json().get("id")
            except ValueError:
                post_urn = None
        if not post_urn:
            log.warning(
                "OfficialAPIPublisher.publish: post %s accepted (status %s) but no URN "
                "found in the response — external_id will be empty",
                post.id, response.status_code,
            )

        url = f"https://www.linkedin.com/feed/update/{post_urn}/" if post_urn else None
        return PublishResult(ok=True, external_id=post_urn, url=url)

    def healthcheck(self) -> PublishResult:
        token = _load_access_token()
        if not token:
            return PublishResult(ok=False, needs_human=True, error=f"No access token at {_token_file()}")
        if not _person_urn():
            return PublishResult(ok=False, error="LINKEDIN_PERSON_URN is not set")

        try:
            response = requests.get(
                f"{_api_base()}/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_request_timeout_seconds(),
            )
        except requests.RequestException as exc:
            return PublishResult(ok=False, error=str(exc))

        if response.status_code == 401:
            return PublishResult(ok=False, needs_human=True, error="Access token rejected (401) — refresh it")
        if response.status_code != 200:
            return PublishResult(ok=False, error=f"LinkedIn API returned {response.status_code}")
        return PublishResult(ok=True)
