# schemas/contact.py
# ---------------------------------------------------------------------------
# Pydantic v2 schemas for Contact module.
#
# Rails validations → Pydantic v2:
#   validates :first_name, presence: true, length: { max: 64 }
#     → first_name: str, min_length=1, max_length=64
#   validates :last_name, presence: true, length: { max: 64 }
#     → last_name: str, min_length=1, max_length=64
#   validates :access, inclusion: %w[Public Private Shared]
#     → Literal type
#   validates :do_not_call, inclusion: [true, false]
#     → do_not_call: bool (Pydantic enforces bool type)
#   validates :email, format: /.../
#     → Optional[EmailStr]
#   validates :born_on, timeliness: { type: :date }
#     → Optional[date]
#   accepts_nested_attributes_for :business_address
#     → Optional[AddressCreate] nested schema
# ---------------------------------------------------------------------------

from datetime import date, datetime
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Address sub-schema
# Rails: accepts_nested_attributes_for :business_address
# The Address model is polymorphic (addressable_type/addressable_id).
# ---------------------------------------------------------------------------
class AddressBase(BaseModel):
    """
    Mirrors Rails Address polymorphic model fields.
    Used for nested accepts_nested_attributes_for conversions.
    """
    address_type: str = Field(default="Business", description="Business | Billing | Shipping")
    street1: Optional[str] = Field(None, max_length=255)
    street2: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=64)
    state: Optional[str] = Field(None, max_length=64)
    zipcode: Optional[str] = Field(None, max_length=16)
    country: Optional[str] = Field(None, max_length=64)
    full_address: Optional[str] = Field(None, max_length=255)


class AddressCreate(AddressBase):
    """Schema for creating/updating a nested address."""
    # Rails: reject_if: proc { |attrs| Address.reject_address(attrs) }
    # → at least one non-type field must be non-empty; validated in model_validator

    @model_validator(mode="after")
    def reject_empty_address(self) -> "AddressCreate":
        """
        Rails: reject_if: proc { |attributes| Address.reject_address(attributes) }
        Address.reject_address returns true (reject) when all address fields are blank.
        """
        has_data = any([
            self.street1, self.street2, self.city,
            self.state, self.zipcode, self.country, self.full_address,
        ])
        if not has_data:
            raise ValueError("Address must have at least one non-empty field")
        return self


class AddressResponse(AddressBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------
class ContactBase(BaseModel):
    """
    Fields shared between Create and Update schemas.
    Rails: attr_accessible whitelist.
    """

    # Rails: validates :first_name, presence: true, length: { max: 64 }
    first_name: str = Field(..., min_length=1, max_length=64)

    # Rails: validates :last_name, presence: true, length: { max: 64 }
    last_name: str = Field(..., min_length=1, max_length=64)

    # Rails: validates :access, inclusion: { in: %w[Public Private Shared] }
    access: Literal["Public", "Private", "Shared"] = "Public"

    title: Optional[str] = Field(None, max_length=64)
    department: Optional[str] = Field(None, max_length=64)
    source: Optional[str] = Field(None, max_length=32)

    email: Optional[EmailStr] = None
    alt_email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=32)
    mobile: Optional[str] = Field(None, max_length=32)
    fax: Optional[str] = Field(None, max_length=32)

    blog: Optional[str] = Field(None, max_length=128)
    linkedin: Optional[str] = Field(None, max_length=128)
    facebook: Optional[str] = Field(None, max_length=128)
    twitter: Optional[str] = Field(None, max_length=128)

    # Rails: validates :born_on, timeliness: { type: :date }, allow_blank: true
    born_on: Optional[date] = None

    # Rails: validates :do_not_call, inclusion: [true, false]
    # Rails controller ensures this is never nil — it's a checkbox (always bool).
    do_not_call: bool = False

    background_info: Optional[str] = Field(None, max_length=255)

    # Rails: belongs_to :assignee, FK: assigned_to
    assigned_to: Optional[int] = None

    # Rails: belongs_to :reporting_user, FK: reports_to
    reports_to: Optional[int] = None

    # Rails: belongs_to :lead (track conversion source)
    lead_id: Optional[int] = None

    # Rails: accepts_nested_attributes_for :business_address
    business_address: Optional[AddressCreate] = None

    @field_validator("born_on", mode="before")
    @classmethod
    def parse_born_on(cls, v):
        """
        Rails: validates :born_on using validates_timeliness gem.
        Accept both date objects and ISO-8601 strings.
        """
        if v is None or isinstance(v, date):
            return v
        if isinstance(v, str):
            try:
                return date.fromisoformat(v)
            except ValueError:
                raise ValueError(f"Invalid date format for born_on: {v}")
        return v


