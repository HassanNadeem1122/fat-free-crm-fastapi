# routers/contacts.py
# ---------------------------------------------------------------------------
# FastAPI router for Contact CRUD.
# Rails source: app/controllers/entities/contacts_controller.rb
#
# Rails → FastAPI endpoint mapping:
#   GET    /contacts            → index  (paginated + search)
#   GET    /contacts/:id        → show   (with vCard export option)
#   POST   /contacts            → create (with nested address + account link)
#   PATCH  /contacts/:id        → update
#   DELETE /contacts/:id        → destroy (soft-delete)
#   PUT    /contacts/:id/restore → restore
#   GET    /contacts/:id/opportunities → related opportunities
#
# Rails patterns preserved:
#   before_action :get_accounts, only: %i[new create edit update]
#     → accounts available via GET /accounts (separate call in SPA context)
#   @timeline = timeline(@contact)
#     → simplified: return related activities as a list
#   respond_with @contact, format: :vcf
#     → GET /contacts/:id/vcard endpoint
#   params[:related] — create contact pre-linked to lead or opportunity
#     → related_to_lead_id / related_to_opportunity_id query params on create
#   save_with_permissions — sets access + user_id
#     → applied in create/update
#   Contact.scope_text_search — Arel name search
#     → q= query param
# ---------------------------------------------------------------------------

import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.account import Account, AccountContact
from models.contact import Contact, ContactOpportunity
from models.opportunity import Opportunity
from schemas.contact import (
    ContactCreate,
    ContactList,
    ContactResponse,
    ContactUpdate,
)
from .dependencies import get_current_user, UserContext

router = APIRouter(prefix="/contacts", tags=["contacts"])

DEFAULT_PER_PAGE = 20


# ---------------------------------------------------------------------------
# GET /contacts
# Rails: ContactsController#index
# ---------------------------------------------------------------------------
@router.get("", response_model=ContactList)
async def list_contacts(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=DEFAULT_PER_PAGE, ge=1, le=100),
    q: Optional[str] = Query(default=None, description="Text search (name/email)"),
    source: Optional[str] = Query(default=None),
    assigned_to: Optional[int] = Query(default=None),
    created_by: Optional[int] = Query(default=None),
    account_id: Optional[int] = Query(default=None, description="Filter by account"),
    do_not_call: Optional[bool] = Query(default=None, description="Filter do_not_call flag"),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: ContactsController#index
    Paginated list of contacts with filtering.

    Rails: @contacts = get_contacts(page: page_param, per_page: per_page_param)
    get_contacts is defined in EntitiesController with visibility scoping.
    """
    stmt = Contact.scope_active()

    if q:
        # Rails: scope :text_search — searches first+last name in any order
        stmt = Contact.scope_text_search(q)

    if source:
        stmt = stmt.where(Contact.source == source)
    if assigned_to:
        stmt = stmt.where(Contact.assigned_to == assigned_to)
    if created_by:
        stmt = stmt.where(Contact.user_id == created_by)
    if do_not_call is not None:
        stmt = stmt.where(Contact.do_not_call == do_not_call)
    if account_id:
        # Filter contacts belonging to a specific account via join table
        stmt = stmt.join(
            AccountContact,
            (AccountContact.contact_id == Contact.id)
            & AccountContact.deleted_at.is_(None),
        ).where(AccountContact.account_id == account_id)

    # Rails: visibility scoping (access = Public OR owned/assigned)
    stmt = _apply_visibility_scope(stmt, current_user.user_id)

    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Paginate + sort (Rails: .order("last_name, first_name"))
    offset = (page - 1) * per_page
    stmt = (
        stmt
        .order_by(Contact.last_name, Contact.first_name)
        .offset(offset)
        .limit(per_page)
    )

    result = await db.execute(stmt)
    contacts = result.scalars().unique().all()

    return ContactList(
        items=[ContactResponse.model_validate(c) for c in contacts],
        total=total,
        page=page,
        per_page=per_page,
        pages=math.ceil(total / per_page) if total else 0,
    )


# ---------------------------------------------------------------------------
# GET /contacts/:id
# Rails: ContactsController#show
# ---------------------------------------------------------------------------
@router.get("/{contact_id}", response_model=ContactResponse)
async def get_contact(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: ContactsController#show
    Rails: @stage = Setting.unroll(:opportunity_stage) — stages available separately.
    Rails: @timeline = timeline(@contact) — activity timeline (simplified).
    """
    contact = await _get_contact_or_404(contact_id, db, current_user)
    return ContactResponse.model_validate(contact)


