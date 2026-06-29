# schemas/opportunity.py
# ---------------------------------------------------------------------------
# Pydantic v2 schemas for Opportunity module.
#
# Rails validations → Pydantic v2:
#   validates :name, presence: true, length: { max: 64 }
#   validates :access, inclusion: %w[Public Private Shared]
#   validates :stage, inclusion: Setting.unroll(:opportunity_stage), allow_nil: true
#   validates :probability, numericality: { only_integer: true, 0..100 }, allow_nil: true
#   validates :amount, numericality: { gte: 0 }, allow_blank: true
#   validates :closes_on, timeliness: { type: :date }, allow_blank: true
#
# Key business logic:
#   OpportunitiesController#load_settings  → stage/source lists
#   scope :weighted_sort                   → weighted_amount computed property
#   scope :visible_on_dashboard            → dashboard filter endpoint
#   OpportunityObserver#before_update      → stage-change notifications
# ---------------------------------------------------------------------------

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Stage and Source literals
# Rails: Setting.unroll(:opportunity_stage), Setting.unroll(:lead_source)
# ---------------------------------------------------------------------------
OPPORTUNITY_STAGE_VALUES = Literal[
    "prospecting", "analysis", "needs_analysis", "value_proposition",
    "id_decision_makers", "perception_analysis", "proposal",
    "negotiation", "review", "delivery", "won", "lost",
]
OPPORTUNITY_SOURCE_VALUES = Literal[
    "cold_call", "existing_customer", "self_generated", "employee",
    "partner", "public_relations", "direct_mail", "conference",
    "trade_show", "web_site", "word_of_mouth", "other",
]


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------
class OpportunityBase(BaseModel):
    """
    Shared fields for Opportunity Create/Update.
    Rails: attr_accessible whitelist.
    """

    # Rails: validates :name, presence: true, length: { max: 64 }
    name: str = Field(..., min_length=1, max_length=64)

    # Rails: validates :access, inclusion: { in: %w[Public Private Shared] }
    access: Literal["Public", "Private", "Shared"] = "Public"

    source: Optional[OPPORTUNITY_SOURCE_VALUES] = None  # type: ignore[valid-type]

    # Rails: stage nil is valid (represents unset stage in :pipeline scope)
    stage: Optional[OPPORTUNITY_STAGE_VALUES] = None  # type: ignore[valid-type]

    # Rails: validates :probability, numericality: { only_integer: true }, 0..100
    probability: Optional[Annotated[int, Field(ge=0, le=100)]] = None

    # Rails: validates :amount, numericality: { gte: 0 }, allow_blank: true
    amount: Optional[Annotated[Decimal, Field(ge=0)]] = None

    # Rails: validates :discount, numericality: { gte: 0 }, allow_blank: true
    discount: Optional[Annotated[Decimal, Field(ge=0)]] = None

    # Rails: validates :closes_on, timeliness: { type: :date }, allow_blank: true
    closes_on: Optional[date] = None

    background_info: Optional[str] = Field(None, max_length=255)

    # Foreign keys
    assigned_to: Optional[int] = None
    campaign_id: Optional[int] = None

    # Rails: account link (via account_opportunity join)
    account_id: Optional[int] = Field(
        None, description="Account to link this opportunity to"
    )

    @field_validator("closes_on", mode="before")
    @classmethod
    def parse_closes_on(cls, v):
        """
        Rails: validates_timeliness for :closes_on.
        Accept both date objects and ISO-8601 strings.
        """
        if v is None or isinstance(v, date):
            return v
        if isinstance(v, str):
            try:
                return date.fromisoformat(v)
            except ValueError:
                raise ValueError(f"Invalid date format for closes_on: {v}")
        return v

    @model_validator(mode="after")
    def validate_amount_vs_discount(self) -> "OpportunityBase":
        """
        Rails: validate { errors.add(:discount, ...) if discount > amount }
        Discount must not exceed the full amount.
        """
        if self.amount is not None and self.discount is not None:
            if self.discount > self.amount:
                raise ValueError("Discount cannot exceed the opportunity amount")
        return self


