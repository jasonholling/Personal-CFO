"""
Independent, hand-verified reference cases for two-age Roth Conversion --
written BEFORE jason_ret_age/justin_ret_age exist on
run_roth_conversion_analysis (backend/docs/CALCULATION_CONTRACT.md
section 30, 2026-09-08, Milestone 2 of 4). This file is expected to
fail at collection until that implementation exists.

Verification strategy: unlike SWR (a binary search with no closed-form
answer), this function's per-year math IS closed-form given the shared,
already-reviewed primitives it's built from (simulate_withdrawal_year,
simulate_conversion, the 2026 MFJ bracket table). Every number below was
computed by hand, replaying those primitives' own documented contracts
(taxable drawn first at 0% tax, growth applied last within
simulate_withdrawal_year, simulate_conversion layered on the
already-grown closing state) -- not inferred from running the code
under test.

Bracket constants used throughout (retirement_tools_engine.py, 2026 MFJ):
BRACKET_TOP_22 = 211400, STD_DEDUCTION = 32200, RMD_START_AGE for anyone
currently 65-73 (born 1953-1961) = 73 in this environment's current date.
"""

import pytest

from simulation_engine import run_roth_conversion_analysis, _pretax_marginal_tax_rate

TAXABLE = lambda balance: [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": balance}]
PRETAX = lambda balance: [{"name": "IRA", "account_type": "ira", "owner": "joint", "balance": balance}]


def base_inputs(**overrides):
    inputs = {
        "jason_age": 71, "justin_age": 71,
        "inflation_rate": 0.0,
        "expected_return_pre_retirement": 0.0,
        "expected_return_post_retirement": 0.0,
        "retirement_income_today_dollars": 50000,
        "annual_hsa_contribution": 0, "annual_rsu_value": 0,
        "jason_social_security": 0, "justin_social_security": 0,
        "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
        "justin_w2_salary": 0, "justin_employee_401k_pct": 0, "justin_employer_401k_pct": 0,
        "justin_annual_bonus_pct": 0, "justin_annual_rsu_value": 0,
        "w2_salary": 0, "employee_401k_pct": 0, "employer_401k_pct": 0,
        "annual_bonus_pct": 0,
        "pension_55": 0, "pension_60": 0, "pension_65": 0,
        "retirement_end_age": 99,
    }
    inputs.update(overrides)
    return inputs


class TestBasicConversionMathTwoAge:
    """Both retire simultaneously at 71 (RMD_START_AGE=73 -- born 1955,
    under the 1960 cutoff -- so a clean 2-year conversion window),
    $300,000 pretax / $0 Roth / $500,000 taxable, 0% inflation/growth/
    guaranteed income, $50,000/yr spending (taxable easily covers it
    both years, so pretax is untouched by spending -- isolating the
    conversion math from the withdrawal waterfall's own gross-up
    formulas).

    Year 0 (age 71): spending fully drawn from taxable (untaxed) ->
    taxable 500000-50000=450000, pretax_draw=0. base_taxable =
    0+0+0-32200 = -32200 -> room_in_22 = 211400-(-32200) = 243600.
    max_conversion_affordable = 450000/0.22 = 2,045,454.5. optimal =
    min(243600, 300000, 2045454.5) = 243600 (bracket-room-bound).
    tax_cost = 243600*0.22 = 53592. pretax_after = 300000-243600=56400.
    roth_after = 243600. taxable_after = 450000-53592=396408.
    yrs_to_rmd = 73-71-1 = 1 (0% growth, doesn't matter) -> roth_fv_73 =
    243600. tax_avoided = 243600*0.24 = 58464. net_benefit =
    58464-53592 = 4872.

    Year 1 (age 72): opening pretax=56400, roth=243600, taxable=396408.
    Spending 50000, still fully from taxable -> taxable pre-conversion =
    346408, pretax_draw=0. base_taxable = -32200 -> room_in_22 = 243600
    again, but pretax only has 56400 left -> optimal = min(243600,
    56400, 346408/0.22) = 56400 (pretax-bound this time -- the entire
    remaining IRA converts). tax_cost = 56400*0.22 = 12408.
    pretax_after = 0. roth_after = 243600+56400=300000. taxable_after =
    346408-12408=334000. yrs_to_rmd = 73-72-1 = 0 -> roth_fv_73=56400.
    tax_avoided = 56400*0.24=13536. net_benefit=13536-12408=1128.

    Totals: total_conversions=300000 (the entire original IRA, over 2
    years). total_tax_cost=53592+12408=66000. total_tax_avoided=
    58464+13536=72000. net_lifetime_benefit=72000-66000=6000.

    No-conversion baseline (same spending, no conversions): taxable
    drains by 50000/yr from its own 500000 start (450000 -> 400000),
    pretax untouched at 300000 both years (never drawn, since taxable
    always covers spending). estimated_rmd_without_conversions =
    _rmd(300000, 73, 73) = 300000/26.5 = 11320.75 -> 11321.
    pretax_at_rmd_age_no_conversion = 300000.
    pretax_at_rmd_age_with_conversion = 0 (the with-conversions path
    above drains the IRA to exactly 0)."""

    def test_two_year_schedule_and_totals(self):
        """Conversion tax is now PROGRESSIVE (tax(base+conversion) -
        tax(base)), not a flat 22% -- independent review, 2026-09-08,
        Roth Conversion follow-up, P2. Year 0: base_taxable=-32200 (no
        other income), conversion=243600 -> post=max(0,-32200+243600)=
        211400, pre=max(0,-32200)=0. Progressive tax(211400) = 2480
        (10% of 24800) + 9120 (12% of 76000) + 24332 (22% of 110600) =
        35932; tax(0)=0 -> tax_cost=35932 (not the old flat
        243600*0.22=53592). Year 1: base_taxable=-32200 again,
        conversion=56400 (pretax-bound) -> post=max(0,-32200+56400)=
        24200, entirely within the 10% bracket -> tax_cost=2420 (not
        the old flat 56400*0.22=12408)."""
        inputs = base_inputs()
        r = run_roth_conversion_analysis(inputs, PRETAX(300000) + TAXABLE(500000),
                                          jason_ret_age=71, justin_ret_age=71)
        assert r["mode"] == "two_age"
        assert r["conversion_years"] == 2
        assert r["rmd_start_age"] == 73
        assert len(r["schedule"]) == 2

        y0, y1 = r["schedule"]
        assert y0["age"] == 71
        assert y0["base_taxable_income"] == -32200
        assert y0["room_in_22_bracket"] == 243600
        assert y0["optimal_conversion"] == 243600
        assert y0["tax_cost"] == 35932
        assert y0["roth_fv_at_73"] == 243600
        assert y0["tax_avoided_at_73"] == 58464
        assert y0["net_benefit"] == 22532
        assert y0["pretax_after"] == 56400
        assert y0["roth_after"] == 243600
        assert y0["taxable_after"] == 414068

        assert y1["age"] == 72
        assert y1["optimal_conversion"] == 56400
        assert y1["tax_cost"] == 2420
        assert y1["roth_fv_at_73"] == 56400
        assert y1["tax_avoided_at_73"] == 13536
        assert y1["net_benefit"] == 11116
        assert y1["pretax_after"] == 0
        assert y1["roth_after"] == 300000
        assert y1["taxable_after"] == 361648

        assert r["total_conversions"] == 300000
        assert r["total_tax_cost"] == 38352
        assert r["total_tax_avoided"] == 72000
        assert r["net_lifetime_benefit"] == 33648
        assert r["pretax_at_rmd_age_with_conversion"] == 0
        assert r["pretax_at_rmd_age_no_conversion"] == 300000
        assert r["estimated_rmd_without_conversions"] == 11321


class TestEitherRetirementOrderIsSymmetric:
    """Justin retires first (2 years before Jason, a working-spouse
    salary folded into both bracket capacity AND, net of tax, gap
    income) vs. Jason retiring first (mirror, Jason's own salary
    instead) -- the conversion schedule's own tax math must be
    IDENTICAL in both directions, proving the two-age formulas are
    symmetric, not hardcoded to either spouse. $100,000 salary (a
    partial, not total, bracket reduction -- chosen so the comparison
    is meaningful, not trivially zero both ways) -- gross wages fold
    into base_taxable in full: 0(other income)+100000-32200=67800,
    room_in_22=211400-67800=143600. optimal_conversion=143600
    (bracket-bound, well under the $300,000 pretax balance).
    tax_cost: post=67800+143600=211400 (progressive tax 35932, same
    figure as the no-wage test's own 211400 boundary -- coincidence of
    round numbers, verified independently), pre=67800 (progressive tax
    7640: 2480 + 12%*43000=5160) -> incremental=35932-7640=28292."""

    def test_justin_retires_first_jason_two_years_later(self):
        inputs = base_inputs(retirement_income_today_dollars=50000, w2_salary=100000)
        r = run_roth_conversion_analysis(inputs, PRETAX(300000) + TAXABLE(500000),
                                          jason_ret_age=73, justin_ret_age=71)
        assert r["later_retiree"] == "jason"
        assert r["phase2_start_age"] == 71
        assert r["conversion_years"] == 2
        y0 = r["schedule"][0]
        assert y0["base_taxable_income"] == 67800
        assert y0["room_in_22_bracket"] == 143600
        assert y0["optimal_conversion"] == 143600
        assert y0["tax_cost"] == 28292

    def test_jason_retires_first_justin_two_years_later(self):
        """Mirror -- Justin's own salary this time, same numbers."""
        inputs = base_inputs(retirement_income_today_dollars=50000, justin_w2_salary=100000)
        r = run_roth_conversion_analysis(inputs, PRETAX(300000) + TAXABLE(500000),
                                          jason_ret_age=71, justin_ret_age=73)
        assert r["later_retiree"] == "justin"
        assert r["phase2_start_age"] == 71
        assert r["conversion_years"] == 2
        y0 = r["schedule"][0]
        assert y0["base_taxable_income"] == 67800
        assert y0["room_in_22_bracket"] == 143600
        assert y0["optimal_conversion"] == 143600
        assert y0["tax_cost"] == 28292


class TestWorkingIncomeConsumesBracketCapacitySymmetrically:
    """Independent review, 2026-09-08, Roth Conversion follow-up, P1:
    the working spouse's GROSS wages (either spouse) now reduce
    base_taxable/room_in_22 the same way pension/SS/pretax draws
    already do -- the previous "no effect, in either direction" rule
    was wrong. The 65% net-of-tax factor still funds the spending-need
    offset unchanged; a separate gross figure (derived by dividing the
    net figure back out, since the factor is applied as a flat
    multiplier) now also enters base_taxable. Reproduced (the review's
    own case): $500,000 salary, no other income, ample assets -- room
    used to be $243,600 (as if the salary didn't exist); under the
    repo's own deductions/bracket table, that salary ALONE
    (500000-32200=467800 > 211400) already leaves $0 room."""

    def test_a_partial_wage_reduces_room_identically_regardless_of_spouse(self):
        # No pension here (unlike the other tests in this file) --
        # pension gates to Jason's own actual retirement
        # (two_age_pension_for_year), so a version of this test giving
        # JASON the wage (making him the later, still-working retiree)
        # would also defer HIS pension, confounding the wage-only
        # comparison this test is isolating. Isolating just the wage
        # effect (no pension at all) keeps both directions genuinely
        # symmetric.
        no_wages    = base_inputs(retirement_income_today_dollars=50000)
        with_justin = base_inputs(retirement_income_today_dollars=50000, justin_w2_salary=50000)
        with_jason  = base_inputs(retirement_income_today_dollars=50000, w2_salary=50000)
        r_no     = run_roth_conversion_analysis(no_wages, PRETAX(300000) + TAXABLE(500000),
                                                  jason_ret_age=71, justin_ret_age=71)
        r_justin = run_roth_conversion_analysis(with_justin, PRETAX(300000) + TAXABLE(500000),
                                                  jason_ret_age=71, justin_ret_age=73)
        r_jason  = run_roth_conversion_analysis(with_jason, PRETAX(300000) + TAXABLE(500000),
                                                  jason_ret_age=73, justin_ret_age=71)
        # No wages, no other income: base_taxable=-32200, room=243600
        # (matches TestBasicConversionMathTwoAge's own baseline).
        assert r_no["schedule"][0]["room_in_22_bracket"] == 243600
        # A $50,000 gross salary (regardless of which spouse) reduces
        # room by exactly $50,000: 0+50000-32200=17800 ->
        # room=211400-17800=193600 -- symmetric, and a REAL reduction
        # from the no-wage baseline (not the same 243600).
        assert r_justin["schedule"][0]["base_taxable_income"] == 17800
        assert r_justin["schedule"][0]["room_in_22_bracket"] == 193600
        assert r_jason["schedule"][0]["base_taxable_income"] == 17800
        assert r_jason["schedule"][0]["room_in_22_bracket"] == 193600
        assert r_justin["schedule"][0]["room_in_22_bracket"] != r_no["schedule"][0]["room_in_22_bracket"]

    def test_a_large_wage_alone_can_exhaust_the_bracket_entirely(self):
        """The review's own reproduction: $500,000 salary, no other
        income, ample assets -- room used to be reported as $243,600 as
        if the salary were invisible to bracket math; it's actually $0
        (500000-32200=467800 > 211400)."""
        inputs = base_inputs(retirement_income_today_dollars=50000, justin_w2_salary=500000)
        r = run_roth_conversion_analysis(inputs, PRETAX(300000) + TAXABLE(500000),
                                          jason_ret_age=71, justin_ret_age=73)
        assert r["schedule"][0]["base_taxable_income"] == 467800
        assert r["schedule"][0]["room_in_22_bracket"] == 0
        assert r["schedule"][0]["optimal_conversion"] == 0
        assert r["total_conversions"] == 0


class TestPastRetirementSelectionAndUnequalAges:
    def test_past_retirement_selection_starts_the_window_immediately(self):
        """Jason already 2 years past his selected retirement age (73
        today, selected 71) -- phase2 must start at his real current
        age, 73. Justin, already retired too (justin_ret_age=69,
        matching his current age 69). RMD_START_AGE for someone
        currently 73 (born 1953) is 73 -- already AT it, so the
        conversion window is exactly 0 years (no conversions possible,
        this household is already at RMD age)."""
        inputs = base_inputs(jason_age=73, justin_age=69)
        r = run_roth_conversion_analysis(inputs, PRETAX(300000) + TAXABLE(500000),
                                          jason_ret_age=71, justin_ret_age=69)
        assert r["phase2_start_age"] == 73
        assert r["conversion_years"] == 0
        assert r["schedule"] == []
        assert r["total_conversions"] == 0

    def test_unequal_ages_use_each_spouses_own_ss_claim_age(self):
        """8-year age gap (Jason 71, Justin 63). Justin's own spousal SS
        (claim age 67 default) isn't active yet at phase2_start (Justin
        is 63) -- SS shouldn't appear in base_taxable until Justin's OWN
        age reaches 67, not Jason's."""
        inputs = base_inputs(jason_age=71, justin_age=63, justin_social_security=20000)
        r = run_roth_conversion_analysis(inputs, PRETAX(300000) + TAXABLE(500000),
                                          jason_ret_age=71, justin_ret_age=71)
        # Justin is 63 at phase2_start (age 71 for Jason, gap=8) --
        # nowhere near his own 67 claim age yet.
        assert r["schedule"][0]["base_taxable_income"] == -32200


class TestNonzeroInflationAndReturns:
    def test_conversion_amounts_grow_with_inflation_and_growth(self):
        """A positive growth rate must show up in roth_fv_at_73 (the
        second year's conversion, valued one fewer year of compounding
        than the naive age difference, per section 30.4) -- and 0%
        growth/inflation everywhere else in this file's tests would mask
        a broken compounding formula entirely."""
        inputs = base_inputs(expected_return_post_retirement=0.10, inflation_rate=0.03)
        r = run_roth_conversion_analysis(inputs, PRETAX(300000) + TAXABLE(500000),
                                          jason_ret_age=71, justin_ret_age=71)
        y0, y1 = r["schedule"]
        # Year 1's conversion (whatever it is) is only ONE year from RMD
        # age (73-72-1=0), so its roth_fv_at_73 must equal its own
        # optimal_conversion exactly (no compounding at all) --
        # independent of the exact dollar figures, this relationship
        # must hold under nonzero growth just as it did at 0%.
        assert y1["roth_fv_at_73"] == y1["optimal_conversion"]
        # Year 0's conversion DOES compound one year at the real growth
        # rate (73-71-1=1).
        assert y0["roth_fv_at_73"] == pytest.approx(y0["optimal_conversion"] * 1.10, rel=1e-6)


class TestShortfallReportsUnmetNeed:
    def test_insufficient_resources_report_unmet_need(self):
        """$0 pretax/roth/taxable, $50,000/yr spending, no guaranteed
        income at all -- every year is a real shortfall."""
        inputs = base_inputs(retirement_income_today_dollars=50000)
        r = run_roth_conversion_analysis(inputs, PRETAX(0) + TAXABLE(0),
                                          jason_ret_age=71, justin_ret_age=71)
        assert r["any_unmet_need"] is True
        assert r["total_unmet_need"] == 100000  # 2 years x 50000
        assert r["total_conversions"] == 0


class TestGrossIncomeIncludesBonusAndRsu:
    """Independent review, 2026-09-08, Roth Conversion follow-up, second
    pass, P1: the first fix derived the gross-wage figure by dividing
    the net spending-offset figure back out, which only ever recovered
    the SALARY component (two_age_still_working_income_inputs itself
    only reads w2_salary/justin_w2_salary) -- bonus and RSU
    compensation never entered base_taxable at all. Fixed by computing
    the gross figure directly from salary+bonus+RSU (the same input
    fields run_two_dimensional_retirement_projection's own
    accumulation-phase math already reads for this spouse)."""

    def test_rsu_alone_reduces_room_the_same_way_salary_does(self):
        """No base salary at all -- $100,000 RSU value only. Gross
        income = 0+0+100000 = 100000 -> base_taxable=100000-32200=67800
        -> room=211400-67800=143600, the exact same figure a $100,000
        W2 salary alone would produce (this file's own
        TestEitherRetirementOrderIsSymmetric case) -- proving RSU counts
        identically to salary, not zero."""
        inputs = base_inputs(retirement_income_today_dollars=50000,
                              justin_w2_salary=0, justin_annual_rsu_value=100000)
        r = run_roth_conversion_analysis(inputs, PRETAX(300000) + TAXABLE(500000),
                                          jason_ret_age=71, justin_ret_age=73)
        assert r["schedule"][0]["base_taxable_income"] == 67800
        assert r["schedule"][0]["room_in_22_bracket"] == 143600

    def test_bonus_and_rsu_both_add_to_a_base_salary(self):
        """$100,000 salary + 20% bonus ($20,000) + $30,000 RSU = 150000
        gross -> base_taxable=150000-32200=117800 ->
        room=211400-117800=93600 -- different from (less than) the
        salary-only $100,000 case's own 143600 room, proving bonus/RSU
        each add real, additional taxable income on top of salary."""
        inputs = base_inputs(retirement_income_today_dollars=50000,
                              justin_w2_salary=100000, justin_annual_bonus_pct=0.20,
                              justin_annual_rsu_value=30000)
        r = run_roth_conversion_analysis(inputs, PRETAX(300000) + TAXABLE(500000),
                                          jason_ret_age=71, justin_ret_age=73)
        assert r["schedule"][0]["base_taxable_income"] == 117800
        assert r["schedule"][0]["room_in_22_bracket"] == 93600


class TestSpendingWithdrawalRateReflectsWorkingIncome:
    """Independent review, 2026-09-08, Roth Conversion follow-up, second
    pass, P2: _pretax_marginal_tax_rate's estimate (used to gross up the
    year's own pretax SPENDING draw, not the conversion) never included
    gross wages -- a household with substantial working income priced
    its own withdrawal at an artificially low rate the same year its
    conversion bracket-capacity math already correctly counted that
    income. Direct unit-level verification of the fixed function itself
    (the cleanest way to isolate this specific change: gross wages
    affect BOTH the year's real cash flow AND its tax rate
    simultaneously in the full per-year loop, by design, so comparing
    two full schedules can't isolate the rate effect alone without also
    changing the cash flow being taxed)."""

    def test_gross_income_raises_the_marginal_rate(self):
        # No other income: taxable_income_est=max(0,0-32200)=0 ->
        # bottom bracket, 10%.
        rate_without = _pretax_marginal_tax_rate(0, 0, 0, 0.0, 0)
        assert rate_without == pytest.approx(0.10)
        # gross_income=500000 -> taxable_income_est=500000-32200=467800,
        # which falls in the 24%-403550..32%-512450 bracket -> 32%.
        rate_with = _pretax_marginal_tax_rate(0, 0, 0, 0.0, 0, gross_income=500000)
        assert rate_with == pytest.approx(0.32)


class TestAffordabilityCapIncludesUnusedDeductionRoom:
    """Independent review, 2026-09-08, Roth Conversion follow-up, second
    pass, P1: _max_conversion_for_tax_budget floored pre_conversion_
    taxable at 0 before walking the bracket table, silently dropping
    the SAME unused-standard-deduction free zone
    _incremental_conversion_tax's own formula already accounts for
    (both sides of its subtraction floored at 0, so a conversion
    "fills" leftover deduction room tax-free first) -- understating how
    much a low-taxable-income household could actually afford,
    inconsistent with the very tax formula the cap is supposed to
    match. $40,000 taxable (a budget deliberately small enough that
    affordability, not room_in_22, used to bind), $1,000,000 pretax, no
    income at all (base_taxable=-32200, room_in_22=243600). The old
    formula (ignoring the $32,200 free zone) capped the affordable
    conversion at $228,350 -- LESS than the true $243,600 room, so
    affordability was the (wrongly) binding constraint. The fixed
    formula affords $260,550 (the same $228,350 bracket-walk PLUS the
    $32,200 free zone), which now exceeds room_in_22 -- so room_in_22
    (243600) becomes the correctly-binding constraint again, matching
    every other zero-income test case in this file."""

    def test_free_deduction_room_lets_the_search_reach_the_full_bracket_room(self):
        inputs = base_inputs(retirement_income_today_dollars=0)
        r = run_roth_conversion_analysis(inputs, PRETAX(1000000) + TAXABLE(40000),
                                          jason_ret_age=71, justin_ret_age=71)
        y0 = r["schedule"][0]
        assert y0["base_taxable_income"] == -32200
        assert y0["room_in_22_bracket"] == 243600
        assert y0["optimal_conversion"] == 243600
        assert y0["tax_cost"] == 35932

    def test_state_tax_still_applies_to_the_free_zone(self):
        """Independent review, 2026-09-08, third follow-up, P1: the free
        zone is only free of FEDERAL tax -- state tax applies to the
        WHOLE conversion, including that zone, matching
        _incremental_conversion_tax's own formula exactly.
        Reproduced: $0 taxable cash, 5% state tax, $1,000,000 pretax, no
        other income (base_taxable=-32200, all of it "free" federally).
        The old fix treated the entire $32,200 free zone as costing
        $0 regardless of state tax, so it reported $32,200 as
        affordable with $0 cash on hand -- the real $1,610 state-tax
        bill on that conversion then had nowhere to be funded from and
        silently ate into Roth via simulate_conversion's own
        conversion_shortfall fallback. With $0 taxable cash, the
        correctly-fixed affordability is $0 -- nothing is convertible
        without any cash to pay even the state tax."""
        inputs = base_inputs(retirement_income_today_dollars=0, state_income_tax_rate=0.05)
        r = run_roth_conversion_analysis(inputs, PRETAX(1000000) + TAXABLE(0),
                                          jason_ret_age=71, justin_ret_age=71)
        y0 = r["schedule"][0]
        assert y0["base_taxable_income"] == -32200
        assert y0["optimal_conversion"] == 0
        assert y0["tax_cost"] == 0
        assert y0["taxable_after"] == 0
        assert y0["roth_after"] == 0  # no conversion_shortfall silently draining Roth


class TestTwoAgeRothConversionModeRequiresBothAges:
    def test_only_jason_ret_age_raises(self):
        inputs = base_inputs()
        with pytest.raises(ValueError):
            run_roth_conversion_analysis(inputs, PRETAX(300000), jason_ret_age=71)

    def test_only_justin_ret_age_raises(self):
        inputs = base_inputs()
        with pytest.raises(ValueError):
            run_roth_conversion_analysis(inputs, PRETAX(300000), justin_ret_age=71)


class TestSingleAgeModeUnaffected:
    def test_default_call_has_no_two_age_fields(self, sample_inputs, sample_accounts):
        r = run_roth_conversion_analysis(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        assert "mode" not in r
        assert "jason_ret_age" not in r
