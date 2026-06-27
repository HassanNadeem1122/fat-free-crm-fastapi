# models/user.py
# ---------------------------------------------------------------------------
# Rails equivalent: app/models/user.rb (Devise)
#
# Rails → SQLAlchemy pattern map:
#   devise :database_authenticatable    → bcrypt password hashing (passlib)
#   validates :email, uniqueness: true  → UniqueConstraint on email column
#   before_save :downcase_email         → SQLAlchemy event listener
#   attr_accessor :remember_token       → JWT replaces remember tokens
# ---------------------------------------------------------------------------

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class User(Base):
    """
    Rails: class User < ApplicationRecord (with Devise)
    Stores CRM users who can log in and own/be assigned to CRM records.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Rails: Devise :database_authenticatable
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # Rails: Devise :trackable
    first_name: Mapped[Optional[str]] = mapped_column(String(64))
    last_name: Mapped[Optional[str]] = mapped_column(String(64))

    # Rails: admin boolean on User
    admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Rails: ActiveRecord timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Rails: acts_as_paranoid (soft delete)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"