# ---------------------------------------------------------------------------
# Create schema
# ---------------------------------------------------------------------------
class OpportunityCreate(OpportunityBase):
    """
    Schema for POST /opportunities.
    Rails: OpportunitiesController#create — also accepts :related param
    to auto-link to a contact (contact_id) or account (account_id).
    """
    user_id: Optional[int] = Field(None, description="Owner user ID (set server-side)")

    # Rails: OpportunitiesController sets stage to Opportunity.default_stage on new
    stage: Optional[OPPORTUNITY_STAGE_VALUES] = "prospecting"  # type: ignore[valid-type]

    # Rails: link from related contact on creation (contact_id passed via :related param)
    contact_id: Optional[int] = Field(
        None, description="Contact ID to link via contact_opportunity join table"
    )


# ---------------------------------------------------------------------------
# Update schema — all fields optional
# ---------------------------------------------------------------------------
class OpportunityUpdate(BaseModel):
    """Schema for PATCH /opportunities/{id}."""

    name: Optional[str] = Field(None, min_length=1, max_length=64)
    access: Optional[Literal["Public", "Private", "Shared"]] = None
    source: Optional[OPPORTUNITY_SOURCE_VALUES] = None  # type: ignore[valid-type]
    stage: Optional[OPPORTUNITY_STAGE_VALUES] = None  # type: ignore[valid-type]
    probability: Optional[Annotated[int, Field(ge=0, le=100)]] = None
    amount: Optional[Annotated[Decimal, Field(ge=0)]] = None
    discount: Optional[Annotated[Decimal, Field(ge=0)]] = None
    closes_on: Optional[date] = None
    background_info: Optional[str] = Field(None, max_length=255)
    assigned_to: Optional[int] = None
    campaign_id: Optional[int] = None
    account_id: Optional[int] = None
    contact_id: Optional[int] = None

    @model_validator(mode="after")
    def validate_amount_vs_discount(self) -> "OpportunityUpdate":
        """Rails: validate discount <= amount"""
        if self.amount is not None and self.discount is not None:
            if self.discount > self.amount:
                raise ValueError("Discount cannot exceed the opportunity amount")
        return self


# ---------------------------------------------------------------------------
# Nested helpers
# ---------------------------------------------------------------------------
class OpportunityContactSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    first_name: str
    last_name: str


class OpportunityAccountSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------
class OpportunityResponse(BaseModel):
    """
    Full Opportunity representation.
    Rails: respond_with(@opportunity) / opportunity.as_json.

    Includes computed fields:
      - weighted_amount: amount * probability / 100
      - net_amount: amount - discount
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    campaign_id: Optional[int] = None
    assigned_to: Optional[int] = None
    name: str
    access: str
    source: Optional[str] = None
    stage: Optional[str] = None
    probability: Optional[int] = None
    amount: Optional[Decimal] = None
    discount: Optional[Decimal] = None
    closes_on: Optional[date] = None
    background_info: Optional[str] = None
    deleted_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Computed fields (Rails: model methods)
    weighted_amount: Optional[Decimal] = None
    net_amount: Optional[Decimal] = None

    # Relationships
    account: Optional[OpportunityAccountSummary] = None
    contacts: List[OpportunityContactSummary] = []

    @model_validator(mode="after")
    def compute_derived_fields(self) -> "OpportunityResponse":
        """
        Rails: weighted_sort scope uses amount*probability computed at DB level.
        We compute here for API consumers.
        """
        if self.amount is not None and self.probability is not None:
            self.weighted_amount = self.amount * Decimal(self.probability) / Decimal(100)
        if self.amount is not None:
            discount = self.discount or Decimal(0)
            self.net_amount = self.amount - discount
        return self


# ---------------------------------------------------------------------------
# Dashboard summary — used in GET /opportunities/dashboard
# Rails: @opportunities = Opportunity.visible_on_dashboard(current_user)
# ---------------------------------------------------------------------------
class OpportunityDashboard(BaseModel):
    """Pipeline summary for dashboard widget."""
    pipeline: List[OpportunityResponse]
    total_pipeline_value: Decimal = Decimal(0)
    total_weighted_value: Decimal = Decimal(0)
    won_this_month: List[OpportunityResponse] = []
    lost_this_month: List[OpportunityResponse] = []


# ---------------------------------------------------------------------------
# List / paginated response
# ---------------------------------------------------------------------------
class OpportunityList(BaseModel):
    """Rails: @opportunities = Opportunity.page(n).per(m)"""
    items: List[OpportunityResponse]
    total: int
    page: int
    per_page: int
    pages: int
