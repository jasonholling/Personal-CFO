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


class TestInvestmentPolicyValidation:
    """External review finding #6: a policy with negative targets or
    targets not summing to 100% used to save without error and feed
    straight into every downstream recommendation calculation."""

    def test_targets_not_summing_to_100_rejected(self, client):
        r = client.post("/api/investment-policy", json={
            "target_us_large_cap_pct": 50, "target_us_bonds_pct": 30,  # sums to 80
        })
        assert r.status_code == 422

    def test_targets_over_100_rejected(self, client):
        r = client.post("/api/investment-policy", json={
            "target_us_large_cap_pct": 80, "target_us_bonds_pct": 40,  # sums to 120
        })
        assert r.status_code == 422

    def test_negative_target_rejected(self, client):
        r = client.post("/api/investment-policy", json={
            "target_us_large_cap_pct": 120, "target_us_bonds_pct": -20,  # sums to 100 but negative
        })
        assert r.status_code == 422

    def test_valid_100_total_accepted(self, client):
        r = client.post("/api/investment-policy", json={
            "target_us_large_cap_pct": 60, "target_us_bonds_pct": 40,
        })
        assert r.status_code == 200, r.text

    def test_small_rounding_tolerance_accepted(self, client):
        """99.7%/100.3% from real-world rounded entries shouldn't be
        rejected outright -- the tolerance is 0.5, not exact-100 only."""
        r = client.post("/api/investment-policy", json={
            "target_us_large_cap_pct": 60.2, "target_us_bonds_pct": 39.6,  # 99.8
        })
        assert r.status_code == 200, r.text

    def test_invalid_policy_limits_rejected(self, client):
        assert client.post("/api/investment-policy", json={
            "target_us_large_cap_pct": 60, "target_us_bonds_pct": 40,
            "max_single_security_pct": 0,
        }).status_code == 422
        assert client.post("/api/investment-policy", json={
            "target_us_large_cap_pct": 60, "target_us_bonds_pct": 40,
            "minimum_cash_reserve": -1,
        }).status_code == 422
        assert client.post("/api/investment-policy", json={
            "target_us_large_cap_pct": 60, "target_us_bonds_pct": 40,
            "account_constraints": [{"account_id": 1, "allowed_asset_classes": ["made_up"]}],
        }).status_code == 422


