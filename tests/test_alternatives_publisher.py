from __future__ import annotations

"""Tests publishing/alternatives.py with requests.post monkeypatched — no network."""

from types import SimpleNamespace

from database.models import MonthlyPlan, Post, SessionLocal
from publishing import alternatives


def _make_post(**overrides) -> Post:
    db = SessionLocal()
    plan = MonthlyPlan(month="2026-08", plan_json="{}", status="approved")
    db.add(plan)
    db.commit()
    db.refresh(plan)
    defaults = dict(plan_id=plan.id, theme="AI", content="hello", hashtags="#Wimbee", status="approved")
    defaults.update(overrides)
    post = Post(**defaults)
    db.add(post)
    db.commit()
    db.refresh(post)
    db.close()
    return post


def _fake_response(status_code=200, body=None, text=""):
    return SimpleNamespace(status_code=status_code, text=text, json=lambda: body if body is not None else {})


def test_relay_fails_without_webhook_url(monkeypatch):
    monkeypatch.delenv("WIMBEE_RELAY_WEBHOOK_URL", raising=False)
    result = alternatives.RelayPublisher().publish(_make_post())
    assert result.ok is False


def test_relay_success_parses_body(monkeypatch):
    monkeypatch.setenv("WIMBEE_RELAY_WEBHOOK_URL", "https://relay.example.com/hook")
    monkeypatch.setattr(
        alternatives.requests, "post",
        lambda *a, **kw: _fake_response(200, body={"external_id": "abc", "url": "https://x/abc"}),
    )
    result = alternatives.RelayPublisher().publish(_make_post())
    assert result.ok is True
    assert result.external_id == "abc"
    assert result.url == "https://x/abc"


def test_relay_failure_status(monkeypatch):
    monkeypatch.setenv("WIMBEE_RELAY_WEBHOOK_URL", "https://relay.example.com/hook")
    monkeypatch.setattr(alternatives.requests, "post", lambda *a, **kw: _fake_response(500, text="boom"))
    result = alternatives.RelayPublisher().publish(_make_post())
    assert result.ok is False


def test_manual_fallback_always_needs_human():
    result = alternatives.ManualFallbackPublisher().publish(_make_post())
    assert result.ok is False
    assert result.needs_human is True
