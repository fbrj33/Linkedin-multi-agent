from __future__ import annotations

"""
Tests publishing/browser_publisher.py against a fully faked Playwright layer
— no real browser, no network, no LinkedIn. sync_playwright is monkeypatched
to a fake whose locator/wait_for behavior is driven by a small scenario dict,
so each test controls exactly which selectors "exist" on the page.
"""

import datetime

import pytest
from playwright.sync_api import TimeoutError as PWTimeout

from database.models import MonthlyPlan, Post, SessionLocal
from publishing import browser_publisher as bp


@pytest.fixture(autouse=True)
def _run_in_tmp_dir(tmp_path, monkeypatch):
    """browser_publisher.py hardcodes debug_screenshots/ relative to the CWD
    (per spec) — chdir into a scratch directory so tests never write real
    files into the repo."""
    monkeypatch.chdir(tmp_path)


# ---------------------------------------------------------------------------
# Fake Playwright layer
# ---------------------------------------------------------------------------

class _FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def wait_for(self, state="visible", timeout=0):
        if state == "visible":
            if self.selector in self.page.available_selectors:
                return
            raise PWTimeout(f"selector not visible: {self.selector}")
        if state == "detached":
            if self.page.composer_closes:
                return
            raise PWTimeout("composer still open")
        raise PWTimeout(f"unsupported state {state!r}")

    def click(self):
        self.page.clicks.append(self.selector)

    def text_content(self):
        return self.page.actor_text

    def is_visible(self):
        return self.selector in self.page.available_selectors


class _FakeKeyboard:
    def __init__(self):
        self.typed = []
        self.presses = []

    def type(self, char, delay=0):
        self.typed.append(char)

    def press(self, key):
        self.presses.append(key)


class _FakePage:
    def __init__(self, available_selectors=(), actor_text="Wimbee", login_wall=False, composer_closes=True):
        self.available_selectors = set(available_selectors)
        self.actor_text = actor_text
        self.login_wall = login_wall
        self.composer_closes = composer_closes
        self.url = "https://www.linkedin.com/company/wimbee/admin/page-posts/published/"
        self.clicks: list[str] = []
        self.screenshots: list[str] = []
        self.goto_calls: list[str] = []
        self.keyboard = _FakeKeyboard()

    def goto(self, url, wait_until=None):
        self.goto_calls.append(url)
        self.url = "https://www.linkedin.com/uas/login" if self.login_wall else url

    def locator(self, selector):
        return _FakeLocator(self, selector)

    def screenshot(self, path=None, full_page=False):
        self.screenshots.append(path)
        with open(path, "wb") as f:
            f.write(b"fake-png")


class _FakeContext:
    def __init__(self, page):
        self._page = page
        self.closed = False

    def new_page(self):
        return self._page

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, context):
        self._context = context
        self.launch_calls = []

    def launch_persistent_context(self, user_data_dir, headless=True, **kwargs):
        self.launch_calls.append((user_data_dir, headless))
        return self._context


class _FakePlaywrightHandle:
    def __init__(self, context):
        self.chromium = _FakeChromium(context)


class _FakeSyncPlaywright:
    def __init__(self, context):
        self._context = context

    def __enter__(self):
        return _FakePlaywrightHandle(self._context)

    def __exit__(self, *exc_info):
        return False


def _patch_playwright(monkeypatch, page: _FakePage) -> _FakeContext:
    context = _FakeContext(page)
    monkeypatch.setattr(bp, "sync_playwright", lambda: _FakeSyncPlaywright(context))
    return context


DEFAULT_SELECTORS = (
    bp.COMPOSER_TRIGGER_SELECTORS[0],
    bp.ACTOR_CHIP_SELECTORS[0],
    bp.EDITOR_SELECTORS[0],
    bp.POST_BUTTON_SELECTORS[0],
)


def _set_common_env(monkeypatch, allow=True, dry_run=False, org_name="Wimbee"):
    monkeypatch.setenv("WIMBEE_ORG_NAME", org_name)
    monkeypatch.setenv("WIMBEE_ORG_SLUG", "wimbee")
    monkeypatch.setenv("WIMBEE_ALLOW_BROWSER_PUBLISHER", "yes" if allow else "no")
    monkeypatch.setenv("WIMBEE_DRY_RUN", "yes" if dry_run else "no")
    monkeypatch.setenv("WIMBEE_MAX_POSTS_PER_DAY", "3")


