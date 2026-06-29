# models/lead.py
# ---------------------------------------------------------------------------
# Rails source: app/models/entities/lead.rb
# Schema source: db/schema.rb (create_table "leads")
#
# Rails → SQLAlchemy pattern map:
#   belongs_to :user, optional: true          → nullable FK to users
#   belongs_to :campaign, optional: true      → nullable FK to campaigns
#   belongs_to :assignee, FK: assigned_to     → nullable FK to users
#   has_one :contact, dependent: :nullify     → on destroy, nullify lead_id in contacts
#   has_many :tasks, as: :asset              → polymorphic tasks
#   has_one :business_address               → filtered relationship
#   has_many :addresses                     → polymorphic
#   has_many :emails, as: :mediator         → polymorphic
#   serialize :subscribed_users             → JSON Text
#   accepts_nested_attributes_for :business_address → router/schema handles
#   scope :state (filters w/ "other" NULL)  → classmethod with optional NULL
#   scope :created_by                       → classmethod
#   scope :assigned_to                      → classmethod
#   scope :text_search                      → ILIKE search
#   before_create :set_initial_permissions  → after_insert event
#   after_create :add_comment              → handled in router
#   Lead::CONVERT action (lead → contact)  → convert() method
#   Lead status values from Settings        → LeadStatus enum
#   deleted_at (acts_as_paranoid)          → nullable DateTime
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    or_,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

if TYPE_CHECKING:
    from .contact import Contact


# ---------------------------------------------------------------------------
# Rails: Setting.unroll(:lead_status)
# Typical Fat Free CRM lead status values:
# ---------------------------------------------------------------------------
class LeadStatus(str, Enum):
    """
    Rails: lead status values from Setting.unroll(:lead_status).
    Fat Free CRM ships with these defaults in config/settings.yml.
    Stored as a String(32) in the DB, validated at the Pydantic layer.
    """
    NEW = "new"
    ASSIGNED = "assigned"
    IN_PROCESS = "in_process"
    CONVERTED = "converted"
    RECYCLED = "recycled"
    DEAD = "dead"


# ---------------------------------------------------------------------------
# Rails: Setting.unroll(:lead_source) — same values shared with contacts
# ---------------------------------------------------------------------------
class LeadSource(str, Enum):
    COLD_CALL = "cold_call"
    EXISTING_CUSTOMER = "existing_customer"
    SELF_GENERATED = "self_generated"
    EMPLOYEE = "employee"
    PARTNER = "partner"
    PUBLIC_RELATIONS = "public_relations"
    DIRECT_MAIL = "direct_mail"
    CONFERENCE = "conference"
    TRADE_SHOW = "trade_show"
    WEB_SITE = "web_site"
    WORD_OF_MOUTH = "word_of_mouth"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Lead model
