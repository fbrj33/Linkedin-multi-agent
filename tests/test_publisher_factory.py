from __future__ import annotations

"""Tests publishing/factory.py — env-driven backend selection and caching."""

import pytest

from publishing import factory
from publishing.alternatives import ManualFallbackPublisher, RelayPublisher
from publishing.browser_publisher import BrowserPublisher
from publishing.official import OfficialAPIPublisher


@pytest.fixture(autouse=True)
def _reset_factory_cache():
    factory.reset_cache()
    yield
    factory.reset_cache()


def test_factory_switches_backend_via_env_with_no_code_change(monkeypatch):
    monkeypatch.setenv("WIMBEE_PUBLISHER", "browser")
    assert isinstance(factory.get_publisher(), BrowserPublisher)

    factory.reset_cache()
    monkeypatch.setenv("WIMBEE_PUBLISHER", "manual")
    assert isinstance(factory.get_publisher(), ManualFallbackPublisher)

    factory.reset_cache()
    monkeypatch.setenv("WIMBEE_PUBLISHER", "relay")
    assert isinstance(factory.get_publisher(), RelayPublisher)

    factory.reset_cache()
    monkeypatch.setenv("WIMBEE_PUBLISHER", "official")
    assert isinstance(factory.get_publisher(), OfficialAPIPublisher)


def test_factory_rejects_unknown_publisher(monkeypatch):
    monkeypatch.setenv("WIMBEE_PUBLISHER", "not-a-real-backend")
    with pytest.raises(ValueError):
        factory.get_publisher()


def test_factory_caches_instance(monkeypatch):
    monkeypatch.setenv("WIMBEE_PUBLISHER", "manual")
    first = factory.get_publisher()
    second = factory.get_publisher()
    assert first is second
