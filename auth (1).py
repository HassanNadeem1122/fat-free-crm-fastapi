# schemas/lead.py
# ---------------------------------------------------------------------------
# Pydantic v2 schemas for Lead module.
#
# Rails validations → Pydantic v2:
#   validates :first_name, presence: true, length: { max: 64 }
#   validates :last_name,  presence: true, length: { max: 64 }
#   validates :access, inclusion: %w[Public Private Shared]
#   validates :rating, numericality: { only_integer: true, gte: 0 }
#   validates :status, inclusion: %w[new assigned in_process converted recycled dead], allow_nil: true
#   validates :do_not_call, inclusion: [true, false]
#   validates :email, format: /.../
#
# Key business logic:
#   LeadsController#convert → LeadConvert schema handles the conversion payload
#   save_with_permissions   → permission handling in router
#   add_comment_by_user     → comment_body in LeadCreate
# ---------------------------------------------------------------------------

from datetime import datetime
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .contact import AddressCreate


# ---------------------------------------------------------------------------
# Status and Source literals
# Rails: Setting.unroll(:lead_status), Setting.unroll(:lead_source)
# ---------------------------------------------------------------------------
LEAD_STATUS_VALUES = Literal[
    "new", "assigned", "in_process", "converted", "recycled", "dead"
]
LEAD_SOURCE_VALUES = Literal[
    "cold_call", "existing_customer", "self_generated", "employee",
    "partner", "public_relations", "direct_mail", "conference",
    "trade_show", "web_site", "word_of_mouth", "other",
]


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------
class LeadBase(BaseModel):
    """
    Shared fields for Lead Create/Update.
    Rails: attr_accessible whitelist.
    """

    # Rails: validates :first_name, presence: true, length: { max: 64 }
    first_name: str = Field(..., min_length=1, max_length=64)

    # Rails: validates :last_name, presence: true, length: { max: 64 }
    last_name: str = Field(..., min_length=1, max_length=64)

    # Rails: validates :access, inclusion: { in: %w[Public Private Shared] }
    access: Literal["Public", "Private", "Shared"] = "Public"

    title: Optional[str] = Field(None, max_length=64)
    company: Optional[str] = Field(None, max_length=64)

    # Rails: status nil means "other" in the :state scope
    status: Optional[LEAD_STATUS_VALUES] = None  # type: ignore[valid-type]

    source: Optional[LEAD_SOURCE_VALUES] = None  # type: ignore[valid-type]
    referred_by: Optional[str] = Field(None, max_length=64)

    email: Optional[EmailStr] = None
    alt_email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=32)
    mobile: Optional[str] = Field(None, max_length=32)

    blog: Optional[str] = Field(None, max_length=128)
    linkedin: Optional[str] = Field(None, max_length=128)
    facebook: Optional[str] = Field(None, max_length=128)
    twitter: Optional[str] = Field(None, max_length=128)

    # Rails: validates :rating, numericality: { only_integer: true, gte: 0 }
    rating: Annotated[int, Field(ge=0, le=5)] = 0

    # Rails: validates :do_not_call, inclusion: [true, false]
    do_not_call: bool = False

    background_info: Optional[str] = Field(None, max_length=255)

    assigned_to: Optional[int] = None
    campaign_id: Optional[int] = None

    # Rails: accepts_nested_attributes_for :business_address
    business_address: Optional[AddressCreate] = None


# ---------------------------------------------------------------------------
# Create schema
# ---------------------------------------------------------------------------
class LeadCreate(LeadBase):
    """
    Schema for POST /leads.
    Rails: LeadsController#create with save_with_permissions and add_comment_by_user.
    """
    user_id: Optional[int] = Field(None, description="Owner user ID (set server-side)")

    # Rails: params[:comment_body] — inline comment on lead creation
    comment_body: Optional[str] = Field(
        None, description="Optional comment to add on creation (Rails: @comment_body)"
    )


