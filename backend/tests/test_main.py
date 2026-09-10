"""
Tests for main.py's FastAPI routes, via TestClient against an isolated
temp db (see conftest.py's `client`/`temp_db` fixtures — never the real
cfo.db).
"""
import json

import auth
from projection_engine import CURRENT_YEAR


def _seed_planning_inputs(client, sample_inputs):
    r = client.put("/api/planning-inputs", json=sample_inputs)
    assert r.status_code == 200, r.text
    return r


def _seed_accounts(client, sample_accounts):
    for a in sample_accounts:
        payload = {k: v for k, v in a.items() if k != "id"}
        r = client.post("/api/accounts", json=payload)
        assert r.status_code == 200, r.text


class TestAccountsCrud:
    def test_create_and_list_account(self, client):
        r = client.post("/api/accounts", json={
            "name": "Test Checking", "account_type": "checking", "owner": "joint",
            "institution": "Test Bank", "balance": 1000, "notes": None,
        })
        assert r.status_code == 200
        created = r.json()
        assert created["id"] is not None

        r2 = client.get("/api/accounts")
        assert r2.status_code == 200
        assert any(a["name"] == "Test Checking" for a in r2.json())

    def test_update_account(self, client):
        created = client.post("/api/accounts", json={
            "name": "Old Name", "account_type": "savings", "owner": "joint",
            "institution": "", "balance": 100, "notes": None,
        }).json()
        r = client.put(f"/api/accounts/{created['id']}", json={
            "name": "New Name", "account_type": "savings", "owner": "joint",
            "institution": "", "balance": 200, "notes": None,
        })
        assert r.status_code == 200
        assert r.json()["name"] == "New Name"

    def test_delete_account(self, client):
        created = client.post("/api/accounts", json={
            "name": "To Delete", "account_type": "checking", "owner": "joint",
            "institution": "", "balance": 0, "notes": None,
        }).json()
        r = client.delete(f"/api/accounts/{created['id']}")
        assert r.status_code == 200
        remaining = client.get("/api/accounts").json()
        assert not any(a["id"] == created["id"] for a in remaining)

    def test_create_rejects_unrecognized_account_type(self, client):
        """Regression: account_type used to be an unchecked str — a value
        outside VALID_ACCOUNT_TYPES (e.g. "pretax_401k" instead of the real
        "401k") wasn't rejected here, it just went on to silently vanish
        from net worth and every retirement/Monte Carlo number later,
        found via a bug-hunt sandbox. Must 422 instead of silently
        succeeding with a balance that then goes uncounted everywhere."""
        r = client.post("/api/accounts", json={
            "name": "Mistyped 401k", "account_type": "pretax_401k", "owner": "jason",
            "institution": "", "balance": 650000, "notes": None,
        })
        assert r.status_code == 422

    def test_update_rejects_unrecognized_account_type(self, client):
        created = client.post("/api/accounts", json={
            "name": "Valid", "account_type": "401k", "owner": "jason",
            "institution": "", "balance": 1000, "notes": None,
        }).json()
        r = client.put(f"/api/accounts/{created['id']}", json={
            "name": "Valid", "account_type": "pretax_401k", "owner": "jason",
            "institution": "", "balance": 1000, "notes": None,
        })
        assert r.status_code == 422


class TestAccountFreshness:
    def test_fresh_with_no_accounts(self, client):
        r = client.get("/api/accounts/freshness")
        assert r.status_code == 200
        data = r.json()
        assert data == {"account_count": 0, "stale_count": 0, "stale_accounts": [],
                         "fresh": True, "possible_duplicate_accounts": []}

    def test_freshly_created_account_is_not_stale(self, client):
        client.post("/api/accounts", json={
            "name": "Fresh Checking", "account_type": "checking", "owner": "joint",
            "institution": "", "balance": 1000, "notes": None,
        })
        data = client.get("/api/accounts/freshness").json()
        assert data["stale_count"] == 0
        assert data["fresh"] is True

    def test_same_name_different_type_flagged_as_possible_duplicate(self, client):
        """Regression guard for the Quicken-import scenario: a re-import
        that maps the same real-world account to a different account_type
        than before creates a second row instead of updating the first
        (import matches on name+type), silently leaving a stale orphan that
        still counts toward net worth. This should surface immediately,
        not only once the orphan crosses the 35-day staleness threshold."""
        client.post("/api/accounts", json={
            "name": "Chase Checking", "account_type": "checking", "owner": "joint",
            "institution": "Chase", "balance": 1000, "notes": None,
        })
        client.post("/api/accounts", json={
            "name": "Chase Checking", "account_type": "savings", "owner": "joint",
            "institution": "Chase", "balance": 1000, "notes": None,
        })
        data = client.get("/api/accounts/freshness").json()
        assert data["fresh"] is False
        assert len(data["possible_duplicate_accounts"]) == 1
        group = data["possible_duplicate_accounts"][0]
        assert len(group) == 2
        assert {a["account_type"] for a in group} == {"checking", "savings"}

    def test_distinct_names_are_not_flagged_as_duplicates(self, client):
        client.post("/api/accounts", json={
            "name": "Chase Checking", "account_type": "checking", "owner": "joint",
            "institution": "Chase", "balance": 1000, "notes": None,
        })
        client.post("/api/accounts", json={
            "name": "Ally Savings", "account_type": "savings", "owner": "joint",
            "institution": "Ally", "balance": 1000, "notes": None,
        })
        data = client.get("/api/accounts/freshness").json()
        assert data["possible_duplicate_accounts"] == []
        assert data["fresh"] is True


class TestInsurancePoliciesCrud:
    def test_create_list_update_delete(self, client):
        created = client.post("/api/insurance-policies", json={
            "who": "Alex", "policy_type": "Life — Term", "benefit": "$500,000",
            "premium": "$500/yr", "notes": "", "sort_order": 0,
        }).json()
        assert created["id"] is not None

        listed = client.get("/api/insurance-policies").json()
        assert any(p["id"] == created["id"] for p in listed)

        updated = client.put(f"/api/insurance-policies/{created['id']}", json={
            "who": "Alex", "policy_type": "Life — Term", "benefit": "$600,000",
            "premium": "$500/yr", "notes": "", "sort_order": 0,
        }).json()
        assert updated["benefit"] == "$600,000"

        r = client.delete(f"/api/insurance-policies/{created['id']}")
        assert r.status_code == 200
        listed_after = client.get("/api/insurance-policies").json()
        assert not any(p["id"] == created["id"] for p in listed_after)


class TestPropertyPoliciesCrud:
    def test_create_list_update_delete(self, client):
        created = client.post("/api/property-policies", json={
            "item": "Primary Home", "coverage": "$400k dwelling", "renewal": "1/1", "sort_order": 0,
        }).json()
        assert created["id"] is not None

        updated = client.put(f"/api/property-policies/{created['id']}", json={
            "item": "Primary Home", "coverage": "$450k dwelling", "renewal": "1/1", "sort_order": 0,
        }).json()
        assert updated["coverage"] == "$450k dwelling"

        r = client.delete(f"/api/property-policies/{created['id']}")
        assert r.status_code == 200


