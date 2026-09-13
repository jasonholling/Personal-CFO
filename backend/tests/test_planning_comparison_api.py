"""
/api/portfolio/planning-comparison (codex/portfolio-coach-
recommendations). Via TestClient against an isolated temp db (see
conftest.py's `client`/`temp_db` fixtures — never the real cfo.db).

Covers the planning-integration requirement: compares retirement
projection / Monte Carlo / SWR outcomes between the current, classified
portfolio and a proposed target allocation.  If there are no usable
holdings, the endpoint explicitly falls back to saved assumptions.
"""
import json


def _seed_planning_inputs(client, sample_inputs):
    r = client.put("/api/planning-inputs", json=sample_inputs)
    assert r.status_code == 200, r.text


def _seed_current_portfolio(client):
    """A real current mix is required to prove this is a rebalance
    comparison, rather than a disguised saved-return sensitivity test."""
    account = client.post("/api/accounts", json={
        "name": "Portfolio", "account_type": "taxable", "owner": "jason",
        "institution": "Test", "balance": 100000,
    })
    assert account.status_code == 200, account.text
    account_id = account.json()["id"]
    for name, value, asset_class in (
        ("US Equity", 80000, "us_large_cap"),
        ("Bonds", 20000, "us_bonds"),
    ):
        created = client.post("/api/holdings", json={
            "account_id": account_id, "security_name": name,
            "market_value": value, "asset_class": asset_class,
        })
        assert created.status_code == 200, created.text


class TestPlanningComparisonByteIdentical:
    def test_defaults_to_latest_saved_scenario_instead_of_hidden_generic_defaults(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        import db
        conn = db.get_db()
        conn.execute(
            "INSERT INTO saved_scenarios (name, retirement_age, ss_timing, summary_json) VALUES (?,?,?,?)",
            ("Retire at 58", 58, "delayed", "{}"),
        )
        conn.commit()
        conn.close()

        r = client.post("/api/portfolio/planning-comparison", json={})
        assert r.status_code == 200, r.text
        assert r.json()["scenario"] == {
            "retirement_age": 58, "ss_timing": "delayed",
            "saved_scenario_id": 1, "saved_scenario_name": "Retire at 58",
        }

    def test_no_proposed_allocation_baseline_matches_direct_engine_call(self, client, sample_inputs):
        """Byte-identical requirement: with no proposed_allocation, the
        comparison endpoint's own baseline figures must exactly match
        calling the plain retirement-projection endpoint directly with
        unmodified inputs -- proving no engine input was touched."""
        _seed_planning_inputs(client, sample_inputs)
        direct = client.get("/api/projections/retirement").json()
        direct_scenario = next(s for s in direct["scenarios"] if s["retirement_age"] == 60 and s["ss_timing"] == "early")

        r = client.post("/api/portfolio/planning-comparison", json={"ret_age": 60, "ss_timing": "early"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["proposed"] is None
        assert body["baseline"]["projected_surplus"] == direct_scenario["projected_surplus"]
        assert body["baseline"]["percent_funded"] == direct_scenario["percent_funded"]
        assert body["baseline"]["on_track"] == direct_scenario["on_track"]

    def test_no_proposed_allocation_monte_carlo_matches_direct_call(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        direct = client.get("/api/simulation/monte-carlo", params={"ret_age": 60, "ss_timing": "early"}).json()
        r = client.post("/api/portfolio/planning-comparison", json={"ret_age": 60, "ss_timing": "early"})
        body = r.json()
        assert body["baseline"]["monte_carlo_success_rate"] == direct["success_rate"]
        assert body["baseline"]["monte_carlo_median_final_balance"] == direct["median_final_balance"]

class TestPlanningComparisonProposed:
    def test_uses_current_holdings_not_saved_return_assumptions(self, client, sample_inputs):
        _seed_planning_inputs(client, {**sample_inputs,
                                       "expected_return_pre_retirement": 0.01,
                                       "expected_return_post_retirement": 0.01})
        _seed_current_portfolio(client)
        r = client.post("/api/portfolio/planning-comparison", json={
            "ret_age": 60, "ss_timing": "early",
            "proposed_allocation": {"us_large_cap": 60, "us_bonds": 40},
        })
        assert r.status_code == 200, r.text
        body = r.json()
        # 80% at 9% plus 20% at 4.5% = the current portfolio's 8.1%,
        # not the deliberately incompatible 1% saved planning assumption.
        assert body["baseline_source"] == "current_portfolio_mix"
        assert body["baseline_expected_return_pre_retirement"] == 0.081
        assert body["baseline_expected_return_post_retirement"] == 0.081
        assert body["baseline_classified_pct"] == 100

    def test_proposed_allocation_runs_a_second_comparison(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        r = client.post("/api/portfolio/planning-comparison", json={
            "ret_age": 60, "ss_timing": "early",
            "proposed_allocation": {"us_large_cap": 60, "us_bonds": 40},
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["proposed"] is not None
        assert body["proposed_blended_expected_return"] is not None
        # Every requested comparison figure is present on both sides.
        for key in ("projected_surplus", "percent_funded", "monte_carlo_success_rate", "safe_withdrawal_annual"):
            assert key in body["baseline"]
            assert key in body["proposed"]

    def test_different_proposed_allocation_changes_the_result(self, client, sample_inputs):
        """Mutation-style check: a more aggressive vs. more conservative
        proposed mix produces genuinely different blended expected
        returns and therefore different projected outcomes."""
        _seed_planning_inputs(client, sample_inputs)
        aggressive = client.post("/api/portfolio/planning-comparison", json={
            "ret_age": 60, "proposed_allocation": {"us_large_cap": 100},
        }).json()
        conservative = client.post("/api/portfolio/planning-comparison", json={
            "ret_age": 60, "proposed_allocation": {"us_bonds": 100},
        }).json()
        assert aggressive["proposed_blended_expected_return"] != conservative["proposed_blended_expected_return"]
        assert aggressive["proposed_portfolio_volatility"] > conservative["proposed_portfolio_volatility"]
        assert aggressive["proposed"]["projected_surplus"] != conservative["proposed"]["projected_surplus"]

    def test_entirely_unclassified_allocation_errors_rather_than_inventing_a_return(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        r = client.post("/api/portfolio/planning-comparison", json={
            "ret_age": 60, "proposed_allocation": {"unclassified": 100},
        })
        assert r.status_code == 400

    def test_proposed_return_does_not_mutate_saved_planning_inputs(self, client, sample_inputs):
        """The proposed-side override must be entirely in-memory --
        never persisted back to planning_inputs."""
        _seed_planning_inputs(client, sample_inputs)
        client.post("/api/portfolio/planning-comparison", json={
            "ret_age": 60, "proposed_allocation": {"us_bonds": 100},
        })
        saved = client.get("/api/planning-inputs").json()
        assert saved["expected_return_pre_retirement"] == sample_inputs["expected_return_pre_retirement"]
        assert saved["expected_return_post_retirement"] == sample_inputs["expected_return_post_retirement"]