# ---------------------------------------------------------------------------
# Update schema — all fields optional
# ---------------------------------------------------------------------------
class LeadUpdate(BaseModel):
    """Schema for PATCH /leads/{id}."""

    first_name: Optional[str] = Field(None, min_length=1, max_length=64)
    last_name: Optional[str] = Field(None, min_length=1, max_length=64)
    access: Optional[Literal["Public", "Private", "Shared"]] = None
    title: Optional[str] = Field(None, max_length=64)
    company: Optional[str] = Field(None, max_length=64)
    status: Optional[LEAD_STATUS_VALUES] = None  # type: ignore[valid-type]
    source: Optional[LEAD_SOURCE_VALUES] = None  # type: ignore[valid-type]
    referred_by: Optional[str] = Field(None, max_length=64)
    email: Optional[EmailStr] = None
    alt_email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=32)
    mobile: Optional[str] = Field(None, max_length=32)
    blog: Optional[str] = Field(None, max_length=128)
    linkedin: Optional[str] = Field(None, max_length=128)
    facebook: Optional[str] = Field(None, max_length=128)
    twitter: Optional[str] = Field(None, max_length=128)
    rating: Optional[Annotated[int, Field(ge=0, le=5)]] = None
    do_not_call: Optional[bool] = None
    background_info: Optional[str] = Field(None, max_length=255)
    assigned_to: Optional[int] = None
    campaign_id: Optional[int] = None
    business_address: Optional[AddressCreate] = None


# ---------------------------------------------------------------------------
# Convert schema
# Rails: LeadsController#convert
# The convert action creates a Contact (required), and optionally
# creates/links an Account and Opportunity from the lead data.
# ---------------------------------------------------------------------------
class LeadConvertAccount(BaseModel):
    """
    Rails: params[:account] in convert action.
    Can link to an existing account (by id) or create a new one.
    """
    id: Optional[int] = Field(None, description="Existing account ID to link to")
    name: Optional[str] = Field(None, max_length=64, description="New account name")
    access: Literal["Public", "Private", "Shared"] = "Public"

    @field_validator("name", mode="before")
    @classmethod
    def name_or_id_required(cls, v, info):
        """Either id or name must be provided (not both blank)."""
        return v


class LeadConvertOpportunity(BaseModel):
    """
    Rails: params[:opportunity] in convert action.
    Creates a new Opportunity linked to the contact + account.
    """
    name: str = Field(..., min_length=1, max_length=64)
    stage: Optional[str] = Field(default="prospecting", max_length=32)
    amount: Optional[float] = Field(None, ge=0)
    probability: Optional[int] = Field(None, ge=0, le=100)
    closes_on: Optional[str] = None


class LeadConvert(BaseModel):
    """
    Rails: LeadsController#convert endpoint payload.
    Converts a Lead into a Contact with optional Account + Opportunity.

    Rails actions preserved:
      1. lead.status = :converted
      2. Create Contact from lead fields
      3. Optionally create/link Account
      4. Optionally create linked Opportunity
    """
    # Rails: lead is identified by URL param, but we allow overrides
    contact_access: Literal["Public", "Private", "Shared"] = "Public"
    contact_assigned_to: Optional[int] = None

    # Rails: params[:account] — optional account creation/linking
    account: Optional[LeadConvertAccount] = Field(
        None, description="Account to create or link during conversion"
    )

    # Rails: params[:opportunity] — optional opportunity creation
    opportunity: Optional[LeadConvertOpportunity] = Field(
        None, description="Opportunity to create during conversion"
    )


# ---------------------------------------------------------------------------
# Nested helpers
# ---------------------------------------------------------------------------
class LeadContactSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    first_name: str
    last_name: str
    email: Optional[str] = None


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------
class LeadResponse(BaseModel):
    """
    Full Lead representation.
    Rails: respond_with(@lead) / lead.as_json.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    campaign_id: Optional[int] = None
    assigned_to: Optional[int] = None
    first_name: str
    last_name: str
    access: str
    title: Optional[str] = None
    company: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    referred_by: Optional[str] = None
    email: Optional[str] = None
    alt_email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    blog: Optional[str] = None
    linkedin: Optional[str] = None
    facebook: Optional[str] = None
    twitter: Optional[str] = None
    rating: int
    do_not_call: bool
    background_info: Optional[str] = None
    deleted_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Computed
    full_name: Optional[str] = None

    # Relationships
    contact: Optional[LeadContactSummary] = None

    def model_post_init(self, __context) -> None:
        """Rails: def full_name; '#{first_name} #{last_name}'.strip; end"""
        if self.full_name is None:
            self.full_name = f"{self.first_name or ''} {self.last_name or ''}".strip()


# ---------------------------------------------------------------------------
# List / paginated response
# ---------------------------------------------------------------------------
class LeadList(BaseModel):
    """Rails: @leads = Lead.page(n).per(m)"""
    items: List[LeadResponse]
    total: int
    page: int
    per_page: int
    pages: int