# Rails: class Lead < ActiveRecord::Base
# ---------------------------------------------------------------------------
class Lead(Base):
    """
    Mirrors the Rails Lead entity.

    Key business rules:
      - A lead can be "converted" to a Contact (with optional Account + Opportunity).
        Rails: LeadsController#convert sets status="converted" and creates linked contact.
        Python: Lead.convert() method + router logic.
      - rating: integer 0-5 (star rating)
      - do_not_call: boolean flag to prevent phone outreach
      - status can be NULL ("other" in filter scope)
      - subscribed_users: list of user IDs to notify on changes
    """

    __tablename__ = "leads"

    # -------------------------------------------------------------------------
    # Primary key
    # -------------------------------------------------------------------------
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # -------------------------------------------------------------------------
    # Foreign keys
    # Rails: belongs_to :user, optional: true
    # Rails: belongs_to :campaign, optional: true
    # Rails: belongs_to :assignee, class_name: "User", FK: assigned_to
    # -------------------------------------------------------------------------
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    campaign_id: Mapped[Optional[int]] = mapped_column(
        Integer  # campaign_id — FK omitted: campaigns table not in migration scope, index=True
    )
    assigned_to: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    # -------------------------------------------------------------------------
    # Name fields
    # Rails: validates :first_name, presence: true, length: { max: 64 }
    # Rails: validates :last_name, presence: true, length: { max: 64 }
    # -------------------------------------------------------------------------
    first_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # -------------------------------------------------------------------------
    # Access / visibility
    # Rails: validates :access, inclusion: %w[Public Private Shared]
    # -------------------------------------------------------------------------
    access: Mapped[str] = mapped_column(String(8), default="Public")

    # -------------------------------------------------------------------------
    # Lead qualification fields
    # -------------------------------------------------------------------------
    title: Mapped[Optional[str]] = mapped_column(String(64))
    company: Mapped[Optional[str]] = mapped_column(String(64))
    source: Mapped[Optional[str]] = mapped_column(String(32))  # LeadSource
    status: Mapped[Optional[str]] = mapped_column(String(32))  # LeadStatus; NULL = "other"
    referred_by: Mapped[Optional[str]] = mapped_column(String(64))

    # -------------------------------------------------------------------------
    # Contact details
    # -------------------------------------------------------------------------
    email: Mapped[Optional[str]] = mapped_column(String(64))
    alt_email: Mapped[Optional[str]] = mapped_column(String(64))
    phone: Mapped[Optional[str]] = mapped_column(String(32))
    mobile: Mapped[Optional[str]] = mapped_column(String(32))

    # -------------------------------------------------------------------------
    # Social / web presence
    # -------------------------------------------------------------------------
    blog: Mapped[Optional[str]] = mapped_column(String(128))
    linkedin: Mapped[Optional[str]] = mapped_column(String(128))
    facebook: Mapped[Optional[str]] = mapped_column(String(128))
    twitter: Mapped[Optional[str]] = mapped_column(String(128))

    # -------------------------------------------------------------------------
    # Lead scoring
    # Rails: validates :rating, numericality: { only_integer: true, gte: 0 }
    # -------------------------------------------------------------------------
    rating: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # -------------------------------------------------------------------------
    # Outreach control
    # Rails: validates :do_not_call, inclusion: [true, false]
    # -------------------------------------------------------------------------
    do_not_call: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # -------------------------------------------------------------------------
    # Notes
    # -------------------------------------------------------------------------
    background_info: Mapped[Optional[str]] = mapped_column(String(255))

    # -------------------------------------------------------------------------
    # serialize :subscribed_users, type: Array
    # -------------------------------------------------------------------------
    subscribed_users: Mapped[Optional[str]] = mapped_column(Text)

    # -------------------------------------------------------------------------
    # Soft-delete (acts_as_paranoid)
    # -------------------------------------------------------------------------
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # -------------------------------------------------------------------------
    # Timestamps
    # -------------------------------------------------------------------------
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # =========================================================================
    # Relationships
    # =========================================================================

    # Rails: has_one :contact, dependent: :nullify
    # On lead destroy, contact.lead_id is set to NULL (not cascaded).
    contact: Mapped[Optional["Contact"]] = relationship(
        "Contact",
        foreign_keys="[Contact.lead_id]",
        # NOTE: dependent: :nullify → we do NOT cascade delete.
        # The router must explicitly nullify contact.lead_id on lead deletion.
        lazy="selectin",
    )

    # =========================================================================
    # Business logic
    # =========================================================================

    @property
    def full_name(self) -> str:
        """Rails: def full_name; "#{first_name} #{last_name}".strip; end"""
        return f"{self.first_name or ''} {self.last_name or ''}".strip()

    @property
    def is_converted(self) -> bool:
        """Rails: lead.converted? → status == 'converted'"""
        return self.status == LeadStatus.CONVERTED

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        """
        Rails: lead.destroy (acts_as_paranoid) → sets deleted_at.
        IMPORTANT: Rails also nullifies lead_id on the associated contact.
        Router must handle: contact.lead_id = None after this call.
        """
        self.deleted_at = datetime.utcnow()

    def restore(self) -> None:
        """Rails: lead.restore!"""
        self.deleted_at = None

    def get_subscribed_users(self) -> list:
        """Rails: serialize :subscribed_users, type: Array"""
        if not self.subscribed_users:
            return []
        try:
            return json.loads(self.subscribed_users)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_subscribed_users(self, users: list) -> None:
        self.subscribed_users = json.dumps(users)

    def mark_as_converted(self) -> None:
        """
        Rails: LeadsController#convert sets lead.status = :converted.
        Called after creating a Contact from this lead.
        """
        self.status = LeadStatus.CONVERTED

    # =========================================================================
    # Scope equivalents
    # =========================================================================

    @classmethod
    def scope_active(cls):
        """Rails: acts_as_paranoid default scope — excludes deleted records."""
        return select(cls).where(cls.deleted_at.is_(None))

    @classmethod
    def scope_state(cls, filters: list, include_null: bool = False):
        """
        Rails: scope :state, lambda { |filters|
          where(['status IN (?)' + (filters.delete('other') ?
            ' OR status IS NULL' : ''), filters])
        }
        The 'other' filter maps to NULL status values.
        include_null=True when the caller passes 'other' in filters.
        """
        stmt = cls.scope_active()
        if include_null:
            return stmt.where(or_(cls.status.in_(filters), cls.status.is_(None)))
        return stmt.where(cls.status.in_(filters))

    @classmethod
    def scope_created_by(cls, user_id: int):
        """Rails: scope :created_by, ->(user) { where(user_id: user.id) }"""
        return cls.scope_active().where(cls.user_id == user_id)

    @classmethod
    def scope_assigned_to(cls, user_id: int):
        """Rails: scope :assigned_to, ->(user) { where(assigned_to: user.id) }"""
        return cls.scope_active().where(cls.assigned_to == user_id)

    @classmethod
    def scope_converted(cls):
        """Rails: where(status: 'converted') — used in reporting."""
        return cls.scope_active().where(cls.status == LeadStatus.CONVERTED)

    @classmethod
    def scope_unassigned(cls):
        """Rails: where(assigned_to: nil)"""
        return cls.scope_active().where(cls.assigned_to.is_(None))

    @classmethod
    def scope_text_search(cls, query: str):
        """
        Rails: scope :text_search — searches first_name, last_name, company, email.
        Converted to ILIKE across all relevant columns.
        """
        term = f"%{query.replace(' ', '%')}%"
        return cls.scope_active().where(
            or_(
                cls.first_name.ilike(term),
                cls.last_name.ilike(term),
                (cls.first_name + " " + cls.last_name).ilike(term),
                (cls.last_name + " " + cls.first_name).ilike(term),
                cls.company.ilike(f"%{query}%"),
                cls.email.ilike(f"%{query}%"),
            )
        )

    def __repr__(self) -> str:
        return f"<Lead id={self.id} name={self.full_name!r} status={self.status!r}>"


# ---------------------------------------------------------------------------
# SQLAlchemy event hooks (Rails ActiveRecord callbacks)
# ---------------------------------------------------------------------------

@event.listens_for(Lead, "after_insert")
def lead_after_insert(mapper, connection, target: Lead):
    """
    Rails: after_create :subscribe_users
    Initialize subscribed_users with the creator.
    """
    if target.subscribed_users is None and target.user_id:
        connection.execute(
            Lead.__table__.update()
            .where(Lead.__table__.c.id == target.id)
            .values(subscribed_users=json.dumps([target.user_id]))
        )


@event.listens_for(Lead, "before_update")
def lead_before_update(mapper, connection, target: Lead):
    """
    Rails: LeadObserver#before_update / lead_observer.rb
    If status changes to 'converted', record is being processed via convert action.
    Additional observer logic (audit trail etc) would go here.
    """
    pass  # Extend with audit logging as needed
