# database.py
# ---------------------------------------------------------------------------
# Rails equivalent: config/database.yml + db/schema.rb + ApplicationRecord
#
# Key pattern changes:
#   - Rails uses ActiveRecord with synchronous I/O.
#     Here we use SQLAlchemy 2.0 with asyncpg driver for full async I/O.
#   - Rails connection pool is configured in database.yml.
#     Here pool_size, max_overflow are set on create_async_engine().
#   - Rails auto-timestamp (created_at/updated_at) via ActiveRecord::Base
#     is replicated via SQLAlchemy's func.now() server_default + onupdate.
#   - Soft-delete (deleted_at) from Rails acts_as_paranoid is preserved
#     as a nullable DateTime column; filtering is done at query time.
# ---------------------------------------------------------------------------

import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, MappedColumn, mapped_column
from sqlalchemy import DateTime, func

# ---------------------------------------------------------------------------
# Database URL
# Rails: DATABASE_URL or config/database.yml
# FastAPI: read from environment variable, fall back to a local dev default.
# ---------------------------------------------------------------------------
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/crm_fastapi",
)

# ---------------------------------------------------------------------------
# Async engine
# Rails: ActiveRecord connection pool (pool: 5 in database.yml)
# ---------------------------------------------------------------------------
engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,          # Rails default pool: 5 — we double for API concurrency
    max_overflow=20,
    pool_pre_ping=True,    # Check connection health before use
    echo=False,            # Set True for SQL debug output (Rails: config.log_level)
)

# ---------------------------------------------------------------------------
# Session factory
# Rails: ApplicationRecord inherits ActiveRecord::Base which manages sessions.
# FastAPI: We create a new async session per request via dependency injection.
# ---------------------------------------------------------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,   # Prevent "DetachedInstanceError" after commit
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# Declarative base
# Rails: class MyModel < ApplicationRecord
# SQLAlchemy: class MyModel(Base)
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    """
    Shared base for all ORM models.
    Mirrors Rails ApplicationRecord < ActiveRecord::Base.
    """
    pass


# ---------------------------------------------------------------------------
# FastAPI dependency: yields a DB session per request, auto-closes on done.
# Rails equivalent: ActiveRecord connection checkout from the pool per request.
# ---------------------------------------------------------------------------
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency injected into every FastAPI route that needs DB access.

    Usage:
        @router.get("/contacts")
        async def list_contacts(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