def _make_post(**overrides) -> Post:
    db = SessionLocal()
    plan = MonthlyPlan(month="2026-08", plan_json="{}", status="approved")
    db.add(plan)
    db.commit()
    db.refresh(plan)

    defaults = dict(
        plan_id=plan.id,
        theme="AI",
        content="Line one\nLine two",
        hashtags="#Wimbee #Data",
        status="approved",
        scheduled_date="2026-08-10",
        scheduled_time="09:00",
    )
    defaults.update(overrides)
    post = Post(**defaults)
    db.add(post)
    db.commit()
    db.refresh(post)
    db.close()
    return post


# ---------------------------------------------------------------------------
# Config gates — these must trip before any browser is ever launched
# ---------------------------------------------------------------------------

def test_publish_refuses_when_org_name_unset(monkeypatch):
    monkeypatch.delenv("WIMBEE_ORG_NAME", raising=False)
    post = _make_post()
    result = bp.BrowserPublisher().publish(post)
    assert result.ok is False
    assert "WIMBEE_ORG_NAME" in result.error


def test_publish_refuses_when_kill_switch_off(monkeypatch):
    _set_common_env(monkeypatch, allow=False)
    post = _make_post()
    result = bp.BrowserPublisher().publish(post)
    assert result.ok is False
    assert "kill switch" in result.error.lower()


def test_publish_refuses_when_daily_cap_reached(monkeypatch):
    _set_common_env(monkeypatch, allow=True)
    monkeypatch.setenv("WIMBEE_MAX_POSTS_PER_DAY", "1")
    _make_post(status="published", published_at=datetime.datetime.utcnow())

    post = _make_post()
    result = bp.BrowserPublisher().publish(post)
    assert result.ok is False
    assert "daily cap" in result.error.lower()


# ---------------------------------------------------------------------------
# Session death
# ---------------------------------------------------------------------------

def test_publish_detects_session_death(monkeypatch):
    _set_common_env(monkeypatch, allow=True)
    page = _FakePage(login_wall=True)
    _patch_playwright(monkeypatch, page)

    result = bp.BrowserPublisher().publish(_make_post())
    assert result.ok is False
    assert result.needs_human is True
    assert "login" in result.error.lower()
    assert page.screenshots  # failure was captured


# ---------------------------------------------------------------------------
# Identity assertion — the most important guard in this file
# ---------------------------------------------------------------------------

def test_publish_aborts_when_actor_chip_missing(monkeypatch):
    _set_common_env(monkeypatch, allow=True)
    selectors = {bp.COMPOSER_TRIGGER_SELECTORS[0]}  # no actor chip selector present
    page = _FakePage(available_selectors=selectors)
    _patch_playwright(monkeypatch, page)

    result = bp.BrowserPublisher().publish(_make_post())
    assert result.ok is False
    assert "actor chip" in result.error.lower()
    assert page.keyboard.typed == []  # never got as far as typing


def test_publish_aborts_when_actor_is_wrong(monkeypatch):
    _set_common_env(monkeypatch, allow=True, org_name="Wimbee")
    selectors = {bp.COMPOSER_TRIGGER_SELECTORS[0], bp.ACTOR_CHIP_SELECTORS[0]}
    page = _FakePage(available_selectors=selectors, actor_text="Jane Doe (Personal)")
    _patch_playwright(monkeypatch, page)

    result = bp.BrowserPublisher().publish(_make_post())
    assert result.ok is False
    assert "Jane Doe" in result.error
    assert "Wimbee" in result.error
    assert page.keyboard.typed == []


def test_publish_proceeds_when_actor_matches(monkeypatch):
    _set_common_env(monkeypatch, allow=True, dry_run=True, org_name="Wimbee")
    page = _FakePage(available_selectors=DEFAULT_SELECTORS, actor_text="Wimbee — Company Page")
    _patch_playwright(monkeypatch, page)

    result = bp.BrowserPublisher().publish(_make_post(content="hello"))
    assert result.ok is True
    assert page.keyboard.typed  # it did type this time


# ---------------------------------------------------------------------------
# Selector fallbacks / missing elements
# ---------------------------------------------------------------------------

