"""
/api/recommendations generate/list/decide (codex/portfolio-coach-
recommendations). Via TestClient against an isolated temp db (see
conftest.py's `client`/`temp_db` fixtures — never the real cfo.db).

Covers reference tests #19 (rejected recommendation doesn't reappear
without a changed input), #20 (policy change invalidates prior
recommendations), and #21 (unreconciled discrepancy blocks the
high-confidence drift/new-money/rebalance tiers).
"""
import json


def _create_account(client, account_type="taxable", name="Brokerage", balance=100000):
    r = client.post("/api/accounts", json={
        "name": name, "account_type": account_type, "owner": "jason", "institution": "Test Institution",
        "balance": balance,
    })
    assert r.status_code == 200, r.text
    return r.json()


def _save_policy(client, **overrides):
    body = {
        "target_us_large_cap_pct": 60, "target_us_mid_cap_pct": 0, "target_us_small_cap_pct": 0,
        "target_international_developed_pct": 0, "target_emerging_markets_pct": 0,
        "target_us_bonds_pct": 40, "target_international_bonds_pct": 0, "target_cash_pct": 0,
        "target_real_estate_pct": 0, "target_alternatives_pct": 0, "drift_band_pct": 5,
    }
    body.update(overrides)
    r = client.post("/api/investment-policy", json=body)
    assert r.status_code == 200, r.text
    return r.json()


class TestRecommendationsGenerate:
    def test_no_policy_yields_establish_policy_card(self, client):
        _create_account(client)
        client.post("/api/holdings", json={
            "account_id": 1, "security_name": "Fund", "market_value": 100000, "asset_class": "us_large_cap",
        })
        r = client.get("/api/recommendations")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["has_policy"] is False
        assert any(c["payload"]["recommendation_key"] == "no_investment_policy:None:None:None:" for c in body["recommendations"])

    def test_drift_recommendation_generated_when_policy_violated(self, client):
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "All Stock", "market_value": 100000, "asset_class": "us_large_cap",
        })
        _save_policy(client)
        r = client.get("/api/recommendations")
        body = r.json()
        assert body["has_policy"] is True
        assert any(c["category"] == "policy_violation" for c in body["recommendations"])

    def test_exact_match_no_drift_recommendation(self, client):
        """Reference test #1: portfolio exactly matches policy -> no
        drift/rebalance recommendation."""
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Stock", "market_value": 60000, "asset_class": "us_large_cap",
        })
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Bonds", "market_value": 40000, "asset_class": "us_bonds",
        })
        _save_policy(client)
        body = client.get("/api/recommendations").json()
        assert not any(c["category"] in ("policy_violation", "tax_advantaged_rebalance", "taxable_rebalance") for c in body["recommendations"])

    def test_material_reconciliation_discrepancy_blocks_high_confidence_tiers(self, client):
        """Reference test #21: unreconciled holdings block drift/new-
        money/rebalance recommendations, but the reconciliation warning
        itself (tier 1) still surfaces."""
        acc = _create_account(client, balance=100000)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Stock", "market_value": 40000, "asset_class": "us_large_cap",
        })  # $60,000 short of the $100,000 account balance -- material mismatch
        _save_policy(client)
        body = client.get("/api/recommendations").json()
        categories = {c["category"] for c in body["recommendations"]}
        assert "missing_data" in categories
        assert "policy_violation" not in categories
        assert "tax_advantaged_rebalance" not in categories
        assert "taxable_rebalance" not in categories


