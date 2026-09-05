from __future__ import annotations

"""
Publishes an approved Post to a LinkedIn company page by driving a real,
persistent Chromium profile through the composer UI.

Why this exists, and why it's built this way:

- launch_persistent_context with a real user-data dir (not storage_state) —
  a cookie-only session gets invalidated far more aggressively than a full
  profile. The profile keeps localStorage/IndexedDB too, which is what makes
  the session last months instead of days.

- The identity assertion (read the composer's author chip, abort unless it
  contains WIMBEE_ORG_NAME) is the single most important thing in this file.
  LinkedIn's composer can default to posting as a personal profile instead of
  the intended company page — after any UI change, a silent default-actor
  switch is exactly the failure mode that turns into "client content posted
  to someone's personal feed." Every other requirement here is secondary to
  never letting that happen.

- Login is never automated. WIMBEE_ALLOW_BROWSER_PUBLISHER, the identity
  check, and `python -m publishing.browser_publisher login` all exist because
  a scripted login flow is the single strongest signal LinkedIn's anti-abuse
  systems look for — this module only ever reuses a session a human created
  by hand.

- ok=True is only returned once the composer is confirmed closed after
  clicking Post. If it's still open, that's a failure, not an unverified
  success — LinkedIn gives no clean "your post is live" signal, so "the
  composer went away" is the least-bad proxy available, and anything less
  sure than that must not be reported as success.
"""

import datetime
import json
import logging
import os
import random
import sys
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

from database.models import Post, SessionLocal
from publishing.base import Publisher, PublishResult, build_post_text

log = logging.getLogger(__name__)

SCREENSHOT_DIR = Path("debug_screenshots")

# Every one of these is read fresh on each call, not frozen at import time —
# a module-level constant sourced from os.getenv() would silently ignore
# monkeypatch.setenv() in tests (the env var convention this codebase
# follows requires values to be injectable without monkeypatching code).


def _org_name() -> str:
    return os.getenv("WIMBEE_ORG_NAME", "").strip()


def _org_slug() -> str:
    return os.getenv("WIMBEE_ORG_SLUG", _org_name()).strip()


def _allow_browser_publisher() -> bool:
    return os.getenv("WIMBEE_ALLOW_BROWSER_PUBLISHER", "no").strip().lower() == "yes"


def _dry_run() -> bool:
    return os.getenv("WIMBEE_DRY_RUN", "yes").strip().lower() == "yes"


def _admin_url_template() -> str:
    return os.getenv(
        "WIMBEE_ADMIN_URL", "https://www.linkedin.com/company/{slug}/admin/page-posts/published/"
    )


def _profile_dir() -> str:
    return os.getenv("WIMBEE_PROFILE_DIR", ".li_profile")


def _max_posts_per_day() -> int:
    return int(os.getenv("WIMBEE_MAX_POSTS_PER_DAY", "3"))


def _headless() -> bool:
    return os.getenv("WIMBEE_HEADLESS", "true").strip().lower() != "false"

# Each list is tried in order — first entry is the current mock page / current
# LinkedIn DOM as of this writing; the rest are fallbacks for whenever
# LinkedIn reships the composer (which it does regularly).
COMPOSER_TRIGGER_SELECTORS = [
    "button.share-box-feed-entry__trigger",
    "button[aria-label='Start a post']",
    "button[aria-label*='Create a post']",
]
ACTOR_CHIP_SELECTORS = [
    ".share-box__actor-name",
    "[data-test-id='share-box-actor-name']",
    ".share-creation-state__actor .feed-shared-actor__name",
]
EDITOR_SELECTORS = [
    "div.ql-editor[contenteditable='true']",
    "div[data-placeholder][role='textbox']",
]
POST_BUTTON_SELECTORS = [
    "button.share-actions__primary-action",
    "button[aria-label='Post']",
    "button[data-control-name='share.post']",
]

def _selector_timeout_ms() -> int:
    return int(os.getenv("WIMBEE_SELECTOR_TIMEOUT_MS", "4000"))


def _post_verify_timeout_ms() -> int:
    return int(os.getenv("WIMBEE_POST_VERIFY_TIMEOUT_MS", "20000"))


