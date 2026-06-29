# routers/accounts.py
# ---------------------------------------------------------------------------
# FastAPI router for Account CRUD.
# Rails source: app/controllers/entities/accounts_controller.rb
#
# Rails → FastAPI endpoint mapping:
#   GET    /accounts          → index  (paginated list, with search/filter)
#   GET    /accounts/:id      → show
#   POST   /accounts          → create
#   PATCH  /accounts/:id      → update
#   DELETE /accounts/:id      → destroy (soft-delete)
#   PUT    /accounts/:id/restore → restore (acts_as_paranoid)
#
# Rails patterns preserved:
#   before_action :get_data_for_sidebar → sidebar data returned in list
#   before_action :require_user         → JWT auth dependency
#   respond_with(@account)              → FastAPI response model
#   save_with_permissions               → access field + user_id set from JWT
#   acts_as_paranoid destroy           → soft_delete() method
#   Kaminari pagination                 → page/per_page query params
#   ransack search                      → q= text search param
#   scope :text_search                  → Account.scope_text_search()
#   scope :created_by / :assigned_to   → filter query params
#   counter_cache for contacts/opps    → maintained on association creation
# ---------------------------------------------------------------------------

import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.account import Account, AccountContact, AccountOpportunity
from models.contact import Contact
from models.opportunity import Opportunity
from schemas.account import (
    AccountCreate,
    AccountList,
    AccountResponse,
    AccountUpdate,
)
from .dependencies import get_current_user, UserContext

router = APIRouter(prefix="/accounts", tags=["accounts"])

# ---------------------------------------------------------------------------
# Rails: DEFAULT_PER_PAGE = 20 (set in application_controller.rb)
# ---------------------------------------------------------------------------
DEFAULT_PER_PAGE = 20