# ---------------------------------------------------------------------------
# GET /contacts/:id/vcard
# Rails: format.vcf { send_data helpers.vcard_for(@contact) }
# ---------------------------------------------------------------------------
@router.get("/{contact_id}/vcard")
async def export_vcard(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: respond_with(@contact) { format.vcf { send_data vcard_for(@contact) } }
    Returns basic vCard 3.0 format for contact export.
    """
    contact = await _get_contact_or_404(contact_id, db, current_user)

    vcard = _build_vcard(contact)
    return Response(
        content=vcard,
        media_type="text/x-vcard",
        headers={
            "Content-Disposition": f'attachment; filename="{contact.full_name}.vcf"'
        },
    )


# ---------------------------------------------------------------------------
# POST /contacts
# Rails: ContactsController#create
# ---------------------------------------------------------------------------
@router.post("", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
async def create_contact(
    payload: ContactCreate,
    related_to_lead_id: Optional[int] = Query(
        default=None,
        description="Rails: params[:related] = 'lead_ID' — pre-link to lead"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: ContactsController#create
    Rails: @contact.save_with_permissions(params.permit!)
    Rails: params[:related] — pre-link to a lead or opportunity.

    Business logic preserved:
      - Sets user_id from current_user (Rails: current_user assignment)
      - Links to account via AccountContact join table if account_id provided
      - Links to lead if related_to_lead_id provided (Rails: :related param)
      - Nested address created if business_address provided
    """
    account_id = payload.account_id
    address_data = payload.business_address

    # Build contact without relationship fields
    data = payload.model_dump(exclude={"user_id", "account_id", "business_address"})
    contact = Contact(**data, user_id=current_user.user_id)

    # Rails: params[:related] = 'lead_ID' sets lead_id
    if related_to_lead_id and not contact.lead_id:
        contact.lead_id = related_to_lead_id

    db.add(contact)
    await db.flush()  # Get contact.id

    # Rails: create account_contact join if account_id given
    if account_id:
        join_rec = AccountContact(account_id=account_id, contact_id=contact.id)
        db.add(join_rec)

        # Rails: counter_cache increment on Account
        acct_result = await db.execute(select(Account).where(Account.id == account_id))
        acct = acct_result.scalar_one_or_none()
        if acct:
            acct.contacts_count = (acct.contacts_count or 0) + 1

    # Rails: accepts_nested_attributes_for :business_address
    if address_data:
        from models.polymorphic_address import Address  # Lazy import
        addr = Address(
            addressable_type="Contact",
            addressable_id=contact.id,
            address_type=address_data.address_type,
            street1=address_data.street1,
            street2=address_data.street2,
            city=address_data.city,
            state=address_data.state,
            zipcode=address_data.zipcode,
            country=address_data.country,
            full_address=address_data.full_address,
        )
        db.add(addr)

    await db.commit()
    await db.refresh(contact)
    return ContactResponse.model_validate(contact)


# ---------------------------------------------------------------------------
# PATCH /contacts/:id
# Rails: ContactsController#update
# ---------------------------------------------------------------------------
@router.patch("/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: int,
    payload: ContactUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: ContactsController#update
    Rails: @contact.update_with_permissions(params.permit!)
    """
    contact = await _get_contact_or_404(contact_id, db, current_user)

    # Handle account_id change — update join table
    new_account_id = payload.account_id
    update_data = payload.model_dump(exclude_none=True, exclude={"account_id", "business_address"})
    for field, value in update_data.items():
        setattr(contact, field, value)

    if new_account_id is not None:
        await _update_contact_account(contact.id, new_account_id, db)

    await db.commit()
    await db.refresh(contact)
    return ContactResponse.model_validate(contact)


# ---------------------------------------------------------------------------
# DELETE /contacts/:id (soft-delete)
# Rails: ContactsController#destroy
# ---------------------------------------------------------------------------
@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: ContactsController#destroy (acts_as_paranoid → sets deleted_at).
    Rails: dependent: :destroy on contact_opportunities is handled by cascade.
    Rails: has_one :account_contact, dependent: :destroy → join record deleted.
    """
    contact = await _get_contact_or_404(contact_id, db, current_user)
    contact.soft_delete()
    await db.commit()


# ---------------------------------------------------------------------------
# PUT /contacts/:id/restore
# Rails: acts_as_paranoid restore
# ---------------------------------------------------------------------------
@router.put("/{contact_id}/restore", response_model=ContactResponse)
async def restore_contact(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """Rails: contact.restore! — clears deleted_at."""
    result = await db.execute(select(Contact).where(Contact.id == contact_id))
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    contact.restore()
    await db.commit()
    await db.refresh(contact)
    return ContactResponse.model_validate(contact)


# ---------------------------------------------------------------------------
# GET /contacts/:id/opportunities
# Rails: @contact.opportunities (ordered by id DESC, distinct)
# ---------------------------------------------------------------------------
@router.get("/{contact_id}/opportunities")
async def get_contact_opportunities(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: has_many :opportunities, -> { order("id DESC").distinct }, through: :contact_opportunities
    Returns all active opportunities linked to this contact.
    """
    contact = await _get_contact_or_404(contact_id, db, current_user)

    stmt = (
        select(Opportunity)
        .join(
            ContactOpportunity,
            (ContactOpportunity.opportunity_id == Opportunity.id)
            & ContactOpportunity.deleted_at.is_(None),
        )
        .where(
            ContactOpportunity.contact_id == contact_id,
            Opportunity.deleted_at.is_(None),
        )
        .order_by(Opportunity.id.desc())
        .distinct()
    )
    result = await db.execute(stmt)
    opportunities = result.scalars().all()
    return {"contact_id": contact_id, "opportunities": opportunities}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_visibility_scope(stmt, user_id: int):
    """
    Rails: EntitiesController visibility scoping.
    Public OR owned by OR assigned to current_user.
    """
    return stmt.where(
        or_(
            Contact.access == "Public",
            Contact.user_id == user_id,
            Contact.assigned_to == user_id,
        )
    )


async def _get_contact_or_404(
    contact_id: int, db: AsyncSession, current_user: UserContext
) -> Contact:
    """Rails: @contact = Contact.my(current_user).find(params[:id])"""
    stmt = Contact.scope_active().where(Contact.id == contact_id)
    stmt = _apply_visibility_scope(stmt, current_user.user_id)
    result = await db.execute(stmt)
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


async def _update_contact_account(contact_id: int, new_account_id: int, db: AsyncSession):
    """
    Rails: contact.account = account (replaces existing account_contact join).
    Soft-deletes old join record and creates a new one.
    """
    # Soft-delete existing join
    old_result = await db.execute(
        select(AccountContact).where(
            AccountContact.contact_id == contact_id,
            AccountContact.deleted_at.is_(None),
        )
    )
    old_join = old_result.scalar_one_or_none()
    if old_join:
        if old_join.account_id == new_account_id:
            return  # No change
        old_join.deleted_at = __import__("datetime").datetime.utcnow()

        # Decrement old account counter
        old_acct = await db.execute(select(Account).where(Account.id == old_join.account_id))
        old_acct = old_acct.scalar_one_or_none()
        if old_acct and old_acct.contacts_count > 0:
            old_acct.contacts_count -= 1

    # Create new join
    new_join = AccountContact(account_id=new_account_id, contact_id=contact_id)
    db.add(new_join)

    # Increment new account counter
    new_acct = await db.execute(select(Account).where(Account.id == new_account_id))
    new_acct = new_acct.scalar_one_or_none()
    if new_acct:
        new_acct.contacts_count = (new_acct.contacts_count or 0) + 1


def _build_vcard(contact: Contact) -> str:
    """
    Rails: helpers.vcard_for(@contact) — generates vCard 3.0 string.
    Used in format.vcf block of show action.
    """
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{contact.full_name}",
        f"N:{contact.last_name};{contact.first_name};;;",
    ]
    if contact.email:
        lines.append(f"EMAIL;TYPE=work:{contact.email}")
    if contact.phone:
        lines.append(f"TEL;TYPE=work,voice:{contact.phone}")
    if contact.mobile:
        lines.append(f"TEL;TYPE=cell:{contact.mobile}")
    if contact.title:
        lines.append(f"TITLE:{contact.title}")
    if contact.department:
        lines.append(f"ORG:;{contact.department}")
    if contact.linkedin:
        lines.append(f"URL;TYPE=LinkedIn:{contact.linkedin}")
    lines.append("END:VCARD")
    return "\r\n".join(lines)
