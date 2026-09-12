"""
/api/portfolio/planning-comparison (codex/portfolio-coach-
recommendations). Via TestClient against an isolated temp db (see
conftest.py's `client`/`temp_db` fixtures — never the real cfo.db).

Covers the brief's planning-integration requirement: compares
retirement projection / Monte Carlo / SWR outcomes between saved
assumptions and a proposed allocation's blended expected return, by
calling projection_engine.py/simulation_engine.py directly -- and must
be byte-identical to the underlying engines' own output when no
proposed allocation is supplied.
"""
import json


def _seed_planning_inputs(client, sample_inputs):
    r = client.put("/api/planning-inputs", json=sample_inputs)
    assert r.status_code == 200, r.text


class TestPlanningComparisonByteIdentical:
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