def _admin_url(slug: str) -> str:
    return _admin_url_template().format(slug=slug)


def _first_visible(page: Page, selectors: list[str], timeout_ms: int | None = None):
    """Try each selector in turn; return the first Locator that becomes visible, else None."""
    timeout = timeout_ms if timeout_ms is not None else _selector_timeout_ms()
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=timeout)
            return locator
        except PWTimeout:
            continue
    return None


def _looks_like_login_wall(page: Page) -> bool:
    url = page.url.lower()
    if "/login" in url or "/uas/login" in url or "checkpoint/challenge" in url:
        return True
    return page.locator("input#username, input[name='session_key']").first.is_visible()


def _screenshot(page: Page, tag: str) -> str:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
    path = SCREENSHOT_DIR / f"{tag}_{timestamp}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        log.exception("Failed to capture debug screenshot for tag=%s", tag)
        return ""
    return str(path)


def _type_human_like(page: Page, editor, text: str) -> None:
    """Type line by line with Shift+Enter breaks and jittered per-character delay.

    A bulk fill() collapses paragraph formatting in the Quill editor and
    mangles multi-paragraph posts — this is why it isn't used here.
    """
    editor.click()
    lines = text.split("\n")
    for index, line in enumerate(lines):
        for char in line:
            page.keyboard.type(char, delay=random.uniform(15, 60))
        if index < len(lines) - 1:
            page.keyboard.press("Shift+Enter")


def _carousel_paths(post: Post) -> list[str]:
    """Return existing carousel files in slide order, or the legacy image."""
    if getattr(post, "carousel_json", None):
        try:
            slides = json.loads(post.carousel_json)
            paths = [slide.get("image_path") for slide in slides if slide.get("image_path")]
            existing = [path for path in paths if os.path.isfile(path)]
            if existing:
                return existing
        except (TypeError, ValueError, json.JSONDecodeError):
            log.warning("Could not parse carousel metadata for post %s", post.id)
    image_path = getattr(post, "image_path", None)
    return [image_path] if image_path and os.path.isfile(image_path) else []


def _count_published_today(db) -> int:
    start_of_day = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(Post)
        .filter(Post.status == "published", Post.published_at >= start_of_day)
        .count()
    )