def test_publish_fails_when_no_composer_trigger_found(monkeypatch):
    _set_common_env(monkeypatch, allow=True)
    page = _FakePage(available_selectors=set())
    _patch_playwright(monkeypatch, page)

    result = bp.BrowserPublisher().publish(_make_post())
    assert result.ok is False
    assert "composer trigger" in result.error.lower()


def test_publish_falls_back_to_second_selector(monkeypatch):
    _set_common_env(monkeypatch, allow=True, dry_run=True)
    # Only the *second* candidate selector for each element is present.
    selectors = {
        bp.COMPOSER_TRIGGER_SELECTORS[1],
        bp.ACTOR_CHIP_SELECTORS[1],
        bp.EDITOR_SELECTORS[1],
    }
    page = _FakePage(available_selectors=selectors, actor_text="Wimbee")
    _patch_playwright(monkeypatch, page)

    result = bp.BrowserPublisher().publish(_make_post())
    assert result.ok is True


# ---------------------------------------------------------------------------
# Dry run vs real publish
# ---------------------------------------------------------------------------

def test_dry_run_never_clicks_post_button(monkeypatch):
    _set_common_env(monkeypatch, allow=True, dry_run=True)
    page = _FakePage(available_selectors=DEFAULT_SELECTORS, actor_text="Wimbee")
    _patch_playwright(monkeypatch, page)

    result = bp.BrowserPublisher().publish(_make_post())
    assert result.ok is True
    assert bp.POST_BUTTON_SELECTORS[0] not in page.clicks
    assert any("dry_run" in s for s in page.screenshots)


def test_real_publish_verifies_composer_closed(monkeypatch):
    _set_common_env(monkeypatch, allow=True, dry_run=False)
    page = _FakePage(available_selectors=DEFAULT_SELECTORS, actor_text="Wimbee", composer_closes=True)
    _patch_playwright(monkeypatch, page)

    result = bp.BrowserPublisher().publish(_make_post())
    assert result.ok is True
    assert bp.POST_BUTTON_SELECTORS[0] in page.clicks


def test_real_publish_fails_when_composer_stays_open(monkeypatch):
    _set_common_env(monkeypatch, allow=True, dry_run=False)
    page = _FakePage(available_selectors=DEFAULT_SELECTORS, actor_text="Wimbee", composer_closes=False)
    _patch_playwright(monkeypatch, page)

    result = bp.BrowserPublisher().publish(_make_post())
    assert result.ok is False
    assert "unverified" in result.error.lower() or "did not close" in result.error.lower()
    assert any("composer_still_open" in s for s in page.screenshots)


# ---------------------------------------------------------------------------
# Human-like typing
# ---------------------------------------------------------------------------

def test_typing_uses_shift_enter_between_lines_not_bulk_fill(monkeypatch):
    _set_common_env(monkeypatch, allow=True, dry_run=True)
    page = _FakePage(available_selectors=DEFAULT_SELECTORS, actor_text="Wimbee")
    _patch_playwright(monkeypatch, page)

    bp.BrowserPublisher().publish(_make_post(content="line one\nline two\nline three", hashtags=""))

    assert page.keyboard.presses.count("Shift+Enter") == 2
    assert "".join(page.keyboard.typed) == "line oneline twoline three"


# ---------------------------------------------------------------------------
# healthcheck
# ---------------------------------------------------------------------------

def test_healthcheck_fails_without_org_name(monkeypatch):
    monkeypatch.delenv("WIMBEE_ORG_NAME", raising=False)
    result = bp.BrowserPublisher().healthcheck()
    assert result.ok is False


def test_healthcheck_flags_needs_human_when_profile_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("WIMBEE_ORG_NAME", "Wimbee")
    monkeypatch.setenv("WIMBEE_PROFILE_DIR", str(tmp_path / "does-not-exist"))
    result = bp.BrowserPublisher().healthcheck()
    assert result.ok is False
    assert result.needs_human is True


def test_healthcheck_detects_session_death(monkeypatch, tmp_path):
    monkeypatch.setenv("WIMBEE_ORG_NAME", "Wimbee")
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    monkeypatch.setenv("WIMBEE_PROFILE_DIR", str(profile_dir))

    page = _FakePage(login_wall=True)
    _patch_playwright(monkeypatch, page)

    result = bp.BrowserPublisher().healthcheck()
    assert result.ok is False
    assert result.needs_human is True