class TestRecommendationsGenerate:
    def test_tax_location_uses_resolved_current_account_types_and_real_destinations(self, client):
        """A brokerage bond plus an otherwise-empty traditional IRA is a
        location-review opportunity. Older code looked only at accounts with
        a holding and compared obsolete ``ira``/``401k`` strings, so it
        missed this real setup."""
        brokerage = _create_account(client, "taxable", "Brokerage", 20000)
        ira = _create_account(client, "ira", "Rollover IRA", 0)
        client.post("/api/holdings", json={
            "account_id": brokerage["id"], "security_name": "Bond Fund", "market_value": 20000,
            "asset_class": "us_bonds",
        })
        _save_policy(client)

        body = client.get("/api/recommendations").json()
        card = next(c for c in body["recommendations"]
                    if c["payload"]["recommendation_key"].startswith("asset_location_review:"))
        assert card["payload"]["affected_accounts"] == [brokerage["id"], ira["id"]]
        assert "future purchases" in card["payload"]["action_text"]
        assert "do not sell solely" in card["payload"]["proposed_change"]

    def test_tax_location_does_not_offer_a_policy_excluded_ira(self, client):
        brokerage = _create_account(client, "taxable", "Brokerage", 20000)
        ira = _create_account(client, "ira", "Excluded IRA", 0)
        client.post("/api/holdings", json={
            "account_id": brokerage["id"], "security_name": "Bond Fund", "market_value": 20000,
            "asset_class": "us_bonds",
        })
        _save_policy(client, excluded_accounts=[ira["id"]])
        body = client.get("/api/recommendations").json()
        assert not any(c["payload"]["recommendation_key"].startswith("asset_location_review:")
                       for c in body["recommendations"])

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

    def test_excluded_cash_only_account_does_not_trigger_missing_holdings(self, client):
        invested = _create_account(client, name="Brokerage", balance=100000)
        checking = _create_account(client, name="Checking", balance=25000)
        client.post("/api/holdings", json={
            "account_id": invested["id"], "security_name": "Stock", "market_value": 60000,
            "asset_class": "us_large_cap",
        })
        client.post("/api/holdings", json={
            "account_id": invested["id"], "security_name": "Bonds", "market_value": 40000,
            "asset_class": "us_bonds",
        })
        _save_policy(client, excluded_accounts=[checking["id"]])
        body = client.get("/api/recommendations").json()
        # The invested holdings may still have their own missing cost-basis,
        # fee, or confidence cards. The cash-only checking account must not
        # create a holdings-reconciliation card of its own.
        assert not any(
            c["category"] == "missing_data" and checking["id"] in c.get("relevant_account_ids", [])
            for c in body["recommendations"]
        )

    def test_production_planning_results_reach_goal_aware_queue(self, client, sample_inputs, monkeypatch):
        """Finding 10: the production call path must not keep passing
        the old empty {} into minor_optimization_recommendations."""
        import projection_engine
        import simulation_engine
        assert client.put("/api/planning-inputs", json=sample_inputs).status_code == 200
        acc = _create_account(client, balance=100000)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Stock", "market_value": 100000,
            "asset_class": "us_large_cap", "expense_ratio": 0.001, "cost_basis": 90000,
            "confidence": "high",
        })
        _save_policy(client, target_us_large_cap_pct=100, target_us_bonds_pct=0)
        monkeypatch.setattr(projection_engine, "run_retirement_projection", lambda *a, **k: {
            "scenarios": [{"retirement_age": 60, "percent_funded": 70, "on_track": False}],
        })
        monkeypatch.setattr(simulation_engine, "run_monte_carlo", lambda *a, **k: {
            "success_rate": 65, "median_depletion_age": 80,
        })
        body = client.get("/api/recommendations").json()
        assert any(c["title"] == "Monte Carlo success rate is below 80%" for c in body["recommendations"])


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


class TestContributionDestinationNamesAccountAndFund:
    """External review finding #1 (2026-09-12, commit 4812d84):
    "Where should new money go?" must name an actual account and fund,
    not stop at the asset class."""

    def test_names_the_recorded_eligible_option(self, client):
        acc = _create_account(client, account_type="401k", name="401k")
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "All Stock", "market_value": 100000, "asset_class": "us_large_cap",
        })
        client.post("/api/account-investment-options", json={
            "account_id": acc["id"], "option_name": "Stable Value Fund", "asset_class": "us_bonds",
        })
        _save_policy(client, target_us_large_cap_pct=60, target_us_bonds_pct=40)
        r = client.post("/api/portfolio/contribution-destination", json={"amount": 10000})
        assert r.status_code == 200, r.text
        actions = r.json()["actions"]
        bonds_action = next(a for a in actions if a["asset_class"] == "us_bonds")
        assert bonds_action["destination"]["option_name"] == "Stable Value Fund"
        assert bonds_action["destination"]["account_id"] == acc["id"]
        assert bonds_action["destination"]["account_name"] == "401k"

    def test_no_recorded_option_reports_destination_as_none(self, client):
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "All Stock", "market_value": 100000, "asset_class": "us_large_cap",
        })
        _save_policy(client, target_us_large_cap_pct=60, target_us_bonds_pct=40)
        r = client.post("/api/portfolio/contribution-destination", json={"amount": 10000})
        actions = r.json()["actions"]
        bonds_action = next(a for a in actions if a["asset_class"] == "us_bonds")
        assert bonds_action["destination"] is None


