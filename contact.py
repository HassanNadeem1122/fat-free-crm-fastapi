# schemas/account.py
# ---------------------------------------------------------------------------
# Pydantic v2 schemas for Account module.
#
# Rails → Pydantic v2 conversion notes:
#   validates :name, presence: true, length: { max: 64 }
#     → name: str with min_length=1, max_length=64
#   validates :access, inclusion: %w[Public Private Shared]
#     → access: Literal["Public", "Private", "Shared"]
#   validates :rating, numericality: { only_integer: true, gte: 0 }
#     → rating: int with ge=0
#   validates :email, format: email_regex
#     → email: Optional[EmailStr]
#   Rails: attr_accessible — represented by Create/Update split schemas
#   Rails: to_json / as_json — represented by Response schema
# ---------------------------------------------------------------------------

from datetime import datetime
from decimal import Decimal
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ---------------------------------------------------------------------------
# Shared / base fields
# ---------------------------------------------------------------------------
class AccountBase(BaseModel):
    """
    Fields shared between Create and Update schemas.
    Rails: attr_accessible whitelist (mass-assignment protection).
    """

    # Rails: validates :name, presence: true, length: { maximum: 64 }
    name: str = Field(..., min_length=1, max_length=64, description="Company name")

    # Rails: validates :access, inclusion: { in: %w[Public Private Shared] }
    access: Literal["Public", "Private", "Shared"] = Field(
        default="Public", description="Visibility: Public, Private, or Shared"
    )

    website: Optional[str] = Field(None, max_length=64)
    toll_free_phone: Optional[str] = Field(None, max_length=32)
    phone: Optional[str] = Field(None, max_length=32)
    fax: Optional[str] = Field(None, max_length=32)

    # Rails: validates :email (loose regex, not strict RFC)
    email: Optional[EmailStr] = None

    background_info: Optional[str] = Field(None, max_length=255)

    # Rails: validates :rating, numericality: { only_integer: true, gte: 0 }
    rating: Annotated[int, Field(ge=0, le=5)] = 0

    category: Optional[str] = Field(None, max_length=32)

    # Social media
    blog: Optional[str] = Field(None, max_length=128)
    linkedin: Optional[str] = Field(None, max_length=128)
    facebook: Optional[str] = Field(None, max_length=128)
    twitter: Optional[str] = Field(None, max_length=128)
    bluesky: Optional[str] = Field(None, max_length=128)
    instagram: Optional[str] = Field(None, max_length=128)
    mastodon: Optional[str] = Field(None, max_length=128)

    # Wikidata enrichment
    wikidata_id: Optional[str] = Field(None, max_length=64)
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None

    # Rails: belongs_to :assignee, FK: assigned_to
    assigned_to: Optional[int] = Field(None, description="User ID of the assignee")

    @field_validator("website")
    @classmethod
    def validate_website(cls, v: Optional[str]) -> Optional[str]:
        """
        Rails: validates :website, format: { with: URI_REGEXP }, allow_blank: true
        Loose URL validation — prefix with https:// if no scheme present.
        """
        if v and not v.startswith(("http://", "https://", "ftp://")):
            return f"https://{v}"
        return v


# ---------------------------------------------------------------------------
# Create schema
# Rails: AccountsController#create (strong_parameters / attr_accessible)
# ---------------------------------------------------------------------------
class AccountCreate(AccountBase):
    """Schema for POST /accounts."""

    # Rails: user_id is set from current_user in controller
    user_id: Optional[int] = Field(None, description="Owner user ID (set server-side)")


# ---------------------------------------------------------------------------
# Update schema — all fields optional (PATCH semantics)
# Rails: AccountsController#update with partial params
# ---------------------------------------------------------------------------
class AccountUpdate(BaseModel):
    """Schema for PATCH /accounts/{id}. All fields optional."""

    name: Optional[str] = Field(None, min_length=1, max_length=64)
    access: Optional[Literal["Public", "Private", "Shared"]] = None
    website: Optional[str] = Field(None, max_length=64)
    toll_free_phone: Optional[str] = Field(None, max_length=32)
    phone: Optional[str] = Field(None, max_length=32)
    fax: Optional[str] = Field(None, max_length=32)
    email: Optional[EmailStr] = None
    background_info: Optional[str] = Field(None, max_length=255)
    rating: Optional[Annotated[int, Field(ge=0, le=5)]] = None
    category: Optional[str] = Field(None, max_length=32)
    blog: Optional[str] = Field(None, max_length=128)
    linkedin: Optional[str] = Field(None, max_length=128)
    facebook: Optional[str] = Field(None, max_length=128)
    twitter: Optional[str] = Field(None, max_length=128)
    bluesky: Optional[str] = Field(None, max_length=128)
    instagram: Optional[str] = Field(None, max_length=128)
    mastodon: Optional[str] = Field(None, max_length=128)
    wikidata_id: Optional[str] = Field(None, max_length=64)
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    assigned_to: Optional[int] = None


# ---------------------------------------------------------------------------
# Nested response helpers
# ---------------------------------------------------------------------------
class AccountContactSummary(BaseModel):
    """Minimal contact info embedded in AccountResponse."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    first_name: str
    last_name: str
    email: Optional[str] = None


class AccountOpportunitySummary(BaseModel):
    """Minimal opportunity info embedded in AccountResponse."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    stage: Optional[str] = None
    amount: Optional[Decimal] = None
    closes_on: Optional[str] = None


# ---------------------------------------------------------------------------
# Response schema
# Rails: account.as_json / to_json / respond_with(@account)
# ---------------------------------------------------------------------------
class AccountResponse(BaseModel):
    """
    Full Account representation returned by GET endpoints.
    Rails: respond_with(@account) → serializes the full object.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    assigned_to: Optional[int] = None
    name: str
    access: str
    website: Optional[str] = None
    toll_free_phone: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    email: Optional[str] = None
    background_info: Optional[str] = None
    rating: int
    category: Optional[str] = None
    blog: Optional[str] = None
    linkedin: Optional[str] = None
    facebook: Optional[str] = None
    twitter: Optional[str] = None
    bluesky: Optional[str] = None
    instagram: Optional[str] = None
    mastodon: Optional[str] = None
    wikidata_id: Optional[str] = None
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    contacts_count: int = 0
    opportunities_count: int = 0
    deleted_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Nested relationships
    contacts: List[AccountContactSummary] = []
    opportunities: List[AccountOpportunitySummary] = []


# ---------------------------------------------------------------------------
# List / paginated response
# Rails: @accounts = Account.page(n).per(m) → Kaminari pagination
# ---------------------------------------------------------------------------
class AccountList(BaseModel):
    """Paginated list of accounts. Rails: will_paginate / kaminari response."""

    items: List[AccountResponse]
    total: int
    page: int
    per_page: int
    pages: int
