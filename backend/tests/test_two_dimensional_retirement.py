"""
Independent, hand-calculated reference tests for
run_two_dimensional_retirement_projection — written BEFORE that function
exists (backend/docs/TWO_DIMENSIONAL_RETIREMENT_DESIGN.md section 7,
2026-09-08), per the explicit instruction to write these cases before
touching calculations. This file is expected to fail at import/collection
until the function is implemented in projection_engine.py.

Every expected number below was computed by hand from the contract in the
design doc, not by running a draft implementation first (same standard
this session used for the Survivor insurance-calculation fixes). Every
case uses 0% inflation and 0% pre/post-retirement returns unless the case
is specifically about growth, so the arithmetic is exact, not
approximate — amounts are asserted with `==`, not pytest.approx(), except
where noted.

Covers every category named in the instruction: either spouse retiring
first (2 cases), simultaneous retirement, unequal ages, an already-
retired spouse, an income surplus, and insufficient funds.
"""

import pytest

from projection_engine import run_two_dimensional_retirement_projection

TAXABLE = lambda balance: [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": balance}]


def base_inputs(**overrides):
    """Zeroed-out household: no pension/SS/healthcare/life events/RSU/
    bonus/401k, so every dollar in a test case's arithmetic comes from
    the explicit spending target and salary figures the test sets,
    nothing implicit."""
    inputs = {
        "jason_age": 60, "justin_age": 60,
        "inflation_rate": 0.0,
        "expected_return_pre_retirement": 0.0,
        "expected_return_post_retirement": 0.0,
        "retirement_income_today_dollars": 80000,
        "annual_hsa_contribution": 0, "annual_rsu_value": 0,
        "jason_social_security": 0, "justin_social_security": 0,
        "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
        "justin_w2_salary": 0, "justin_employee_401k_pct": 0, "justin_employer_401k_pct": 0,
        "justin_annual_bonus_pct": 0, "justin_annual_rsu_value": 0,
        "w2_salary": 0, "employee_401k_pct": 0, "employer_401k_pct": 0,
        "annual_bonus_pct": 0,
    }
    inputs.update(overrides)
    return inputs


class TestEitherSpouseRetiringFirst:
    def test_jason_retires_first_justin_still_working(self):
        """jason_ret_age=61 (1yr), justin_ret_age=63 (3yr) -- Justin is
        the still-working spouse for the 2-year middle phase (ages
        61-62), then both retired at 63. Justin salary $100,000, no
        401k/bonus/RSU -> net gap income = $100,000 * 0.65 = $65,000/yr
        flat (0% salary growth). Household need $80,000/yr flat.
        taxable starts at $200,000, 0% growth throughout.

        yr0 (age61, phase2): draw = 80000-65000 = 15000, bal 200000->185000
        yr1 (age62, phase2): draw = 15000, bal 185000->170000
        yr2 (age63, phase3, no gap income): draw = 80000, bal 170000->90000
        """
        inputs = base_inputs(retirement_end_age=64, justin_w2_salary=100000)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(200000), jason_ret_age=61, justin_ret_age=63)
        assert result["phase2_start_age"] == 61
        assert result["phase3_start_age"] == 63
        assert result["later_retiree"] == "justin"
        yearly = result["yearly_detail"]
        assert [y["jason_age"] for y in yearly] == [61, 62, 63]
        assert [y["phase"] for y in yearly] == ["phase2", "phase2", "phase3"]
        assert [y["still_working_spouse_income"] for y in yearly] == [65000, 65000, 0]
        assert [y["income_need"] for y in yearly] == [80000, 80000, 80000]
        assert [y["draw"] for y in yearly] == [15000, 15000, 80000]
        assert [y["portfolio_balance"] for y in yearly] == [185000, 170000, 90000]
        assert result["second_earner_net_of_tax_factor"] == 0.65

    def test_justin_retires_first_jason_still_working(self):
        """Mirror image of the case above: justin_ret_age=61 (1yr),
        jason_ret_age=63 (3yr) -- Jason is the still-working spouse.
        Jason salary $100,000, no 401k/bonus/RSU -> identical numbers to
        the Justin-still-working case, proving the 65% offset applies
        symmetrically to whichever spouse is later, not hardcoded to
        Justin."""
        inputs = base_inputs(retirement_end_age=64, w2_salary=100000)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(200000), jason_ret_age=63, justin_ret_age=61)
        assert result["phase2_start_age"] == 61
        assert result["phase3_start_age"] == 63
        assert result["later_retiree"] == "jason"
        yearly = result["yearly_detail"]
        assert [y["jason_age"] for y in yearly] == [61, 62, 63]
        assert [y["phase"] for y in yearly] == ["phase2", "phase2", "phase3"]
        assert [y["still_working_spouse_income"] for y in yearly] == [65000, 65000, 0]
        assert [y["draw"] for y in yearly] == [15000, 15000, 80000]
        assert [y["portfolio_balance"] for y in yearly] == [185000, 170000, 90000]


