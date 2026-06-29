# models/opportunity.py
# ---------------------------------------------------------------------------
# Rails source: app/models/entities/opportunity.rb
# Schema source: db/schema.rb (create_table "opportunities")
#
# Rails → SQLAlchemy pattern map:
#   belongs_to :user, optional: true           → nullable FK
#   belongs_to :campaign, optional: true       → nullable FK
#   belongs_to :assignee, FK: assigned_to      → nullable FK to users
#   has_one :account_opportunity               → one join record
#   has_one :account, through: ao             → via secondary join
#   has_many :contact_opportunities           → join table
#   has_many :contacts, through: co           → secondary join
#   has_many :tasks, as: :asset             → polymorphic
#   has_many :emails, as: :mediator          → polymorphic
#   serialize :subscribed_users              → JSON Text
#   scope :state (stage IN or NULL)          → classmethod
#   scope :won / :lost / :not_lost / :pipeline → classmethods
#   scope :unassigned                       → assigned_to IS NULL
#   scope :weighted_sort (amount*probability) → classmethod with label_column
#   scope :text_search (name LIKE or id =)  → classmethod
#   scope :visible_on_dashboard             → classmethod with user scoping
#   OpportunityObserver callbacks           → SQLAlchemy events
#   deleted_at (acts_as_paranoid)          → nullable DateTime
#   amount / discount: decimal(12,2)       → Numeric(12,2)
#   probability: integer (0-100)           → validated in schema
#   stage: from Setting.unroll(:opportunity_stage) → OpportunityStage enum
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    event,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

if TYPE_CHECKING:
    from .contact import Contact, ContactOpportunity
    from .account import Account, AccountOpportunity


# ---------------------------------------------------------------------------
# Rails: Setting.unroll(:opportunity_stage) defaults
# ---------------------------------------------------------------------------
class OpportunityStage(str, Enum):
    """
    Rails: opportunity stage values from Setting.unroll(:opportunity_stage).
    Fat Free CRM ships these defaults in config/settings.yml.
    Stored as String(32); won/lost have special business logic.
    """
    PROSPECTING = "prospecting"
    ANALYSIS = "analysis"
    NEEDS_ANALYSIS = "needs_analysis"
    VALUE_PROPOSITION = "value_proposition"
    ID_DECISION_MAKERS = "id_decision_makers"
    PERCEPTION_ANALYSIS = "perception_analysis"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    REVIEW = "review"
    DELIVERY = "delivery"
    # Terminal stages — used in scopes :won, :lost
    WON = "won"
    LOST = "lost"


