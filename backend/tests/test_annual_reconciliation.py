"""
Milestone 2 acceptance follow-up (2026-09-10): "Before merging M1/M2,
finish one independently verified annual reconciliation using actual
backend output... Do not count an income offset both as income and as a
reduction in displayed spending... Cover working-spouse wages, bridge
income, positive/negative recurring events, one-time events, taxes, RMD
reinvestment, growth, and depletion."

This does NOT hand-build a fixture and assert self-consistency against
itself (that was section 68/69's honest limitation). It runs the REAL
run_retirement_projection() against a synthetic household engineered to
exercise every flow type listed above, spies on the REAL
annual_engine.simulate_withdrawal_year() calls run_retirement_projection
actually makes (not a re-implementation of its logic), and asserts each
returned AnnualResult.reconcile() -- the engine's own authoritative
per-year ledger check, already used to catch a real bug before any
consumer was migrated onto this engine (see annual_engine.py's own
docstring) -- returns None for every single year. That is the
independently verified reconciliation: proof the actual production code
path balances its own books, not a claim this test file makes about it.

Separately verifies the new gross_spending_need/remaining_portfolio_need/
growth fields (added specifically for this) against the SAME captured
AnnualResult objects, so a household's spending picture can be shown as
gross spending -> income offsets -> remaining portfolio need without any
offset counted twice.
"""
import projection_engine
from projection_engine import run_retirement_projection

BASE_INPUTS = {
    "person1_name": "Alex", "person2_name": "Sam",
    "jason_age": 50, "justin_age": 48,
    "retirement_income_today_dollars": 70000,
    "inflation_rate": 0.02,
    "expected_return_pre_retirement": 0.07,
    "expected_return_post_retirement": 0.05,
    "jason_social_security": 28000,
    "jason_ss_delayed": 42000,
    "justin_social_security": 14000,
    "jason_ss_age": 62,
    "justin_ss_age": 67,
    "annual_hsa_contribution": 6000,
    "annual_rsu_value": 0,
    "pretax_401k_pct": 0.75,
    "employee_401k_pct": 0.06,
    "employer_401k_pct": 0.03,
    "w2_salary": 130000,
    "healthcare_pre_medicare": 18000,
    "healthcare_post_medicare": 5000,
    "healthcare_kids": 0,
    "kids_annual_cost": 0,
    # Bridge income + working-spouse wages/gap income: retiring at 55
    # with a 3-year bridge job, while Justin keeps working (his own
    # w2 salary + a later retirement age) for years beyond that.
    "bridge_income_55": 35000, "bridge_years_55": 3, "kids_years_at_home_55": 0,
    "justin_w2_salary": 90000, "justin_ret_age": 58,
    "justin_employee_401k_pct": 0.06, "justin_employer_401k_pct": 0.03,
    "pension_55": 15000, "pension_60": 22000, "pension_65": 26000,
    "asset1_sale_age": 0, "asset1_sale_net": 0, "asset1_appreciation": 0.03,
    "asset2_sale_age": 0, "asset2_sale_net": 0,
    "retirement_end_age": 95,
    "state_income_tax_rate": 0.04,
}

BASE_ACCOUNTS = [
    {"id": 1, "name": "401k",      "account_type": "401k",     "owner": "jason",  "balance": 900000},
    {"id": 2, "name": "Roth IRA",  "account_type": "roth_ira", "owner": "jason",  "balance": 120000},
    {"id": 3, "name": "Trad IRA",  "account_type": "ira",      "owner": "justin", "balance": 200000},
    {"id": 4, "name": "Brokerage", "account_type": "taxable",  "owner": "joint",  "balance": 250000},
    {"id": 5, "name": "HSA",       "account_type": "hsa",      "owner": "jason",  "balance": 30000},
]

# One-time positive (asset-sale-like windfall), one-time negative (a real
# cost), recurring positive (extra monthly income), recurring negative (an
# ongoing monthly cost) -- all four sign/type combinations the acceptance
# review asked for, using the same life_events shape
# tools/capture_retirement_golden.py already exercises.
# jason_age=50, ret_age=55 -> withdrawal phase starts at CURRENT_YEAR+5;
# every event below is timed AFTER that so it lands in the withdrawal-
# phase life_event_cash/life_event_monthly_adjustment fields being
# reconciled here, not the pre-retirement accumulation phase.
RICH_LIFE_EVENTS = [
    {"event_year": projection_engine.CURRENT_YEAR + 6, "one_time_cash_delta": 60000,
     "monthly_cash_flow_delta": 0, "duration_months": 0},
    {"event_year": projection_engine.CURRENT_YEAR + 8, "one_time_cash_delta": -40000,
     "monthly_cash_flow_delta": 0, "duration_months": 0},
    {"event_year": projection_engine.CURRENT_YEAR + 10, "one_time_cash_delta": 0,
     "monthly_cash_flow_delta": 400, "duration_months": 96},
    {"event_year": projection_engine.CURRENT_YEAR + 20, "one_time_cash_delta": 0,
     "monthly_cash_flow_delta": -300, "duration_months": 60},
]