class TestSimultaneousRetirement:
    def test_equal_ages_has_zero_phase2_years_and_matches_single_axis_reference(self):
        """jason_ret_age == justin_ret_age == 62 -> no middle phase at
        all, every year is phase3 from the start. This is the
        regression check: with justin_ret_age explicitly set equal to
        jason_ret_age, the existing single-axis
        run_retirement_projection(ret_ages=[62]) computes zero gap
        income for this same household (justin_gap_years =
        justin_years_to_retire - years_to_retire = 2-2 = 0) -- the two
        functions' withdrawal-phase numbers must agree exactly."""
        inputs = base_inputs(retirement_end_age=64)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(200000), jason_ret_age=62, justin_ret_age=62)
        assert result["phase2_start_age"] == result["phase3_start_age"] == 62
        assert result["later_retiree"] is None
        yearly = result["yearly_detail"]
        assert [y["jason_age"] for y in yearly] == [62, 63]
        assert [y["phase"] for y in yearly] == ["phase3", "phase3"]
        assert [y["still_working_spouse_income"] for y in yearly] == [0, 0]
        assert [y["draw"] for y in yearly] == [80000, 80000]
        assert [y["portfolio_balance"] for y in yearly] == [120000, 40000]

        from projection_engine import run_retirement_projection
        single_axis_inputs = dict(inputs)
        single_axis_inputs["justin_ret_age"] = 62
        reference = run_retirement_projection(single_axis_inputs, TAXABLE(200000), ret_ages=[62])
        ref_scenario = next(s for s in reference["scenarios"] if s["label"] == "age_62_early")
        assert [y["portfolio_balance"] for y in ref_scenario["yearly_detail"]] == [120000, 40000]


class TestUnequalAges:
    def test_ten_year_age_gap(self):
        """Jason 65 (2yrs to 67), Justin 55 (5yrs to 60), age_gap=10.
        Justin salary $90,000, no 401k/bonus/RSU -> gap income =
        $58,500/yr flat. Household need $70,000/yr. taxable starts at
        $300,000.

        yr0 (jason67/justin57, phase2): draw=70000-58500=11500, bal 300000->288500
        yr1 (jason68/justin58, phase2): draw=11500, bal 288500->277000
        yr2 (jason69/justin59, phase2): draw=11500, bal 277000->265500
        yr3 (jason70/justin60, phase3 -- Justin's own retirement age exactly): draw=70000, bal 265500->195500
        """
        inputs = base_inputs(jason_age=65, justin_age=55, retirement_income_today_dollars=70000,
                              retirement_end_age=71, justin_w2_salary=90000)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(300000), jason_ret_age=67, justin_ret_age=60)
        assert result["phase2_start_age"] == 67
        assert result["phase3_start_age"] == 70
        yearly = result["yearly_detail"]
        assert [y["jason_age"] for y in yearly] == [67, 68, 69, 70]
        assert [y["justin_age"] for y in yearly] == [57, 58, 59, 60]
        assert [y["phase"] for y in yearly] == ["phase2", "phase2", "phase2", "phase3"]
        assert [y["draw"] for y in yearly] == [11500, 11500, 11500, 70000]
        assert [y["portfolio_balance"] for y in yearly] == [288500, 277000, 265500, 195500]