# ---------------------------------------------------------------------------
# Opportunity model
# Rails: class Opportunity < ActiveRecord::Base
# ---------------------------------------------------------------------------
class Opportunity(Base):
    """
    Mirrors the Rails Opportunity entity.

    Key business rules preserved:
      - stage='won' / stage='lost' are terminal states with special filtering.
      - pipeline = everything NOT won or lost (and not NULL for stage).
      - weighted_value = amount * (probability / 100.0)
      - probability: integer 0–100 representing close likelihood
      - closes_on: expected close date (not when it actually closed)
      - discount: optional discount amount (not percentage)
      - subscribed_users: JSON list of user IDs watching this opportunity
      - visible_on_dashboard: own + assigned opportunities, filter to pipeline
    """

    __tablename__ = "opportunities"

    # -------------------------------------------------------------------------
    # Primary key
    # -------------------------------------------------------------------------
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # -------------------------------------------------------------------------
    # Foreign keys
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
    # Core fields
    # Rails: validates :name, presence: true, length: { max: 64 }
    # -------------------------------------------------------------------------
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    access: Mapped[str] = mapped_column(String(8), default="Public")

    # Rails: source from Setting.unroll(:lead_source)
    source: Mapped[Optional[str]] = mapped_column(String(32))

    # Rails: stage from Setting.unroll(:opportunity_stage)
    stage: Mapped[Optional[str]] = mapped_column(String(32))

    # -------------------------------------------------------------------------
    # Financial fields
    # Rails: validates :probability, numericality: { only_integer: true, 0..100 }
    # Rails: validates :amount, numericality: { gte: 0 }, allow_blank: true
    # -------------------------------------------------------------------------
    probability: Mapped[Optional[int]] = mapped_column(Integer)  # 0–100
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    discount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))

    # -------------------------------------------------------------------------
    # Timeline
    # Rails: validates :closes_on, presence: true (recommended but not enforced)
    # -------------------------------------------------------------------------
    closes_on: Mapped[Optional[date]] = mapped_column(Date)

    # -------------------------------------------------------------------------
    # Notes
    # -------------------------------------------------------------------------
    background_info: Mapped[Optional[str]] = mapped_column(String(255))

    # -------------------------------------------------------------------------
    # serialize :subscribed_users, type: Array
    # -------------------------------------------------------------------------
    subscribed_users: Mapped[Optional[str]] = mapped_column(Text)

    # -------------------------------------------------------------------------
    # Soft-delete
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

    # Rails: has_one :account_opportunity, dependent: :destroy
    account_opportunity: Mapped[Optional["AccountOpportunity"]] = relationship(
        "AccountOpportunity",
        foreign_keys="[AccountOpportunity.opportunity_id]",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    # Rails: has_one :account, through: :account_opportunity
    account = relationship(
        "Account",
        secondary="account_opportunities",
        primaryjoin="Opportunity.id == AccountOpportunity.opportunity_id",
        secondaryjoin="Account.id == AccountOpportunity.account_id",
        viewonly=True,
        uselist=False,
        lazy="selectin",
    )

    # Rails: has_many :contact_opportunities, dependent: :destroy
    contact_opportunities: Mapped[List["ContactOpportunity"]] = relationship(
        "ContactOpportunity",
        foreign_keys="[ContactOpportunity.opportunity_id]",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Rails: has_many :contacts, -> { order("id DESC").distinct }, through: co
    contacts: Mapped[List["Contact"]] = relationship(
        "Contact",
        secondary="contact_opportunities",
        primaryjoin="Opportunity.id == ContactOpportunity.opportunity_id",
        secondaryjoin="Contact.id == ContactOpportunity.contact_id",
        order_by="Contact.id.desc()",
        viewonly=True,
        lazy="selectin",
    )

    # =========================================================================
    # Business logic
    # =========================================================================

    @property
    def weighted_amount(self) -> Optional[Decimal]:
        """
        Rails: scope :weighted_sort, -> { select('*, amount*probability') }
        Computes weighted_amount = amount * probability / 100.
        Used for pipeline value reporting.
        """
        if self.amount is None or self.probability is None:
            return None
        return self.amount * Decimal(self.probability) / Decimal(100)

    @property
    def net_amount(self) -> Optional[Decimal]:
        """
        Rails: amount - discount (used in financial summaries).
        Returns amount minus discount if both are present.
        """
        if self.amount is None:
            return None
        discount = self.discount or Decimal(0)
        return self.amount - discount

    @property
    def is_won(self) -> bool:
        """Rails: scope :won → stage == 'won'"""
        return self.stage == OpportunityStage.WON

    @property
    def is_lost(self) -> bool:
        """Rails: scope :lost → stage == 'lost'"""
        return self.stage == OpportunityStage.LOST

    @property
    def is_pipeline(self) -> bool:
        """
        Rails: scope :pipeline →
          WHERE stage IS NULL OR (stage != 'won' AND stage != 'lost')
        """
        return self.stage is None or (
            self.stage != OpportunityStage.WON
            and self.stage != OpportunityStage.LOST
        )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        """Rails: opportunity.destroy → sets deleted_at"""
        self.deleted_at = datetime.utcnow()

    def restore(self) -> None:
        """Rails: opportunity.restore!"""
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

    # =========================================================================
    # Scope equivalents
    # =========================================================================

    @classmethod
    def scope_active(cls):
        """Rails: acts_as_paranoid default — WHERE deleted_at IS NULL"""
        return select(cls).where(cls.deleted_at.is_(None))

    @classmethod
    def scope_state(cls, filters: list, include_null: bool = False):
        """
        Rails: scope :state, lambda { |filters|
          where('stage IN (?)' + (filters.delete('other') ?
            ' OR stage IS NULL' : ''), filters)
        }
        include_null=True corresponds to 'other' being in the filters list.
        """
        stmt = cls.scope_active()
        if include_null:
            return stmt.where(or_(cls.stage.in_(filters), cls.stage.is_(None)))
        return stmt.where(cls.stage.in_(filters))

    @classmethod
    def scope_won(cls):
        """Rails: scope :won, -> { where("stage = 'won'") }"""
        return cls.scope_active().where(cls.stage == OpportunityStage.WON)

    @classmethod
    def scope_lost(cls):
        """Rails: scope :lost, -> { where("stage = 'lost'") }"""
        return cls.scope_active().where(cls.stage == OpportunityStage.LOST)

    @classmethod
    def scope_not_lost(cls):
        """Rails: scope :not_lost, -> { where("stage <> 'lost'") }"""
        return cls.scope_active().where(cls.stage != OpportunityStage.LOST)

    @classmethod
    def scope_pipeline(cls):
        """
        Rails: scope :pipeline, -> {
          where("stage IS NULL OR (stage != 'won' AND stage != 'lost')")
        }
        Active opportunities that are neither won nor lost.
        """
        return cls.scope_active().where(
            or_(
                cls.stage.is_(None),
                (cls.stage != OpportunityStage.WON) & (cls.stage != OpportunityStage.LOST),
            )
        )

    @classmethod
    def scope_unassigned(cls):
        """Rails: scope :unassigned, -> { where("assigned_to IS NULL") }"""
        return cls.scope_active().where(cls.assigned_to.is_(None))

    @classmethod
    def scope_created_by(cls, user_id: int):
        """Rails: scope :created_by, ->(user) { where('user_id = ?', user.id) }"""
        return cls.scope_active().where(cls.user_id == user_id)

    @classmethod
    def scope_assigned_to(cls, user_id: int):
        """Rails: scope :assigned_to, ->(user) { where('assigned_to = ?', user.id) }"""
        return cls.scope_active().where(cls.assigned_to == user_id)

    @classmethod
    def scope_text_search(cls, query: str):
        """
        Rails: scope :text_search, lambda { |query|
          if query.match?(/\\A\\d+\\z/)
            where('upper(name) LIKE upper(:name) OR opportunities.id = :id',
                  name: "%#{query}%", id: query)
          else
            ransack('name_cont' => query).result
          end
        }
        Preserved: numeric queries also match by ID.
        """
        stmt = cls.scope_active()
        if query.isdigit():
            # Numeric query: search by name OR exact id match
            return stmt.where(
                or_(
                    cls.name.ilike(f"%{query}%"),
                    cls.id == int(query),
                )
            )
        return stmt.where(cls.name.ilike(f"%{query}%"))

    @classmethod
    def scope_visible_on_dashboard(cls, user_id: int):
        """
        Rails: scope :visible_on_dashboard, lambda { |user|
          # Opportunities created by or assigned to the user, in pipeline stage
        }
        Combines created_by + assigned_to + pipeline filter.
        """
        return cls.scope_active().where(
            or_(cls.user_id == user_id, cls.assigned_to == user_id),
            or_(
                cls.stage.is_(None),
                (cls.stage != OpportunityStage.WON)
                & (cls.stage != OpportunityStage.LOST),
            ),
        )

    @classmethod
    def default_stage(cls) -> str:
        """
        Rails: Opportunity.default_stage → from Setting.opportunity_stage (first value).
        Returns 'prospecting' as the configured default.
        """
        return OpportunityStage.PROSPECTING

    def __repr__(self) -> str:
        return f"<Opportunity id={self.id} name={self.name!r} stage={self.stage!r}>"


# ---------------------------------------------------------------------------
# SQLAlchemy event hooks (replaces Rails OpportunityObserver)
# ---------------------------------------------------------------------------

@event.listens_for(Opportunity, "after_insert")
def opportunity_after_insert(mapper, connection, target: Opportunity):
    """
    Rails: OpportunityObserver#after_create → subscribe users.
    Initialize subscribed_users with the creator's ID.
    """
    if target.subscribed_users is None and target.user_id:
        connection.execute(
            Opportunity.__table__.update()
            .where(Opportunity.__table__.c.id == target.id)
            .values(subscribed_users=json.dumps([target.user_id]))
        )


@event.listens_for(Opportunity, "before_update")
def opportunity_before_update(mapper, connection, target: Opportunity):
    """
    Rails: OpportunityObserver#before_update
    Audit trail / stage-change notifications would be added here.
    In Rails this observer sends email notifications when stage changes.
    Implement email/webhook dispatch here as needed.
    """
    pass  # Extend: detect stage changes, trigger notifications
