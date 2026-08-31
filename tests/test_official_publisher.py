from __future__ import annotations

"""Tests publishing/official.py with requests.post/get monkeypatched — no network."""

import json
from types import SimpleNamespace

from database.models import MonthlyPlan, Post, SessionLocal
from publishing import official


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


def _fake_response(status_code=201, headers=None, body=None, text=""):
    return SimpleNamespace(
        status_code=status_code,
        headers=headers or {},
        text=text,
        json=lambda: body if body is not None else {},
    )


def _write_token(tmp_path, monkeypatch, token="tok-123"):
    token_file = tmp_path / "linkedin_token.json"
    token_file.write_text(json.dumps({"access_token": token}), encoding="utf-8")
    monkeypatch.setenv("WIMBEE_LINKEDIN_TOKEN_FILE", str(token_file))


def test_publish_fails_without_person_urn(monkeypatch):
    monkeypatch.delenv("LINKEDIN_PERSON_URN", raising=False)
    result = official.OfficialAPIPublisher().publish(_make_post())
    assert result.ok is False
    assert "LINKEDIN_PERSON_URN" in result.error


def test_publish_needs_human_when_token_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDIN_PERSON_URN", "urn:li:person:123")
    monkeypatch.setenv("WIMBEE_LINKEDIN_TOKEN_FILE", str(tmp_path / "missing.json"))
    result = official.OfficialAPIPublisher().publish(_make_post())
    assert result.ok is False
    assert result.needs_human is True


def test_publish_success_captures_urn_from_header(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDIN_PERSON_URN", "urn:li:person:123")
    _write_token(tmp_path, monkeypatch)

    monkeypatch.setattr(
        official.requests, "post",
        lambda *a, **kw: _fake_response(201, headers={"x-restli-id": "urn:li:share:999"}),
    )

    result = official.OfficialAPIPublisher().publish(_make_post())
    assert result.ok is True
    assert result.external_id == "urn:li:share:999"
    assert "urn:li:share:999" in result.url


def test_publish_success_without_urn_still_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDIN_PERSON_URN", "urn:li:person:123")
    _write_token(tmp_path, monkeypatch)

    monkeypatch.setattr(
        official.requests, "post",
        lambda *a, **kw: _fake_response(201, headers={}, body={}),
    )

    result = official.OfficialAPIPublisher().publish(_make_post())
    assert result.ok is True
    assert result.external_id is None


def test_publish_401_is_needs_human(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDIN_PERSON_URN", "urn:li:person:123")
    _write_token(tmp_path, monkeypatch)

    monkeypatch.setattr(
        official.requests, "post",
        lambda *a, **kw: _fake_response(401, text="unauthorized"),
    )

    result = official.OfficialAPIPublisher().publish(_make_post())
    assert result.ok is False
    assert result.needs_human is True


def test_healthcheck_detects_expired_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDIN_PERSON_URN", "urn:li:person:123")
    _write_token(tmp_path, monkeypatch)
    monkeypatch.setattr(official.requests, "get", lambda *a, **kw: _fake_response(401))

    result = official.OfficialAPIPublisher().healthcheck()
    assert result.ok is False
    assert result.needs_human is True