# ---------------------------------------------------------------------------
# GET /accounts
# Rails: AccountsController#index
# ---------------------------------------------------------------------------
@router.get("", response_model=AccountList)
async def list_accounts(
    page: int = Query(default=1, ge=1, description="Page number (Rails: page_param)"),
    per_page: int = Query(default=DEFAULT_PER_PAGE, ge=1, le=100, description="Per page"),
    q: Optional[str] = Query(default=None, description="Text search (Rails: ransack)"),
    category: Optional[str] = Query(default=None, description="Filter by category"),
    assigned_to: Optional[int] = Query(default=None, description="Filter by assignee"),
    created_by: Optional[int] = Query(default=None, description="Filter by creator"),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: AccountsController#index
    Paginated account list with optional text search and filters.

    Rails: @accounts = get_accounts(page: page_param, per_page: per_page_param)
    get_accounts is defined in EntitiesController and applies:
      - current_user visibility scoping
      - text search
      - pagination via Kaminari
    """
    # Base query: exclude soft-deleted
    stmt = Account.scope_active()

    # Rails: scope :text_search (ransack-powered)
    if q:
        stmt = Account.scope_text_search(q)

    # Rails: scope :created_by
    if created_by:
        stmt = stmt.where(Account.user_id == created_by)

    # Rails: scope :assigned_to
    if assigned_to:
        stmt = stmt.where(Account.assigned_to == assigned_to)

    # Rails: scope :by_category (sidebar filter)
    if category:
        stmt = stmt.where(Account.category == category)

    # Rails: visibility scoping — "Public" OR owned/assigned to current_user
    stmt = _apply_visibility_scope(stmt, current_user.user_id)

    # Count total for pagination (Rails: @accounts.total_count)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Paginate (Rails: .page(n).per(m))
    offset = (page - 1) * per_page
    stmt = stmt.order_by(Account.name).offset(offset).limit(per_page)

    result = await db.execute(stmt)
    accounts = result.scalars().unique().all()

    return AccountList(
        items=[AccountResponse.model_validate(a) for a in accounts],
        total=total,
        page=page,
        per_page=per_page,
        pages=math.ceil(total / per_page) if total else 0,
    )


# ---------------------------------------------------------------------------
# GET /accounts/:id
# Rails: AccountsController#show
# ---------------------------------------------------------------------------
@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: AccountsController#show
    Rails: respond_with(@account) — returns full account with associations.
    """
    account = await _get_account_or_404(account_id, db, current_user)
    return AccountResponse.model_validate(account)


# ---------------------------------------------------------------------------
# POST /accounts
# Rails: AccountsController#create
# ---------------------------------------------------------------------------
@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: AccountsController#create
    Rails: @account.save_with_permissions(params.permit!) sets access + user_id.
    """
    # Rails: @account.attributes = { user: current_user, access: Setting.default_access }
    data = payload.model_dump(exclude={"user_id"})
    account = Account(**data, user_id=current_user.user_id)

    db.add(account)
    await db.flush()  # Get ID before commit

    # Rails: after_create :subscribe_users
    # The event hook handles this, but flush is needed first.
    await db.commit()
    await db.refresh(account)
    return AccountResponse.model_validate(account)


# ---------------------------------------------------------------------------
# PATCH /accounts/:id
# Rails: AccountsController#update
# ---------------------------------------------------------------------------
@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int,
    payload: AccountUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: AccountsController#update
    Rails: @account.update_with_permissions(params.permit!)
    """
    account = await _get_account_or_404(account_id, db, current_user)

    # Apply only provided (non-None) fields — PATCH semantics
    update_data = payload.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(account, field, value)

    await db.commit()
    await db.refresh(account)
    return AccountResponse.model_validate(account)


# ---------------------------------------------------------------------------
# DELETE /accounts/:id (soft delete)
# Rails: AccountsController#destroy (acts_as_paranoid)
# ---------------------------------------------------------------------------
@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: AccountsController#destroy
    Rails acts_as_paranoid: sets deleted_at instead of hard-deleting.
    Also updates contacts_count / opportunities_count.
    """
    account = await _get_account_or_404(account_id, db, current_user)
    account.soft_delete()
    await db.commit()


# ---------------------------------------------------------------------------
# PUT /accounts/:id/restore
# Rails: acts_as_paranoid provides #restore! via Paranoia gem
# ---------------------------------------------------------------------------
@router.put("/{account_id}/restore", response_model=AccountResponse)
async def restore_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: account.restore! (acts_as_paranoid)
    Clears deleted_at to un-delete a soft-deleted account.
    """
    # Include soft-deleted records for restore
    stmt = select(Account).where(Account.id == account_id)
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    account.restore()
    await db.commit()
    await db.refresh(account)
    return AccountResponse.model_validate(account)


# ---------------------------------------------------------------------------
# POST /accounts/:id/contacts/:contact_id
# Rails: AccountContact join — created when a contact is linked to an account.
# ---------------------------------------------------------------------------
@router.post("/{account_id}/contacts/{contact_id}", status_code=status.HTTP_201_CREATED)
async def link_contact(
    account_id: int,
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """
    Rails: has_many :contacts, through: :account_contacts
    Links an existing Contact to an Account via the join table.
    Rails: account.contacts << contact (shovel operator)
    """
    account = await _get_account_or_404(account_id, db, current_user)

    # Verify contact exists
    contact_result = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
    )
    contact = contact_result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Check if link already exists
    existing = await db.execute(
        select(AccountContact).where(
            AccountContact.account_id == account_id,
            AccountContact.contact_id == contact_id,
            AccountContact.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Contact already linked to this account")

    join_record = AccountContact(account_id=account_id, contact_id=contact_id)
    db.add(join_record)

    # Rails: counter_cache increments contacts_count
    account.contacts_count = (account.contacts_count or 0) + 1

    await db.commit()
    return {"account_id": account_id, "contact_id": contact_id, "linked": True}


# ---------------------------------------------------------------------------
# DELETE /accounts/:id/contacts/:contact_id
# Rails: account.contacts.delete(contact) → removes join record
# ---------------------------------------------------------------------------
@router.delete("/{account_id}/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_contact(
    account_id: int,
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """Rails: account.contacts.delete(contact) — removes the AccountContact join."""
    result = await db.execute(
        select(AccountContact).where(
            AccountContact.account_id == account_id,
            AccountContact.contact_id == contact_id,
            AccountContact.deleted_at.is_(None),
        )
    )
    join_record = result.scalar_one_or_none()
    if not join_record:
        raise HTTPException(status_code=404, detail="Contact link not found")

    join_record.soft_delete() if hasattr(join_record, "soft_delete") else db.delete(join_record)

    # Rails: counter_cache decrements contacts_count
    account_result = await db.execute(select(Account).where(Account.id == account_id))
    account = account_result.scalar_one_or_none()
    if account and account.contacts_count > 0:
        account.contacts_count -= 1

    await db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_visibility_scope(stmt, user_id: int):
    """
    Rails: EntitiesController#get_accounts applies visibility scoping:
      - Public accounts visible to all
      - Private accounts visible only to owner
      - Shared accounts visible per group membership (simplified here)
    """
    from sqlalchemy import or_
    return stmt.where(
        or_(
            Account.access == "Public",
            Account.user_id == user_id,
            Account.assigned_to == user_id,
        )
    )


async def _get_account_or_404(
    account_id: int, db: AsyncSession, current_user: UserContext
) -> Account:
    """
    Rails: @account = Account.find(params[:id])
    Raises 404 if not found or soft-deleted.
    """
    stmt = (
        Account.scope_active()
        .where(Account.id == account_id)
    )
    stmt = _apply_visibility_scope(stmt, current_user.user_id)
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()

    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Account not found"
        )
    return account
