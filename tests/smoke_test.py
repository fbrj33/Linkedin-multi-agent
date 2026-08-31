#!/usr/bin/env python
from __future__ import annotations


import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_linkedin_server import start_mock_server  # noqa: E402


def _configure_env(base_url: str, tmp_dir: Path) -> None:
    os.environ["WIMBEE_DATABASE_URL"] = f"sqlite:///{tmp_dir / 'smoke.db'}"
    os.environ["WIMBEE_ORG_NAME"] = "Wimbee"
    os.environ["WIMBEE_ORG_SLUG"] = "wimbee"
    os.environ["WIMBEE_ALLOW_BROWSER_PUBLISHER"] = "yes"
    os.environ["WIMBEE_DRY_RUN"] = "no"
    os.environ["WIMBEE_PROFILE_DIR"] = str(tmp_dir / "profile")
    os.environ["WIMBEE_HEADLESS"] = "true"
    os.environ["WIMBEE_MAX_POSTS_PER_DAY"] = "100"
    os.environ["WIMBEE_ADMIN_URL"] = f"{base_url}/index.html"


def main() -> int:
    start = time.time()

    with tempfile.TemporaryDirectory(prefix="wimbee-smoke-") as tmp:
        tmp_dir = Path(tmp)
        server, base_url = start_mock_server()
        try:
            _configure_env(base_url, tmp_dir)

            from database.models import init_db
            init_db()

            from publishing.browser_publisher import BrowserPublisher

            publisher = BrowserPublisher()

            print("[1/2] Publishing as the correct actor...")
            post = SimpleNamespace(id=1, content="Hello from the Wimbee smoke test", hashtags="#Wimbee #Smoke")
            result = publisher.publish(post)
            if not result.ok:
                print(f"FAILED: expected a successful publish, got: {result.error}")
                return 1
            print("    OK — composer closed, publish verified.")

            print("[2/2] Publishing as the wrong actor (identity guard)...")
            os.environ["WIMBEE_ADMIN_URL"] = f"{base_url}/index.html?actor=Someone+Else"
            wrong_actor_post = SimpleNamespace(id=2, content="This must never post", hashtags="")
            result = publisher.publish(wrong_actor_post)
            if result.ok:
                print("FAILED: publish succeeded with the wrong actor — identity guard did not stop it")
                return 1
            print(f"    OK — refused: {result.error}")
        finally:
            server.shutdown()
            # SQLAlchemy's connection pool holds the sqlite file open, which
            # blocks the TemporaryDirectory cleanup below on Windows.
            from database.models import engine
            engine.dispose()

    elapsed = time.time() - start
    print(f"\nSmoke test passed in {elapsed:.1f}s")
    if elapsed > 30:
        print("WARNING: exceeded the 30s budget for this test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
