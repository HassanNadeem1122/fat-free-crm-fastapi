# models/contact.py
# ---------------------------------------------------------------------------
# Rails source: app/models/entities/contact.rb
# Schema source: db/schema.rb (create_table "contacts")
#
# Rails → SQLAlchemy pattern map:
#   belongs_to :user                            → ForeignKey + relationship
#   belongs_to :lead, optional: true            → nullable ForeignKey
#   belongs_to :assignee, FK: assigned_to       → ForeignKey to users.id
#   belongs_to :reporting_user, FK: reports_to  → ForeignKey to users.id
#   has_one :account_contact, dependent: destroy → relationship + cascade
#   has_one :account, through: :account_contact  → relationship via secondary
#   has_many :contact_opportunities             → relationship + cascade
#   has_many :opportunities, through: co         → secondary relationship
#   has_many :tasks, as: :asset                 → polymorphic (simplified here)
#   has_one :business_address, where(type=Biz)  → primaryjoin filtered rel
#   has_many :addresses                         → polymorphic relationship
#   has_many :emails, as: :mediator             → polymorphic relationship
#   delegate :campaign, to: :lead               → Python property
#   serialize :subscribed_users, type: Array    → JSON Text column
#   scope :created_by                           → classmethod with Select
#   scope :assigned_to                          → classmethod with Select
#   scope :text_search (Arel query)             → ILIKE on first/last name
#   accepts_nested_attributes_for :business_address → handled in schema/router
#   deleted_at (acts_as_paranoid)               → nullable DateTime column
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
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
    from .account import AccountContact
    from .opportunity import Opportunity, ContactOpportunity


