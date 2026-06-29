# tests/test_accounts.py
# ---------------------------------------------------------------------------
# Tests for Account CRUD endpoints.
# Rails equivalent: spec/controllers/entities/accounts_controller_spec.rb
# ---------------------------------------------------------------------------

import pytest
from httpx import AsyncClient

from tests.conftest import make_account_payload

BASE = "/api/v1/accounts"


@pytest.mark.asyncio
class TestAccountList:
    """Rails: GET /accounts (index action)"""

    async def test_list_accounts_unauthenticated(self, client: AsyncClient):
        """Rails: before_action :require_user — redirect to sign in if not auth'd"""
        resp = await client.get(BASE)
        assert resp.status_code == 401

    async def test_list_accounts_empty(self, client: AsyncClient, auth_headers: dict):
        """Rails: @accounts is empty when no records exist"""
        resp = await client.get(BASE, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    async def test_list_accounts_pagination(self, client: AsyncClient, auth_headers: dict):
        """Rails: Kaminari pagination — page/per_page params"""
        # Create 3 accounts
        for i in range(3):
            await client.post(BASE, json=make_account_payload(name=f"Corp {i}"), headers=auth_headers)

        resp = await client.get(f"{BASE}?page=1&per_page=2", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["per_page"] == 2
        assert data["pages"] >= 2

    async def test_list_accounts_text_search(self, client: AsyncClient, auth_headers: dict):
        """Rails: scope :text_search — ransack name search"""
        await client.post(BASE, json=make_account_payload(name="Acme Corp"), headers=auth_headers)
        await client.post(BASE, json=make_account_payload(name="Global Ltd"), headers=auth_headers)

        resp = await client.get(f"{BASE}?q=Acme", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "Acme Corp"

    async def test_list_accounts_category_filter(self, client: AsyncClient, auth_headers: dict):
        """Rails: scope :by_category (sidebar filter)"""
        await client.post(
            BASE, json=make_account_payload(name="Tech Corp", category="Technology"),
            headers=auth_headers
        )
        await client.post(
            BASE, json=make_account_payload(name="Finance Ltd", category="Finance"),
            headers=auth_headers
        )

        resp = await client.get(f"{BASE}?category=Technology", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(i["category"] == "Technology" for i in items)


@pytest.mark.asyncio
class TestAccountCreate:
    """Rails: POST /accounts (create action)"""

    async def test_create_account_success(self, client: AsyncClient, auth_headers: dict):
        """Rails: @account.save_with_permissions(params.permit!)"""
        payload = make_account_payload()
        resp = await client.post(BASE, json=payload, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == payload["name"]
        assert data["id"] is not None
        # Rails: user_id set from current_user
        assert data["user_id"] == 1  # from auth fixture

    async def test_create_account_missing_name(self, client: AsyncClient, auth_headers: dict):
        """Rails: validates :name, presence: true → 422"""
        resp = await client.post(BASE, json={"access": "Public"}, headers=auth_headers)
        assert resp.status_code == 422

    async def test_create_account_invalid_access(self, client: AsyncClient, auth_headers: dict):
        """Rails: validates :access, inclusion: %w[Public Private Shared]"""
        payload = make_account_payload(access="InvalidAccess")
        resp = await client.post(BASE, json=payload, headers=auth_headers)
        assert resp.status_code == 422

    async def test_create_account_invalid_rating(self, client: AsyncClient, auth_headers: dict):
        """Rails: validates :rating, numericality: { gte: 0 }"""
        payload = make_account_payload(rating=-1)
        resp = await client.post(BASE, json=payload, headers=auth_headers)
        assert resp.status_code == 422

    async def test_create_account_website_normalization(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Rails: validates :website, format: URI — we prepend https:// if missing"""
        payload = make_account_payload(website="example.com")
        resp = await client.post(BASE, json=payload, headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["website"] == "https://example.com"


@pytest.mark.asyncio
class TestAccountShow:
    """Rails: GET /accounts/:id (show action)"""

    async def test_show_account(self, client: AsyncClient, auth_headers: dict):
        """Rails: respond_with(@account)"""
        create_resp = await client.post(
            BASE, json=make_account_payload(), headers=auth_headers
        )
        account_id = create_resp.json()["id"]

        resp = await client.get(f"{BASE}/{account_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == account_id

    async def test_show_account_not_found(self, client: AsyncClient, auth_headers: dict):
        """Rails: rescue_from ActiveRecord::RecordNotFound"""
        resp = await client.get(f"{BASE}/99999", headers=auth_headers)
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestAccountUpdate:
    """Rails: PATCH /accounts/:id (update action)"""

    async def test_update_account_name(self, client: AsyncClient, auth_headers: dict):
        """Rails: @account.update_with_permissions(params.permit!)"""
        create_resp = await client.post(
            BASE, json=make_account_payload(name="Old Name"), headers=auth_headers
        )
        account_id = create_resp.json()["id"]

        resp = await client.patch(
            f"{BASE}/{account_id}", json={"name": "New Name"}, headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    async def test_update_account_partial(self, client: AsyncClient, auth_headers: dict):
        """Rails: PATCH — only provided fields updated, others preserved"""
        create_resp = await client.post(
            BASE, json=make_account_payload(phone="555-0100", rating=3), headers=auth_headers
        )
        account_id = create_resp.json()["id"]

        resp = await client.patch(
            f"{BASE}/{account_id}", json={"rating": 5}, headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rating"] == 5
        assert data["phone"] == "555-0100"  # Unchanged


@pytest.mark.asyncio
class TestAccountDelete:
    """Rails: DELETE /accounts/:id (destroy — acts_as_paranoid)"""

    async def test_soft_delete_account(self, client: AsyncClient, auth_headers: dict):
        """Rails: account.destroy (acts_as_paranoid) → sets deleted_at"""
        create_resp = await client.post(
            BASE, json=make_account_payload(), headers=auth_headers
        )
        account_id = create_resp.json()["id"]

        # Delete
        del_resp = await client.delete(f"{BASE}/{account_id}", headers=auth_headers)
        assert del_resp.status_code == 204

        # Verify soft-deleted record is not returned in list
        list_resp = await client.get(BASE, headers=auth_headers)
        ids = [i["id"] for i in list_resp.json()["items"]]
        assert account_id not in ids

    async def test_restore_account(self, client: AsyncClient, auth_headers: dict):
        """Rails: account.restore! (acts_as_paranoid)"""
        create_resp = await client.post(
            BASE, json=make_account_payload(), headers=auth_headers
        )
        account_id = create_resp.json()["id"]

        await client.delete(f"{BASE}/{account_id}", headers=auth_headers)
        restore_resp = await client.put(
            f"{BASE}/{account_id}/restore", headers=auth_headers
        )
        assert restore_resp.status_code == 200
        assert restore_resp.json()["deleted_at"] is None