def _run_with_spy(monkeypatch, inputs, accounts, **kwargs):
    """Runs the REAL run_retirement_projection, spying on the REAL
    annual_engine.simulate_withdrawal_year calls it makes along the way
    (patched at projection_engine's own name binding -- `from annual_engine
    import simulate_withdrawal_year` means patching annual_engine's copy
    would not affect projection_engine's calls). Returns
    (result, captured_annual_results) where the latter is one AnnualResult
    per year, in order, for the single ret_age requested."""
    captured = []
    original = projection_engine.simulate_withdrawal_year

    def spy(*args, **kw):
        r = original(*args, **kw)
        captured.append(r)
        return r

    monkeypatch.setattr(projection_engine, "simulate_withdrawal_year", spy)
    result = run_retirement_projection(inputs, accounts, **kwargs)
    return result, captured


class TestAnnualReconciliationAgainstRealBackendOutput:
    """Every test in this class runs the ACTUAL engine — no hand-typed
    fixture stands in for a real run anywhere here."""

    def test_every_year_reconciles_per_the_engines_own_authoritative_check(self, monkeypatch):
        result, captured = _run_with_spy(
            monkeypatch, dict(BASE_INPUTS), [dict(a) for a in BASE_ACCOUNTS],
            ret_ages=[55], life_events=RICH_LIFE_EVENTS,
        )
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        yearly = scenario["yearly_detail"]
        assert len(yearly) > 30
        # jason_ss_options processes "early" before "delayed" (projection_
        # engine.py), each running its own full year loop against the
        # SAME shared simulate_withdrawal_year spy -- captured[:len(yearly)]
        # is exactly the "early" scenario's own per-year AnnualResult
        # sequence, in order.
        assert len(captured) == 2 * len(yearly)
        early_results = captured[:len(yearly)]

        discrepancies = [(i, y["jason_age"], r.reconcile()) for i, (y, r) in enumerate(zip(yearly, early_results))
                          if r.reconcile() is not None]
        assert discrepancies == [], f"Ledger did not reconcile for years: {discrepancies}"

    def test_coverage_every_requested_flow_type_is_genuinely_exercised(self, monkeypatch):
        """Guards against the reconciliation check above passing
        vacuously because every flow type happened to be zero for this
        household -- explicitly confirms each one listed in the
        acceptance review is nonzero somewhere in this run."""
        result, captured = _run_with_spy(
            monkeypatch, dict(BASE_INPUTS), [dict(a) for a in BASE_ACCOUNTS],
            ret_ages=[55], life_events=RICH_LIFE_EVENTS,
        )
        yearly = next(s for s in result["scenarios"] if s["ss_timing"] == "early")["yearly_detail"]

        assert any(y["bridge_income"] > 0 for y in yearly), "bridge income never appeared"
        assert any(y["justin_gap_income"] > 0 for y in yearly), "working-spouse gap income never appeared"
        assert any(y["life_event_cash"] > 0 for y in yearly), "positive one-time event never appeared"
        assert any(y["life_event_cash"] < 0 for y in yearly), "negative one-time event never appeared"
        assert any(y["life_event_monthly_adjustment"] > 0 for y in yearly), "positive recurring event never appeared"
        assert any(y["life_event_monthly_adjustment"] < 0 for y in yearly), "negative recurring event never appeared"
        assert any(y["rmd_reinvested"] > 0 for y in yearly), "RMD reinvestment never appeared"
        assert any(y["estimated_tax"] > 0 for y in yearly), "tax was never owed"
        assert any(y["growth"] > 0 for y in yearly), "growth was never applied"
        assert any(r.growth for r in captured if sum(r.growth.values()) > 0), "no AnnualResult ever reported growth"

    def test_gross_spending_minus_income_offsets_equals_remaining_portfolio_need(self, monkeypatch):
        """The specific double-counting the review flagged: an income
        offset (bridge income, justin_gap_income,
        life_event_monthly_adjustment, pension, social_security,
        life_event_cash) must reduce remaining_portfolio_need by exactly
        its own value relative to gross_spending_need -- not be
        countable as both income AND already-baked into a lower
        displayed spending figure with no accounting for where it went.
        bridge_income is included here (review finding P2, 2026-09-10):
        gross_spending_need is now captured BEFORE the bridge
        subtraction, so bridge income is a real offset like every other
        one, not already-invisible inside a lower "gross" figure."""
        result, _ = _run_with_spy(
            monkeypatch, dict(BASE_INPUTS), [dict(a) for a in BASE_ACCOUNTS],
            ret_ages=[55], life_events=RICH_LIFE_EVENTS,
        )
        yearly = next(s for s in result["scenarios"] if s["ss_timing"] == "early")["yearly_detail"]
        checked_a_nonzero_offset_year = False
        for y in yearly:
            income_offsets = (y["life_event_monthly_adjustment"] + y["justin_gap_income"] + y["bridge_income"]
                               + y["pension"] + y["social_security"] + y["life_event_cash"])
            reconstructed = y["gross_spending_need"] - income_offsets
            # Each field independently rounded to the nearest dollar, so
            # allow a small tolerance rather than requiring bit-exact
            # equality of five independently-rounded numbers.
            assert abs(reconstructed - y["remaining_portfolio_need"]) <= 5, (
                f"year {y['jason_age']}: gross {y['gross_spending_need']} - offsets {income_offsets} "
                f"= {reconstructed}, but remaining_portfolio_need is {y['remaining_portfolio_need']}"
            )
            if income_offsets != y["pension"] + y["social_security"]:  # some non-baseline offset active
                checked_a_nonzero_offset_year = True
        assert checked_a_nonzero_offset_year, "no year in this run actually exercised a non-baseline income offset"

    def test_growth_field_matches_the_real_annual_results_own_growth_dict(self, monkeypatch):
        result, captured = _run_with_spy(
            monkeypatch, dict(BASE_INPUTS), [dict(a) for a in BASE_ACCOUNTS],
            ret_ages=[55], life_events=RICH_LIFE_EVENTS,
        )
        yearly = next(s for s in result["scenarios"] if s["ss_timing"] == "early")["yearly_detail"]
        for y, r in zip(yearly, captured):
            assert y["growth"] == round(sum(r.growth.values()))

    def test_depletion_still_reconciles_every_year_including_after_the_portfolio_hits_zero(self, monkeypatch):
        """A household with deliberately insufficient funds -- unmet_need
        must appear, and the ledger must still balance in every year,
        including the ones where the portfolio is already at $0."""
        poor_inputs = {**BASE_INPUTS, "retirement_income_today_dollars": 200000,
                       "bridge_income_55": 0, "bridge_years_55": 0,
                       "justin_w2_salary": 0, "justin_ret_age": 0}
        poor_accounts = [{"id": 1, "name": "401k", "account_type": "401k", "owner": "jason", "balance": 60000}]
        result, captured = _run_with_spy(
            monkeypatch, poor_inputs, poor_accounts, ret_ages=[55],
        )
        yearly = next(s for s in result["scenarios"] if s["ss_timing"] == "early")["yearly_detail"]
        assert any(y["unmet_need"] > 0 for y in yearly), "this household was supposed to run out of money"
        assert any(y["portfolio_balance"] == 0 for y in yearly), "portfolio never actually hit zero"

        discrepancies = [(y["jason_age"], r.reconcile()) for y, r in zip(yearly, captured) if r.reconcile() is not None]
        assert discrepancies == [], f"Ledger did not reconcile during/after depletion: {discrepancies}"

    def test_bridge_income_reference_case_matches_independent_reproduction(self, monkeypatch):
        """Review finding P2 (2026-09-10): an exact, independently
        reproduced household -- $100,000 annual spending, $35,000 bridge
        income, no other income/taxes/growth -- exists specifically
        because the earlier version of gross_spending_need silently
        excluded bridge income from "gross spending" for bridge-active
        years (it captured the value AFTER bridge was already subtracted).
        The portfolio withdrawal was already correct; only the
        explanation was wrong. Locks in the exact numbers from that
        reproduction so this can't regress silently."""
        bridge_inputs = {
            **BASE_INPUTS,
            "jason_age": 55, "justin_age": 55,  # already at ret_age -> no pre-retirement growth/inflation to account for
            "retirement_income_today_dollars": 100000,
            "bridge_income_55": 35000, "bridge_years_55": 1, "kids_years_at_home_55": 0, "kids_annual_cost": 0,
            "inflation_rate": 0, "expected_return_pre_retirement": 0, "expected_return_post_retirement": 0,
            "state_income_tax_rate": 0,
            "pension_55": 0, "pension_60": 0, "pension_65": 0,
            "jason_social_security": 0, "jason_ss_delayed": 0, "justin_social_security": 0,
            "justin_w2_salary": 0, "justin_ret_age": 0,
            "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
        }
        bridge_accounts = [
            {"id": 1, "name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 500000},
        ]
        result, captured = _run_with_spy(
            monkeypatch, bridge_inputs, bridge_accounts, ret_ages=[55], life_events=[],
        )
        yearly = next(s for s in result["scenarios"] if s["ss_timing"] == "early")["yearly_detail"]
        y0 = yearly[0]

        assert y0["gross_spending_need"] == 100000
        income_offsets = (y0["pension"] + y0["social_security"] + y0["bridge_income"]
                           + y0["justin_gap_income"] + y0["life_event_cash"] + y0["life_event_monthly_adjustment"])
        assert income_offsets == 35000
        assert y0["remaining_portfolio_need"] == 65000
        assert y0["gross_spending_need"] - income_offsets == y0["remaining_portfolio_need"]
        # The portfolio draw itself was never wrong -- only the
        # explanation of it. Taxable-only draws are untaxed in this
        # model (CALCULATION_CONTRACT.md 3.1), so withdrawal == remaining
        # need exactly with no growth/tax in play.
        assert y0["withdrawal"] == 65000
        assert y0["estimated_tax"] == 0
        assert y0["growth"] == 0
        assert captured[0].reconcile() is None
