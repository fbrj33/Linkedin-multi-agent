from __future__ import annotations

"""
Builds a Publisher from WIMBEE_PUBLISHER — mirrors llm/factory.py's pattern
so switching backends (browser | official | relay | manual) never requires a
code change, just an env var.
"""

import os

from publishing.alternatives import ManualFallbackPublisher, RelayPublisher
from publishing.base import Publisher
from publishing.browser_publisher import BrowserPublisher
from publishing.official import OfficialAPIPublisher

_PUBLISHERS = {
    "browser": BrowserPublisher,
    "official": OfficialAPIPublisher,
    "relay": RelayPublisher,
    "manual": ManualFallbackPublisher,
}

_cache: dict[str, Publisher] = {}


def get_publisher() -> Publisher:
    # Manual handoff is the safe default. LinkedIn prohibits unauthorized
    # third-party tools that automate activity on its site, including posts.
    name = os.getenv("WIMBEE_PUBLISHER", "manual").strip().lower()
    if name not in _PUBLISHERS:
        raise ValueError(f"Unknown WIMBEE_PUBLISHER '{name}', expected one of {sorted(_PUBLISHERS)}")

    if name not in _cache:
        _cache[name] = _PUBLISHERS[name]()
    return _cache[name]


def reset_cache() -> None:
    """Test-only: drop cached publisher instances so env-var changes take effect."""
    _cache.clear()
