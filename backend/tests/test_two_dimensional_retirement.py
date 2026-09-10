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


class TestPensionGatedToJasonsOwnRetirement:
    def test_pension_does_not_start_until_jason_actually_retires(self):
        """Independent review, 2026-09-08 (P1) -- reproduction: both
        spouses 60, Justin retires at 61 (1yr), Jason at 63 (3yr) --
        Jason is later_retiree, so his own salary funds the phase2
        offset while his PENSION must not start until he actually
        retires at 63. Jason salary $100,000 -> gap income $65,000/yr.
        Pension $30,000/yr. Household need $80,000/yr. taxable starts
        at $200,000, 0% growth/inflation.

        yr0 (age61, phase2): pension=0 (not yet retired), draw=80000-65000=15000, bal 200000->185000
        yr1 (age62, phase2): pension=0, draw=15000, bal 185000->170000
        yr2 (age63, phase3): pension=30000 (now retired), draw=80000-30000=50000, bal 170000->120000

        The bug (pension added unconditionally from phase2_start) would
        instead treat pension as a $30,000/yr surplus on top of the
        $65,000 gap income during the two premature years, giving a
        final balance of $180,000 -- two premature $30,000 payments
        above the correct $120,000."""
        inputs = base_inputs(retirement_end_age=64, w2_salary=100000,
                              pension_55=30000, pension_60=30000, pension_65=30000)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(200000), jason_ret_age=63, justin_ret_age=61)
        yearly = result["yearly_detail"]
        assert [y["jason_age"] for y in yearly] == [61, 62, 63]
        assert [y["phase"] for y in yearly] == ["phase2", "phase2", "phase3"]
        assert [y["pension"] for y in yearly] == [0, 0, 30000]
        assert [y["draw"] for y in yearly] == [15000, 15000, 50000]
        assert [y["portfolio_balance"] for y in yearly] == [185000, 170000, 120000]
        old_buggy_final_balance = 180000  # matches the independent review's own reported old value
        assert result["yearly_detail"][-1]["portfolio_balance"] != old_buggy_final_balance

    def test_pension_starts_immediately_when_jason_retires_first(self):
        """The mirror case -- Jason retires FIRST (61), Justin later
        (63). Jason's pension should start right at phase2_start (61),
        not be delayed to phase3 -- he's already retired by then."""
        inputs = base_inputs(retirement_end_age=64, justin_w2_salary=100000,
                              pension_55=30000, pension_60=30000, pension_65=30000)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(200000), jason_ret_age=61, justin_ret_age=63)
        yearly = result["yearly_detail"]
        assert [y["pension"] for y in yearly] == [30000, 30000, 30000]