class TestRebalanceDestinationAndDecisionWorkflow:
    def test_rebalance_buy_names_same_account_and_fund(self, client):
        acc = _create_account(client, account_type="401k", name="Workplace 401k", balance=100000)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Stock Fund", "market_value": 100000,
            "asset_class": "us_large_cap",
        })
        client.post("/api/account-investment-options", json={
            "account_id": acc["id"], "option_name": "Bond Index", "ticker": "BND",
            "asset_class": "us_bonds", "available_for_exchange": True,
        })
        _save_policy(client, target_us_large_cap_pct=50, target_us_bonds_pct=50)
        response = client.post("/api/portfolio/rebalance", json={"amount": 0})
        assert response.status_code == 200, response.text
        buy = next(a for a in response.json()["rebalance_actions"] if a["action"] == "buy")
        assert buy["destination"]["account_name"] == "Workplace 401k"
        assert buy["destination"]["option_name"] == "Bond Index"

    def test_accept_creates_task_complete_closes_it_and_defer_saves_review_date(self, client):
        acc = _create_account(client, balance=10000)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Fund", "market_value": 10000,
            "asset_class": "us_large_cap",
        })
        rec_id = client.get("/api/recommendations").json()["recommendations"][0]["id"]
        accepted = client.post(f"/api/recommendations/{rec_id}/decide", json={"status": "accepted"})
        assert accepted.status_code == 200
        tasks = client.get("/api/tasks").json()
        task_rows = tasks if isinstance(tasks, list) else tasks.get("tasks", [])
        assert any(t.get("auto_key") == f"portfolio_coach_{rec_id}" for t in task_rows)
        completed = client.post(f"/api/recommendations/{rec_id}/decide", json={"status": "completed"})
        assert completed.status_code == 200
        deferred = client.post(f"/api/recommendations/{rec_id}/decide", json={
            "status": "deferred", "review_date": "2030-01-15"
        })
        assert deferred.json()["review_date"] == "2030-01-15"


class TestMultiAccountContributionDestination:
    """Reference test #18: multiple-account new money reduces household
    drift, via /api/portfolio/contribution-destination/multi-account."""

    def test_no_holdings_400s(self, client):
        r = client.post("/api/portfolio/contribution-destination/multi-account", json={
            "pools": [{"account_id": 1, "amount": 1000, "eligible_classes": None}],
        })
        assert r.status_code == 400

    def test_open_pool_honors_blocked_asset_classes(self, client):
        account = _create_account(client, balance=10000)
        client.post("/api/holdings", json={
            "account_id": account["id"], "security_name": "Stock Fund",
            "market_value": 10000, "asset_class": "us_large_cap",
        })
        _save_policy(client, target_us_large_cap_pct=40, target_us_bonds_pct=40,
                     target_cash_pct=20, account_constraints=[{
                         "account_id": account["id"], "excluded_asset_classes": ["us_bonds"],
                     }])
        response = client.post("/api/portfolio/contribution-destination/multi-account", json={
            "pools": [{"account_id": account["id"], "amount": 1000, "eligible_classes": None}],
        })
        assert response.status_code == 200, response.text
        assert [(a["asset_class"], a["amount"]) for a in response.json()["actions"]] == [("cash", 1000)]

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

    def test_excluded_account_pool_is_left_unallocated(self, client):
        invested = _create_account(client, name="Brokerage")
        checking = _create_account(client, name="Checking", balance=5000)
        client.post("/api/holdings", json={
            "account_id": invested["id"], "security_name": "All Stock", "market_value": 100000,
            "asset_class": "us_large_cap",
        })
        _save_policy(client, excluded_accounts=[checking["id"]])
        body = client.post("/api/portfolio/contribution-destination/multi-account", json={
            "pools": [{"account_id": checking["id"], "amount": 5000, "eligible_classes": None}],
        }).json()
        assert body["actions"] == []
        assert body["unallocated"] == [{
            "account_id": checking["id"], "amount": 5000.0,
            "reason": "Account is excluded from the investment policy.",
        }]

    def test_deleted_account_pool_is_left_unallocated(self, client):
        invested = _create_account(client, name="Brokerage")
        client.post("/api/holdings", json={
            "account_id": invested["id"], "security_name": "All Stock", "market_value": 100000,
            "asset_class": "us_large_cap",
        })
        _save_policy(client)
        body = client.post("/api/portfolio/contribution-destination/multi-account", json={
            "pools": [{"account_id": 999999, "amount": 5000, "eligible_classes": None}],
        }).json()
        assert body["actions"] == []
        assert body["unallocated"] == [{
            "account_id": 999999, "amount": 5000.0,
            "reason": "Account no longer exists.",
        }]

    def test_negative_contributions_are_rejected(self, client):
        assert client.post("/api/portfolio/contribution-destination", json={"amount": -1}).status_code == 422
        assert client.post("/api/portfolio/contribution-destination/multi-account", json={
            "pools": [{"account_id": 1, "amount": -1}],
        }).status_code == 422


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


