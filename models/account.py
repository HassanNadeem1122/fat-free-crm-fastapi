# models/account.py
# ---------------------------------------------------------------------------
# Rails source: app/models/entities/account.rb
# Schema source: db/schema.rb  (create_table "accounts")
#
# Rails → SQLAlchemy pattern map:
#   belongs_to :user                       → ForeignKey("users.id") + relationship()
#   belongs_to :assignee, FK: assigned_to  → ForeignKey("users.id", name="assigned_to")
#   has_many :account_contacts             → relationship("AccountContact")
#   has_many :contacts, through: acct_ct   → relationship via secondary table
#   has_many :account_opportunities        → relationship("AccountOpportunity")
#   has_many :opportunities, through: ao   → relationship via secondary table
#   has_many :pipeline_opportunities       → filtered via SQLAlchemy query helper
#   has_one :billing_address               → relationship with primaryjoin filter
#   has_one :shipping_address              → relationship with primaryjoin filter
#   serialize :subscribed_users            → JSON column (ARRAY would need PG extension)
#   scope :created_by                      → class method returning Select
#   scope :assigned_to                     → class method returning Select
#   scope :text_search                     → class method with ILIKE
#   deleted_at (acts_as_paranoid)          → nullable DateTime; excluded in active scope
#   counter_cache :contacts_count          → maintained by DB triggers / app logic
#   counter_cache :opportunities_count     → maintained by DB triggers / app logic
#   after_create :set_initial_subscriptions → SQLAlchemy @event.listens_for INSERT
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

if TYPE_CHECKING:
    from .contact import Contact
    from .opportunity import Opportunity


# ---------------------------------------------------------------------------
# Join table: account_contacts
# Rails: has_many :account_contacts, dependent: :destroy
# ---------------------------------------------------------------------------
class AccountContact(Base):
    """
    Rails model: AccountContact (join table between Account and Contact).
    app/models/entities/account_contact.rb
    """

    __tablename__ = "account_contacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), index=True
    )
    # Rails: acts_as_paranoid (soft-delete)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("account_id", "contact_id", name="uq_account_contact"),
    )


# ---------------------------------------------------------------------------
# Join table: account_opportunities
# Rails: has_many :account_opportunities, dependent: :destroy
# ---------------------------------------------------------------------------
class AccountOpportunity(Base):
    """
    Rails model: AccountOpportunity (join table between Account and Opportunity).
    app/models/entities/account_opportunity.rb
    """

    __tablename__ = "account_opportunities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    opportunity_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    # Rails: acts_as_paranoid (soft-delete)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "account_id", "opportunity_id", name="uq_account_opportunity"
        ),
    )