class BrowserPublisher(Publisher):
    def publish(self, post: Post) -> PublishResult:
        org_name = _org_name()
        if not org_name:
            return PublishResult(ok=False, error="WIMBEE_ORG_NAME is not set — refusing to run")

        if not _allow_browser_publisher():
            return PublishResult(
                ok=False,
                error="WIMBEE_ALLOW_BROWSER_PUBLISHER is not 'yes' — kill switch is engaged",
            )

        db = SessionLocal()
        try:
            published_today = _count_published_today(db)
        finally:
            db.close()

        cap = _max_posts_per_day()
        if published_today >= cap:
            return PublishResult(
                ok=False,
                error=f"daily cap reached ({published_today}/{cap} posts published today)",
            )

        text = build_post_text(post.content, post.hashtags)

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(_profile_dir(), headless=_headless())
                try:
                    page = context.new_page()
                    return self._publish_in_page(page, text, org_name)
                finally:
                    context.close()
        except Exception as exc:
            log.exception("BrowserPublisher.publish: unexpected error for post %s", post.id)
            return PublishResult(ok=False, error=str(exc))

    def _publish_in_page(self, page: Page, text: str, org_name: str) -> PublishResult:
        page.goto(_admin_url(_org_slug()), wait_until="domcontentloaded")

        if _looks_like_login_wall(page):
            _screenshot(page, "session_death")
            return PublishResult(
                ok=False,
                needs_human=True,
                error=(
                    "LinkedIn redirected to a login page — the saved session has died. "
                    "Re-run `python -m publishing.browser_publisher login` to restore it."
                ),
            )

        trigger = _first_visible(page, COMPOSER_TRIGGER_SELECTORS)
        if trigger is None:
            _screenshot(page, "no_composer_trigger")
            return PublishResult(ok=False, error="Could not find the composer trigger button")
        trigger.click()

        actor_chip = _first_visible(page, ACTOR_CHIP_SELECTORS)
        if actor_chip is None:
            _screenshot(page, "no_actor_chip")
            return PublishResult(
                ok=False,
                error="Could not read the composer's actor chip — refusing to post "
                "since identity can't be verified",
            )

        actor_text = (actor_chip.text_content() or "").strip()
        if org_name.lower() not in actor_text.lower():
            _screenshot(page, "wrong_actor")
            return PublishResult(
                ok=False,
                error=f"Composer is posting as {actor_text!r}, not {org_name!r} — aborted",
            )

        editor = _first_visible(page, EDITOR_SELECTORS)
        if editor is None:
            _screenshot(page, "no_editor")
            return PublishResult(ok=False, error="Could not find the post editor")

        _type_human_like(page, editor, text)

        image_paths = _carousel_paths(post)
        if image_paths:
            file_input = page.locator("input[type='file']").first
            file_input.set_input_files(image_paths)
            page.wait_for_timeout(3000)

        if _dry_run():
            screenshot_path = _screenshot(page, "dry_run")
            log.info("Dry run — composed post as %s, screenshot at %s", actor_text, screenshot_path)
            return PublishResult(ok=True, error=None)

        post_button = _first_visible(page, POST_BUTTON_SELECTORS)
        if post_button is None:
            _screenshot(page, "no_post_button")
            return PublishResult(ok=False, error="Could not find the Post button")
        post_button.click()

        try:
            page.locator(EDITOR_SELECTORS[0]).first.wait_for(state="detached", timeout=_post_verify_timeout_ms())
        except PWTimeout:
            _screenshot(page, "composer_still_open")
            return PublishResult(
                ok=False,
                error="Composer did not close after clicking Post — publish unverified, treating as failed",
            )

        # LinkedIn's UI gives no post URN here; a browser-automated publish
        # can confirm "it went through" but not capture an external_id the
        # way publishing/official.py's API call can.
        return PublishResult(ok=True)

    def healthcheck(self) -> PublishResult:
        if not _org_name():
            return PublishResult(ok=False, error="WIMBEE_ORG_NAME is not set")
        profile_dir = _profile_dir()
        if not os.path.isdir(profile_dir):
            return PublishResult(
                ok=False, needs_human=True,
                error=f"No profile at {profile_dir} — run `python -m publishing.browser_publisher login`",
            )

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(profile_dir, headless=_headless())
                try:
                    page = context.new_page()
                    page.goto(_admin_url(_org_slug()), wait_until="domcontentloaded")
                    if _looks_like_login_wall(page):
                        return PublishResult(
                            ok=False, needs_human=True,
                            error="Session has expired — re-run the login helper",
                        )
                    return PublishResult(ok=True)
                finally:
                    context.close()
        except Exception as exc:
            log.exception("BrowserPublisher.healthcheck failed")
            return PublishResult(ok=False, error=str(exc))


def run_login_helper() -> None:
    """Opens a headed browser for the operator to log in by hand, then
    verifies admin access to WIMBEE_ORG_SLUG and leaves the session saved in
    PROFILE_DIR (launch_persistent_context writes there continuously — no
    separate save step is needed). Never fills in credentials itself.
    """
    if not _org_name():
        print("WIMBEE_ORG_NAME is not set — set it before running the login helper.")
        return

    profile_dir = _profile_dir()
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            profile_dir, headless=False, viewport={"width": 1280, "height": 800}
        )
        try:
            page = context.new_page()
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

            print("A browser window has opened. Log in to LinkedIn by hand.")
            input("Press Enter once you're logged in and can see your feed... ")

            admin_url = _admin_url(_org_slug())
            page.goto(admin_url, wait_until="domcontentloaded")
            print(f"Opened {admin_url} — confirm in the browser that you have admin access to this page.")
            input("Press Enter once you've confirmed admin access... ")
        finally:
            context.close()

    print(f"Session saved to {profile_dir}. The publisher can now reuse it.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        run_login_helper()
    else:
        print("Usage: python -m publishing.browser_publisher login")