# ---------------------------------------------------------------------------
# Create schema
# ---------------------------------------------------------------------------
class ContactCreate(ContactBase):
    """Schema for POST /contacts."""
    user_id: Optional[int] = Field(None, description="Owner user ID (set server-side)")

    # Rails: account_id for linking contact to account on creation
    account_id: Optional[int] = Field(
        None, description="Account ID to link this contact to"
    )


# ---------------------------------------------------------------------------
# Update schema
# ---------------------------------------------------------------------------
class ContactUpdate(BaseModel):
    """Schema for PATCH /contacts/{id}. All fields optional."""

    first_name: Optional[str] = Field(None, min_length=1, max_length=64)
    last_name: Optional[str] = Field(None, min_length=1, max_length=64)
    access: Optional[Literal["Public", "Private", "Shared"]] = None
    title: Optional[str] = Field(None, max_length=64)
    department: Optional[str] = Field(None, max_length=64)
    source: Optional[str] = Field(None, max_length=32)
    email: Optional[EmailStr] = None
    alt_email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=32)
    mobile: Optional[str] = Field(None, max_length=32)
    fax: Optional[str] = Field(None, max_length=32)
    blog: Optional[str] = Field(None, max_length=128)
    linkedin: Optional[str] = Field(None, max_length=128)
    facebook: Optional[str] = Field(None, max_length=128)
    twitter: Optional[str] = Field(None, max_length=128)
    born_on: Optional[date] = None
    do_not_call: Optional[bool] = None
    background_info: Optional[str] = Field(None, max_length=255)
    assigned_to: Optional[int] = None
    reports_to: Optional[int] = None
    lead_id: Optional[int] = None
    business_address: Optional[AddressCreate] = None
    account_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Nested helpers
# ---------------------------------------------------------------------------
class ContactOpportunitySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    stage: Optional[str] = None
    amount: Optional[float] = None


class ContactAccountSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------
class ContactResponse(BaseModel):
    """
    Full Contact representation.
    Rails: respond_with(@contact) / contact.as_json.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    lead_id: Optional[int] = None
    assigned_to: Optional[int] = None
    reports_to: Optional[int] = None
    first_name: str
    last_name: str
    access: str
    title: Optional[str] = None
    department: Optional[str] = None
    source: Optional[str] = None
    email: Optional[str] = None
    alt_email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    fax: Optional[str] = None
    blog: Optional[str] = None
    linkedin: Optional[str] = None
    facebook: Optional[str] = None
    twitter: Optional[str] = None
    born_on: Optional[date] = None
    do_not_call: bool
    background_info: Optional[str] = None
    deleted_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Computed (Rails: def full_name)
    full_name: Optional[str] = None

    # Relationships
    account: Optional[ContactAccountSummary] = None
    opportunities: List[ContactOpportunitySummary] = []
    business_address: Optional[AddressResponse] = None

    @model_validator(mode="after")
    def compute_full_name(self) -> "ContactResponse":
        """Rails: def full_name; '#{first_name} #{last_name}'.strip; end"""
        if self.full_name is None:
            self.full_name = f"{self.first_name or ''} {self.last_name or ''}".strip()
        return self


# ---------------------------------------------------------------------------
# List / paginated response
# ---------------------------------------------------------------------------
class ContactList(BaseModel):
    """Rails: @contacts = Contact.page(n).per(m)"""
    items: List[ContactResponse]
    total: int
    page: int
    per_page: int
    pages: int