class TestAlreadyRetiredSpouse:
    def test_jason_already_past_his_selected_retirement_age(self):
        """Jason 68 today, selected ret_age 65 -- already 3 years past,
        clamps to 0 years-to-retire, so phase2 (withdrawal) begins
        IMMEDIATELY this year, not at some fictional future age. Justin
        60, ret_age 64 (4yrs), salary $80,000 -> gap income $52,000/yr.
        Household need $60,000/yr. taxable starts at $100,000.

        yr0 (age68, phase2): draw=60000-52000=8000, bal 100000->92000
        yr1 (age69): draw=8000, bal 92000->84000
        yr2 (age70): draw=8000, bal 84000->76000
        yr3 (age71): draw=8000, bal 76000->68000
        yr4 (age72, phase3): draw=60000, bal 68000->8000
        """
        inputs = base_inputs(jason_age=68, justin_age=60, retirement_income_today_dollars=60000,
                              retirement_end_age=73, justin_w2_salary=80000)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(100000), jason_ret_age=65, justin_ret_age=64)
        assert result["phase2_start_age"] == 68   # this year, not 65
        assert result["phase3_start_age"] == 72
        yearly = result["yearly_detail"]
        assert [y["jason_age"] for y in yearly] == [68, 69, 70, 71, 72]
        assert [y["phase"] for y in yearly] == ["phase2", "phase2", "phase2", "phase2", "phase3"]
        assert [y["draw"] for y in yearly] == [8000, 8000, 8000, 8000, 60000]
        assert [y["portfolio_balance"] for y in yearly] == [92000, 84000, 76000, 68000, 8000]


class TestIncomeSurplus:
    def test_still_working_spouse_income_exceeding_need_is_swept_into_savings(self):
        """Justin salary $200,000 -> gap income $130,000/yr, well above
        the $50,000/yr household need -- the $80,000/yr surplus during
        phase2 must be swept into the portfolio as savings (the shared
        engine's existing surplus behavior, reused here with no new
        code), not discarded or floored at zero. taxable starts small
        ($10,000) to make the effect the dominant driver of the balance.

        yr0 (age61, phase2): net = 50000-130000 = -80000 (surplus), bal 10000+80000=90000
        yr1 (age62, phase2): surplus 80000, bal 90000+80000=170000
        yr2 (age63, phase2): surplus 80000, bal 170000+80000=250000
        yr3 (age64, phase3, no gap income): draw=50000, bal 250000-50000=200000
        """
        inputs = base_inputs(retirement_income_today_dollars=50000, retirement_end_age=65, justin_w2_salary=200000)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(10000), jason_ret_age=61, justin_ret_age=64)
        yearly = result["yearly_detail"]
        assert [y["jason_age"] for y in yearly] == [61, 62, 63, 64]
        assert [y["phase"] for y in yearly] == ["phase2", "phase2", "phase2", "phase3"]
        # A negative draw isn't reported -- surplus years show draw==0
        # and unmet_need==0, same convention every other consumer's
        # `draw = max(0, need)` already uses.
        assert [y["draw"] for y in yearly] == [0, 0, 0, 50000]
        assert [y["portfolio_balance"] for y in yearly] == [90000, 170000, 250000, 200000]
        assert [y["unmet_need"] for y in yearly] == [0, 0, 0, 0]
        assert result["any_year_underfunded"] is False


class TestInsufficientFunds:
    def test_shortfall_persists_and_is_reported_across_the_phase_boundary(self):
        """Justin salary only $50,000 -> gap income $32,500/yr, well
        short of the $80,000/yr need, and taxable starts at only
        $20,000 -- nowhere near enough to cover any year. Every year
        floors the portfolio at 0 and reports the real unmet_need,
        including the phase2->phase3 transition year, rather than
        silently resetting or hiding the shortfall at the boundary.

        yr0 (age61, phase2): draw=80000-32500=47500, bal 20000<47500 -> unmet=27500, bal floors at 0
        yr1 (age62, phase2): draw=47500, bal 0<47500 -> unmet=47500, bal 0
        yr2 (age63, phase3): draw=80000, bal 0<80000 -> unmet=80000, bal 0
        """
        inputs = base_inputs(retirement_end_age=64, justin_w2_salary=50000)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(20000), jason_ret_age=61, justin_ret_age=63)
        yearly = result["yearly_detail"]
        assert [y["jason_age"] for y in yearly] == [61, 62, 63]
        assert [y["phase"] for y in yearly] == ["phase2", "phase2", "phase3"]
        assert [y["draw"] for y in yearly] == [47500, 47500, 80000]
        assert [y["unmet_need"] for y in yearly] == [27500, 47500, 80000]
        assert [y["portfolio_balance"] for y in yearly] == [0, 0, 0]
        assert result["any_year_underfunded"] is True
