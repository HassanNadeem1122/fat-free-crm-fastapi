# tests/test_opportunities.py
# ---------------------------------------------------------------------------
# Tests for Opportunity CRUD and dashboard endpoints.
# Rails: spec/controllers/entities/opportunities_controller_spec.rb
# ---------------------------------------------------------------------------

import pytest
from httpx import AsyncClient

from tests.conftest import make_account_payload, make_contact_payload, make_opportunity_payload

BASE = "/api/v1/opportunities"


@pytest.mark.asyncio
class TestOpportunityList:
    """Rails: OpportunitiesController#index"""

    async def test_list_opportunities_stage_scopes(self, client: AsyncClient, auth_headers: dict):
        """Rails: scope :won, :lost, :pipeline"""
        await client.post(BASE, json=make_opportunity_payload(stage="won"), headers=auth_headers)
        await client.post(BASE, json=make_opportunity_payload(stage="lost"), headers=auth_headers)
        await client.post(BASE, json=make_opportunity_payload(stage="prospecting"), headers=auth_headers)

        # scope :won
        resp_won = await client.get(f"{BASE}?stage=won", headers=auth_headers)
        assert len(resp_won.json()["items"]) == 1
        assert resp_won.json()["items"][0]["stage"] == "won"

        # scope :pipeline
        resp_pipeline = await client.get(f"{BASE}?stage=pipeline", headers=auth_headers)
        assert len(resp_pipeline.json()["items"]) == 1
        assert resp_pipeline.json()["items"][0]["stage"] == "prospecting"

    async def test_list_opportunities_text_search_by_id(self, client: AsyncClient, auth_headers: dict):
        """Rails: scope :text_search numeric match OR name match"""
        resp_create = await client.post(BASE, json=make_opportunity_payload(name="Alpha Deal"), headers=auth_headers)
        opp_id = resp_create.json()["id"]

        resp = await client.get(f"{BASE}?q={opp_id}", headers=auth_headers)
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == opp_id


@pytest.mark.asyncio
class TestOpportunityDashboard:
    """Rails: scope :visible_on_dashboard"""

    async def test_dashboard_summary(self, client: AsyncClient, auth_headers: dict):
        await client.post(BASE, json=make_opportunity_payload(amount=1000, probability=50), headers=auth_headers)
        await client.post(BASE, json=make_opportunity_payload(amount=2000, probability=25), headers=auth_headers)

        resp = await client.get(f"{BASE}/dashboard", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["pipeline"]) == 2
        # 1000 + 2000
        assert data["total_pipeline_value"] == "3000.0"
        # 1000*0.5 + 2000*0.25 = 500 + 500
        assert data["total_weighted_value"] == "1000.0"


@pytest.mark.asyncio
class TestOpportunityCreate:
    """Rails: OpportunitiesController#create"""

    async def test_create_opportunity_computed_amounts(self, client: AsyncClient, auth_headers: dict):
        """Rails: weighted_amount and net_amount computation"""
        payload = make_opportunity_payload(amount=1000, discount=100, probability=20)
        resp = await client.post(BASE, json=payload, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        
        # amount - discount
        assert data["net_amount"] == "900.0"
        # amount * probability / 100
        assert data["weighted_amount"] == "200.0"

    async def test_create_opportunity_with_related_links(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Rails: params[:related] linking"""
        acct_resp = await client.post("/api/v1/accounts", json=make_account_payload(), headers=auth_headers)
        cont_resp = await client.post("/api/v1/contacts", json=make_contact_payload(), headers=auth_headers)
        
        acct_id = acct_resp.json()["id"]
        cont_id = cont_resp.json()["id"]

        payload = make_opportunity_payload(account_id=acct_id, contact_id=cont_id)
        resp = await client.post(BASE, json=payload, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        
        assert data["account"]["id"] == acct_id
        assert len(data["contacts"]) == 1
        assert data["contacts"][0]["id"] == cont_id


@pytest.mark.asyncio
class TestOpportunityUpdate:
    """Rails: OpportunitiesController#update"""

    async def test_update_opportunity_discount_validation(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Rails: validate discount <= amount"""
        resp_create = await client.post(
            BASE, json=make_opportunity_payload(amount=500), headers=auth_headers
        )
        opp_id = resp_create.json()["id"]

        # Discount > Amount should fail
        resp = await client.patch(f"{BASE}/{opp_id}", json={"discount": 600}, headers=auth_headers)
        assert resp.status_code == 422