class TestPlanningInputs:
    def test_get_returns_defaults_before_any_save(self, client):
        r = client.get("/api/planning-inputs")
        assert r.status_code == 200
        assert r.json()["person1_name"] == "Person 1"

    def test_save_and_reload(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        r = client.get("/api/planning-inputs")
        assert r.json()["person1_name"] == "Alex"
        assert r.json()["w2_salary"] == 150000


class TestNetWorth:
    def test_empty_accounts_gives_zero_net_worth(self, client):
        r = client.get("/api/net-worth")
        assert r.status_code == 200
        assert r.json()["net_worth"] == 0

    def test_net_worth_reflects_accounts(self, client, sample_accounts):
        _seed_accounts(client, sample_accounts)
        r = client.get("/api/net-worth")
        data = r.json()
        # 500k 401k + 100k roth + 50k ira + 200k taxable + 20k hsa - 200k mortgage
        assert data["net_worth"] == 670000

    def test_kids_assets_excluded_from_main_categories(self, client):
        client.post("/api/accounts", json={
            "name": "Kid Roth", "account_type": "roth_ira", "owner": "kid_1",
            "institution": "", "balance": 5000, "notes": None,
        })
        r = client.get("/api/net-worth").json()
        assert r["kids_assets"] == 5000
        assert r["investment"] == 0


class TestCfoBriefing:
    def test_briefing_is_available_with_a_fresh_install(self, client):
        r = client.get("/api/cfo-briefing")
        assert r.status_code == 200
        body = r.json()
        assert "priorities" in body
        assert body["data_health"]["account_count"] == 0

    def test_briefing_uses_existing_calculations(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, {**sample_inputs, "current_monthly_expenses": 10_000})
        _seed_accounts(client, sample_accounts)
        body = client.get("/api/cfo-briefing").json()
        assert body["emergency_fund"]["has_data"] is True
        assert body["data_health"]["account_count"] == len(sample_accounts)


class TestCashFlow:
    def test_create_list_update_and_delete_cash_flow_item(self, client):
        created = client.post("/api/cash-flow", json={
            "name": "Test take-home pay", "cash_flow_type": "income", "category": "Salary", "amount": 8000,
        })
        assert created.status_code == 200
        item = created.json()
        summary = client.get("/api/cash-flow").json()["summary"]
        assert summary["monthly_income"] == 8000

        updated = client.put(f"/api/cash-flow/{item['id']}", json={
            "name": "Test take-home pay", "cash_flow_type": "income", "category": "Salary", "amount": 8200,
        })
        assert updated.json()["amount"] == 8200
        assert client.delete(f"/api/cash-flow/{item['id']}").status_code == 200

    def test_cash_flow_shortfall_reaches_cfo_briefing(self, client):
        client.post("/api/cash-flow", json={"name":"Income", "cash_flow_type":"income", "amount":4000})
        client.post("/api/cash-flow", json={"name":"Bills", "cash_flow_type":"expense", "amount":5000, "essential":True})
        briefing = client.get("/api/cfo-briefing").json()
        assert briefing["cash_flow"]["status"] == "shortfall"
        assert any(item["destination"] == "cashflow" for item in briefing["priorities"])


class TestProjectionsRequirePlanningInputs:
    def test_retirement_projection_200_with_default_row(self, client):
        # db.init_db() always seeds a planning_inputs row (id=1) with zeroed
        # defaults, so "no planning inputs" never actually happens once the
        # app has started — this exercises that default-data path.
        r = client.get("/api/projections/retirement")
        assert r.status_code == 200

    def test_retirement_projection_200_with_inputs(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)
        r = client.get("/api/projections/retirement")
        assert r.status_code == 200
        assert "scenarios" in r.json()

    def test_retirement_projection_includes_ages_56_through_59(self, client, sample_inputs, sample_accounts):
        """Regression test — the Retirement Projection page's age buttons
        were expanded from [55, 60, 65] to also cover 56-59, which only
        works if the backend actually computes scenarios for those ages."""
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)
        r = client.get("/api/projections/retirement")
        ages = {s["retirement_age"] for s in r.json()["scenarios"]}
        assert {55, 56, 57, 58, 59, 60, 65}.issubset(ages)

    def test_retirement_projection_ignores_a_saved_claim_age_by_design(self, client, sample_inputs, sample_accounts):
        """CALCULATION_CONTRACT.md section 50: /api/projections/retirement
        deliberately does NOT read jason_ss_claim_age/justin_ss_claim_age
        -- passing one would collapse the early/delayed pair into a single
        custom scenario and silently break WhatIf.jsx's hardcoded
        age_X_early label lookups. A household that has saved a claim age
        in Settings must still get the exact same early+delayed pair
        here."""
        _seed_planning_inputs(client, {**sample_inputs, "jason_ss_claim_age": 68, "justin_ss_claim_age": 65})
        _seed_accounts(client, sample_accounts)
        r = client.get("/api/projections/retirement")
        labels = {s["ss_timing"] for s in r.json()["scenarios"]}
        assert labels == {"early", "delayed"}
        assert not any(s["ss_timing"] == "custom" for s in r.json()["scenarios"])

    def test_education_projection_200_with_inputs(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        r = client.get("/api/projections/education")
        assert r.status_code == 200
        assert "goals" in r.json()

    def test_two_dimensional_retirement_projection_200_with_both_ages(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)
        r = client.get("/api/projections/two-dimensional-retirement", params={"jason_ret_age": 62, "justin_ret_age": 60})
        assert r.status_code == 200
        body = r.json()
        assert body["jason_ret_age"] == 62
        assert body["justin_ret_age"] == 60
        assert "yearly_detail" in body

    def test_two_dimensional_retirement_projection_requires_both_ages(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        # Neither query param has a default -- omitting either is a 422,
        # not a silent fallback to the single-axis model (this endpoint
        # is deliberately not reachable by accident).
        r = client.get("/api/projections/two-dimensional-retirement", params={"jason_ret_age": 62})
        assert r.status_code == 422

    def test_insurance_analysis_200_with_inputs(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        r = client.get("/api/projections/insurance")
        assert r.status_code == 200

    def test_kids_projection_handles_missing_inputs_gracefully(self, client):
        r = client.get("/api/projections/kids")
        assert r.status_code == 200


class TestSnapshots:
    def test_take_and_list_snapshot(self, client, sample_accounts):
        _seed_accounts(client, sample_accounts)
        r = client.post("/api/snapshot", json={"note": "Test snapshot"})
        assert r.status_code == 200
        listed = client.get("/api/snapshots").json()
        assert len(listed) == 1
        assert listed[0]["note"] == "Test snapshot"


class TestTasksCrud:
    def test_create_list_update_delete(self, client):
        created = client.post("/api/tasks", json={
            "section": "risk", "title": "Test Task", "description": "desc",
        }).json()
        assert created["id"] is not None

        listed = client.get("/api/tasks").json()
        assert any(t["id"] == created["id"] for t in listed)

        by_section = client.get("/api/tasks?section=risk").json()
        assert any(t["id"] == created["id"] for t in by_section)

        updated = client.patch(f"/api/tasks/{created['id']}", json={"completed": True}).json()
        assert updated["completed"] == 1

        r = client.delete(f"/api/tasks/{created['id']}")
        assert r.status_code == 200

    def test_sync_with_default_planning_inputs_row(self, client):
        # planning_inputs row always exists (see db.init_db()), so sync
        # proceeds and inserts the annual tasks even with zeroed defaults.
        r = client.post("/api/tasks/sync")
        assert r.status_code == 200
        assert r.json()["inserted"] > 0

    def test_sync_with_planning_inputs_inserts_tasks(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)
        r = client.post("/api/tasks/sync")
        assert r.status_code == 200
        assert r.json()["inserted"] > 0

        # Second sync should not duplicate
        r2 = client.post("/api/tasks/sync")
        assert r2.json()["inserted"] == 0


class TestQuickenImport:
    def test_import_without_a_local_mapping_is_rejected(self, client, monkeypatch):
        # This test's whole point is "no local mapping -> nothing matches ->
        # 400" -- it must not depend on whether a real
        # quicken_account_map.local.json happens to exist on the machine
        # running the suite. That file is gitignored and, on a dev machine
        # that has actually used the real Quicken import feature (as this
        # one has), it legitimately exists with a real mapping for
        # "First National Checking" -- which would make this exact CSV
        # import succeed instead of 400, failing the test for reasons that
        # have nothing to do with the code under test. Force the "no
        # mapping" condition explicitly instead of relying on ambient
        # filesystem state.
        import quicken_importer
        monkeypatch.setattr(quicken_importer, "_account_map", lambda: {})
        csv_content = (
            "Net Worth Summary\n---\n"
            'Checking,First National Checking,"1,000.00"\n'
        )
        r = client.post(
            "/api/import/quicken",
            files={"file": ("networth.csv", csv_content, "text/csv")},
        )
        assert r.status_code == 400

    def test_import_csv_with_no_matching_accounts_400s(self, client):
        csv_content = "Net Worth Summary\n---\nSomething,Totally Unmapped Account,0.00\n"
        r = client.post(
            "/api/import/quicken",
            files={"file": ("networth.csv", csv_content, "text/csv")},
        )
        assert r.status_code == 400

    def test_import_skips_and_reports_unrecognized_mapped_account_type(self, client, monkeypatch):
        """Regression: this import path builds accounts straight from raw
        SQL, bypassing the Account model's account_type validation
        entirely — a typo in the hand-maintained, gitignored
        quicken_account_map.local.json (e.g. mapping to "pretax_401k"
        instead of the real "401k") used to import successfully and then
        be silently invisible to net worth and every retirement/Monte
        Carlo number, with nothing in the response to say so. The bad
        mapping must be skipped and named in the response, and a
        correctly-mapped account in the same file must still import."""
        import quicken_importer
        monkeypatch.setattr(quicken_importer, "_account_map", lambda: {
            "401k mistyped": ("pretax_401k", "jason"),
            "checking ok": ("checking", "joint"),
        })
        csv_content = (
            "Net Worth Summary\n---\n"
            'Retirement,401k Mistyped,"650,000.00"\n'
            'Checking,Checking OK,"1,000.00"\n'
        )
        r = client.post(
            "/api/import/quicken",
            files={"file": ("networth.csv", csv_content, "text/csv")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["accounts_created"] == 1
        assert body["accounts_skipped_invalid_type"] == [{"name": "401k Mistyped", "account_type": "pretax_401k"}]
        accounts = client.get("/api/accounts").json()
        assert not any(a["name"] == "401k Mistyped" for a in accounts)
        assert any(a["name"] == "Checking OK" for a in accounts)


class TestAnnualReport:
    def test_report_generates_pdf_with_default_row(self, client):
        r = client.get("/api/report/annual")
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF"

    def test_report_generates_pdf(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)
        r = client.get("/api/report/annual")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content[:4] == b"%PDF"


class TestSimulationEndpoints:
    """Smoke tests — full-depth calc correctness is covered in
    test_simulation_engine.py; these just verify the routes are wired up
    and return 200 with seeded data."""

    def _seed(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)

    def test_monte_carlo(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/monte-carlo?ret_age=60&ss_timing=early")
        assert r.status_code == 200
        assert "success_rate" in r.json()

    def test_monte_carlo_post_with_whatif_overrides(self, client, sample_inputs, sample_accounts):
        """Regression test (external audit 2026-09-06): the What-If
        Builder's modified assumptions used to be silently discarded when
        switching to the Monte Carlo tab. The new POST variant should
        apply the same override fields as /api/projections/whatif and
        produce a materially different result than the plain GET."""
        self._seed(client, sample_inputs, sample_accounts)
        baseline = client.get("/api/simulation/monte-carlo?ret_age=60&ss_timing=early").json()
        overridden = client.post("/api/simulation/monte-carlo", json={
            "ret_age": 60, "ss_timing": "early",
            "income_target": 500000,  # a much higher spending target should hurt success_rate
        }).json()
        assert overridden["success_rate"] <= baseline["success_rate"]

    def test_monte_carlo_post_with_no_overrides_matches_get(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        get_result = client.get("/api/simulation/monte-carlo?ret_age=60&ss_timing=early").json()
        post_result = client.post("/api/simulation/monte-carlo", json={"ret_age": 60, "ss_timing": "early"}).json()
        assert post_result["success_rate"] == get_result["success_rate"]

    def test_monte_carlo_two_age_mode(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/monte-carlo", params={"jason_ret_age": 61, "justin_ret_age": 63})
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "two_age"
        assert body["jason_ret_age"] == 61
        assert body["justin_ret_age"] == 63

    def test_monte_carlo_two_age_mode_requires_both_ages(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/monte-carlo", params={"jason_ret_age": 61})
        assert r.status_code == 400

    def test_monte_carlo_post_two_age_mode_with_whatif_overrides(self, client, sample_inputs, sample_accounts):
        """Independent review, 2026-09-08, P1: two-age mode used to only
        ever call the plain GET endpoint, silently dropping ss_timing
        and any What-If Builder overrides. The POST variant must apply
        both in two-age mode too."""
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/simulation/monte-carlo", json={
            "jason_ret_age": 61, "justin_ret_age": 63, "ss_timing": "delayed",
            "income_target": 500000,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "two_age"
        assert body["ss_timing"] == "delayed"

    def test_monte_carlo_post_two_age_requires_both_ages(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/simulation/monte-carlo", json={"jason_ret_age": 61})
        assert r.status_code == 400


    def test_swr(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/swr?ret_age=60&ss_timing=early")
        assert r.status_code == 200

    def test_swr_two_age_mode_get(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/swr", params={"jason_ret_age": 61, "justin_ret_age": 63})
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "two_age"
        assert body["jason_ret_age"] == 61
        assert body["justin_ret_age"] == 63

    def test_swr_two_age_mode_post_with_whatif_overrides(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/simulation/swr", json={
            "jason_ret_age": 61, "justin_ret_age": 63, "ss_timing": "delayed",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "two_age"
        assert body["ss_timing"] == "delayed"

    def test_swr_two_age_mode_requires_both_ages(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/swr", params={"jason_ret_age": 61})
        assert r.status_code == 400

    def test_swr_batch(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/swr-batch")
        assert r.status_code == 200
        assert set(str(a) for a in range(55, 68)) <= set(r.json()["swr"].keys())

    def test_stress_tests(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/stress-tests?ret_age=55&ss_timing=early")
        assert r.status_code == 200

    def test_stress_tests_post_with_whatif_overrides(self, client, sample_inputs, sample_accounts):
        """Same What-If-carries-through fix as Monte Carlo above, applied
        to Historical Stress."""
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/simulation/stress-tests", json={
            "ret_age": 55, "ss_timing": "early", "healthcare_pre": 90000,
        })
        assert r.status_code == 200
        assert "base" in r.json()["scenarios"]

    def test_stress_tests_post_with_no_overrides_matches_get(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        get_result = client.get("/api/simulation/stress-tests?ret_age=55&ss_timing=early").json()
        post_result = client.post("/api/simulation/stress-tests", json={"ret_age": 55, "ss_timing": "early"}).json()
        assert post_result["scenarios"]["base"]["final_balance"] == get_result["scenarios"]["base"]["final_balance"]

    def test_stress_tests_two_age_mode(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/stress-tests", params={"jason_ret_age": 61, "justin_ret_age": 63})
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "two_age"
        assert "base" in body["scenarios"]

    def test_stress_tests_two_age_mode_requires_both_ages(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/stress-tests", params={"justin_ret_age": 63})
        assert r.status_code == 400

    def test_stress_tests_post_two_age_mode_with_whatif_overrides(self, client, sample_inputs, sample_accounts):
        """Same fix as Monte Carlo's POST variant above, applied to
        Historical Stress."""
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/simulation/stress-tests", json={
            "jason_ret_age": 61, "justin_ret_age": 63, "ss_timing": "delayed",
            "healthcare_pre": 90000,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "two_age"
        assert body["ss_timing"] == "delayed"

    def test_stress_tests_post_two_age_requires_both_ages(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/simulation/stress-tests", json={"justin_ret_age": 63})
        assert r.status_code == 400

    def test_sequence_risk(self, client, sample_inputs, sample_accounts):
        """Regression test for the endpoint that 500'd at every age due to
        result["base"] vs result["scenarios"]["base"]."""
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/sequence-risk?ret_age=55&ss_timing=early")
        assert r.status_code == 200
        assert r.json()["base"] is not None

    def test_roth_conversion(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/roth-conversion?ret_age=60&ss_timing=early")
        assert r.status_code == 200

    def test_roth_conversion_two_age_mode_get(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/roth-conversion", params={"jason_ret_age": 61, "justin_ret_age": 63})
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "two_age"
        assert body["jason_ret_age"] == 61
        assert body["justin_ret_age"] == 63

    def test_roth_conversion_two_age_mode_post_with_whatif_overrides(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/simulation/roth-conversion", json={
            "jason_ret_age": 61, "justin_ret_age": 63, "ss_timing": "delayed",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "two_age"
        assert body["ss_timing"] == "delayed"

    def test_roth_conversion_two_age_mode_requires_both_ages(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/roth-conversion", params={"jason_ret_age": 61})
        assert r.status_code == 400

    def test_tax_efficiency(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/tax-efficiency?ret_age=60&ss_timing=early")
        assert r.status_code == 200

    def test_tax_efficiency_two_age_mode(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/tax-efficiency",
                        params={"jason_ret_age": 61, "justin_ret_age": 63})
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "two_age"
        assert body["jason_ret_age"] == 61
        assert body["justin_ret_age"] == 63

    def test_tax_efficiency_two_age_mode_requires_both_ages(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/tax-efficiency", params={"jason_ret_age": 61})
        assert r.status_code == 400

    def test_contribution_sensitivity(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/simulation/contribution-sensitivity?ret_age=60")
        assert r.status_code == 200

    def test_retirement_sensitivity(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/projections/retirement-sensitivity")
        assert r.status_code == 200
        ages = {s["ret_age"] for s in r.json()["sensitivity"]}
        assert ages == set(range(55, 68))

    def test_income_sources(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/retirement/income-sources?ret_age=60&ss_timing=early")
        assert r.status_code == 200

    def test_income_sources_returns_chart_for_ages_outside_default_three(self, client, sample_inputs, sample_accounts):
        """Regression test — the Monte Carlo/Historical Stress tabs offer
        the full 55-67 age range, but this endpoint called
        run_retirement_projection() with no ret_ages override, defaulting
        to [55, 60, 65] — any other age silently returned {"error": ...}
        with no "chart" key, leaving the Income Sources by Year chart
        empty. Reported: "nothing appears in monte carlo Income Sources
        by Year.\""""
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/retirement/income-sources?ret_age=58&ss_timing=early")
        assert r.status_code == 200
        body = r.json()
        assert "error" not in body
        assert len(body["chart"]) > 0

    def test_whatif(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/projections/whatif", json={"salary_growth_pct": 0.03})
        assert r.status_code == 200
        assert "scenarios" in r.json()

    def test_whatif_pension_and_ss_multipliers(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/projections/whatif", json={"pension_mult": 1.1, "ss_mult": 0.9})
        assert r.status_code == 200
        assert "scenarios" in r.json()

    def test_whatif_with_default_row_and_no_overrides(self, client):
        r = client.post("/api/projections/whatif", json={})
        assert r.status_code == 200
        assert "scenarios" in r.json()

    def test_whatif_includes_scenario_for_requested_age_outside_default_three(self, client, sample_inputs, sample_accounts):
        """Same class of bug as the income-sources gap above — the
        What-If Builder's slider covers 55-67, but this endpoint never
        passed ret_age through to run_retirement_projection() at all, so
        "Impact on Retire at X" silently found nothing for any age other
        than 55/60/65."""
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/projections/whatif", json={"ret_age": 58})
        assert r.status_code == 200
        labels = {s["label"] for s in r.json()["scenarios"]}
        assert "age_58_early" in labels
        # The fixed 55/60/65 baseline cards must still be present too.
        assert {"age_55_early", "age_60_early", "age_65_early"}.issubset(labels)

    def test_retirement_projection_covers_full_55_to_67_range(self, client, sample_inputs, sample_accounts):
        """Regression test — this endpoint is also WhatIf.jsx's baseline
        for its full 55-67 slider; a narrower range here (previously
        [55,56,57,58,59,60,65]) silently broke the base-vs-result
        comparison for any age outside that original set."""
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/projections/retirement")
        ages = {s["retirement_age"] for s in r.json()["scenarios"]}
        assert ages == set(range(55, 68))


class TestLifeEventsAffectRealProjectionAndSimulation:
    """End-to-end: a life event actually moves the numbers on
    /api/projections/retirement and /api/simulation/monte-carlo — and
    toggling it off via included_in_projection removes that effect, while
    the isolated overlay (GET /api/life-events) is unaffected either way."""

    def _seed(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)

    def _portfolio_at_60(self, client):
        r = client.get("/api/projections/retirement")
        scenario = next(s for s in r.json()["scenarios"] if s["label"] == "age_60_early")
        return scenario["portfolio_at_retirement"]

    def test_included_event_moves_real_retirement_projection(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        baseline = self._portfolio_at_60(client)
        client.post("/api/life-events", json={
            "name": "Inheritance", "event_type": "windfall", "event_year": CURRENT_YEAR + 1,
            "one_time_cash_delta": 100000, "monthly_cash_flow_delta": 0, "duration_months": 0,
        })
        with_event = self._portfolio_at_60(client)
        assert with_event > baseline

    def test_toggled_off_event_does_not_move_real_retirement_projection(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        baseline = self._portfolio_at_60(client)
        created = client.post("/api/life-events", json={
            "name": "Inheritance (maybe)", "event_type": "windfall", "event_year": CURRENT_YEAR + 1,
            "one_time_cash_delta": 100000, "monthly_cash_flow_delta": 0, "duration_months": 0,
        })
        event_id = created.json()["id"]
        client.patch(f"/api/life-events/{event_id}/toggle", json={"included_in_projection": False})
        after_toggle_off = self._portfolio_at_60(client)
        assert after_toggle_off == baseline
        # Turning it back on restores the effect.
        client.patch(f"/api/life-events/{event_id}/toggle", json={"included_in_projection": True})
        after_toggle_on = self._portfolio_at_60(client)
        assert after_toggle_on > baseline

    def test_toggled_off_event_still_shows_in_isolated_overlay(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        created = client.post("/api/life-events", json={
            "name": "Inheritance (maybe)", "event_type": "windfall", "event_year": CURRENT_YEAR + 1,
            "one_time_cash_delta": 100000, "monthly_cash_flow_delta": 0, "duration_months": 0,
        })
        event_id = created.json()["id"]
        client.patch(f"/api/life-events/{event_id}/toggle", json={"included_in_projection": False})
        overlay = client.get("/api/life-events")
        event = next(e for e in overlay.json()["events"] if e["id"] == event_id)
        assert event["included_in_projection"] is False
        assert event["retirement_impact"] != 0

    def test_included_event_moves_monte_carlo(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        baseline = client.get("/api/simulation/monte-carlo?ret_age=60&ss_timing=early").json()
        client.post("/api/life-events", json={
            "name": "Big windfall", "event_type": "windfall", "event_year": CURRENT_YEAR + 1,
            "one_time_cash_delta": 500000, "monthly_cash_flow_delta": 0, "duration_months": 0,
        })
        with_event = client.get("/api/simulation/monte-carlo?ret_age=60&ss_timing=early").json()
        assert with_event["portfolio_at_retirement"] > baseline["portfolio_at_retirement"]


class TestSurplusAllocationsAffectRealProjectionAndSimulation:
    """End-to-end: a surplus allocation on one of the two retirement-relevant
    goals actually moves the numbers on /api/projections/retirement and
    /api/simulation/monte-carlo, while a non-retirement goal (and the
    isolated GET /api/surplus-allocations overlay) is unaffected."""

    def _seed(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)

    def _portfolio_at_60(self, client):
        r = client.get("/api/projections/retirement")
        scenario = next(s for s in r.json()["scenarios"] if s["label"] == "age_60_early")
        return scenario["portfolio_at_retirement"]

    def test_retirement_contributions_goal_moves_real_retirement_projection(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        baseline = self._portfolio_at_60(client)
        client.put("/api/surplus-allocations/Retirement contributions", json={
            "goal": "Retirement contributions", "monthly_amount": 500, "notes": None,
        })
        with_alloc = self._portfolio_at_60(client)
        assert with_alloc > baseline

    def test_taxable_investing_goal_moves_real_retirement_projection(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        baseline = self._portfolio_at_60(client)
        client.put("/api/surplus-allocations/Taxable investing", json={
            "goal": "Taxable investing", "monthly_amount": 300, "notes": None,
        })
        with_alloc = self._portfolio_at_60(client)
        assert with_alloc > baseline

    def test_non_retirement_goal_does_not_move_real_retirement_projection(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        baseline = self._portfolio_at_60(client)
        client.put("/api/surplus-allocations/Emergency reserve", json={
            "goal": "Emergency reserve", "monthly_amount": 1000, "notes": None,
        })
        after = self._portfolio_at_60(client)
        assert after == baseline

    def test_surplus_allocation_moves_monte_carlo(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        baseline = client.get("/api/simulation/monte-carlo?ret_age=60&ss_timing=early").json()
        client.put("/api/surplus-allocations/Retirement contributions", json={
            "goal": "Retirement contributions", "monthly_amount": 800, "notes": None,
        })
        with_alloc = client.get("/api/simulation/monte-carlo?ret_age=60&ss_timing=early").json()
        assert with_alloc["portfolio_at_retirement"] > baseline["portfolio_at_retirement"]

    def test_surplus_allocations_overlay_unaffected_by_goal_filtering(self, client, sample_inputs, sample_accounts):
        """GET /api/surplus-allocations is still the user's plain tracking
        view — it must keep returning every goal regardless of which ones
        actually move the projection."""
        self._seed(client, sample_inputs, sample_accounts)
        client.put("/api/surplus-allocations/Emergency reserve", json={
            "goal": "Emergency reserve", "monthly_amount": 1000, "notes": None,
        })
        client.put("/api/surplus-allocations/Retirement contributions", json={
            "goal": "Retirement contributions", "monthly_amount": 500, "notes": None,
        })
        overlay = client.get("/api/surplus-allocations").json()
        goals = {row["goal"] for row in overlay["allocations"]}
        assert goals == {"Emergency reserve", "Retirement contributions"}


class TestPerKidEducationSurplusGoalsFeedEducationAndKidsProjections:
    """The per-kid education-funding surplus_allocations goals (one per
    kids-table row, "Education funding - kid_<id>" — originally two fixed
    keys "Education funding - Abby"/"Cooper" before kids-variable-count)
    must move /api/projections/education and /api/projections/kids for
    that specific kid only, while staying excluded from
    /api/projections/retirement (and Monte Carlo) — regression coverage
    for _get_relevant_surplus_allocations continuing to exclude these
    goals exactly as the old single "Education funding" goal was
    excluded."""

    def _seed(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)
        kid_a = client.post("/api/kids", json={"name": "Kid A", "age": 10, "monthly_529": 100}).json()
        kid_b = client.post("/api/kids", json={"name": "Kid B", "age": 8, "monthly_529": 100}).json()
        return kid_a["id"], kid_b["id"]

    def _from_education(self, client, owner):
        r = client.get("/api/projections/education")
        return next(g for g in r.json()["goals"] if g["child"] == owner)

    def _from_kids(self, client, owner):
        r = client.get("/api/projections/kids")
        return next(k for k in r.json()["kids"] if k["child"] == owner)

    def _portfolio_at_60(self, client):
        r = client.get("/api/projections/retirement")
        scenario = next(s for s in r.json()["scenarios"] if s["label"] == "age_60_early")
        return scenario["portfolio_at_retirement"]

    def test_abby_goal_raises_abby_education_projection_only(self, client, sample_inputs, sample_accounts):
        id_a, id_b = self._seed(client, sample_inputs, sample_accounts)
        owner_a, owner_b = f"kid_{id_a}", f"kid_{id_b}"
        baseline_a = self._from_education(client, owner_a)
        baseline_b = self._from_education(client, owner_b)
        client.put(f"/api/surplus-allocations/Education funding - {owner_a}", json={
            "goal": f"Education funding - {owner_a}", "monthly_amount": 250, "notes": None,
        })
        with_goal_a = self._from_education(client, owner_a)
        with_goal_b = self._from_education(client, owner_b)
        assert with_goal_a["projected_529_at_college"] > baseline_a["projected_529_at_college"]
        assert with_goal_b["projected_529_at_college"] == baseline_b["projected_529_at_college"]

    def test_cooper_goal_raises_cooper_kids_projection_only(self, client, sample_inputs, sample_accounts):
        id_a, id_b = self._seed(client, sample_inputs, sample_accounts)
        owner_a, owner_b = f"kid_{id_a}", f"kid_{id_b}"
        baseline = client.get("/api/projections/kids").json()["kids"]
        baseline_a = next(k for k in baseline if k["child"] == owner_a)
        baseline_b = next(k for k in baseline if k["child"] == owner_b)
        client.put(f"/api/surplus-allocations/Education funding - {owner_b}", json={
            "goal": f"Education funding - {owner_b}", "monthly_amount": 150, "notes": None,
        })
        after = client.get("/api/projections/kids").json()["kids"]
        after_a = next(k for k in after if k["child"] == owner_a)
        after_b = next(k for k in after if k["child"] == owner_b)
        assert after_b["529"]["at_18"] > baseline_b["529"]["at_18"]
        assert after_a["529"]["at_18"] == baseline_a["529"]["at_18"]

    def test_education_surplus_goals_excluded_from_retirement_projection(self, client, sample_inputs, sample_accounts):
        """Regression test proving run_retirement_projection's output is
        completely unaffected by these two new goals having nonzero
        amounts — _get_relevant_surplus_allocations' explicit goal IN (...)
        filter must not accidentally pick them up."""
        id_a, id_b = self._seed(client, sample_inputs, sample_accounts)
        owner_a, owner_b = f"kid_{id_a}", f"kid_{id_b}"
        baseline = self._portfolio_at_60(client)
        client.put(f"/api/surplus-allocations/Education funding - {owner_a}", json={
            "goal": f"Education funding - {owner_a}", "monthly_amount": 1000, "notes": None,
        })
        client.put(f"/api/surplus-allocations/Education funding - {owner_b}", json={
            "goal": f"Education funding - {owner_b}", "monthly_amount": 1000, "notes": None,
        })
        after = self._portfolio_at_60(client)
        assert after == baseline

    def test_surplus_allocations_overlay_still_shows_both_new_goals(self, client, sample_inputs, sample_accounts):
        """GET /api/surplus-allocations (the plain tracking view) must still
        show these goals regardless of them being excluded from the
        retirement-projection wiring."""
        id_a, id_b = self._seed(client, sample_inputs, sample_accounts)
        owner_a = f"kid_{id_a}"
        client.put(f"/api/surplus-allocations/Education funding - {owner_a}", json={
            "goal": f"Education funding - {owner_a}", "monthly_amount": 250, "notes": None,
        })
        overlay = client.get("/api/surplus-allocations").json()
        goals = {row["goal"] for row in overlay["allocations"]}
        assert f"Education funding - {owner_a}" in goals


class TestDebtRecommendationEndpoints:
    def test_recommendation_with_no_debt(self, client):
        r = client.get("/api/debts/recommendation")
        assert r.status_code == 200
        assert r.json()["has_debt"] is False

    def test_recommendation_with_debt(self, client):
        client.post("/api/accounts", json={
            "name": "Test Card", "account_type": "credit_card", "owner": "joint",
            "institution": "", "balance": 5000, "notes": None,
            "interest_rate": 0.22, "minimum_payment": 150,
        })
        r = client.get("/api/debts/recommendation?extra_monthly=200")
        assert r.status_code == 200
        data = r.json()
        assert data["has_debt"] is True
        assert data["strategy"] in ("avalanche", "snowball")
        assert data["reason"]
        assert data["debt_free_date"] is not None

    def test_recommendation_flags_negative_amortization(self, client):
        client.post("/api/accounts", json={
            "name": "Trap Card", "account_type": "credit_card", "owner": "joint",
            "institution": "", "balance": 10000, "notes": None,
            "interest_rate": 0.30, "minimum_payment": 100,
        })
        r = client.get("/api/debts/recommendation").json()
        assert len(r["negative_amortization_debts"]) == 1

    def test_term_months_persists_on_account(self, client):
        created = client.post("/api/accounts", json={
            "name": "Term Test", "account_type": "car_loan", "owner": "joint",
            "institution": "", "balance": 15000, "notes": None,
            "interest_rate": 0.06, "minimum_payment": 300, "term_months": 48,
        }).json()
        listed = client.get("/api/accounts").json()
        found = next(a for a in listed if a["id"] == created["id"])
        assert found["term_months"] == 48

    def test_suggest_minimum_payment_endpoint(self, client):
        r = client.post("/api/debts/suggest-minimum-payment", json={
            "balance": 20000, "interest_rate": 0.055, "term_months": 60,
        })
        assert r.status_code == 200
        assert r.json()["suggested_minimum_payment"] == 382

    def test_high_interest_debt_payoff_surplus_goal_augments_extra_monthly(self, client):
        """The "High-interest debt payoff" surplus_allocations goal must add
        to whatever extra_monthly the user manually enters on the Debt page
        — additively, mirroring how debt-targeted life events feed in
        one-time lump sums (see _get_debt_payoff_surplus_monthly)."""
        client.post("/api/accounts", json={
            "name": "Surplus Card", "account_type": "credit_card", "owner": "joint",
            "institution": "", "balance": 5000, "notes": None,
            "interest_rate": 0.22, "minimum_payment": 150,
        })
        baseline = client.get("/api/debts/recommendation?extra_monthly=50").json()
        client.put("/api/surplus-allocations/High-interest debt payoff", json={
            "goal": "High-interest debt payoff", "monthly_amount": 300, "notes": None,
        })
        boosted = client.get("/api/debts/recommendation?extra_monthly=50").json()
        assert boosted["extra_monthly"] == 350
        assert boosted["manual_extra_monthly"] == 50
        assert boosted["surplus_debt_payoff_monthly"] == 300
        # A bigger effective extra payment should never take longer to pay off.
        assert boosted["months_to_debt_free"] <= baseline["months_to_debt_free"]

        # A goal that ISN'T "High-interest debt payoff" must not leak in.
        client.put("/api/surplus-allocations/Retirement contributions", json={
            "goal": "Retirement contributions", "monthly_amount": 900, "notes": None,
        })
        unaffected = client.get("/api/debts/recommendation?extra_monthly=50").json()
        assert unaffected["surplus_debt_payoff_monthly"] == 300

    def test_payoff_plan_also_reflects_surplus_debt_payoff_goal(self, client):
        client.post("/api/accounts", json={
            "name": "Plan Card", "account_type": "credit_card", "owner": "joint",
            "institution": "", "balance": 5000, "notes": None,
            "interest_rate": 0.22, "minimum_payment": 150,
        })
        # Establish the "true 350" baseline BEFORE the surplus goal exists,
        # so this doesn't also pick up the goal on top of itself.
        no_surplus = client.get("/api/debts/payoff-plan?extra_monthly=350").json()
        client.put("/api/surplus-allocations/High-interest debt payoff", json={
            "goal": "High-interest debt payoff", "monthly_amount": 300, "notes": None,
        })
        plan = client.get("/api/debts/payoff-plan?extra_monthly=50").json()
        assert plan["avalanche"]["months_to_debt_free"] == no_surplus["avalanche"]["months_to_debt_free"]


class TestDebtEndpoints:
    def test_payoff_plan_with_no_debt(self, client):
        r = client.get("/api/debts/payoff-plan")
        assert r.status_code == 200
        assert r.json()["has_debt"] is False

    def test_payoff_plan_with_debt_accounts(self, client):
        client.post("/api/accounts", json={
            "name": "Test Card", "account_type": "credit_card", "owner": "joint",
            "institution": "", "balance": 5000, "notes": None,
            "interest_rate": 0.22, "minimum_payment": 150,
        })
        r = client.get("/api/debts/payoff-plan?extra_monthly=200")
        assert r.status_code == 200
        data = r.json()
        assert data["has_debt"] is True
        assert data["total_balance"] == 5000

    def test_account_interest_rate_and_minimum_payment_persist(self, client):
        created = client.post("/api/accounts", json={
            "name": "Persist Test", "account_type": "student_loan", "owner": "joint",
            "institution": "", "balance": 20000, "notes": None,
            "interest_rate": 0.055, "minimum_payment": 250,
        }).json()
        listed = client.get("/api/accounts").json()
        found = next(a for a in listed if a["id"] == created["id"])
        assert found["interest_rate"] == 0.055
        assert found["minimum_payment"] == 250

    def test_credit_card_payoff_endpoint(self, client):
        r = client.post("/api/debts/credit-card-payoff", json={
            "balance": 5000, "apr": 0.22, "monthly_payment": 150, "extra": 100,
        })
        assert r.status_code == 200
        assert r.json()["months_saved"] > 0

    def test_refinance_analysis_endpoint(self, client):
        r = client.post("/api/debts/refinance-analysis", json={
            "balance": 300000, "current_rate": 0.07, "new_rate": 0.055,
            "term_years": 30, "closing_costs": 5000,
        })
        assert r.status_code == 200
        assert r.json()["worth_it"] is True

    def test_debt_vs_invest_endpoint(self, client):
        r = client.post("/api/debts/debt-vs-invest", json={
            "debt_rate": 0.22, "expected_return": 0.07, "employer_match_pct": 3,
        })
        assert r.status_code == 200
        assert r.json()["recommendation"] == "capture_match_then_pay_debt"

    def test_net_worth_treats_new_debt_types_as_liabilities(self, client):
        """Regression guard: adding credit_card/student_loan/car_loan/
        personal_loan as new liability types must not let their balances
        get miscounted as assets."""
        client.post("/api/accounts", json={
            "name": "Test Card", "account_type": "credit_card", "owner": "joint",
            "institution": "", "balance": 3000, "notes": None,
        })
        r = client.get("/api/net-worth").json()
        assert r["liabilities"] == 3000


class TestDebtTargetedLifeEvents:
    """Validation for POST /api/life-events' target_debt_account_id field,
    and the combined effect on /api/debts/payoff-plan and /recommendation."""

    def _make_debt(self, client, balance=5000, interest_rate=0.06, minimum_payment=500):
        return client.post("/api/accounts", json={
            "name": "Mortgage Test", "account_type": "mortgage", "owner": "joint",
            "institution": "", "balance": balance, "notes": None,
            "interest_rate": interest_rate, "minimum_payment": minimum_payment,
        }).json()["id"]

    def test_positive_one_time_cash_delta_with_debt_target_rejected(self, client):
        debt_id = self._make_debt(client)
        r = client.post("/api/life-events", json={
            "name": "Bad event", "event_type": "windfall", "event_year": 2028,
            "one_time_cash_delta": 5000, "monthly_cash_flow_delta": 0, "duration_months": 0,
            "target_debt_account_id": debt_id,
        })
        assert r.status_code == 400
        assert "not income" in r.json()["detail"] or "positive" in r.json()["detail"].lower() or "zero or negative" in r.json()["detail"]

    def test_non_debt_account_type_rejected(self, client):
        checking_id = client.post("/api/accounts", json={
            "name": "Checking", "account_type": "checking", "owner": "joint",
            "institution": "", "balance": 1000, "notes": None,
        }).json()["id"]
        r = client.post("/api/life-events", json={
            "name": "Bad target", "event_type": "other", "event_year": 2028,
            "one_time_cash_delta": -5000, "monthly_cash_flow_delta": 0, "duration_months": 0,
            "target_debt_account_id": checking_id,
        })
        assert r.status_code == 400

    def test_unknown_account_id_rejected(self, client):
        r = client.post("/api/life-events", json={
            "name": "Bad target", "event_type": "other", "event_year": 2028,
            "one_time_cash_delta": -5000, "monthly_cash_flow_delta": 0, "duration_months": 0,
            "target_debt_account_id": 999999,
        })
        assert r.status_code == 400

    def test_valid_debt_targeted_event_accepted_and_monthly_delta_zeroed(self, client):
        debt_id = self._make_debt(client)
        r = client.post("/api/life-events", json={
            "name": "Mortgage paydown", "event_type": "windfall", "event_year": 2028,
            "one_time_cash_delta": -50000, "monthly_cash_flow_delta": 250, "duration_months": 0,
            "target_debt_account_id": debt_id,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["target_debt_account_id"] == debt_id
        # monthly_cash_flow_delta is silently zeroed when a debt target is
        # set — this feature models a one-time lump sum only.
        assert body["monthly_cash_flow_delta"] == 0

    def test_payoff_plan_reflects_debt_targeted_event(self, client):
        debt_id = self._make_debt(client, balance=20000, interest_rate=0.06, minimum_payment=500)
        client.post("/api/life-events", json={
            "name": "Rental sale paydown", "event_type": "windfall", "event_year": CURRENT_YEAR + 1,
            "one_time_cash_delta": -10000, "monthly_cash_flow_delta": 0, "duration_months": 0,
            "target_debt_account_id": debt_id,
        })
        r = client.get("/api/debts/payoff-plan")
        assert r.status_code == 200
        data = r.json()
        effects = data["one_time_payment_effect"]["avalanche"]
        assert len(effects) == 1
        assert effects[0]["account_id"] == debt_id
        assert effects[0]["new_payoff_month"] <= effects[0]["original_payoff_month"]

    def test_recommendation_reflects_debt_targeted_event(self, client):
        debt_id = self._make_debt(client, balance=20000, interest_rate=0.06, minimum_payment=500)
        client.post("/api/life-events", json={
            "name": "Rental sale paydown", "event_type": "windfall", "event_year": CURRENT_YEAR + 1,
            "one_time_cash_delta": -10000, "monthly_cash_flow_delta": 0, "duration_months": 0,
            "target_debt_account_id": debt_id,
        })
        r = client.get("/api/debts/recommendation")
        assert r.status_code == 200
        data = r.json()
        assert len(data["life_event_debt_payments"]) == 1
        assert data["life_event_debt_payments"][0]["account_id"] == debt_id

    def test_toggled_off_debt_event_has_no_effect_on_payoff_plan(self, client):
        debt_id = self._make_debt(client, balance=20000, interest_rate=0.06, minimum_payment=500)
        created = client.post("/api/life-events", json={
            "name": "Rental sale paydown", "event_type": "windfall", "event_year": CURRENT_YEAR + 1,
            "one_time_cash_delta": -10000, "monthly_cash_flow_delta": 0, "duration_months": 0,
            "target_debt_account_id": debt_id,
        })
        event_id = created.json()["id"]
        client.patch(f"/api/life-events/{event_id}/toggle", json={"included_in_projection": False})
        r = client.get("/api/debts/payoff-plan").json()
        assert r["one_time_payment_effect"]["avalanche"] == []


class TestRetirementToolsEndpoints:
    def test_rmd_planning_no_pretax_balance(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        r = client.get("/api/retirement-tools/rmd-planning")
        assert r.status_code == 200
        assert r.json()["has_pretax_balance"] is False

    def test_rmd_planning_with_401k_balance(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        client.post("/api/accounts", json={
            "name": "Test 401k", "account_type": "401k", "owner": "jason",
            "institution": "", "balance": 500000, "notes": None,
        })
        r = client.get("/api/retirement-tools/rmd-planning")
        assert r.status_code == 200
        data = r.json()
        assert data["has_pretax_balance"] is True
        # With only a $500k 401k and no other assets, sample_inputs' default
        # ret_age=60 spend-down (retirement_income_today_dollars=100000)
        # actually depletes the pretax bucket by RMD age (75, given
        # jason_age=50) in the real projection — so first_rmd_amount is
        # correctly 0 here, not the naive-compounded-balance figure this
        # endpoint used to report before the fix for external audit
        # 2026-09-07 (see TestRunRmdPlanning.test_ignores_real_drawdown in
        # test_retirement_tools_engine.py for the fix's own direct test).
        assert data["first_rmd_amount"] >= 0
        assert data["projected_balance_at_start_age"] >= 0

    def test_rmd_planning_reflects_real_drawdown_via_ret_age_param(self, client, sample_inputs):
        """Retiring later (closer to RMD age) with a big enough balance
        should leave real money left at RMD age and a positive first RMD —
        confirms the ret_age query param actually reaches the engine."""
        _seed_planning_inputs(client, sample_inputs)
        client.post("/api/accounts", json={
            "name": "Big 401k", "account_type": "401k", "owner": "jason",
            "institution": "", "balance": 5_000_000, "notes": None,
        })
        r = client.get("/api/retirement-tools/rmd-planning?ret_age=65")
        assert r.status_code == 200
        data = r.json()
        assert data["has_pretax_balance"] is True
        assert data["first_rmd_amount"] > 0

    def test_pension_vs_lump_sum_endpoint(self, client):
        r = client.post("/api/retirement-tools/pension-vs-lump-sum", json={
            "monthly_pension": 3000, "lump_sum": 400000,
            "current_age": 55, "pension_start_age": 65,
        })
        assert r.status_code == 200
        assert r.json()["favors"] in ("pension", "lump_sum")

    def test_backdoor_roth_endpoint(self, client):
        r = client.post("/api/retirement-tools/backdoor-roth", json={
            "magi": 300000, "existing_traditional_ira_balance": 50000,
            "existing_traditional_ira_basis": 0, "planned_contribution": 7000,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["needs_backdoor"] is True
        assert data["pro_rata_applies"] is True

    def test_qcd_endpoint(self, client):
        r = client.post("/api/retirement-tools/qcd", json={
            "age": 75, "ira_balance": 500000, "rmd_amount": 20000, "desired_qcd_amount": 15000,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["eligible"] is True
        assert data["qcd_amount"] == 15000

    def test_qcd_endpoint_not_eligible(self, client):
        r = client.post("/api/retirement-tools/qcd", json={
            "age": 60, "ira_balance": 500000, "desired_qcd_amount": 15000,
        })
        assert r.status_code == 200
        assert r.json()["eligible"] is False

    def test_hsa_strategy_endpoint(self, client):
        r = client.post("/api/retirement-tools/hsa-strategy", json={
            "oop_expense_this_year": 2000, "years_to_delay": 10,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["has_expense"] is True
        assert data["extra_value_from_delaying"] > 0


class TestSurvivorScenarioEndpoint:
    def test_survivor_scenario_endpoint(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)
        r = client.get("/api/simulation/survivor-scenario?ret_age=60&deceased=jason&death_age=65")
        assert r.status_code == 200
        data = r.json()
        assert data["has_data"] is True
        assert data["deceased"] == "jason"

    def test_survivor_scenario_with_default_planning_inputs_row(self, client):
        # init_db always seeds a default planning_inputs row (id=1), so
        # "no inputs set" is unreachable in practice — this hits that
        # default-row path rather than expecting a 400.
        r = client.get("/api/simulation/survivor-scenario")
        assert r.status_code == 200

    def test_survivor_scenario_two_age_mode(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)
        r = client.get("/api/simulation/survivor-scenario",
                        params={"deceased": "jason", "death_age": 65,
                                "jason_ret_age": 61, "justin_ret_age": 63})
        assert r.status_code == 200
        data = r.json()
        assert data["mode"] == "two_age"
        assert data["jason_ret_age"] == 61
        assert data["justin_ret_age"] == 63

    def test_survivor_scenario_two_age_mode_requires_both_ages(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)
        r = client.get("/api/simulation/survivor-scenario", params={"jason_ret_age": 61})
        assert r.status_code == 400


class TestRentalAnalysisEndpoint:
    def test_no_rental_key_configured(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        r = client.get("/api/rental/analysis")
        assert r.status_code == 200
        assert r.json()["has_data"] is False

    def test_rental_analysis_with_configured_property(self, client, sample_inputs):
        _seed_planning_inputs(client, {**sample_inputs, "rental_property_key": "Rental"})
        client.post("/api/accounts", json={
            "name": "Rental Unit", "account_type": "real_estate", "owner": "joint",
            "institution": "", "balance": 200000, "notes": None,
            "monthly_rental_income": 2000, "monthly_rental_expenses": 500,
        })
        r = client.get("/api/rental/analysis")
        assert r.status_code == 200
        data = r.json()
        assert data["has_data"] is True
        assert data["annual_cash_flow"] == 18000


class TestEmergencyFundEndpoint:
    def test_emergency_fund_endpoint(self, client, sample_inputs):
        _seed_planning_inputs(client, {**sample_inputs, "current_monthly_expenses": 5000})
        client.post("/api/accounts", json={
            "name": "Checking", "account_type": "checking", "owner": "joint",
            "institution": "", "balance": 20000, "notes": None,
        })
        r = client.get("/api/emergency-fund")
        assert r.status_code == 200
        data = r.json()
        assert data["has_data"] is True
        assert data["liquid_assets"] == 20000

    def test_emergency_fund_no_expenses_configured(self, client, sample_inputs):
        _seed_planning_inputs(client, {**sample_inputs, "current_monthly_expenses": 0})
        r = client.get("/api/emergency-fund")
        assert r.status_code == 200
        assert r.json()["has_data"] is False


class TestEstateEndpoints:
    def test_tax_exposure_endpoint(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        r = client.get("/api/estate/tax-exposure")
        assert r.status_code == 200
        data = r.json()
        assert "exposure" in data
        assert "recommendation" in data

    def test_tax_exposure_individual_filing(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        r = client.get("/api/estate/tax-exposure?filing_as_couple=false")
        assert r.status_code == 200
        assert r.json()["filing_as_couple"] is False


class TestAllocationEndpoints:
    def test_allocation_analysis_no_investable_accounts(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        r = client.get("/api/allocation/analysis")
        assert r.status_code == 200
        assert r.json()["has_data"] is False

    def test_allocation_analysis_with_holdings(self, client, sample_inputs):
        _seed_planning_inputs(client, sample_inputs)
        client.post("/api/accounts", json={
            "name": "Test 401k", "account_type": "401k", "owner": "jason",
            "institution": "", "balance": 200000, "notes": None,
            "stock_allocation_pct": 100,
        })
        r = client.get("/api/allocation/analysis")
        assert r.status_code == 200
        data = r.json()
        assert data["has_data"] is True
        assert data["current_stock_pct"] == 100.0

    def test_fee_analysis_no_fee_data(self, client):
        r = client.get("/api/allocation/fees")
        assert r.status_code == 200
        assert r.json()["has_fee_data"] is False

    def test_fee_analysis_with_expense_ratio(self, client):
        client.post("/api/accounts", json={
            "name": "High Fee Fund", "account_type": "401k", "owner": "jason",
            "institution": "", "balance": 200000, "notes": None,
            "expense_ratio": 0.01,
        })
        r = client.get("/api/allocation/fees")
        assert r.status_code == 200
        data = r.json()
        assert data["has_fee_data"] is True
        assert data["total_annual_fee_dollars"] == 2000

    def test_concentration_risk_endpoint(self, client):
        client.post("/api/accounts", json={
            "name": "Concentrated Stock", "account_type": "taxable", "owner": "jason",
            "institution": "", "balance": 200000, "notes": None,
        })
        client.post("/api/accounts", json={
            "name": "Other Fund", "account_type": "401k", "owner": "jason",
            "institution": "", "balance": 50000, "notes": None,
        })
        r = client.get("/api/allocation/concentration")
        assert r.status_code == 200
        data = r.json()
        assert data["has_data"] is True
        assert len(data["flagged_positions"]) >= 1


class TestAuthEndpoints:
    def test_status_disabled_after_setup_skipped(self, client):
        """conftest.py forces APP_PASSPHRASE='' AND simulates first-run
        setup already having been skipped (see _skip_first_run_auth_setup)
        for the whole suite, so this is the state every other test in this
        file runs under too — confirming auth is a no-op once the user has
        explicitly opted out, not by default on a fresh clone (see the
        TestFirstRunSetup tests below for that case)."""
        r = client.get("/api/auth/status")
        assert r.status_code == 200
        data = r.json()
        assert data["auth_enabled"] is False
        assert data["authenticated"] is True
        assert data["setup_required"] is False

    def test_setup_required_blocks_other_routes_on_a_fresh_install(self, client, monkeypatch, tmp_path):
        """Regression test (external audit 2026-09-05): a fresh clone used
        to silently run wide open (auth_enabled=False, authenticated=True)
        whenever APP_PASSPHRASE wasn't set. It should now block everything
        except the auth endpoints until setup runs."""
        monkeypatch.setattr(auth, "PASSPHRASE", None)
        monkeypatch.setattr(auth, "PASSPHRASE_HASH", None)
        monkeypatch.setattr(auth, "_AUTH_DISABLED_PATH", str(tmp_path / "not_there"))
        r = client.get("/api/auth/status")
        assert r.json()["setup_required"] is True
        assert client.get("/api/accounts").status_code == 401

    def test_setup_with_passphrase_unlocks_and_logs_in(self, client, monkeypatch, tmp_path):
        # set_passphrase() rebinds auth state directly, so isolate both the
        # legacy plaintext value and the new password hash for this test.
        monkeypatch.setattr(auth, "PASSPHRASE", None)
        monkeypatch.setattr(auth, "PASSPHRASE_HASH", None)
        monkeypatch.setattr(auth, "_AUTH_DISABLED_PATH", str(tmp_path / "not_there"))
        monkeypatch.setattr(auth, "_ENV_PATH", str(tmp_path / "test.env"))
        r = client.post("/api/auth/setup", json={"passphrase": "new-secret"})
        assert r.status_code == 200
        assert r.json()["auth_enabled"] is True
        assert client.get("/api/accounts").status_code == 200  # session cookie from setup already authenticates

    def test_setup_with_skip_disables_auth(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(auth, "PASSPHRASE", None)
        monkeypatch.setattr(auth, "PASSPHRASE_HASH", None)
        monkeypatch.setattr(auth, "_AUTH_DISABLED_PATH", str(tmp_path / "not_there"))
        r = client.post("/api/auth/setup", json={"skip": True})
        assert r.status_code == 200
        assert r.json()["auth_enabled"] is False
        assert client.get("/api/accounts").status_code == 200

    def test_setup_rejected_once_already_completed(self, client):
        """conftest.py's fixture already marks setup as skipped, so calling
        it again should be rejected rather than silently letting anyone
        reset the passphrase without being logged in."""
        r = client.post("/api/auth/setup", json={"skip": True})
        assert r.status_code == 403

    def test_status_enabled_but_not_authenticated(self, client, monkeypatch):
        monkeypatch.setattr(auth, "PASSPHRASE", "correct-horse")
        r = client.get("/api/auth/status")
        data = r.json()
        assert data["auth_enabled"] is True
        assert data["authenticated"] is False

    def test_login_wrong_passphrase_401(self, client, monkeypatch):
        monkeypatch.setattr(auth, "PASSPHRASE", "correct-horse")
        r = client.post("/api/auth/login", json={"passphrase": "wrong"})
        assert r.status_code == 401

    def test_login_correct_passphrase_authenticates(self, client, monkeypatch):
        monkeypatch.setattr(auth, "PASSPHRASE", "correct-horse")
        r = client.post("/api/auth/login", json={"passphrase": "correct-horse"})
        assert r.status_code == 200
        assert client.get("/api/auth/status").json()["authenticated"] is True

    def test_protected_route_401_without_session_when_enabled(self, client, monkeypatch):
        monkeypatch.setattr(auth, "PASSPHRASE", "correct-horse")
        r = client.get("/api/accounts")
        assert r.status_code == 401

    def test_protected_route_200_after_login(self, client, monkeypatch):
        monkeypatch.setattr(auth, "PASSPHRASE", "correct-horse")
        client.post("/api/auth/login", json={"passphrase": "correct-horse"})
        assert client.get("/api/accounts").status_code == 200

    def test_logout_revokes_session(self, client, monkeypatch):
        monkeypatch.setattr(auth, "PASSPHRASE", "correct-horse")
        client.post("/api/auth/login", json={"passphrase": "correct-horse"})
        assert client.get("/api/accounts").status_code == 200
        client.post("/api/auth/logout")
        assert client.get("/api/accounts").status_code == 401

    def test_webauthn_register_options_requires_session(self, client, monkeypatch):
        monkeypatch.setattr(auth, "PASSPHRASE", "correct-horse")
        r = client.get("/api/auth/webauthn/register-options")
        assert r.status_code == 401

    def test_webauthn_register_options_after_login(self, client, monkeypatch):
        monkeypatch.setattr(auth, "PASSPHRASE", "correct-horse")
        client.post("/api/auth/login", json={"passphrase": "correct-horse"})
        r = client.get("/api/auth/webauthn/register-options")
        assert r.status_code == 200
        assert "challenge" in r.json()

    def test_webauthn_register_verify_rejects_garbage(self, client, monkeypatch):
        monkeypatch.setattr(auth, "PASSPHRASE", "correct-horse")
        client.post("/api/auth/login", json={"passphrase": "correct-horse"})
        r = client.post("/api/auth/webauthn/register-verify", json={"credential": {"not": "real"}})
        assert r.status_code == 400

    def test_webauthn_login_options_400_when_not_registered(self, client):
        r = client.get("/api/auth/webauthn/login-options")
        assert r.status_code == 400

    def test_webauthn_login_verify_rejects_garbage(self, client):
        r = client.post("/api/auth/webauthn/login-verify", json={"credential": {"not": "real"}})
        assert r.status_code == 401


class TestCfoOperatingSystem:
    def test_plan_confidence_and_runway(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)
        confidence = client.get("/api/plan-confidence")
        assert confidence.status_code == 200
        assert confidence.json()["checks"]
        runway = client.get("/api/financial-runway")
        assert runway.status_code == 200
        assert runway.json()["account_count"] == len(sample_accounts)
        briefing = client.get("/api/cfo-briefing")
        assert briefing.status_code == 200
        assert "priorities" in briefing.json()

    def test_life_event_crud(self, client):
        created = client.post("/api/life-events", json={"name":"Career pause","event_type":"career","event_year":2030,"one_time_cash_delta":-1000,"monthly_cash_flow_delta":-100,"duration_months":12})
        assert created.status_code == 200
        assert created.json()["included_in_projection"] is True  # default on
        event_id = created.json()["id"]
        listed = client.get("/api/life-events")
        assert listed.status_code == 200
        assert listed.json()["events"][0]["id"] == event_id
        assert listed.json()["events"][0]["included_in_projection"] is True
        assert client.delete(f"/api/life-events/{event_id}").status_code == 200

    def test_life_event_toggle_flips_by_default(self, client):
        created = client.post("/api/life-events", json={"name":"Sabbatical","event_type":"sabbatical","event_year":2030,"one_time_cash_delta":0,"monthly_cash_flow_delta":-500,"duration_months":6})
        event_id = created.json()["id"]
        toggled = client.patch(f"/api/life-events/{event_id}/toggle")
        assert toggled.status_code == 200
        assert toggled.json()["included_in_projection"] == 0
        toggled_back = client.patch(f"/api/life-events/{event_id}/toggle")
        assert toggled_back.json()["included_in_projection"] == 1

    def test_life_event_toggle_can_set_explicit_value(self, client):
        created = client.post("/api/life-events", json={"name":"Windfall","event_type":"windfall","event_year":2031,"one_time_cash_delta":10000,"monthly_cash_flow_delta":0,"duration_months":0})
        event_id = created.json()["id"]
        r = client.patch(f"/api/life-events/{event_id}/toggle", json={"included_in_projection": False})
        assert r.status_code == 200
        assert r.json()["included_in_projection"] == 0
        r2 = client.patch(f"/api/life-events/{event_id}/toggle", json={"included_in_projection": True})
        assert r2.json()["included_in_projection"] == 1

    def test_life_event_toggle_404_for_missing_event(self, client):
        r = client.patch("/api/life-events/999999/toggle")
        assert r.status_code == 404

    def test_life_event_overlay_shows_toggled_off_events_too(self, client):
        """summarize_life_events() (GET /api/life-events) keeps showing every
        event regardless of included_in_projection, with the flag's current
        value included on each summary."""
        created = client.post("/api/life-events", json={"name":"Toggle test","event_type":"other","event_year":2030,"one_time_cash_delta":5000,"monthly_cash_flow_delta":0,"duration_months":0})
        event_id = created.json()["id"]
        client.patch(f"/api/life-events/{event_id}/toggle", json={"included_in_projection": False})
        listed = client.get("/api/life-events")
        event = next(e for e in listed.json()["events"] if e["id"] == event_id)
        assert event["included_in_projection"] is False
        assert "retirement_impact" in event  # isolated overlay estimate still computed

    def test_estate_documents_and_assumption_review(self, client):
        # Status vocabulary corrected 2026-09-09 (external audit
        # follow-up, P1): this endpoint originally validated against
        # not_started/in_progress/complete, which never matched
        # Estate.jsx's own UI (executed/verify/outdated/pending) --
        # built before ever being wired to the frontend.
        document = {"document_type":"Will","status":"executed","reviewed_on":"2026-01-01","next_review_on":"2027-01-01","location_hint":"Home safe","notes":""}
        assert client.put("/api/estate-documents/Will", json=document).status_code == 200
        documents = client.get("/api/estate-documents")
        assert documents.status_code == 200
        assert documents.json()[0]["status"] == "executed"
        created = client.post("/api/assumption-reviews", json={"label":"Base","assumptions":{"inflation_rate":.025}})
        assert created.status_code == 200
        reviews = client.get("/api/assumption-reviews")
        assert reviews.status_code == 200
        assert reviews.json()[0]["assumptions"]["inflation_rate"] == .025

    def test_estate_document_accepts_both_live_callers_status_vocabularies(self, client):
        """PlanOperatingSystem.jsx calls this SAME endpoint as
        Estate.jsx, with its own disjoint status vocabulary
        (not_started/in_progress/complete) -- both must be accepted
        until the two estate-tracking UIs are reconciled (see the
        endpoint's own comment). A genuinely invalid status must still
        be rejected."""
        assert client.put("/api/estate-documents/Will", json={"document_type":"Will","status":"complete"}).status_code == 200
        assert client.put("/api/estate-documents/wills", json={"document_type":"wills","status":"executed"}).status_code == 200
        assert client.put("/api/estate-documents/x", json={"document_type":"x","status":"bogus"}).status_code == 400

    def test_estate_beneficiaries_crud(self, client):
        """Regression test (external audit, 2026-09-09, P1): beneficiary
        designations used to be localStorage-only in Estate.jsx, never
        backed by any table -- a fresh browser or a restored backup
        lost them entirely. Now a real table + API, same pattern as
        estate-documents just above."""
        assert client.get("/api/estate-beneficiaries").json() == []
        bene = {"account_key": "person1roth", "primary_beneficiary": "Sam", "contingent_beneficiary": "Kids equally"}
        r = client.put("/api/estate-beneficiaries/person1roth", json=bene)
        assert r.status_code == 200
        listed = client.get("/api/estate-beneficiaries").json()
        assert len(listed) == 1
        assert listed[0]["primary_beneficiary"] == "Sam"

        # Upsert: saving again under the same key updates, not duplicates.
        client.put("/api/estate-beneficiaries/person1roth", json={
            "account_key": "person1roth", "primary_beneficiary": "Sam Updated", "contingent_beneficiary": "Kids equally",
        })
        listed = client.get("/api/estate-beneficiaries").json()
        assert len(listed) == 1
        assert listed[0]["primary_beneficiary"] == "Sam Updated"

    def test_estate_beneficiary_rejects_mismatched_key(self, client):
        bene = {"account_key": "other", "primary_beneficiary": "Sam"}
        assert client.put("/api/estate-beneficiaries/person1roth", json=bene).status_code == 400

    def test_backup_export_includes_estate_records_and_restore_round_trips_them(self, client):
        """Regression test (external audit, 2026-09-09, P1): both
        estate_documents and estate_beneficiaries must be part of the
        backed-up data model, and a restore must bring back their
        AT-BACKUP-TIME values (proving the tables actually round-trip,
        not just get included empty)."""
        client.put("/api/estate-documents/Will", json={"document_type": "Will", "status": "executed"})
        client.put("/api/estate-beneficiaries/person1roth", json={"account_key": "person1roth", "primary_beneficiary": "Sam"})
        payload = client.get("/api/backup/export").json()
        assert "estate_documents" in payload["tables"]
        assert "estate_beneficiaries" in payload["tables"]

        client.put("/api/estate-documents/Will", json={"document_type": "Will", "status": "outdated"})
        client.put("/api/estate-beneficiaries/person1roth", json={"account_key": "person1roth", "primary_beneficiary": "Changed"})

        r = client.post("/api/backup/restore", params={"confirm": "true"},
                         files={"file": ("b.json", json.dumps(payload), "application/json")})
        assert r.status_code == 200, r.text
        assert client.get("/api/estate-documents").json()[0]["status"] == "executed"
        assert client.get("/api/estate-beneficiaries").json()[0]["primary_beneficiary"] == "Sam"

    def test_calendar_export_contains_open_tasks(self, client):
        assert client.post("/api/tasks/sync").status_code == 200
        response = client.get("/api/calendar/export")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/calendar")
        assert "BEGIN:VEVENT" in response.text

    def test_backup_export_is_portable_json(self, client):
        response = client.get("/api/backup/export")
        assert response.status_code == 200
        assert response.json()["format"] == "personal-cfo-backup"

    def test_restore_requires_confirmation(self, client):
        payload = client.get("/api/backup/export").json()
        r = client.post("/api/backup/restore", files={"file": ("b.json", json.dumps(payload), "application/json")})
        assert r.status_code == 400

    def test_restore_round_trips_a_real_export(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)
        payload = client.get("/api/backup/export").json()
        r = client.post("/api/backup/restore", params={"confirm": "true"},
                         files={"file": ("b.json", json.dumps(payload), "application/json")})
        assert r.status_code == 200, r.text
        assert len(client.get("/api/accounts").json()) == len(sample_accounts)

    def test_restore_rejects_backup_missing_a_table(self, client, sample_inputs, sample_accounts):
        """Regression (external audit 2026-09-07): a backup payload with
        `tables: {}` (or any table key simply absent) used to pass
        validation — which only checked that tables *present* were known
        names — straight through to a DELETE over every real table,
        re-inserting only whatever the payload happened to include. A
        real export with even one table key removed must be rejected
        outright rather than silently wiping that category, and the
        existing data must survive the attempt untouched."""
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)
        payload = client.get("/api/backup/export").json()
        del payload["tables"]["accounts"]
        r = client.post("/api/backup/restore", params={"confirm": "true"},
                         files={"file": ("b.json", json.dumps(payload), "application/json")})
        assert r.status_code == 400
        assert "accounts" in r.json()["detail"]
        # The rejected restore must not have touched anything.
        assert len(client.get("/api/accounts").json()) == len(sample_accounts)

    def test_restore_rejects_empty_tables_dict(self, client, sample_inputs, sample_accounts):
        """The exact reproduction from the audit: {"tables": {}} used to
        return {"ok": true} while erasing every table."""
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)
        bad_payload = {"format": "personal-cfo-backup", "version": 1, "tables": {}}
        r = client.post("/api/backup/restore", params={"confirm": "true"},
                         files={"file": ("b.json", json.dumps(bad_payload), "application/json")})
        assert r.status_code == 400
        assert len(client.get("/api/accounts").json()) == len(sample_accounts)
        assert client.get("/api/planning-inputs").status_code == 200

    def test_backup_export_includes_kids_and_restore_round_trips_them(self, client):
        """Regression (external audit follow-up, 2026-09-09,
        kids-variable-count): the kids table was added after backup/
        export|restore shipped and got left out of _BACKUP_TABLES --
        export_backup silently omitted it (every OTHER table survived a
        backup/restore cycle; kids quietly didn't), and a household's
        edited name/age/monthly_529 would revert to whatever was in the
        db before the restore, or (worse) restoring a backup taken
        before this fix would need to be rejected outright rather than
        silently leaving kids stale -- covered by the missing-table
        check above, since "kids" not being a key in an old payload now
        makes it into `missing`."""
        created = client.post("/api/kids", json={"name": "Riley", "age": 9, "monthly_529": 150}).json()
        payload = client.get("/api/backup/export").json()
        assert "kids" in payload["tables"]
        assert payload["tables"]["kids"][0]["name"] == "Riley"

        # Change everything about the kid after taking the backup, then
        # restore -- the restore must bring back the AT-BACKUP-TIME
        # values, proving the table round-trips instead of being
        # skipped (a skipped table would instead retain "Changed").
        client.put(f"/api/kids/{created['id']}", json={"name": "Changed", "age": 99, "monthly_529": 999})
        r = client.post("/api/backup/restore", params={"confirm": "true"},
                         files={"file": ("b.json", json.dumps(payload), "application/json")})
        assert r.status_code == 200, r.text
        kids = client.get("/api/kids").json()
        assert len(kids) == 1
        assert kids[0]["name"] == "Riley"
        assert kids[0]["age"] == 9
        assert kids[0]["monthly_529"] == 150


class TestSavedScenariosSsTiming:
    """Regression (external audit 2026-09-07, finding #15): POST
    /api/saved-scenarios used to hardcode "early" SS claiming regardless
    of what the caller asked for, and never persisted which timing had
    actually been used — so a scenario saved under "SS at 67" silently
    recorded early-claiming numbers with no way to tell after the fact."""

    def _seed(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)

    def test_default_ss_timing_is_early(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/saved-scenarios", json={"name": "Default", "retirement_age": 60})
        assert r.status_code == 200, r.text
        saved = next(s for s in client.get("/api/saved-scenarios").json() if s["name"] == "Default")
        assert saved["ss_timing"] == "early"

    def test_delayed_ss_timing_is_persisted_and_produces_different_numbers(
        self, client, sample_inputs, sample_accounts
    ):
        self._seed(client, sample_inputs, sample_accounts)
        client.post("/api/saved-scenarios", json={
            "name": "Early", "retirement_age": 60, "ss_timing": "early",
        })
        client.post("/api/saved-scenarios", json={
            "name": "Delayed", "retirement_age": 60, "ss_timing": "delayed",
        })
        rows = {s["name"]: s for s in client.get("/api/saved-scenarios").json()}
        assert rows["Early"]["ss_timing"] == "early"
        assert rows["Delayed"]["ss_timing"] == "delayed"
        # portfolio_at_retirement covers only the pre-retirement accumulation
        # phase, so it's identical either way — projected_surplus (which
        # reflects SS income actually received during retirement) is where
        # early-vs-delayed claiming diverges. Before the fix both rows would
        # have been computed against the early-claiming scenario regardless
        # of ss_timing, making this value identical too.
        assert (
            rows["Early"]["summary"]["projected_surplus"]
            != rows["Delayed"]["summary"]["projected_surplus"]
        )

    def test_assumptions_snapshot_records_retirement_age_and_ss_timing(
        self, client, sample_inputs, sample_accounts
    ):
        self._seed(client, sample_inputs, sample_accounts)
        client.post("/api/saved-scenarios", json={
            "name": "Snapshot me", "retirement_age": 62, "ss_timing": "delayed",
        })
        saved = next(s for s in client.get("/api/saved-scenarios").json() if s["name"] == "Snapshot me")
        assert saved["assumptions"]["ss_timing"] == "delayed"
        assert saved["assumptions"]["planning_inputs"]["id"] == 1
        assert len(saved["assumptions"]["accounts"]) == len(sample_accounts)
        assert {a["name"] for a in saved["assumptions"]["accounts"]} == {a["name"] for a in sample_accounts}

    def test_invalid_ss_timing_rejected(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/saved-scenarios", json={
            "name": "Bad", "retirement_age": 60, "ss_timing": "yesterday",
        })
        assert r.status_code == 400


class TestSavedScenariosMilestone1:
    """Milestone 1 (2026-09-09): a saved scenario is insert-only —
    re-saving under an existing name no longer silently overwrites it
    (external audit 2026-09-07 finding #15's ON CONFLICT upsert path was
    itself the exact "silently changes a saved snapshot" failure Milestone
    1's acceptance criteria rules out). Recalculating goes through the
    dedicated /recalculate endpoint, which always preserves the original."""

    def _seed(self, client, sample_inputs, sample_accounts):
        _seed_planning_inputs(client, sample_inputs)
        _seed_accounts(client, sample_accounts)

    def test_saving_over_same_name_is_rejected_original_untouched(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        client.post("/api/saved-scenarios", json={
            "name": "Retire at 60", "retirement_age": 60, "ss_timing": "early",
        })
        r = client.post("/api/saved-scenarios", json={
            "name": "Retire at 60", "retirement_age": 60, "ss_timing": "delayed",
        })
        assert r.status_code == 409
        rows = client.get("/api/saved-scenarios").json()
        matching = [s for s in rows if s["name"] == "Retire at 60"]
        assert len(matching) == 1
        assert matching[0]["ss_timing"] == "early"

    def test_new_saves_are_not_legacy(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        client.post("/api/saved-scenarios", json={"name": "Fresh", "retirement_age": 60})
        saved = next(s for s in client.get("/api/saved-scenarios").json() if s["name"] == "Fresh")
        assert saved["is_legacy"] is False
        assert saved["schema_version"] == 2
        assert saved["calculation_version"]

    def test_pre_migration_row_is_flagged_legacy(self, client, sample_inputs, sample_accounts, temp_db):
        """A row that predates this migration (no schema_version/is_legacy
        ever written for it) must still surface as legacy, not be silently
        treated as fully reproducible — the migration's own DEFAULT is what
        makes this true without inventing any missing historical data."""
        self._seed(client, sample_inputs, sample_accounts)
        import sqlite3
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO saved_scenarios (name, retirement_age, ss_timing, summary_json) VALUES (?,?,?,?)",
            ("Old Row", 60, "early", json.dumps({"retirement_age": 60})),
        )
        conn.commit(); conn.close()
        saved = next(s for s in client.get("/api/saved-scenarios").json() if s["name"] == "Old Row")
        assert saved["is_legacy"] is True
        assert saved["schema_version"] == 1
        assert saved["assumptions"] is None

    def test_recalculate_creates_new_revision_and_preserves_original(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/saved-scenarios", json={"name": "Base Plan", "retirement_age": 60, "ss_timing": "early"})
        original_id = r.json()["id"]

        rec = client.post(f"/api/saved-scenarios/{original_id}/recalculate")
        assert rec.status_code == 200, rec.text
        new_id = rec.json()["id"]
        assert new_id != original_id
        assert rec.json()["revision_of"] == original_id
        assert rec.json()["revision_number"] == 2

        rows = {s["id"]: s for s in client.get("/api/saved-scenarios").json()}
        assert rows[original_id]["name"] == "Base Plan"
        assert rows[original_id]["revision_number"] == 1
        assert rows[new_id]["revision_of"] == original_id
        assert rows[new_id]["revision_number"] == 2
        assert "recalculated" in rows[new_id]["name"]

    def test_recalculate_reflects_changed_household_data(self, client, sample_inputs, sample_accounts, temp_db):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/saved-scenarios", json={"name": "Will Change", "retirement_age": 60, "ss_timing": "early"})
        original_id = r.json()["id"]
        original_summary = r.json()

        # Household data changes after the save — update an existing
        # account's balance directly (not another _seed_accounts call,
        # which would only add duplicate rows rather than changing anything).
        import sqlite3
        conn = sqlite3.connect(temp_db)
        conn.execute("UPDATE accounts SET balance = balance + 500000 WHERE name = ?", (sample_accounts[0]["name"],))
        conn.commit(); conn.close()

        rec = client.post(f"/api/saved-scenarios/{original_id}/recalculate")
        assert rec.status_code == 200, rec.text
        assert rec.json()["portfolio_at_retirement"] != original_summary["portfolio_at_retirement"]

        # The original row's own summary must be untouched by the recalculation.
        original_row = client.get(f"/api/saved-scenarios/{original_id}").json()
        assert original_row["summary"]["portfolio_at_retirement"] == original_summary["portfolio_at_retirement"]

    def test_recalculate_missing_scenario_404s(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/saved-scenarios/999999/recalculate")
        assert r.status_code == 404

    def test_get_single_saved_scenario(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        created = client.post("/api/saved-scenarios", json={"name": "One", "retirement_age": 60}).json()
        r = client.get(f"/api/saved-scenarios/{created['id']}")
        assert r.status_code == 200
        assert r.json()["name"] == "One"

    def test_get_single_saved_scenario_missing_404s(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.get("/api/saved-scenarios/999999")
        assert r.status_code == 404

    def test_claim_age_override_is_captured_and_affects_result(self, client, sample_inputs, sample_accounts):
        self._seed(client, sample_inputs, sample_accounts)
        r = client.post("/api/saved-scenarios", json={
            "name": "Custom Claim Age", "retirement_age": 62, "ss_timing": "early",
            "jason_ss_claim_age": 70,
        })
        assert r.status_code == 200, r.text
        saved = next(s for s in client.get("/api/saved-scenarios").json() if s["name"] == "Custom Claim Age")
        assert saved["assumptions"]["jason_ss_claim_age"] == 70
