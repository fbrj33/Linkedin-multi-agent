"""
Pytest configuration and shared fixtures for Wimbee tests.
"""

import pytest
import os
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Set test database URL to avoid corrupting production DB
os.environ["WIMBEE_DATABASE_URL"] = "sqlite:///test_wimbee.db"
os.environ["LLM_PROVIDER"] = "xai"

from database.models import Base, engine, SessionLocal


@pytest.fixture(scope="function")
def test_db():
    """Create fresh test database for each test."""
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    
    # Clean up test DB file
    if os.path.exists("test_wimbee.db"):
        os.remove("test_wimbee.db")


@pytest.fixture
def db_session(test_db):
    """Provide a database session for tests."""
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def admin_email():
    """Provide admin email for testing."""
    return os.getenv("ADMIN_EMAIL", "admin@wimbee.local")