class TestRecommendationProvenance:
    """Item 3: a recommendation records which saved scenario, planning
    inputs, holdings snapshot, and investment policy generated it, and a
    later change is reported with a human-readable reason instead of one
    generic sentence."""

    def test_recommendation_records_holdings_and_policy_provenance(self, client):
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "All Stock", "market_value": 100000, "asset_class": "us_large_cap",
        })
        _save_policy(client, target_us_large_cap_pct=60, target_us_bonds_pct=40)
        body = client.get("/api/recommendations").json()
        card = next(c for c in body["recommendations"] if c["category"] == "policy_violation")
        assert card["holdings_snapshot_hash"]
        assert card["policy_hash"]
        # No saved scenario or assumption review exists yet in this test.
        assert card["saved_scenario_id"] is None
        assert card["assumption_review_id"] is None

    def test_policy_change_invalidation_reason_names_the_policy(self, client):
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "All Stock", "market_value": 100000, "asset_class": "us_large_cap",
        })
        _save_policy(client, target_us_large_cap_pct=60, target_us_bonds_pct=40)
        body = client.get("/api/recommendations").json()
        drift_card = next(c for c in body["recommendations"] if c["category"] == "policy_violation")
        rec_id = drift_card["id"]

        _save_policy(client, target_us_large_cap_pct=100, target_us_bonds_pct=0)
        client.get("/api/recommendations")

        detail = client.get(f"/api/recommendations/{rec_id}").json()
        assert detail["status"] == "invalidated"
        invalidated_event = next(e for e in detail["events"] if e["event_type"] == "invalidated")
        assert "investment policy changed" in invalidated_event["notes"]

    def test_holdings_change_invalidation_reason_names_holdings(self, client):
        acc = _create_account(client)
        holding = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "All Stock", "market_value": 100000, "asset_class": "us_large_cap",
        }).json()
        _save_policy(client, target_us_large_cap_pct=60, target_us_bonds_pct=40)
        body = client.get("/api/recommendations").json()
        drift_card = next(c for c in body["recommendations"] if c["category"] == "policy_violation")
        rec_id = drift_card["id"]

        # Holdings move enough to change the drift facts without touching the policy.
        client.put(f"/api/holdings/{holding['id']}", json={
            "account_id": acc["id"], "security_name": "All Stock", "market_value": 40000, "asset_class": "us_bonds",
        })
        client.get("/api/recommendations")

        detail = client.get(f"/api/recommendations/{rec_id}").json()
        assert detail["status"] == "invalidated"
        invalidated_event = next(e for e in detail["events"] if e["event_type"] == "invalidated")
        assert "holdings changed" in invalidated_event["notes"]