class TestAge55BridgeAndKidsRulesPreserved:
    def test_bridge_income_phase_matches_single_axis_reference_exactly(self):
        """Independent review, 2026-09-08 (P1) -- the age-55 bridge-job/
        kids-at-home spending phases (run_retirement_projection's own
        `if ret_age == 55:` branch) were entirely absent from the first
        cut of this function. Reproduction: both spouses AT 55 today,
        both retiring at 55 (simultaneous -- zero phase2 years, every
        loop year is phase3), $80,000 household spend, $30,000/yr
        bridge income for 5 years, zero starting balance offsets.
        taxable starts at $200,000.

        Bridge-active year: year_need = max(0, 80000 - 30000) = 50000,
        not the plain 80000 a household outside the age-55 bridge would
        see -- draw 50000/yr for 5 years, matching
        run_retirement_projection's own ret_age=55 output exactly for
        the same inputs (direct parity check below), not just this
        function's own arithmetic."""
        inputs = base_inputs(jason_age=55, justin_age=55, retirement_end_age=61,
                              bridge_income_55=30000, bridge_years_55=5)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(200000), jason_ret_age=55, justin_ret_age=55)
        yearly = result["yearly_detail"]
        assert [y["jason_age"] for y in yearly] == [55, 56, 57, 58, 59, 60]
        assert [y["draw"] for y in yearly] == [50000, 50000, 50000, 50000, 50000, 80000]
        assert [y["bridge_income"] for y in yearly] == [30000, 30000, 30000, 30000, 30000, 0]
        assert [y["portfolio_balance"] for y in yearly] == [150000, 100000, 50000, 0, 0, 0]

        from projection_engine import run_retirement_projection
        single_axis_inputs = dict(inputs)
        single_axis_inputs["justin_ret_age"] = 55
        reference = run_retirement_projection(single_axis_inputs, TAXABLE(200000), ret_ages=[55])
        ref_scenario = next(s for s in reference["scenarios"] if s["label"] == "age_55_early")
        ref_balances = [y["portfolio_balance"] for y in ref_scenario["yearly_detail"][:3]]
        assert ref_balances == [150000, 100000, 50000]
        assert [y["portfolio_balance"] for y in yearly[:3]] == ref_balances

    def test_kids_still_home_phase_uses_family_healthcare(self):
        """Bridge phase ends, kids still home (kids_years_at_home_55 >
        bridge_years_55) -- the family-healthcare-cost phase, distinct
        from the pre-Medicare default. Same household as above, but
        bridge only 2 years, kids home for 4."""
        inputs = base_inputs(jason_age=55, justin_age=55, retirement_end_age=58,
                              bridge_income_55=30000, bridge_years_55=2, kids_years_at_home_55=4,
                              healthcare_kids=10000)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        yearly = result["yearly_detail"]
        # yr0-1: bridge active, healthcare=0, need=max(0,80000-30000)=50000
        # yr2: bridge over (jason_yr=2, not <2), kids still home (2<4) -> family healthcare $10,000, need=80000+10000=90000
        assert [y["draw"] for y in yearly] == [50000, 50000, 90000]
        assert [y["healthcare_cost"] for y in yearly] == [0, 0, 10000]

    def test_bridge_and_inflation_use_jasons_effective_start_not_the_raw_selected_age(self):
        """Independent review, 2026-09-08, third follow-up (P1):
        `jason_yr = age - jason_ret_age` used the raw SELECTED age (55)
        even when the household is already well past it today -- for a
        household already 5 years past the selected age, the first
        withdrawal-loop year (which IS the household's real effective
        retirement start) got treated as "5 years into the bridge
        phase" instead of year 0, both over-inflating the spending need
        and expiring bridge income 5 years too early.

        Reproduction: both spouses currently 60, retirement selected at
        55 (already 5 years past -> jason_years_to_retire clamps to 0,
        so the effective start is THIS year, age 60), $80,000 spend,
        3% inflation, $30,000/yr bridge income for 5 years (from the
        effective start, not from the fictional age-55 date). The buggy
        formula compounded 5 years of inflation immediately AND treated
        bridge income as already expired (jason_yr=5, not <
        bridge_years=5, so the bridge branch wasn't even entered) --
        old (buggy) income_need: $80,000 * 1.03**5 = $92,742, with no
        bridge offset applied at all. Correct: no elapsed years yet, so
        the $80,000 base is untouched by inflation and bridge income is
        still active.

        income_need's own convention changed 2026-09-10 (two-age bridge-
        surplus fix, CALCULATION_CONTRACT.md section 73's follow-up):
        it's now the GROSS spending target, matching single-age
        run_retirement_projection's own income_need field after its own
        identical fix -- bridge income above spending used to vanish
        silently (the old `max(0, target - bridge)` clamp discarded any
        surplus before the withdrawal engine ever saw it), fixed by no
        longer netting bridge into the need at all and instead flowing
        it into guaranteed_income, where the shared engine's existing
        surplus-sweep logic handles it correctly. "draw" (informational,
        not the actual withdrawal) still nets bridge via fixed_income
        and is unchanged at $50,000."""
        inputs = base_inputs(jason_age=60, justin_age=60, inflation_rate=0.03, retirement_end_age=63,
                              bridge_income_55=30000, bridge_years_55=5)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        yearly = result["yearly_detail"]
        assert result["phase2_start_age"] == 60  # effective start -- this year, not the fictional 55
        assert yearly[0]["bridge_income"] == 30000  # still active -- not yet expired
        assert yearly[0]["income_need"] == 80000    # gross spending target -- bridge no longer netted into it
        assert yearly[0]["draw"] == 50000           # draw still correctly nets bridge via fixed_income
        old_buggy_income_need = 92742  # matches the independent review's own reported old value (5 years' inflation wrongly applied)
        assert yearly[0]["income_need"] != old_buggy_income_need


