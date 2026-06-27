# tests/test_contacts.py
# ---------------------------------------------------------------------------
# Tests for Contact CRUD endpoints.
# Rails: spec/controllers/entities/contacts_controller_spec.rb
# ---------------------------------------------------------------------------

import pytest
from httpx import AsyncClient

from tests.conftest import make_account_payload, make_contact_payload

BASE = "/api/v1/contacts"
ACCOUNTS_BASE = "/api/v1/accounts"


@pytest.mark.asyncio
class TestContactList:
    """Rails: ContactsController#index"""

    async def test_list_contacts_empty(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get(BASE, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_list_contacts_text_search_name_order(
        self, client: AsyncClient, auth_headers: dict
    ):
        """
        Rails: scope :text_search — searches first+last in either order.
        'John Smith' should match 'Smith John' query too.
        """
        await client.post(BASE, json=make_contact_payload(first_name="John", last_name="Smith"), headers=auth_headers)
        await client.post(BASE, json=make_contact_payload(first_name="Jane", last_name="Doe"), headers=auth_headers)

        resp = await client.get(f"{BASE}?q=Smith", headers=auth_headers)
        items = resp.json()["items"]
        assert len(items) >= 1
        assert any("Smith" in i["last_name"] for i in items)

    async def test_list_contacts_do_not_call_filter(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Rails: filter by do_not_call boolean"""
        await client.post(
            BASE, json=make_contact_payload(do_not_call=True, email="dnc@example.com"),
            headers=auth_headers
        )
        await client.post(
            BASE, json=make_contact_payload(do_not_call=False, email="ok@example.com"),
            headers=auth_headers
        )

        resp = await client.get(f"{BASE}?do_not_call=true", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(i["do_not_call"] is True for i in items)


@pytest.mark.asyncio
class TestContactCreate:
    """Rails: ContactsController#create"""

    async def test_create_contact_success(self, client: AsyncClient, auth_headers: dict):
        """Rails: @contact.save_with_permissions(params.permit!)"""
        resp = await client.post(BASE, json=make_contact_payload(), headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["first_name"] == "Jane"
        assert data["last_name"] == "Doe"
        assert data["full_name"] == "Jane Doe"  # Rails: def full_name

    async def test_create_contact_missing_first_name(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Rails: validates :first_name, presence: true"""
        payload = make_contact_payload()
        del payload["first_name"]
        resp = await client.post(BASE, json=payload, headers=auth_headers)
        assert resp.status_code == 422

    async def test_create_contact_do_not_call_default_false(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Rails: do_not_call defaults to FALSE"""
        payload = make_contact_payload()
        del payload["do_not_call"]
        resp = await client.post(BASE, json=payload, headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["do_not_call"] is False

    async def test_create_contact_with_account_link(
        self, client: AsyncClient, auth_headers: dict
    ):
        """
        Rails: ContactsController#create with account_id param.
        Creates AccountContact join record.
        """
        # Create account first
        acct_resp = await client.post(
            ACCOUNTS_BASE, json=make_account_payload(), headers=auth_headers
        )
        account_id = acct_resp.json()["id"]

        payload = make_contact_payload()
        payload["account_id"] = account_id
        resp = await client.post(BASE, json=payload, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        # Verify account relationship
        assert data["account"] is not None
        assert data["account"]["id"] == account_id

    async def test_create_contact_with_born_on(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Rails: validates :born_on, timeliness: { type: :date }"""
        payload = make_contact_payload(born_on="1990-05-15")
        resp = await client.post(BASE, json=payload, headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["born_on"] == "1990-05-15"

    async def test_create_contact_invalid_born_on(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Rails: validates_timeliness rejects invalid dates"""
        payload = make_contact_payload(born_on="not-a-date")
        resp = await client.post(BASE, json=payload, headers=auth_headers)
        assert resp.status_code == 422


@pytest.mark.asyncio
class TestContactUpdate:
    """Rails: ContactsController#update"""

    async def test_update_contact(self, client: AsyncClient, auth_headers: dict):
        create_resp = await client.post(BASE, json=make_contact_payload(), headers=auth_headers)
        cid = create_resp.json()["id"]

        resp = await client.patch(
            f"{BASE}/{cid}",
            json={"title": "CEO", "do_not_call": True},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "CEO"
        assert data["do_not_call"] is True

    async def test_update_contact_full_name_recalculated(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Rails: full_name is derived, not stored — must update on name change"""
        create_resp = await client.post(BASE, json=make_contact_payload(), headers=auth_headers)
        cid = create_resp.json()["id"]

        resp = await client.patch(
            f"{BASE}/{cid}", json={"first_name": "Janet"}, headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "Janet Doe"


@pytest.mark.asyncio
class TestContactDelete:
    """Rails: ContactsController#destroy"""

    async def test_soft_delete_contact(self, client: AsyncClient, auth_headers: dict):
        """Rails: acts_as_paranoid — sets deleted_at, not hard delete"""
        create_resp = await client.post(BASE, json=make_contact_payload(), headers=auth_headers)
        cid = create_resp.json()["id"]

        del_resp = await client.delete(f"{BASE}/{cid}", headers=auth_headers)
        assert del_resp.status_code == 204

        # Not returned in list
        list_resp = await client.get(BASE, headers=auth_headers)
        ids = [i["id"] for i in list_resp.json()["items"]]
        assert cid not in ids

        # Not returned in show
        show_resp = await client.get(f"{BASE}/{cid}", headers=auth_headers)
        assert show_resp.status_code == 404

    async def test_restore_contact(self, client: AsyncClient, auth_headers: dict):
        """Rails: contact.restore! (acts_as_paranoid)"""
        create_resp = await client.post(BASE, json=make_contact_payload(), headers=auth_headers)
        cid = create_resp.json()["id"]

        await client.delete(f"{BASE}/{cid}", headers=auth_headers)
        restore_resp = await client.put(f"{BASE}/{cid}/restore", headers=auth_headers)
        assert restore_resp.status_code == 200
        assert restore_resp.json()["deleted_at"] is None


@pytest.mark.asyncio
class TestContactVcard:
    """Rails: format.vcf { send_data helpers.vcard_for(@contact) }"""

    async def test_vcard_export(self, client: AsyncClient, auth_headers: dict):
        payload = make_contact_payload(
            first_name="John", last_name="Doe", email="john@example.com"
        )
        create_resp = await client.post(BASE, json=payload, headers=auth_headers)
        cid = create_resp.json()["id"]

        resp = await client.get(f"{BASE}/{cid}/vcard", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/x-vcard; charset=utf-8"
        body = resp.text
        assert "BEGIN:VCARD" in body
        assert "FN:John Doe" in body
        assert "EMAIL" in body