class TestDeferredHighPriorityTaskLinkage:
    """Item 3: an accepted OR deferred HIGH-PRIORITY recommendation
    creates/links an Action-Tracker task; a deferred but not
    high-priority recommendation does not clutter the general task list,
    and no recurring task is ever created."""

    def test_deferred_high_priority_recommendation_creates_a_dated_task(self, client):
        # No policy saved -> the only candidate is the tier-1 missing_data
        # "no_investment_policy" card, which is high priority.
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Fund", "market_value": 10000, "asset_class": "us_large_cap",
        })
        rec_id = client.get("/api/recommendations").json()["recommendations"][0]["id"]
        r = client.post(f"/api/recommendations/{rec_id}/decide", json={"status": "deferred", "review_date": "2030-06-01"})
        assert r.status_code == 200, r.text
        assert r.json()["linked_task_id"] is not None

        tasks = client.get("/api/tasks").json()
        task = next(t for t in tasks if t["auto_key"] == f"portfolio_coach_{rec_id}")
        assert task["due_date"] == "2030-06-01"
        assert task["recurrence"] == "once"
        assert task["completed"] == 0

    def test_deferred_non_high_priority_recommendation_does_not_create_a_task(self, client):
        acc = _create_account(client)
        # A high-expense-ratio holding produces only a tier-4
        # high_cost_or_redundant card -- not high priority -- while the
        # policy matches current allocation exactly, so no drift card
        # also appears.
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Pricey Fund", "market_value": 100000,
            "asset_class": "us_large_cap", "expense_ratio": 0.02,
        })
        _save_policy(client, target_us_large_cap_pct=100, target_us_bonds_pct=0)
        body = client.get("/api/recommendations").json()
        card = next(c for c in body["recommendations"] if c["category"] == "high_cost_or_redundant")
        r = client.post(f"/api/recommendations/{card['id']}/decide", json={
            "status": "deferred", "review_date": "2030-06-01",
        })
        assert r.status_code == 200, r.text
        assert r.json()["linked_task_id"] is None
        tasks = client.get("/api/tasks").json()
        assert not any(t["auto_key"] == f"portfolio_coach_{card['id']}" for t in tasks)

    def test_accepted_recommendation_records_linked_task_id(self, client):
        acc = _create_account(client, balance=10000)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Fund", "market_value": 10000, "asset_class": "us_large_cap",
        })
        rec_id = client.get("/api/recommendations").json()["recommendations"][0]["id"]
        r = client.post(f"/api/recommendations/{rec_id}/decide", json={"status": "accepted"})
        task_id = r.json()["linked_task_id"]
        assert task_id is not None
        tasks = client.get("/api/tasks").json()
        assert any(t["id"] == task_id and t["auto_key"] == f"portfolio_coach_{rec_id}" for t in tasks)


class TestAnnualPortfolioReview:
    """Item 3: an annual-review summary including open recommendations,
    deferred reviews due, stale holding values, unreconciled accounts,
    allocation drift, concentrated positions, and taxable-loss
    candidates -- built from the same recommendation queue
    GET /api/recommendations persists, not a second calculation."""

    def test_summary_partitions_every_required_section(self, client):
        acc = _create_account(client, balance=210000)
        big = client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Concentrated Stock", "market_value": 200000,
            "asset_class": "us_large_cap", "cost_basis": 250000,
        }).json()
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Bond Fund", "market_value": 10000, "asset_class": "us_bonds",
        })
        _save_policy(client, target_us_large_cap_pct=50, target_us_bonds_pct=50, max_single_security_pct=20)

        r = client.get("/api/portfolio/annual-review")
        assert r.status_code == 200, r.text
        body = r.json()
        for key in ("open_recommendations", "deferred_reviews_due", "stale_holding_values",
                    "unreconciled_accounts", "allocation_drift", "concentrated_positions",
                    "taxable_loss_candidates", "other_open", "counts", "as_of", "has_policy"):
            assert key in body
        assert body["open_recommendations"]["count"] == len(body["open_recommendations"]["items"])
        assert any(c["payload"]["affected_holdings"] == [big["id"]] for c in body["concentrated_positions"])
        assert any(c["category"] == "policy_violation" for c in body["allocation_drift"])
        assert any(c["payload"]["affected_holdings"] == [big["id"]] for c in body["taxable_loss_candidates"])

    def test_deferred_reviews_due_reflects_a_past_review_date(self, client):
        acc = _create_account(client)
        client.post("/api/holdings", json={
            "account_id": acc["id"], "security_name": "Fund", "market_value": 10000, "asset_class": "us_large_cap",
        })
        rec_id = client.get("/api/recommendations").json()["recommendations"][0]["id"]
        client.post(f"/api/recommendations/{rec_id}/decide", json={"status": "deferred", "review_date": "2000-01-01"})
        body = client.get("/api/portfolio/annual-review").json()
        assert body["deferred_reviews_due"]["count"] >= 1
        assert any(item["id"] == rec_id for item in body["deferred_reviews_due"]["items"])
