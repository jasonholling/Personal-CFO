"""Cross-engine regressions using synthetic balances and isolated API fixtures."""
import pytest
from projection_engine import run_retirement_projection, _split_life_events, _post_retirement_year_effects
from simulation_engine import run_monte_carlo, run_stress_tests


def cash_plan(inputs, **changes):
    return {**inputs, "jason_age": 60, "justin_age": 60,
            "inflation_rate": 0, "expected_return_pre_retirement": 0,
            "expected_return_post_retirement": 0, "w2_salary": 0,
            "annual_hsa_contribution": 0, "annual_rsu_value": 0,
            "retirement_income_today_dollars": 0, "pension_60": 0,
            "jason_social_security": 0, "jason_ss_delayed": 0,
            "justin_social_security": 0, "healthcare_pre_medicare": 0,
            "healthcare_post_medicare": 0, **changes}


def account(kind, balance):
    return {"name": "Synthetic balance", "account_type": kind,
            "owner": "jason", "balance": balance}


def project(inputs, accounts, events=None):
    return run_retirement_projection(inputs, accounts, ret_ages=[60],
                                     life_events=events)["scenarios"][0]


@pytest.mark.parametrize("kind", ["ira", "roth_ira", "hsa", "taxable"])
def test_event_cost_uses_available_assets(sample_inputs, kind):
    inputs = cash_plan(sample_inputs)
    result = project(inputs, [account(kind, 1000000)],
                     [{"event_year": 2026, "one_time_cash_delta": -500000}])
    year = result["yearly_detail"][0]
    assert year["portfolio_balance"] <= 500000
    assert year["unmet_need"] == 0
    assert year["portfolio_balance"] >= 400000


def test_unaffordable_event_is_reported(sample_inputs):
    result = project(cash_plan(sample_inputs), [],
                     [{"event_year": 2026, "one_time_cash_delta": -500000}])
    assert result["yearly_detail"][0]["unmet_need"] == 500000
    assert not result["on_track"]
    assert result["percent_funded"] < 100


def test_windfall_reconciles_funding_headline(sample_inputs):
    result = project(cash_plan(sample_inputs, retirement_income_today_dollars=10000), [],
                     [{"event_year": 2026, "one_time_cash_delta": 1000000}])
    assert result["on_track"]
    assert result["percent_funded"] == 100
    assert result["yearly_detail"][-1]["portfolio_balance"] == 610000
    assert result["projected_surplus"] == 610000
    assert result["capitalized_income_sources"] == 1000000
    assert result["total_capitalized_need"] == 390000


@pytest.mark.parametrize("months,expected", [(1, -1000), (6, -6000), (12, -12000), (18, -12000)])
def test_partial_year_event(months, expected):
    _, events = _split_life_events([{"event_year": 2026, "monthly_cash_flow_delta": -1000,
                                     "duration_months": months}], 2026)
    assert _post_retirement_year_effects(events, 2026)[1] == expected
    assert _post_retirement_year_effects(events, 2027)[1] == (-6000 if months == 18 else 0)


def test_event_crosses_retirement_without_repeating_lump_sum():
    pre, post = _split_life_events([{"event_year": 2026, "one_time_cash_delta": -500,
                                     "monthly_cash_flow_delta": -1000,
                                     "duration_months": 30}], 2027)
    assert pre[0]["one_time"] == -500
    assert _post_retirement_year_effects(post, 2027) == (0, -12000)
    assert _post_retirement_year_effects(post, 2028) == (0, -6000)


def test_stress_base_matches_cash_flow_healthcare_and_events(sample_inputs):
    inputs = cash_plan(sample_inputs, jason_age=50, justin_age=50, inflation_rate=.02,
                       healthcare_pre_medicare=10000, healthcare_post_medicare=10000)
    accounts = [account("taxable", 1000000)]
    events = [{"event_year": 2036, "one_time_cash_delta": -20000,
               "monthly_cash_flow_delta": -1000, "duration_months": 6}]
    projection = project(inputs, accounts, events)
    stress = run_stress_tests(inputs, accounts, life_events=events)
    assert stress["scenarios"]["base"]["chart"][0]["balance"] == projection["yearly_detail"][0]["portfolio_balance"]


def test_monte_carlo_returns_and_horizon_are_effective(sample_inputs, sample_accounts):
    low = run_monte_carlo({**sample_inputs, "expected_return_post_retirement": .03,
                          "retirement_end_age": 95}, sample_accounts)
    high = run_monte_carlo({**sample_inputs, "expected_return_post_retirement": .10,
                           "retirement_end_age": 95}, sample_accounts)
    longer = run_monte_carlo({**sample_inputs, "expected_return_post_retirement": .03,
                             "retirement_end_age": 100}, sample_accounts)
    assert high["median_final_balance"] > low["median_final_balance"]
    assert longer["retirement_end_age"] == 100
    assert low["retirement_end_age"] == 95
    assert len(longer["chart"]) > len(low["chart"])


def test_salary_growth_reaches_simulation_accumulation(sample_inputs, sample_accounts):
    base = run_monte_carlo(sample_inputs, sample_accounts)
    raised = run_monte_carlo({**sample_inputs, "_salary_growth_pct": .05}, sample_accounts)
    assert raised["portfolio_at_retirement"] > base["portfolio_at_retirement"]


@pytest.mark.parametrize("path", ["/api/simulation/swr", "/api/retirement/income-sources",
                                  "/api/simulation/roth-conversion",
                                  "/api/simulation/contribution-sensitivity"])
def test_companion_endpoints_accept_same_scenario(client, sample_inputs, sample_accounts, path):
    assert client.put("/api/planning-inputs", json=sample_inputs).status_code == 200
    for a in sample_accounts:
        assert client.post("/api/accounts", json={k: v for k, v in a.items() if k != "id"}).status_code == 200
    body = {"ret_age": 62, "ss_timing": "delayed", "income_target": 50000,
            "pre_return": .08, "post_return": .04, "salary_growth_pct": .05}
    result = client.post(path, json=body)
    assert result.status_code == 200, result.text
    baseline = client.get(path, params={"ret_age": 62, "ss_timing": "delayed"})
    assert baseline.status_code == 200
    assert result.json() != baseline.json()
