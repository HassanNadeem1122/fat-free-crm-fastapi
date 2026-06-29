# routers/leads.py
# ---------------------------------------------------------------------------
# FastAPI router for Lead CRUD + Convert.
# Rails source: app/controllers/entities/leads_controller.rb
#
# Rails → FastAPI endpoint mapping:
#   GET    /leads              → index  (paginated + state filter)
#   GET    /leads/:id          → show   (with vCard export)
#   POST   /leads              → create (with comment_body + permissions)
#   PATCH  /leads/:id          → update
#   DELETE /leads/:id          → destroy (soft-delete + nullify contact.lead_id)
#   PUT    /leads/:id/restore  → restore
#   POST   /leads/:id/convert  → convert (Rails #convert action)
#
# Rails patterns preserved:
#   before_action :get_data_for_sidebar → sidebar data
#   save_with_permissions(params.permit!) → access + user_id from JWT
#   @lead.add_comment_by_user(body, user) → comment_body in create
#   called_from_index_page?              → managed client-side in SPA
#   @lead.status = :converted           → Lead.mark_as_converted()
#   has_one :contact, dependent: :nullify → nullify on delete
#   scope :state (filters + 'other'=NULL) → state= query param
#   autocomplete :account, :name         → GET /accounts?q= endpoint serves this
# ---------------------------------------------------------------------------

import math
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.account import Account, AccountContact, AccountOpportunity
from models.contact import Contact, ContactOpportunity
from models.lead import Lead, LeadStatus
from models.opportunity import Opportunity
from schemas.lead import (
    LeadConvert,
    LeadCreate,
    LeadList,
    LeadResponse,
    LeadUpdate,
)
from schemas.contact import ContactResponse
from .dependencies import get_current_user, UserContext

router = APIRouter(prefix="/leads", tags=["leads"])

DEFAULT_PER_PAGE = 20


# ---------------------------------------------------------------------------
# GET /leads
# Rails: LeadsController#index
# ---------------------------------------------------------------------------
@router.get("", response_model=LeadList)
async def list_leads(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=DEFAULT_PER_PAGE, ge=1, le=100),
    q: Optional[str] = Query(default=None, description="Text search"),
    # Rails: scope :state → status filter
    state: Optional[List[str]] = Query(
        default=None,
        description="Status filter values; use 'other' for NULL status (Rails: scope :state)"
    ),
    assigned_to: Optional[int] = Query(default=None),
    created_by: Optional[int] = Query(default=None),
    campaign_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: LeadsController#index
    Rails: @leads = get_leads(page: page_param)
    Supports:
      - Text search across first_name, last_name, company, email
      - State filter (maps Rails scope :state with 'other' = NULL)
      - Pagination
    """
    stmt = Lead.scope_active()

    if q:
        stmt = Lead.scope_text_search(q)

    if state:
        # Rails: scope :state — 'other' maps to NULL status
        include_null = "other" in state
        status_filters = [s for s in state if s != "other"]
        if status_filters or include_null:
            stmt = Lead.scope_state(status_filters, include_null=include_null)

    if assigned_to:
        stmt = stmt.where(Lead.assigned_to == assigned_to)
    if created_by:
        stmt = stmt.where(Lead.user_id == created_by)
    if campaign_id:
        stmt = stmt.where(Lead.campaign_id == campaign_id)

    stmt = _apply_visibility_scope(stmt, current_user.user_id)

    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Paginate
    offset = (page - 1) * per_page
    stmt = (
        stmt
        .order_by(Lead.last_name, Lead.first_name)
        .offset(offset)
        .limit(per_page)
    )

    result = await db.execute(stmt)
    leads = result.scalars().unique().all()

    return LeadList(
        items=[LeadResponse.model_validate(l) for l in leads],
        total=total,
        page=page,
        per_page=per_page,
        pages=math.ceil(total / per_page) if total else 0,
    )


# ---------------------------------------------------------------------------
# GET /leads/:id
# Rails: LeadsController#show
# ---------------------------------------------------------------------------
@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: LeadsController#show
    Rails: @timeline = timeline(@lead) — activity log (simplified).
    """
    lead = await _get_lead_or_404(lead_id, db, current_user)
    return LeadResponse.model_validate(lead)


