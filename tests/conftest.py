# tests/conftest.py
# ---------------------------------------------------------------------------
# Shared pytest fixtures for the CRM FastAPI test suite.
# Rails equivalent: spec/support/ + spec/rails_helper.rb + FactoryBot factories
#
# Pattern changes:
#   Rails: RSpec + FactoryBot + DatabaseCleaner
#   Python: pytest + pytest-asyncio + httpx AsyncClient + SQLite in-memory
# ---------------------------------------------------------------------------

import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import create_access_token, hash_password
from database import Base, get_db
from main import app

# ---------------------------------------------------------------------------
# Use an in-memory SQLite database for tests
# Rails: config/database.yml test: adapter: sqlite3, database: :memory:
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
def event_loop():
    """Rails: no equivalent — async test event loop management."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """Create engine and schema once per test session."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    async with engine.begin() as conn:
        # Import all models to register with Base
        from models.account import Account, AccountContact, AccountOpportunity  # noqa
        from models.contact import Contact, ContactOpportunity  # noqa
        from models.lead import Lead  # noqa
        from models.opportunity import Opportunity  # noqa
        from models.user import User  # noqa
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """
    Rails: DatabaseCleaner strategy :transaction — rolls back after each test.
    Each test gets a fresh transaction that is rolled back on completion.
    """
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        async with session.begin():
            yield session
            await session.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """
    Rails: spec/support/request_helpers.rb — configures request helpers.
    Provides an httpx AsyncClient connected to the test app.
    Overrides the DB dependency to use the test transaction session.
    """

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession):
    """
    Rails: FactoryBot.create(:user) — creates a real user in DB for auth tests.
    Required because get_current_user now verifies user exists in DB.
    """
    from models.user import User
    user = User(
        id=1,
        email="test@example.com",
        hashed_password=hash_password("password"),
        first_name="Test",
        last_name="User",
        admin=False,
        active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession):
    """Rails: FactoryBot.create(:admin_user)"""
    from models.user import User
    user = User(
        id=99,
        email="admin@example.com",
        hashed_password=hash_password("password"),
        first_name="Admin",
        last_name="User",
        admin=True,
        active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
def auth_headers() -> dict:
    """
    Rails: Devise test helpers (sign_in user) / request.headers["Authorization"].
    Returns JWT Bearer headers for an authenticated test user.
    """
    token = create_access_token(user_id=1, email="test@example.com", is_admin=False)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
def admin_headers() -> dict:
    """Rails: sign_in admin_user"""
    token = create_access_token(user_id=99, email="admin@example.com", is_admin=True)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Factory helpers (Rails: FactoryBot factories)
# ---------------------------------------------------------------------------

def make_account_payload(**kwargs) -> dict:
    """Rails: FactoryBot.build(:account)"""
    defaults = {
        "name": "Test Corp",
        "access": "Public",
        "phone": "555-0100",
        "email": "info@testcorp.example.com",
        "rating": 3,
        "category": "Technology",
    }
    defaults.update(kwargs)
    return defaults


def make_contact_payload(**kwargs) -> dict:
    """Rails: FactoryBot.build(:contact)"""
    defaults = {
        "first_name": "Jane",
        "last_name": "Doe",
        "access": "Public",
        "email": "jane.doe@example.com",
        "phone": "555-0101",
        "do_not_call": False,
    }
    defaults.update(kwargs)
    return defaults


def make_lead_payload(**kwargs) -> dict:
    """Rails: FactoryBot.build(:lead)"""
    defaults = {
        "first_name": "John",
        "last_name": "Smith",
        "company": "Prospect Inc",
        "email": "john.smith@prospect.example.com",
        "access": "Public",
        "rating": 2,
        "do_not_call": False,
        "status": "new",
    }
    defaults.update(kwargs)
    return defaults


def make_opportunity_payload(**kwargs) -> dict:
    """Rails: FactoryBot.build(:opportunity)"""
    defaults = {
        "name": "Q4 Deal",
        "stage": "prospecting",
        "probability": 25,
        "amount": "50000.00",
        "closes_on": "2025-12-31",
        "access": "Public",
    }
    defaults.update(kwargs)
    return defaults