class TestRecommendationsDecisionLifecycle:
    def test_reject_then_regenerate_does_not_reappear(self, client):
        """Reference test #19."""
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "All Stock", "market_value": 100000, "asset_class": "us_large_cap",
        })
        _save_policy(client)
        body = client.get("/api/recommendations").json()
        drift_cards = [c for c in body["recommendations"] if c["category"] == "policy_violation"]
        assert drift_cards
        rec_id = drift_cards[0]["id"]

        r = client.post(f"/api/recommendations/{rec_id}/decide", json={"status": "rejected", "reason": "Not now"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "rejected"

        body_after = client.get("/api/recommendations").json()
        assert not any(c["id"] == rec_id for c in body_after["recommendations"])
        # Same underlying facts -> not reinserted as a new row either.
        assert not any(c["category"] == "policy_violation" and c["payload"]["recommendation_key"] == drift_cards[0]["payload"]["recommendation_key"] for c in body_after["recommendations"])

    def test_policy_change_invalidates_prior_recommendation(self, client):
        """Reference test #20."""
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "All Stock", "market_value": 100000, "asset_class": "us_large_cap",
        })
        _save_policy(client, target_us_large_cap_pct=60, target_us_bonds_pct=40)
        body = client.get("/api/recommendations").json()
        drift_cards = [c for c in body["recommendations"] if c["category"] == "policy_violation"]
        assert drift_cards
        rec_id = drift_cards[0]["id"]

        # Policy target changes materially -> the old card's facts moved.
        _save_policy(client, target_us_large_cap_pct=100, target_us_bonds_pct=0)
        body_after = client.get("/api/recommendations").json()

        detail = client.get(f"/api/recommendations/{rec_id}").json()
        assert detail["status"] == "invalidated"
        assert any(e["event_type"] == "invalidated" for e in detail["events"])
        # A fresh card exists (facts recurred under the new policy, or a
        # new drift condition was created) -- the active queue is not
        # simply empty because of the invalidation.
        assert body_after["recommendations"]

    def test_accept_and_complete_round_trip(self, client):
        """Reference acceptance scenario #5: accept a recommendation and
        bring it back for follow-up review."""
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "All Stock", "market_value": 100000, "asset_class": "us_large_cap",
        })
        _save_policy(client)
        body = client.get("/api/recommendations").json()
        rec_id = body["recommendations"][0]["id"]

        r = client.post(f"/api/recommendations/{rec_id}/decide", json={"status": "accepted", "notes": "Will act on this"})
        assert r.json()["status"] == "accepted"
        # Accepted recommendations stay in the active queue (reused, not duplicated).
        body_after = client.get("/api/recommendations").json()
        assert any(c["id"] == rec_id and c["status"] == "accepted" for c in body_after["recommendations"])

        r2 = client.post(f"/api/recommendations/{rec_id}/decide", json={"status": "completed"})
        assert r2.json()["status"] == "completed"
        detail = client.get(f"/api/recommendations/{rec_id}").json()
        assert len(detail["events"]) == 2
        assert [e["event_type"] for e in detail["events"]] == ["accepted", "completed"]

    def test_unknown_status_rejected(self, client):
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Fund", "market_value": 10000, "asset_class": "us_large_cap",
        })
        body = client.get("/api/recommendations").json()
        rec_id = body["recommendations"][0]["id"]
        r = client.post(f"/api/recommendations/{rec_id}/decide", json={"status": "not_a_real_status"})
        assert r.status_code == 400

    def test_decide_unknown_recommendation_404s(self, client):
        r = client.post("/api/recommendations/999999/decide", json={"status": "accepted"})
        assert r.status_code == 404


class TestMultiAccountContributionDestination:
    """Reference test #18: multiple-account new money reduces household
    drift, via /api/portfolio/contribution-destination/multi-account."""

    def test_no_holdings_400s(self, client):
        r = client.post("/api/portfolio/contribution-destination/multi-account", json={
            "pools": [{"account_id": 1, "amount": 1000, "eligible_classes": None}],
        })
        assert r.status_code == 400

    def test_bonds_only_pool_gets_bonds_open_pool_gets_next_largest_gap(self, client):
        acc1 = _create_account(client, name="401k")
        acc2 = _create_account(client, name="Brokerage")
        client.post("/api/holdings", json={
            "account_id": acc1["id"], "security_name": "All Stock", "market_value": 10000, "asset_class": "us_large_cap",
        })
        _save_policy(client, target_us_large_cap_pct=40, target_us_bonds_pct=40, target_cash_pct=20)
        r = client.post("/api/portfolio/contribution-destination/multi-account", json={
            "pools": [
                {"account_id": acc1["id"], "amount": 4000, "eligible_classes": ["us_bonds"]},
                {"account_id": acc2["id"], "amount": 2000, "eligible_classes": None},
            ],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        by_account = {a["account_id"]: a for a in body["actions"]}
        assert by_account[acc1["id"]]["asset_class"] == "us_bonds"
        assert by_account[acc1["id"]]["amount"] == 4000
        assert by_account[acc2["id"]]["asset_class"] == "cash"
        assert by_account[acc2["id"]]["amount"] == 2000
        assert body["unallocated"] == []


class TestRecommendationsPrivacy:
    def test_included_in_backup_export_and_restore(self, client):
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Fund", "market_value": 100000, "asset_class": "us_large_cap",
        })
        client.get("/api/recommendations")
        payload = client.get("/api/backup/export").json()
        assert "recommendations" in payload["tables"]
        assert "recommendation_events" in payload["tables"]
        assert len(payload["tables"]["recommendations"]) > 0

        r = client.post("/api/backup/restore", params={"confirm": "true"},
                         files={"file": ("b.json", json.dumps(payload), "application/json")})
        assert r.status_code == 200, r.text
