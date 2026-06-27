# routers/opportunities.py
# ---------------------------------------------------------------------------
# FastAPI router for Opportunity CRUD.
# Rails source: app/controllers/entities/opportunities_controller.rb
#
# Rails → FastAPI endpoint mapping:
#   GET    /opportunities              → index  (paginated + stage/state filter)
#   GET    /opportunities/dashboard    → visible_on_dashboard scope
#   GET    /opportunities/:id          → show
#   POST   /opportunities              → create (with :related param for contact/account)
#   PATCH  /opportunities/:id          → update
#   DELETE /opportunities/:id          → destroy (soft-delete)
#   PUT    /opportunities/:id/restore  → restore
#
# Rails patterns preserved:
#   before_action :load_settings       → stage options available via /settings/stages
#   before_action :set_params          → filter params from session/request
#   before_action :get_data_for_sidebar → sidebar data
#   scope :state (stage IN or NULL)    → state= param
#   scope :won / :lost / :pipeline     → stage= filter shortcuts
#   scope :visible_on_dashboard        → dashboard endpoint
#   scope :weighted_sort               → weighted_amount in response
#   scope :text_search (name LIKE|id=) → q= param, numeric ID match
#   params[:related] = 'contact_X'    → contact_id param
#   @account = Account.new / related.account → account linking
# ---------------------------------------------------------------------------

import math
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.account import Account, AccountOpportunity
from models.contact import Contact, ContactOpportunity
from models.opportunity import Opportunity, OpportunityStage
from schemas.opportunity import (
    OpportunityCreate,
    OpportunityDashboard,
    OpportunityList,
    OpportunityResponse,
    OpportunityUpdate,
)
from .dependencies import get_current_user, UserContext

router = APIRouter(prefix="/opportunities", tags=["opportunities"])

DEFAULT_PER_PAGE = 20


# ---------------------------------------------------------------------------
# GET /opportunities/dashboard
# Rails: scope :visible_on_dashboard (must be before /:id route)
# ---------------------------------------------------------------------------
@router.get("/dashboard", response_model=OpportunityDashboard)
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: @opportunities = Opportunity.visible_on_dashboard(current_user)
    Returns pipeline opportunities for the current user's dashboard.
    Also computes total pipeline value and weighted pipeline value.
    """
    stmt = Opportunity.scope_visible_on_dashboard(current_user.user_id)
    result = await db.execute(stmt)
    all_opps = result.scalars().unique().all()

    pipeline = [o for o in all_opps if o.is_pipeline]
    responses = [OpportunityResponse.model_validate(o) for o in pipeline]

    total_pipeline = sum(
        (o.amount or Decimal(0)) for o in pipeline
    )
    total_weighted = sum(
        (o.weighted_amount or Decimal(0)) for o in pipeline
    )

    return OpportunityDashboard(
        pipeline=responses,
        total_pipeline_value=total_pipeline,
        total_weighted_value=total_weighted,
    )


# ---------------------------------------------------------------------------
# GET /opportunities
# Rails: OpportunitiesController#index
# ---------------------------------------------------------------------------
@router.get("", response_model=OpportunityList)
async def list_opportunities(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=DEFAULT_PER_PAGE, ge=1, le=100),
    q: Optional[str] = Query(default=None, description="Text search (name or numeric ID)"),
    # Rails: scope :state → stage filters; 'other' = NULL stage
    state: Optional[List[str]] = Query(
        default=None,
        description="Stage filter values; 'other' = NULL stage (Rails: scope :state)"
    ),
    stage: Optional[str] = Query(
        default=None,
        description="Shortcut: won | lost | pipeline (Rails: scope :won/:lost/:pipeline)"
    ),
    assigned_to: Optional[int] = Query(default=None),
    created_by: Optional[int] = Query(default=None),
    account_id: Optional[int] = Query(default=None),
    contact_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: OpportunitiesController#index
    Rails: @opportunities = get_opportunities(page: page_param, per_page: per_page_param)
    Supports all Rails scopes: :state, :won, :lost, :pipeline, :text_search.
    """
    stmt = Opportunity.scope_active()

    # Rails: scope :text_search (LIKE name OR id=N)
    if q:
        stmt = Opportunity.scope_text_search(q)

    # Rails: scope :state (stage IN or NULL)
    if state:
        include_null = "other" in state
        stage_filters = [s for s in state if s != "other"]
        if stage_filters or include_null:
            stmt = Opportunity.scope_state(stage_filters, include_null=include_null)

    # Rails: shortcut scopes — applied on top of base
    if stage == "won":
        stmt = Opportunity.scope_won()
    elif stage == "lost":
        stmt = Opportunity.scope_lost()
    elif stage == "pipeline":
        stmt = Opportunity.scope_pipeline()

    if assigned_to:
        stmt = stmt.where(Opportunity.assigned_to == assigned_to)
    if created_by:
        stmt = stmt.where(Opportunity.user_id == created_by)

    # Filter by account (via join table)
    if account_id:
        stmt = stmt.join(
            AccountOpportunity,
            (AccountOpportunity.opportunity_id == Opportunity.id)
            & AccountOpportunity.deleted_at.is_(None),
        ).where(AccountOpportunity.account_id == account_id)

    # Filter by contact (via join table)
    if contact_id:
        stmt = stmt.join(
            ContactOpportunity,
            (ContactOpportunity.opportunity_id == Opportunity.id)
            & ContactOpportunity.deleted_at.is_(None),
        ).where(ContactOpportunity.contact_id == contact_id)

    stmt = _apply_visibility_scope(stmt, current_user.user_id)

    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Paginate (Rails: order by id DESC by default for opportunities)
    offset = (page - 1) * per_page
    stmt = stmt.order_by(Opportunity.id.desc()).offset(offset).limit(per_page)

    result = await db.execute(stmt)
    opportunities = result.scalars().unique().all()

    return OpportunityList(
        items=[OpportunityResponse.model_validate(o) for o in opportunities],
        total=total,
        page=page,
        per_page=per_page,
        pages=math.ceil(total / per_page) if total else 0,
    )