class TestJasonRsuStaysFlatUnlikeSalaryDerivedContributions:
    def test_jason_rsu_does_not_grow_with_salary_growth_pct(self):
        """Independent review, 2026-09-08, third follow-up (P2):
        run_retirement_projection treats Jason's own RSU grant as a
        FLAT dollar amount every year (`_fv_annuity`, unconditionally --
        never the growing-annuity variant 401k contributions/bonus use),
        but the new function's shared _contrib_fv helper applied
        salary_growth_pct to RSU too.

        Reproduction: 3 years to simultaneous retirement, $100,000
        annual RSU, 10% salary growth, 0% investment returns -- at the
        65% net-of-tax factor, flat contributions sum to
        $65,000 * 3 = $195,000. The buggy growing-annuity version added
        $215,150 instead."""
        inputs = base_inputs(retirement_income_today_dollars=0, annual_rsu_value=100000,
                              retirement_end_age=64, _salary_growth_pct=0.10)
        result = run_two_dimensional_retirement_projection(inputs, [], jason_ret_age=63, justin_ret_age=63)
        assert result["portfolio_at_phase2_start"] == 195000
        old_buggy_value = 215150  # matches the independent review's own reported old value
        assert result["portfolio_at_phase2_start"] != old_buggy_value

    def test_justin_rsu_still_grows_with_salary_growth_pct_matching_reference(self):
        """The asymmetry itself is an EXISTING, preserved convention --
        run_retirement_projection's own justin_annual_rsu handling (via
        _justin_contrib_fv) already lets Justin's RSU grow with
        salary_growth_pct, unlike Jason's. This function must match
        that existing asymmetry exactly, not "fix" it into a new,
        different symmetric behavior."""
        inputs = base_inputs(retirement_income_today_dollars=0,
                              justin_annual_rsu_value=100000, retirement_end_age=64, _salary_growth_pct=0.10)
        result = run_two_dimensional_retirement_projection(inputs, [], jason_ret_age=63, justin_ret_age=63)
        assert result["portfolio_at_phase2_start"] == 215150  # matches the reference's own growing-annuity value for Justin


class TestExpandedSimultaneousRetirementParity:
    """Independent review, 2026-09-08, third follow-up -- explicit
    instruction: expand the simultaneous-retirement parity check
    (already present as TestSimultaneousRetirement's own case) to also
    cover a past retirement selection, nonzero inflation, nonzero
    salary growth, and RSUs -- the exact dimensions the two bugs above
    were found in, none of which the original parity check exercised."""

    def test_past_retirement_selection_with_inflation(self):
        """Both spouses currently 60, retirement selected at 55
        (already past), 3% inflation, $80,000 spend, $500,000 taxable,
        0% returns -- direct numeric parity against
        run_retirement_projection's own output for the same inputs
        across the first three years, not just this function's own
        arithmetic in isolation."""
        inputs = base_inputs(jason_age=60, justin_age=60, inflation_rate=0.03, retirement_end_age=64)
        accounts = TAXABLE(500000)
        result = run_two_dimensional_retirement_projection(inputs, accounts, jason_ret_age=55, justin_ret_age=55)

        from projection_engine import run_retirement_projection
        reference = run_retirement_projection({**inputs, "justin_ret_age": 55}, accounts, ret_ages=[55])
        ref_scenario = next(s for s in reference["scenarios"] if s["label"] == "age_55_early")
        ref_balances = [y["portfolio_balance"] for y in ref_scenario["yearly_detail"][:3]]
        assert [y["portfolio_balance"] for y in result["yearly_detail"][:3]] == ref_balances

    def test_salary_growth_and_rsus(self):
        """Both spouses currently 57, retiring simultaneously at 60 (3
        years away -- not yet retired, so contributions are still
        accruing), 5% salary growth, Jason's RSU $50,000/yr, 0%
        pre/post returns, zero starting balance -- isolates the
        accumulation-phase arithmetic (portfolio at the moment
        withdrawal starts) against the reference's own output for the
        same inputs."""
        inputs = base_inputs(jason_age=57, justin_age=57, retirement_income_today_dollars=0,
                              annual_rsu_value=50000, retirement_end_age=61, _salary_growth_pct=0.05)
        accounts = []
        result = run_two_dimensional_retirement_projection(inputs, accounts, jason_ret_age=60, justin_ret_age=60)

        from projection_engine import run_retirement_projection
        reference = run_retirement_projection({**inputs, "justin_ret_age": 60}, accounts, ret_ages=[60],
                                                salary_growth_pct=0.05)
        ref_scenario = next(s for s in reference["scenarios"] if s["label"] == "age_60_early")
        assert result["portfolio_at_phase2_start"] == ref_scenario["portfolio_at_retirement"]