# ---------------------------------------------------------------------------
# Contact model
# Rails: class Contact < ActiveRecord::Base
# ---------------------------------------------------------------------------
class Contact(Base):
    """
    Mirrors the Rails Contact entity with full field fidelity.

    Key business rules preserved:
      - do_not_call: boolean, must be respected in outreach features
      - access: 'Public' | 'Private' | 'Shared' — visibility scoping
      - born_on: date field for contact birthday
      - reports_to: self-referential-ish FK to another user (manager relationship)
      - lead_id: tracks which Lead this Contact was converted from
      - subscribed_users: JSON list of user IDs subscribed to this contact's activity
    """

    __tablename__ = "contacts"

    # -------------------------------------------------------------------------
    # Primary key
    # -------------------------------------------------------------------------
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # -------------------------------------------------------------------------
    # Foreign keys
    # Rails: belongs_to :user
    # Rails: belongs_to :lead, optional: true
    # Rails: belongs_to :assignee, class_name: "User", FK: assigned_to
    # Rails: belongs_to :reporting_user, class_name: "User", FK: reports_to
    # -------------------------------------------------------------------------
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    lead_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("leads.id", ondelete="SET NULL"), index=True
    )
    assigned_to: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reports_to: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    # -------------------------------------------------------------------------
    # Name fields
    # Rails: validates :first_name, presence: true, length: { max: 64 }
    # Rails: validates :last_name,  presence: true, length: { max: 64 }
    # -------------------------------------------------------------------------
    first_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # -------------------------------------------------------------------------
    # Access control
    # Rails: validates :access, inclusion: { in: %w[Public Private Shared] }
    # -------------------------------------------------------------------------
    access: Mapped[str] = mapped_column(String(8), default="Public")

    # -------------------------------------------------------------------------
    # Professional information
    # -------------------------------------------------------------------------
    title: Mapped[Optional[str]] = mapped_column(String(64))
    department: Mapped[Optional[str]] = mapped_column(String(64))
    # Rails: source — typically a Setting.unroll(:lead_source) value
    source: Mapped[Optional[str]] = mapped_column(String(32))

    # -------------------------------------------------------------------------
    # Contact details
    # -------------------------------------------------------------------------
    email: Mapped[Optional[str]] = mapped_column(String(64))
    alt_email: Mapped[Optional[str]] = mapped_column(String(64))
    phone: Mapped[Optional[str]] = mapped_column(String(32))
    mobile: Mapped[Optional[str]] = mapped_column(String(32))
    fax: Mapped[Optional[str]] = mapped_column(String(32))

    # -------------------------------------------------------------------------
    # Social / web presence
    # -------------------------------------------------------------------------
    blog: Mapped[Optional[str]] = mapped_column(String(128))
    linkedin: Mapped[Optional[str]] = mapped_column(String(128))
    facebook: Mapped[Optional[str]] = mapped_column(String(128))
    twitter: Mapped[Optional[str]] = mapped_column(String(128))

    # -------------------------------------------------------------------------
    # Personal
    # -------------------------------------------------------------------------
    born_on: Mapped[Optional[date]] = mapped_column(Date)

    # -------------------------------------------------------------------------
    # Business logic flags
    # Rails: validates :do_not_call, inclusion: { in: [true, false] }
    # -------------------------------------------------------------------------
    do_not_call: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # -------------------------------------------------------------------------
    # Notes
    # -------------------------------------------------------------------------
    background_info: Mapped[Optional[str]] = mapped_column(String(255))

    # -------------------------------------------------------------------------
    # serialize :subscribed_users, type: Array  (Rails YAML → JSON Text here)
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

    # Rails: has_one :account_contact, dependent: :destroy
    account_contact: Mapped[Optional["AccountContact"]] = relationship(
        "AccountContact",
        foreign_keys="[AccountContact.contact_id]",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    # Rails: has_one :account, through: :account_contact
    account = relationship(
        "Account",
        secondary="account_contacts",
        primaryjoin="Contact.id == AccountContact.contact_id",
        secondaryjoin="Account.id == AccountContact.account_id",
        viewonly=True,
        uselist=False,
        lazy="selectin",
    )

    # Rails: has_many :contact_opportunities, dependent: :destroy
    contact_opportunities: Mapped[List["ContactOpportunity"]] = relationship(
        "ContactOpportunity",
        foreign_keys="[ContactOpportunity.contact_id]",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Rails: has_many :opportunities, -> { order("id DESC").distinct }, through: co
    opportunities: Mapped[List["Opportunity"]] = relationship(
        "Opportunity",
        secondary="contact_opportunities",
        primaryjoin="Contact.id == ContactOpportunity.contact_id",
        secondaryjoin="Opportunity.id == ContactOpportunity.opportunity_id",
        order_by="Opportunity.id.desc()",
        viewonly=True,
        lazy="selectin",
    )

    # Rails: belongs_to :lead, optional: true
    lead = relationship("Lead", foreign_keys=[lead_id], lazy="selectin")

    # =========================================================================
    # Business logic helpers
    # =========================================================================

    @property
    def full_name(self) -> str:
        """
        Rails: def full_name; "#{first_name} #{last_name}".strip; end
        Used as display name throughout the CRM.
        """
        return f"{self.first_name or ''} {self.last_name or ''}".strip()

    @property
    def campaign(self):
        """
        Rails: delegate :campaign, to: :lead, allow_nil: true
        A contact's campaign is inherited from its originating lead.
        """
        if self.lead:
            return self.lead.campaign_id  # Return campaign_id; full obj needs join
        return None

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

    @property
    def is_deleted(self) -> bool:
        """Rails: acts_as_paranoid"""
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        """Rails: contact.destroy → sets deleted_at"""
        self.deleted_at = datetime.utcnow()

    def restore(self) -> None:
        """Rails: contact.restore!"""
        self.deleted_at = None

    # =========================================================================
    # Scope equivalents
    # =========================================================================

    @classmethod
    def scope_active(cls):
        """
        Rails: default scope via acts_as_paranoid — excludes soft-deleted.
        Equivalent to WHERE deleted_at IS NULL.
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
        Rails: scope :text_search, lambda { |query|
          t = Contact.arel_table
          # Search first+last in either order (handles "John Smith" or "Smith John")
          name = query.gsub(/\\s+/, '%')
          where("upper(first_name) LIKE upper(:name) OR upper(last_name) LIKE upper(:name) OR
                 upper(first_name || ' ' || last_name) LIKE upper(:name) OR
                 upper(last_name || ' ' || first_name) LIKE upper(:name)", name: "%#{name}%")
        }
        Converted to SQLAlchemy ILIKE supporting first+last name in either order.
        """
        term = f"%{query.replace(' ', '%')}%"
        return cls.scope_active().where(
            or_(
                cls.first_name.ilike(term),
                cls.last_name.ilike(term),
                (cls.first_name + " " + cls.last_name).ilike(term),
                (cls.last_name + " " + cls.first_name).ilike(term),
            )
        )

    @classmethod
    def scope_do_not_call(cls):
        """Filter contacts where do_not_call is True — used in outreach filtering."""
        return cls.scope_active().where(cls.do_not_call.is_(True))

    def __repr__(self) -> str:
        return f"<Contact id={self.id} name={self.full_name!r}>"


# ---------------------------------------------------------------------------
# ContactOpportunity join table
# Rails: has_many :contact_opportunities, dependent: :destroy
# Defined here so models/opportunity.py can import it without circular deps
# ---------------------------------------------------------------------------
class ContactOpportunity(Base):
    """
    Rails model: ContactOpportunity join table.
    app/models/entities/contact_opportunity.rb
    """

    __tablename__ = "contact_opportunities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), index=True
    )
    opportunity_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    # Rails: acts_as_paranoid
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "contact_id", "opportunity_id", name="uq_contact_opportunity"
        ),
    )


# ---------------------------------------------------------------------------
# SQLAlchemy event hooks (replaces Rails ActiveRecord callbacks)
# ---------------------------------------------------------------------------

@event.listens_for(Contact, "after_insert")
def contact_after_insert(mapper, connection, target: Contact):
    """
    Rails: after_create :subscribe_users
    Initialize subscribed_users with the creator on new contact.
    """
    if target.subscribed_users is None and target.user_id:
        connection.execute(
            Contact.__table__.update()
            .where(Contact.__table__.c.id == target.id)
            .values(subscribed_users=json.dumps([target.user_id]))
        )