# ---------------------------------------------------------------------------
# GET /opportunities/:id
# Rails: OpportunitiesController#show
# ---------------------------------------------------------------------------
@router.get("/{opportunity_id}", response_model=OpportunityResponse)
async def get_opportunity(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: OpportunitiesController#show
    Rails: @comment = Comment.new; @timeline = timeline(@opportunity)
    """
    opp = await _get_opportunity_or_404(opportunity_id, db, current_user)
    return OpportunityResponse.model_validate(opp)


# ---------------------------------------------------------------------------
# POST /opportunities
# Rails: OpportunitiesController#create
# ---------------------------------------------------------------------------
@router.post(
    "", response_model=OpportunityResponse, status_code=status.HTTP_201_CREATED
)
async def create_opportunity(
    payload: OpportunityCreate,
    # Rails: params[:related] = 'contact_X' / 'account_X'
    # We expose as explicit query params for API clarity
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: OpportunitiesController#create
    Rails: @opportunity.attributes = {
      user: current_user,
      stage: Opportunity.default_stage,
      access: Setting.default_access
    }
    Rails: params[:related] → pre-links to a contact or account.

    Handles:
      - Auto-sets user_id from JWT
      - Sets default stage if not provided
      - Links to account via AccountOpportunity join
      - Links to contact via ContactOpportunity join
      - Updates account.opportunities_count counter cache
    """
    account_id = payload.account_id
    contact_id = payload.contact_id

    data = payload.model_dump(
        exclude={"user_id", "account_id", "contact_id"}
    )
    # Rails: default_stage = Setting.opportunity_stage.first
    if not data.get("stage"):
        data["stage"] = OpportunityStage.PROSPECTING

    opportunity = Opportunity(**data, user_id=current_user.user_id)
    db.add(opportunity)
    await db.flush()

    # Rails: has_one :account_opportunity → create join on save
    if account_id:
        ao_join = AccountOpportunity(
            account_id=account_id, opportunity_id=opportunity.id
        )
        db.add(ao_join)
        # Rails: counter_cache on account.opportunities_count
        acct = await db.execute(select(Account).where(Account.id == account_id))
        acct = acct.scalar_one_or_none()
        if acct:
            acct.opportunities_count = (acct.opportunities_count or 0) + 1

    # Rails: params[:related] = 'contact_ID' → links contact
    if contact_id:
        co_join = ContactOpportunity(
            contact_id=contact_id, opportunity_id=opportunity.id
        )
        db.add(co_join)

    await db.commit()
    await db.refresh(opportunity)
    return OpportunityResponse.model_validate(opportunity)


# ---------------------------------------------------------------------------
# PATCH /opportunities/:id
# Rails: OpportunitiesController#update
# ---------------------------------------------------------------------------
@router.patch("/{opportunity_id}", response_model=OpportunityResponse)
async def update_opportunity(
    opportunity_id: int,
    payload: OpportunityUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: OpportunitiesController#update
    Rails: OpportunityObserver#before_update — stage change triggers notification.
    The SQLAlchemy event hook handles this (models/opportunity.py).
    """
    opp = await _get_opportunity_or_404(opportunity_id, db, current_user)

    new_account_id = payload.account_id
    new_contact_id = payload.contact_id
    update_data = payload.model_dump(
        exclude_none=True, exclude={"account_id", "contact_id"}
    )

    for field, value in update_data.items():
        setattr(opp, field, value)

    # Handle account change (update join table)
    if new_account_id is not None:
        await _update_opportunity_account(opp.id, new_account_id, db)

    # Handle contact link change
    if new_contact_id is not None:
        await _link_opportunity_contact(opp.id, new_contact_id, db)

    await db.commit()
    await db.refresh(opp)
    return OpportunityResponse.model_validate(opp)


# ---------------------------------------------------------------------------
# DELETE /opportunities/:id (soft-delete)
# Rails: OpportunitiesController#destroy
# ---------------------------------------------------------------------------
@router.delete("/{opportunity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_opportunity(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: OpportunitiesController#destroy (acts_as_paranoid → deleted_at).
    Rails: dependent: :destroy on account_opportunity and contact_opportunities
           handled via cascade delete in the join tables.
    Also updates account.opportunities_count.
    """
    opp = await _get_opportunity_or_404(opportunity_id, db, current_user)

    # Decrement account counter_cache before soft-delete
    if opp.account_opportunity and opp.account_opportunity.account_id:
        acct = await db.execute(
            select(Account).where(Account.id == opp.account_opportunity.account_id)
        )
        acct = acct.scalar_one_or_none()
        if acct and acct.opportunities_count > 0:
            acct.opportunities_count -= 1

    opp.soft_delete()
    await db.commit()


# ---------------------------------------------------------------------------
# PUT /opportunities/:id/restore
# ---------------------------------------------------------------------------
@router.put("/{opportunity_id}/restore", response_model=OpportunityResponse)
async def restore_opportunity(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """Rails: opportunity.restore! (acts_as_paranoid)"""
    result = await db.execute(
        select(Opportunity).where(Opportunity.id == opportunity_id)
    )
    opp = result.scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    opp.restore()
    await db.commit()
    await db.refresh(opp)
    return OpportunityResponse.model_validate(opp)


# ---------------------------------------------------------------------------
# GET /settings/stages (helper for UI dropdowns)
# Rails: before_action :load_settings → @stage = Setting.unroll(:opportunity_stage)
# ---------------------------------------------------------------------------
@router.get("/settings/stages")
async def get_opportunity_stages():
    """
    Rails: before_action :load_settings in OpportunitiesController.
    Returns available stage values for dropdowns.
    """
    return {"stages": [s.value for s in OpportunityStage]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_visibility_scope(stmt, user_id: int):
    """Rails: EntitiesController visibility scoping."""
    return stmt.where(
        or_(
            Opportunity.access == "Public",
            Opportunity.user_id == user_id,
            Opportunity.assigned_to == user_id,
        )
    )


async def _get_opportunity_or_404(
    opportunity_id: int, db: AsyncSession, current_user: UserContext
) -> Opportunity:
    """Rails: @opportunity = Opportunity.my(current_user).find(params[:id])"""
    stmt = (
        Opportunity.scope_active()
        .where(Opportunity.id == opportunity_id)
    )
    stmt = _apply_visibility_scope(stmt, current_user.user_id)
    result = await db.execute(stmt)
    opp = result.scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return opp


async def _update_opportunity_account(
    opportunity_id: int, new_account_id: int, db: AsyncSession
):
    """
    Rails: opportunity.account = account
    Replace the AccountOpportunity join record.
    """
    from datetime import datetime

    old_result = await db.execute(
        select(AccountOpportunity).where(
            AccountOpportunity.opportunity_id == opportunity_id,
            AccountOpportunity.deleted_at.is_(None),
        )
    )
    old_join = old_result.scalar_one_or_none()

    if old_join:
        if old_join.account_id == new_account_id:
            return
        old_join.deleted_at = datetime.utcnow()
        # Decrement old account
        old_acct = await db.execute(select(Account).where(Account.id == old_join.account_id))
        old_acct = old_acct.scalar_one_or_none()
        if old_acct and old_acct.opportunities_count > 0:
            old_acct.opportunities_count -= 1

    new_join = AccountOpportunity(
        account_id=new_account_id, opportunity_id=opportunity_id
    )
    db.add(new_join)

    # Increment new account
    new_acct = await db.execute(select(Account).where(Account.id == new_account_id))
    new_acct = new_acct.scalar_one_or_none()
    if new_acct:
        new_acct.opportunities_count = (new_acct.opportunities_count or 0) + 1


async def _link_opportunity_contact(
    opportunity_id: int, contact_id: int, db: AsyncSession
):
    """Rails: opportunity.contacts << contact (shovel operator)"""
    existing = await db.execute(
        select(ContactOpportunity).where(
            ContactOpportunity.opportunity_id == opportunity_id,
            ContactOpportunity.contact_id == contact_id,
            ContactOpportunity.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none():
        return  # Already linked

    new_join = ContactOpportunity(
        contact_id=contact_id, opportunity_id=opportunity_id
    )
    db.add(new_join)
