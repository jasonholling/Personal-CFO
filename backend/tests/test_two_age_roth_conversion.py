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

from simulation_engine import run_roth_conversion_analysis

TAXABLE = lambda balance: [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": balance}]
PRETAX = lambda balance: [{"name": "IRA", "account_type": "pretax", "owner": "joint", "balance": balance}]


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
        assert y0["tax_cost"] == 53592
        assert y0["roth_fv_at_73"] == 243600
        assert y0["tax_avoided_at_73"] == 58464
        assert y0["net_benefit"] == 4872
        assert y0["pretax_after"] == 56400
        assert y0["roth_after"] == 243600
        assert y0["taxable_after"] == 396408

        assert y1["age"] == 72
        assert y1["optimal_conversion"] == 56400
        assert y1["tax_cost"] == 12408
        assert y1["roth_fv_at_73"] == 56400
        assert y1["tax_avoided_at_73"] == 13536
        assert y1["net_benefit"] == 1128
        assert y1["pretax_after"] == 0
        assert y1["roth_after"] == 300000
        assert y1["taxable_after"] == 334000

        assert r["total_conversions"] == 300000
        assert r["total_tax_cost"] == 66000
        assert r["total_tax_avoided"] == 72000
        assert r["net_lifetime_benefit"] == 6000
        assert r["pretax_at_rmd_age_with_conversion"] == 0
        assert r["pretax_at_rmd_age_no_conversion"] == 300000
        assert r["estimated_rmd_without_conversions"] == 11321


class TestEitherRetirementOrderIsSymmetric:
    """Justin retires first (2 years before Jason, large working-spouse
    salary folded into gap income) vs. Jason retiring first (mirror,
    Jason's own salary instead) -- since gap income never touches
    base_taxable/room_in_22 (section 30's contract), the conversion
    schedule's own tax math must be IDENTICAL in both directions,
    proving the two-age formulas are symmetric, not hardcoded to either
    spouse. $300,000 pretax / $500,000 taxable, no pension/SS,
    $50,000/yr spending, 0% inflation/growth. RMD_START_AGE=73 either
    way (Jason's age drives it, and Jason is 71 in both cases)."""

    def test_justin_retires_first_jason_two_years_later(self):
        inputs = base_inputs(retirement_income_today_dollars=50000, w2_salary=500000)
        r = run_roth_conversion_analysis(inputs, PRETAX(300000) + TAXABLE(500000),
                                          jason_ret_age=73, justin_ret_age=71)
        assert r["later_retiree"] == "jason"
        # phase2_start_age = 71 (Justin, the earlier retiree) -- same
        # conversion-window LENGTH as the simultaneous-retirement case
        # (73-71=2), just anchored to Justin's earlier date instead of
        # Jason's own.
        assert r["phase2_start_age"] == 71
        assert r["conversion_years"] == 2
        y0, y1 = r["schedule"]
        assert y0["optimal_conversion"] == 243600
        assert y0["tax_cost"] == 53592
        assert y1["optimal_conversion"] == 56400
        assert y1["tax_cost"] == 12408
        assert r["total_conversions"] == 300000

    def test_jason_retires_first_justin_two_years_later(self):
        """Mirror -- Jason's own salary this time, same numbers."""
        inputs = base_inputs(retirement_income_today_dollars=50000, justin_w2_salary=500000)
        r = run_roth_conversion_analysis(inputs, PRETAX(300000) + TAXABLE(500000),
                                          jason_ret_age=71, justin_ret_age=73)
        assert r["later_retiree"] == "justin"
        assert r["phase2_start_age"] == 71
        assert r["conversion_years"] == 2
        y0, y1 = r["schedule"]
        assert y0["optimal_conversion"] == 243600
        assert y0["tax_cost"] == 53592
        assert y1["optimal_conversion"] == 56400
        assert y1["tax_cost"] == 12408
        assert r["total_conversions"] == 300000


class TestWorkingIncomeNeverAffectsBracketCapacity:
    """Section 30.1/30.3's contract: working income (wages/bonus/RSU,
    either spouse) is folded ONLY into the spending-need offset, never
    into base_taxable/room_in_22 -- in either direction. A household
    with a large pension (exhausting the 22% bracket on its own) and a
    huge working-spouse salary must show EXACTLY the same base_taxable/
    room_in_22/optimal_conversion as the identical household with no
    working income at all."""

    def test_identical_bracket_math_with_and_without_a_working_spouse(self):
        no_wages = base_inputs(retirement_income_today_dollars=50000,
                                pension_55=150000, pension_60=150000, pension_65=150000)
        with_wages = base_inputs(retirement_income_today_dollars=50000,
                                  pension_55=150000, pension_60=150000, pension_65=150000,
                                  w2_salary=800000)
        r_no = run_roth_conversion_analysis(no_wages, PRETAX(300000) + TAXABLE(500000),
                                             jason_ret_age=71, justin_ret_age=71)
        r_with = run_roth_conversion_analysis(with_wages, PRETAX(300000) + TAXABLE(500000),
                                               jason_ret_age=73, justin_ret_age=71)
        assert r_no["schedule"][0]["base_taxable_income"] == r_with["schedule"][0]["base_taxable_income"]
        assert r_no["schedule"][0]["room_in_22_bracket"] == r_with["schedule"][0]["room_in_22_bracket"]
        assert r_no["schedule"][0]["optimal_conversion"] == r_with["schedule"][0]["optimal_conversion"]
        # Sanity: pension alone (150000-32200=117800) leaves real,
        # nonzero room (211400-117800=93600) -- a meaningful comparison,
        # not a trivially-zero one.
        assert r_no["schedule"][0]["room_in_22_bracket"] == 93600

    def test_pension_alone_can_exhaust_the_bracket_regardless_of_wages(self):
        """A large enough pension leaves ZERO room -- and a huge working
        salary alongside it doesn't create any either."""
        inputs = base_inputs(retirement_income_today_dollars=50000,
                              pension_55=300000, pension_60=300000, pension_65=300000,
                              w2_salary=900000)
        r = run_roth_conversion_analysis(inputs, PRETAX(300000) + TAXABLE(500000),
                                          jason_ret_age=73, justin_ret_age=71)
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
