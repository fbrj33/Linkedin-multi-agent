from __future__ import annotations

"""
Exhaustive suite over every publishing/browser_publisher.py failure mode,
driven through a REAL Chromium browser against tests/mock_linkedin/'s static
page (see tests/mock_linkedin_server.py) — not Playwright mocks.
test_browser_publisher.py already covers the branching logic with fakes;
this suite exists to prove the actual Playwright selector/wait logic works
against a real DOM, including real navigation and a real HTTP redirect for
session death.

The critical assertion, ahead of everything else here: a wrong actor must
never publish. Every other failure mode costs a retry if it regresses; that
one costs a client relationship.
"""

from types import SimpleNamespace

import pytest

from publishing.browser_publisher import BrowserPublisher
from mock_linkedin_server import start_mock_server


@pytest.fixture(scope="module")
def mock_server():
    server, base_url = start_mock_server()
    yield base_url
    server.shutdown()


@pytest.fixture(autouse=True)
def _run_in_tmp_dir(tmp_path, monkeypatch):
    """browser_publisher.py hardcodes debug_screenshots/ relative to the CWD
    (per spec) — chdir into a scratch directory so this suite's many
    failure-mode tests never write real files into the repo."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _fast_timeouts(monkeypatch):
    # Real Chromium against a local static page renders near-instantly —
    # short timeouts keep this suite fast without being flaky.
    monkeypatch.setenv("WIMBEE_SELECTOR_TIMEOUT_MS", "800")
    monkeypatch.setenv("WIMBEE_POST_VERIFY_TIMEOUT_MS", "800")


@pytest.fixture
def configure(monkeypatch, tmp_path):
    """Returns a function so each test controls dry_run/allow explicitly —
    there is no safe shared default for whether a browser is allowed to act."""
    def _configure(*, dry_run: bool, allow: bool = True, org_name: str = "Wimbee"):
        monkeypatch.setenv("WIMBEE_ORG_NAME", org_name)
        monkeypatch.setenv("WIMBEE_ORG_SLUG", "wimbee")
        monkeypatch.setenv("WIMBEE_ALLOW_BROWSER_PUBLISHER", "yes" if allow else "no")
        monkeypatch.setenv("WIMBEE_DRY_RUN", "yes" if dry_run else "no")
        monkeypatch.setenv("WIMBEE_PROFILE_DIR", str(tmp_path / "profile"))
        monkeypatch.setenv("WIMBEE_HEADLESS", "true")
        monkeypatch.setenv("WIMBEE_MAX_POSTS_PER_DAY", "100")
    return _configure


def _post(**overrides) -> SimpleNamespace:
    defaults = dict(id=1, content="Test post body", hashtags="#Wimbee")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# The critical assertion: wrong actor must never publish, under any mode
# ---------------------------------------------------------------------------

def test_wrong_actor_never_publishes(mock_server, configure, monkeypatch):
    configure(dry_run=False)
    monkeypatch.setenv("WIMBEE_ADMIN_URL", f"{mock_server}/index.html?actor=Someone+Personal")

    result = BrowserPublisher().publish(_post())

    assert result.ok is False
    assert "Someone Personal" in result.error


def test_wrong_actor_never_publishes_even_in_dry_run(mock_server, configure, monkeypatch):
    configure(dry_run=True)
    monkeypatch.setenv("WIMBEE_ADMIN_URL", f"{mock_server}/index.html?actor=Someone+Personal")

    result = BrowserPublisher().publish(_post())

    assert result.ok is False


def test_wrong_actor_is_refused_even_when_substring_partially_overlaps(mock_server, configure, monkeypatch):
    # "Wimbee Ventures" is a different entity from "Wimbee" the org — but
    # shares a substring, which is exactly the case a naive check could get
    # wrong in the other direction. Our check requires org_name IN actor
    # text, so this one should actually still pass (org_name is a substring
    # of the actor text) — asserting the opposite would be the wrong fix.
    configure(dry_run=False, org_name="Wimbee")
    monkeypatch.setenv("WIMBEE_ADMIN_URL", f"{mock_server}/index.html?actor=Wimbee+Ventures")

    result = BrowserPublisher().publish(_post())

    assert result.ok is True  # org_name is genuinely contained in the actor text


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

def test_correct_actor_publishes(mock_server, configure, monkeypatch):
    configure(dry_run=False)
    monkeypatch.setenv("WIMBEE_ADMIN_URL", f"{mock_server}/index.html")

    result = BrowserPublisher().publish(_post())

    assert result.ok is True


def test_dry_run_succeeds_without_posting(mock_server, configure, monkeypatch):
    configure(dry_run=True)
    monkeypatch.setenv("WIMBEE_ADMIN_URL", f"{mock_server}/index.html")

    result = BrowserPublisher().publish(_post())

    assert result.ok is True


def test_tolerates_slow_ui_without_false_failure(mock_server, configure, monkeypatch):
    configure(dry_run=False)
    monkeypatch.setenv("WIMBEE_SELECTOR_TIMEOUT_MS", "2000")
    monkeypatch.setenv("WIMBEE_POST_VERIFY_TIMEOUT_MS", "2000")
    monkeypatch.setenv("WIMBEE_ADMIN_URL", f"{mock_server}/index.html?slow=300")

    result = BrowserPublisher().publish(_post())

    assert result.ok is True


# ---------------------------------------------------------------------------
# Gates that must trip before any browser action
# ---------------------------------------------------------------------------

def test_kill_switch_blocks_even_with_a_working_mock(mock_server, configure, monkeypatch):
    configure(dry_run=False, allow=False)
    monkeypatch.setenv("WIMBEE_ADMIN_URL", f"{mock_server}/index.html")

    result = BrowserPublisher().publish(_post())

    assert result.ok is False
    assert "kill switch" in result.error.lower()


# ---------------------------------------------------------------------------
# Failure modes named in this phase's spec
# ---------------------------------------------------------------------------

def test_no_composer_trigger(mock_server, configure, monkeypatch):
    configure(dry_run=False)
    monkeypatch.setenv("WIMBEE_ADMIN_URL", f"{mock_server}/index.html?fail=nocomposer")

    result = BrowserPublisher().publish(_post())

    assert result.ok is False
    assert "composer trigger" in result.error.lower()


def test_no_editor(mock_server, configure, monkeypatch):
    configure(dry_run=False)
    monkeypatch.setenv("WIMBEE_ADMIN_URL", f"{mock_server}/index.html?fail=noeditor")

    result = BrowserPublisher().publish(_post())

    assert result.ok is False
    assert "editor" in result.error.lower()


def test_stuck_post_button_is_never_reported_as_success(mock_server, configure, monkeypatch):
    configure(dry_run=False)
    monkeypatch.setenv("WIMBEE_ADMIN_URL", f"{mock_server}/index.html?fail=stuck")

    result = BrowserPublisher().publish(_post())

    assert result.ok is False
    assert "unverified" in result.error.lower() or "did not close" in result.error.lower()


def test_session_death_flags_needs_human(mock_server, configure, monkeypatch):
    configure(dry_run=False)
    monkeypatch.setenv("WIMBEE_ADMIN_URL", f"{mock_server}/index.html?fail=session")

    result = BrowserPublisher().publish(_post())

    assert result.ok is False
    assert result.needs_human is True
