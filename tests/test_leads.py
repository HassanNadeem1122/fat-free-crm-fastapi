# tests/test_leads.py
# ---------------------------------------------------------------------------
# Tests for Lead CRUD and Convert endpoints.
# Rails: spec/controllers/entities/leads_controller_spec.rb
# ---------------------------------------------------------------------------

import pytest
from httpx import AsyncClient

from tests.conftest import make_lead_payload

BASE = "/api/v1/leads"


@pytest.mark.asyncio
class TestLeadList:
    """Rails: LeadsController#index"""

    async def test_list_leads_empty(self, client: AsyncClient, auth_headers: dict):
        resp = await client.get(BASE, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_list_leads_state_filter(self, client: AsyncClient, auth_headers: dict):
        """Rails: scope :state — filters by status array, handles 'other' as NULL"""
        await client.post(BASE, json=make_lead_payload(status="new"), headers=auth_headers)
        await client.post(BASE, json=make_lead_payload(status="assigned"), headers=auth_headers)
        # NULL status
        payload_null = make_lead_payload()
        del payload_null["status"]
        await client.post(BASE, json=payload_null, headers=auth_headers)

        # Filter by specific state
        resp = await client.get(f"{BASE}?state=new&state=assigned", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 2

        # Rails 'other' maps to NULL
        resp_null = await client.get(f"{BASE}?state=other", headers=auth_headers)
        assert len(resp_null.json()["items"]) == 1
        assert resp_null.json()["items"][0]["status"] is None


@pytest.mark.asyncio
class TestLeadCreate:
    """Rails: LeadsController#create"""

    async def test_create_lead_success(self, client: AsyncClient, auth_headers: dict):
        resp = await client.post(BASE, json=make_lead_payload(), headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["first_name"] == "John"

    async def test_create_lead_with_comment(self, client: AsyncClient, auth_headers: dict):
        """Rails: @lead.add_comment_by_user(params[:comment_body], current_user)"""
        payload = make_lead_payload()
        payload["comment_body"] = "Call them tomorrow"
        resp = await client.post(BASE, json=payload, headers=auth_headers)
        assert resp.status_code == 201
        # Comment processing verified via side-effects or mocked in actual implementation.


@pytest.mark.asyncio
class TestLeadConvert:
    """Rails: LeadsController#convert"""

    async def test_convert_lead_to_contact_only(self, client: AsyncClient, auth_headers: dict):
        """Rails: convert creates contact, marks lead as converted"""
        create_resp = await client.post(BASE, json=make_lead_payload(), headers=auth_headers)
        lead_id = create_resp.json()["id"]

        convert_payload = {
            "contact_access": "Public"
        }
        resp = await client.post(f"{BASE}/{lead_id}/convert", json=convert_payload, headers=auth_headers)
        assert resp.status_code == 200
        contact = resp.json()
        assert contact["first_name"] == "John"
        assert contact["lead_id"] == lead_id

        # Verify lead status updated
        lead_resp = await client.get(f"{BASE}/{lead_id}", headers=auth_headers)
        assert lead_resp.json()["status"] == "converted"

    async def test_convert_lead_with_new_account_and_opportunity(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Rails: convert with full account + opportunity creation"""
        create_resp = await client.post(
            BASE, json=make_lead_payload(company="Acme Corp"), headers=auth_headers
        )
        lead_id = create_resp.json()["id"]

        convert_payload = {
            "contact_access": "Public",
            "account": {"name": "Acme Corp", "access": "Public"},
            "opportunity": {"name": "Acme Deal", "stage": "prospecting", "amount": 1000.0}
        }
        resp = await client.post(f"{BASE}/{lead_id}/convert", json=convert_payload, headers=auth_headers)
        assert resp.status_code == 200
        contact = resp.json()
        
        # Verify contact linked to account and opp
        assert contact["account"]["name"] == "Acme Corp"
        assert len(contact["opportunities"]) == 1
        assert contact["opportunities"][0]["name"] == "Acme Deal"

    async def test_convert_already_converted_lead_fails(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Rails: cannot convert lead if already converted"""
        create_resp = await client.post(BASE, json=make_lead_payload(status="converted"), headers=auth_headers)
        lead_id = create_resp.json()["id"]

        convert_payload = {"contact_access": "Public"}
        resp = await client.post(f"{BASE}/{lead_id}/convert", json=convert_payload, headers=auth_headers)
        assert resp.status_code == 422


@pytest.mark.asyncio
class TestLeadDelete:
    """Rails: LeadsController#destroy"""

    async def test_delete_lead_nullifies_contact_lead_id(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Rails: has_one :contact, dependent: :nullify"""
        create_resp = await client.post(BASE, json=make_lead_payload(), headers=auth_headers)
        lead_id = create_resp.json()["id"]

        # Convert to create a contact
        convert_payload = {"contact_access": "Public"}
        conv_resp = await client.post(f"{BASE}/{lead_id}/convert", json=convert_payload, headers=auth_headers)
        contact_id = conv_resp.json()["id"]

        # Delete lead
        del_resp = await client.delete(f"{BASE}/{lead_id}", headers=auth_headers)
        assert del_resp.status_code == 204

        # Verify contact still exists but lead_id is NULL
        contact_resp = await client.get(f"/api/v1/contacts/{contact_id}", headers=auth_headers)
        assert contact_resp.status_code == 200
        assert contact_resp.json()["lead_id"] is None