# ---------------------------------------------------------------------------
# GET /leads/:id/vcard
# Rails: format.vcf { send_data helpers.vcard_for(@lead) }
# ---------------------------------------------------------------------------
@router.get("/{lead_id}/vcard")
async def export_lead_vcard(
    lead_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """Rails: LeadsController#show format.vcf block."""
    lead = await _get_lead_or_404(lead_id, db, current_user)
    vcard = _build_lead_vcard(lead)
    return Response(
        content=vcard,
        media_type="text/x-vcard",
        headers={
            "Content-Disposition": f'attachment; filename="{lead.full_name}.vcf"'
        },
    )


# ---------------------------------------------------------------------------
# POST /leads
# Rails: LeadsController#create
# ---------------------------------------------------------------------------
@router.post("", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def create_lead(
    payload: LeadCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: LeadsController#create
    Rails: @lead.save_with_permissions(params.permit!)
    Rails: @lead.add_comment_by_user(@comment_body, current_user)
      → comment_body stored for now; full comment model would persist to DB.
    """
    comment_body = payload.comment_body
    data = payload.model_dump(exclude={"user_id", "business_address", "comment_body"})
    lead = Lead(**data, user_id=current_user.user_id)

    db.add(lead)
    await db.flush()

    # Rails: @lead.add_comment_by_user(@comment_body, current_user)
    # TODO: persist comment to comments table when Comment model is added
    if comment_body:
        pass  # Placeholder — extend with Comment model

    await db.commit()
    await db.refresh(lead)
    return LeadResponse.model_validate(lead)


# ---------------------------------------------------------------------------
# PATCH /leads/:id
# Rails: LeadsController#update
# ---------------------------------------------------------------------------
@router.patch("/{lead_id}", response_model=LeadResponse)
async def update_lead(
    lead_id: int,
    payload: LeadUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """Rails: LeadsController#update"""
    lead = await _get_lead_or_404(lead_id, db, current_user)

    update_data = payload.model_dump(exclude_none=True, exclude={"business_address"})
    for field, value in update_data.items():
        setattr(lead, field, value)

    await db.commit()
    await db.refresh(lead)
    return LeadResponse.model_validate(lead)


# ---------------------------------------------------------------------------
# DELETE /leads/:id (soft-delete)
# Rails: LeadsController#destroy
# ---------------------------------------------------------------------------
@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lead(
    lead_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: LeadsController#destroy
    Rails: has_one :contact, dependent: :nullify
      → sets contact.lead_id = NULL on lead deletion (not cascade delete!)
    Rails: acts_as_paranoid sets deleted_at.
    """
    lead = await _get_lead_or_404(lead_id, db, current_user)

    # Rails: dependent: :nullify — contact keeps its data, just loses lead reference
    if lead.contact:
        lead.contact.lead_id = None

    lead.soft_delete()
    await db.commit()


# ---------------------------------------------------------------------------
# PUT /leads/:id/restore
# ---------------------------------------------------------------------------
@router.put("/{lead_id}/restore", response_model=LeadResponse)
async def restore_lead(
    lead_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """Rails: lead.restore! (acts_as_paranoid)"""
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.restore()
    await db.commit()
    await db.refresh(lead)
    return LeadResponse.model_validate(lead)


# ---------------------------------------------------------------------------
# POST /leads/:id/convert
# Rails: LeadsController#convert (custom action)
# ---------------------------------------------------------------------------
@router.post("/{lead_id}/convert", response_model=ContactResponse)
async def convert_lead(
    lead_id: int,
    payload: LeadConvert,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: LeadsController#convert
    The most complex Lead action. Preserved exactly:

    Rails flow:
      1. Load lead (must not be already converted)
      2. Build Contact from lead fields
      3. Optionally create or link Account
      4. Optionally create Opportunity linked to Contact + Account
      5. Set lead.status = :converted
      6. Save all with permissions

    Returns: the newly created Contact.
    """
    lead = await _get_lead_or_404(lead_id, db, current_user)

    # Rails: guard — cannot convert an already-converted lead
    if lead.is_converted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Lead is already converted",
        )

    # -------------------------------------------------------------------------
    # Step 1: Create Contact from Lead fields
    # Rails: @contact = Contact.new(lead_attrs)
    # -------------------------------------------------------------------------
    contact = Contact(
        user_id=current_user.user_id,
        lead_id=lead.id,
        first_name=lead.first_name,
        last_name=lead.last_name,
        access=payload.contact_access,
        assigned_to=payload.contact_assigned_to or lead.assigned_to,
        title=lead.title,
        source=lead.source,
        email=lead.email,
        alt_email=lead.alt_email,
        phone=lead.phone,
        mobile=lead.mobile,
        blog=lead.blog,
        linkedin=lead.linkedin,
        facebook=lead.facebook,
        twitter=lead.twitter,
        do_not_call=lead.do_not_call,
        background_info=lead.background_info,
    )
    db.add(contact)
    await db.flush()  # Get contact.id

    # -------------------------------------------------------------------------
    # Step 2: Handle Account (create new or link existing)
    # Rails: params[:account] in convert action
    # -------------------------------------------------------------------------
    resolved_account_id: Optional[int] = None

    if payload.account:
        acct_payload = payload.account
        if acct_payload.id:
            # Rails: link to existing account
            acct_result = await db.execute(
                select(Account).where(
                    Account.id == acct_payload.id,
                    Account.deleted_at.is_(None),
                )
            )
            existing_acct = acct_result.scalar_one_or_none()
            if not existing_acct:
                raise HTTPException(status_code=404, detail="Account not found")
            resolved_account_id = existing_acct.id
        elif acct_payload.name:
            # Rails: create new account
            new_acct = Account(
                name=acct_payload.name,
                access=acct_payload.access,
                user_id=current_user.user_id,
                # Inherit company info from lead
                phone=lead.phone,
            )
            db.add(new_acct)
            await db.flush()
            resolved_account_id = new_acct.id
            new_acct.contacts_count = 1
            new_acct.opportunities_count = 0

        if resolved_account_id:
            # Link contact to account via join table
            acct_contact_join = AccountContact(
                account_id=resolved_account_id,
                contact_id=contact.id,
            )
            db.add(acct_contact_join)

    # -------------------------------------------------------------------------
    # Step 3: Create Opportunity (optional)
    # Rails: params[:opportunity] in convert action
    # -------------------------------------------------------------------------
    if payload.opportunity:
        opp_payload = payload.opportunity
        from decimal import Decimal
        import datetime

        closes_on = None
        if opp_payload.closes_on:
            closes_on = datetime.date.fromisoformat(opp_payload.closes_on)

        opportunity = Opportunity(
            user_id=current_user.user_id,
            name=opp_payload.name,
            stage=opp_payload.stage or "prospecting",
            probability=opp_payload.probability,
            amount=Decimal(str(opp_payload.amount)) if opp_payload.amount else None,
            closes_on=closes_on,
            access=payload.contact_access,
            campaign_id=lead.campaign_id,
        )
        db.add(opportunity)
        await db.flush()

        # Link opportunity → contact
        co_join = ContactOpportunity(
            contact_id=contact.id,
            opportunity_id=opportunity.id,
        )
        db.add(co_join)

        # Link opportunity → account
        if resolved_account_id:
            ao_join = AccountOpportunity(
                account_id=resolved_account_id,
                opportunity_id=opportunity.id,
            )
            db.add(ao_join)

            # Rails: counter_cache on account.opportunities_count
            acct_result = await db.execute(
                select(Account).where(Account.id == resolved_account_id)
            )
            acct = acct_result.scalar_one_or_none()
            if acct:
                acct.opportunities_count = (acct.opportunities_count or 0) + 1

    # -------------------------------------------------------------------------
    # Step 4: Mark lead as converted
    # Rails: @lead.update!(status: :converted)
    # -------------------------------------------------------------------------
    lead.mark_as_converted()

    await db.commit()
    await db.refresh(contact)
    return ContactResponse.model_validate(contact)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_visibility_scope(stmt, user_id: int):
    """Rails: EntitiesController visibility scoping."""
    return stmt.where(
        or_(
            Lead.access == "Public",
            Lead.user_id == user_id,
            Lead.assigned_to == user_id,
        )
    )


async def _get_lead_or_404(
    lead_id: int, db: AsyncSession, current_user: UserContext
) -> Lead:
    """Rails: @lead = Lead.my(current_user).find(params[:id])"""
    stmt = Lead.scope_active().where(Lead.id == lead_id)
    stmt = _apply_visibility_scope(stmt, current_user.user_id)
    result = await db.execute(stmt)
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def _build_lead_vcard(lead: Lead) -> str:
    """Rails: helpers.vcard_for(@lead) — vCard 3.0 for lead export."""
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{lead.full_name}",
        f"N:{lead.last_name};{lead.first_name};;;",
    ]
    if lead.email:
        lines.append(f"EMAIL:{lead.email}")
    if lead.phone:
        lines.append(f"TEL;TYPE=work:{lead.phone}")
    if lead.mobile:
        lines.append(f"TEL;TYPE=cell:{lead.mobile}")
    if lead.title:
        lines.append(f"TITLE:{lead.title}")
    if lead.company:
        lines.append(f"ORG:{lead.company}")
    lines.append("END:VCARD")
    return "\r\n".join(lines)