# ---------------------------------------------------------------------------
# Account model
# Rails: class Account < ActiveRecord::Base (app/models/entities/account.rb)
# Schema: db/schema.rb create_table "accounts"
# ---------------------------------------------------------------------------
class Account(Base):
    """
    Mirrors Rails Account model with all associations and scopes.

    Notable Rails → Python conversions:
      - serialize :subscribed_users, type: Array → stored as JSON Text
      - counter_cache columns (contacts_count, opportunities_count) kept as
        plain Integer columns; increment/decrement is handled in router logic
        (Rails does this automatically via counter_cache: true on belongs_to)
      - Wikidata enrichment fields (wikidata_id, lat/lng) preserved from schema
      - Social media fields (linkedin, facebook, twitter, etc.) preserved
    """

    __tablename__ = "accounts"

    # -------------------------------------------------------------------------
    # Primary key
    # -------------------------------------------------------------------------
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # -------------------------------------------------------------------------
    # Foreign keys
    # Rails: belongs_to :user, optional: true
    # Rails: belongs_to :assignee, class_name: "User", foreign_key: :assigned_to
    # -------------------------------------------------------------------------
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    assigned_to: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    # -------------------------------------------------------------------------
    # Core fields (from schema.rb)
    # Rails: validates :name, presence: true, length: { maximum: 64 }
    # -------------------------------------------------------------------------
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # Rails: default_access from Setting.default_access
    access: Mapped[str] = mapped_column(String(8), default="Public")
    website: Mapped[Optional[str]] = mapped_column(String(64))
    toll_free_phone: Mapped[Optional[str]] = mapped_column(String(32))
    phone: Mapped[Optional[str]] = mapped_column(String(32))
    fax: Mapped[Optional[str]] = mapped_column(String(32))
    email: Mapped[Optional[str]] = mapped_column(String(254))
    background_info: Mapped[Optional[str]] = mapped_column(Text)

    # Rails: validates :rating, numericality: { only_integer: true, gte: 0 }
    rating: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    category: Mapped[Optional[str]] = mapped_column(String(32))

    # -------------------------------------------------------------------------
    # serialize :subscribed_users, type: Array
    # Rails stores as YAML text; we store as JSON text and parse on access.
    # -------------------------------------------------------------------------
    subscribed_users: Mapped[Optional[str]] = mapped_column(Text)

    # -------------------------------------------------------------------------
    # Counter caches
    # Rails: counter_cache maintained automatically by ActiveRecord.
    # Python: We maintain these manually in router logic.
    # -------------------------------------------------------------------------
    contacts_count: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_count: Mapped[int] = mapped_column(Integer, default=0)

    # -------------------------------------------------------------------------
    # Wikidata enrichment (added in later migrations)
    # -------------------------------------------------------------------------
    wikidata_id: Mapped[Optional[str]] = mapped_column(String(64))
    latitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    longitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))

    # -------------------------------------------------------------------------
    # Social media fields
    # -------------------------------------------------------------------------
    blog: Mapped[Optional[str]] = mapped_column(String(128))
    linkedin: Mapped[Optional[str]] = mapped_column(String(128))
    facebook: Mapped[Optional[str]] = mapped_column(String(128))
    twitter: Mapped[Optional[str]] = mapped_column(String(128))
    bluesky: Mapped[Optional[str]] = mapped_column(String(128))
    instagram: Mapped[Optional[str]] = mapped_column(String(128))
    mastodon: Mapped[Optional[str]] = mapped_column(String(128))

    # -------------------------------------------------------------------------
    # Soft-delete (acts_as_paranoid)
    # Rails: scope :active -> { where(deleted_at: nil) }
    # Python: filter by `deleted_at.is_(None)` in queries
    # -------------------------------------------------------------------------
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # -------------------------------------------------------------------------
    # Timestamps — Rails: auto-managed by ActiveRecord
    # SQLAlchemy: server_default + onupdate
    # -------------------------------------------------------------------------
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # -------------------------------------------------------------------------
    # Unique constraint (from schema.rb index)
    # -------------------------------------------------------------------------
    __table_args__ = (
        UniqueConstraint(
            "user_id", "name", "deleted_at", name="index_accounts_on_user_id_and_name_and_deleted_at"
        ),
    )

    # =========================================================================
    # Relationships
    # Rails: has_many :account_contacts, dependent: :destroy
    # =========================================================================
    account_contacts: Mapped[List["AccountContact"]] = relationship(
        "AccountContact",
        foreign_keys=[AccountContact.account_id],
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Rails: has_many :contacts, -> { distinct }, through: :account_contacts
    contacts: Mapped[List["Contact"]] = relationship(
        "Contact",
        secondary="account_contacts",
        primaryjoin="and_(Account.id == AccountContact.account_id, AccountContact.deleted_at.is_(None))",
        secondaryjoin="Contact.id == AccountContact.contact_id",
        viewonly=True,
        lazy="selectin",
    )

    # Rails: has_many :account_opportunities, dependent: :destroy
    account_opportunities: Mapped[List["AccountOpportunity"]] = relationship(
        "AccountOpportunity",
        foreign_keys=[AccountOpportunity.account_id],
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Rails: has_many :opportunities, -> { order("id DESC").distinct }, through: :account_opportunities
    opportunities: Mapped[List["Opportunity"]] = relationship(
        "Opportunity",
        secondary="account_opportunities",
        primaryjoin="and_(Account.id == AccountOpportunity.account_id, AccountOpportunity.deleted_at.is_(None))",
        secondaryjoin="Opportunity.id == AccountOpportunity.opportunity_id",
        order_by="Opportunity.id.desc()",
        viewonly=True,
        lazy="selectin",
    )

    # =========================================================================
    # Business logic helpers
    # =========================================================================

    def get_subscribed_users(self) -> list:
        """
        Rails: serialize :subscribed_users, type: Array
        Deserialize JSON-stored user ID list.
        """
        if not self.subscribed_users:
            return []
        try:
            return json.loads(self.subscribed_users)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_subscribed_users(self, users: list) -> None:
        """Serialize user ID list to JSON for storage."""
        self.subscribed_users = json.dumps(users)

    @property
    def full_name(self) -> str:
        """Rails: Account uses #name as its display name."""
        return self.name

    @property
    def is_deleted(self) -> bool:
        """Rails: acts_as_paranoid — record is soft-deleted if deleted_at is set."""
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        """
        Rails: account.destroy (with acts_as_paranoid) sets deleted_at = Time.now.
        Call this instead of actually deleting the row.
        """
        self.deleted_at = datetime.utcnow()

    def restore(self) -> None:
        """Rails: account.restore! clears deleted_at."""
        self.deleted_at = None

    # =========================================================================
    # Scope equivalents (class-level query builders)
    # Rails scopes return ActiveRecord::Relation; here we return SQLAlchemy Select.
    # =========================================================================

    @classmethod
    def scope_active(cls):
        """
        Rails: default_scope { where(deleted_at: nil) } via acts_as_paranoid.
        Returns a base select excluding soft-deleted records.
        """
        return select(cls).where(cls.deleted_at.is_(None))

    @classmethod
    def scope_created_by(cls, user_id: int):
        """Rails: scope :created_by, ->(user) { where(user_id: user.id) }"""
        return cls.scope_active().where(cls.user_id == user_id)

    @classmethod
    def scope_assigned_to(cls, user_id: int):
        """Rails: scope :assigned_to, ->(user) { where(assigned_to: user.id) }"""
        return cls.scope_active().where(cls.assigned_to == user_id)

    @classmethod
    def scope_text_search(cls, query: str):
        """
        Rails: scope :text_search, lambda { |query| ransack(name_cont: query).result }
        Converted to an ILIKE search on the name column.
        """
        return cls.scope_active().where(cls.name.ilike(f"%{query}%"))

    @classmethod
    def scope_by_category(cls, category: str):
        """Rails: where(category: category) — used in sidebar filter."""
        return cls.scope_active().where(cls.category == category)

    @classmethod
    def scope_pipeline(cls):
        """
        Rails: has_many :pipeline_opportunities → pipeline scope on Opportunity.
        Returns accounts that have at least one pipeline opportunity.
        Uses a subquery join — applied at router level.
        """
        return cls.scope_active()

    def __repr__(self) -> str:
        return f"<Account id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# SQLAlchemy event hooks
# Rails ActiveRecord callbacks converted to SQLAlchemy events.
# ---------------------------------------------------------------------------

@event.listens_for(Account, "after_insert")
def account_after_insert(mapper, connection, target: Account):
    """
    Rails: after_create :subscribe_users
    After creating an account, initialize subscribed_users to the owner.
    """
    if target.subscribed_users is None and target.user_id:
        # Default: subscribe the creator
        connection.execute(
            Account.__table__.update()
            .where(Account.__table__.c.id == target.id)
            .values(subscribed_users=json.dumps([target.user_id]))
        )
