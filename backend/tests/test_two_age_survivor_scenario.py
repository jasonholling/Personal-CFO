"""
Independent, hand/script-verified reference cases for two-age Survivor
Scenario + account ownership (CALCULATION_CONTRACT.md sections 36-37,
Milestone 4 of 4, design approved by Jason 2026-09-08 before any of
this existed). Every number below was derived either by hand (the
ownership-transfer arithmetic -- plain sums/halves of already-
reconciled owner-split balances) or by directly driving the shared,
already-reviewed primitives (_rmd, simulate_withdrawal_year) through a
standalone script, not inferred from the code under test.
"""

import pytest

from simulation_engine import run_survivor_scenario
from projection_engine import rmd_start_age

TAXABLE = lambda balance, owner: {"name": "Taxable", "account_type": "taxable", "owner": owner, "balance": balance}
IRA     = lambda balance, owner: {"name": "IRA", "account_type": "ira", "owner": owner, "balance": balance}


def base_inputs(**overrides):
    inputs = {
        "jason_age": 61, "justin_age": 61,
        "inflation_rate": 0.0,
        "expected_return_pre_retirement": 0.0,
        "expected_return_post_retirement": 0.0,
        "retirement_income_today_dollars": 80000,
        "jason_social_security": 0, "justin_social_security": 0,
        "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
        "w2_salary": 0, "justin_w2_salary": 0,
        "pension_55": 0, "pension_60": 0, "pension_65": 0,
        "retirement_end_age": 70,
    }
    inputs.update(overrides)
    return inputs


class TestOwnershipTransferAtDeath:
    """Both retire now at 61, $80,000/yr spending, 0% inflation/growth,
    no guaranteed income. Jason owns a $200,000 IRA; Justin owns
    $100,000 taxable; joint holds $50,000 taxable; a trust holds
    $80,000. Death at 62 -- the walk covers ages 61 and 62 (2 years of
    $80,000 spending each, the death year itself included, per section
    37.5's "runs as a normal both-alive year"), drawn taxable-first
    (pooled: justin+joint+trust = $230,000), owner-order joint ->
    jason(none) -> justin -> trust:

    Year 0: draw $80,000 from pooled taxable ($250,000 available) ->
    joint (50000, exhausted) contributes 50000, justin contributes the
    remaining 30000 -> joint=0, justin=70000, trust=80000 (untouched).
    Year 1 (death year): draw $80,000 again -> joint=0 (nothing),
    justin (70000) contributes 70000 (exhausted), trust contributes the
    remaining 10000 -> justin=0, trust=70000. Jason's $200,000 pretax
    is untouched both years (taxable always covered the need).

    portfolio_at_death = 200000 (jason pretax, untouched) + 0 (justin)
    + 0 (joint) + 70000 (trust) = 270000."""

    ACCOUNTS = [
        IRA(200000, "jason"),
        TAXABLE(100000, "justin"),
        TAXABLE(50000, "joint"),
        TAXABLE(80000, "trust"),
    ]

    def test_portfolio_at_death_matches_the_owner_split_walk(self):
        r = run_survivor_scenario(base_inputs(), self.ACCOUNTS, deceased="jason", death_age=62,
                                   jason_ret_age=61, justin_ret_age=61)
        assert r["mode"] == "two_age"
        assert r["portfolio_at_death"] == 270000
        assert r["trust_balance_at_death"] == 70000
        assert r["joint_balance_at_death"] == 0

    def test_default_rollover_merges_deceased_pretax_into_survivor(self):
        """Default spousal_rollover_election=True: Jason's own $200,000
        pretax (untouched pre-death) merges into Justin's (the
        survivor's) resources, on top of Justin's own $0 taxable and
        trust's $70,000 (NOT included by default -- see the next test).
        starting_balance = 200000 (rolled-over pretax) + 0 (Justin's own)
        + 0 (joint, already drained) + 0 (trust, excluded by default) =
        200000."""
        r = run_survivor_scenario(base_inputs(), self.ACCOUNTS, deceased="jason", death_age=62,
                                   jason_ret_age=61, justin_ret_age=61)
        assert r["starting_balance_after_payout"] == 200000

    def test_declining_rollover_excludes_the_deceased_pretax_entirely(self):
        """spousal_rollover_election=False: the $200,000 rolled-over
        pretax above is EXCLUDED -- v1 doesn't attempt to model the
        alternative (a separately-scheduled inherited IRA). starting
        balance drops to $0 (everything else in this household was
        already drained pre-death)."""
        r = run_survivor_scenario(base_inputs(), self.ACCOUNTS, deceased="jason", death_age=62,
                                   jason_ret_age=61, justin_ret_age=61, spousal_rollover_election=False)
        assert r["starting_balance_after_payout"] == 0
        assert r["spousal_rollover_election"] is False

    def test_trust_available_flag_adds_the_trust_balance(self):
        r = run_survivor_scenario(base_inputs(), self.ACCOUNTS, deceased="jason", death_age=62,
                                   jason_ret_age=61, justin_ret_age=61, trust_available_to_survivor=True)
        # 200000 (rollover) + 0 + 0 + 70000 (trust, now included) = 270000
        assert r["starting_balance_after_payout"] == 270000


class TestJointAccountSurvivorshipAssumption:
    """A single $500,000 joint account, $200,000 Jason-owned pretax, no
    other assets, no spending before death (income_today=0, immediate
    death at 61 -- the household's own current age, so the pre-death
    walk's single mandatory year spends $0)."""

    ACCOUNTS = [IRA(200000, "jason"), TAXABLE(500000, "joint")]

    def test_full_survivorship_passes_the_whole_joint_balance(self):
        inputs = base_inputs(retirement_income_today_dollars=0, retirement_end_age=61)
        r = run_survivor_scenario(inputs, self.ACCOUNTS, deceased="jason", death_age=61,
                                   jason_ret_age=61, justin_ret_age=61)
        assert r["joint_balance_at_death"] == 500000
        assert r["starting_balance_after_payout"] == 700000  # 200000 + 500000

    def test_tenants_in_common_default_passes_only_half(self):
        """The stated legal default when survivorship is explicitly
        declined -- half the joint balance is modeled as lost to the
        deceased's estate, not silently split as an ownership guess."""
        inputs = base_inputs(retirement_income_today_dollars=0, retirement_end_age=61)
        r = run_survivor_scenario(inputs, self.ACCOUNTS, deceased="jason", death_age=61,
                                   jason_ret_age=61, justin_ret_age=61, joint_accounts_survivorship=False)
        assert r["starting_balance_after_payout"] == 450000  # 200000 + 500000*0.5


class TestPensionCommencementBeforeJasonsOwnRetirement:
    """Section 37.5, decided (Option A): a $30,000 pension pays the
    survivor in FULL even if Jason (its owner) dies before his own
    actual retirement. Jason retiring at 70 (has NOT retired at death,
    age 63), Justin retiring now (63) -- Jason is later_retiree, still
    "working" in this model's own terms at the moment of death."""

    def test_full_pension_despite_jason_not_yet_retired(self):
        inputs = base_inputs(jason_age=63, justin_age=63, retirement_income_today_dollars=80000,
                              pension_55=30000, pension_60=30000, pension_65=30000, retirement_end_age=64)
        accounts = [TAXABLE(1000000, "joint")]
        r = run_survivor_scenario(inputs, accounts, deceased="jason", death_age=63,
                                   jason_ret_age=70, justin_ret_age=63)
        assert r["pension_annual"] == 30000

    def test_survivor_pension_unaffected_when_justin_is_deceased(self):
        """Jason (the survivor) simply continues receiving his own
        pension normally when JUSTIN is the one who died -- no special
        casing needed, it was always his own benefit."""
        inputs = base_inputs(jason_age=63, justin_age=63, retirement_income_today_dollars=80000,
                              pension_55=30000, pension_60=30000, pension_65=30000, retirement_end_age=64)
        accounts = [TAXABLE(1000000, "joint")]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=63,
                                   jason_ret_age=63, justin_ret_age=70)
        assert r["pension_annual"] == 30000


class TestRealPostDeathRmdOnSurvivorsOwnAge:
    """$1,000,000 pretax (Jason-owned), $0 spending (isolates the RMD
    mechanic from ordinary draws), immediate death at 61. Justin (the
    survivor)'s own rmd_start_age is 75 (born 1965, current-date-
    relative). Script-verified: at survivor age 75, _rmd(1000000, 75,
    75) = 1000000/24.6 = 40650.41, forced regardless of the $0 spending
    need (swept into the "other" bucket as savings, not spent) --
    reducing the survivor's own PRETAX sub-balance (not Jason's age,
    which every OTHER two-age consumer still uses for its own
    still-out-of-scope aggregate RMD -- this is a genuinely new,
    correctly-survivor-anchored capability)."""

    def test_rmd_start_age_is_75_for_someone_currently_61(self):
        assert rmd_start_age(61) == 75

    def test_no_rmd_before_the_survivors_own_rmd_start_age(self):
        inputs = base_inputs(retirement_income_today_dollars=0, retirement_end_age=70)
        accounts = [IRA(1000000, "jason")]
        r = run_survivor_scenario(inputs, accounts, deceased="jason", death_age=61,
                                   jason_ret_age=61, justin_ret_age=61)
        assert all(row["required_minimum_distribution"] == 0 for row in r["schedule"])
        assert all(row["ending_balance"] == 1000000 for row in r["schedule"])  # nothing spent, no RMD yet

    def test_rmd_becomes_nonzero_once_the_survivor_reaches_their_own_rmd_age(self):
        inputs = base_inputs(retirement_income_today_dollars=0, retirement_end_age=78)
        accounts = [IRA(1000000, "jason")]
        r = run_survivor_scenario(inputs, accounts, deceased="jason", death_age=61,
                                   jason_ret_age=61, justin_ret_age=61)
        last_row = r["schedule"][-1]
        assert last_row["age"] == 76  # sampled every other year; 76 is the last <= 78
        assert last_row["required_minimum_distribution"] > 0
        # RMD is reinvested (swept to savings), not spent -- the TOTAL
        # balance is unaffected even though the RMD amount is nonzero.
        assert last_row["ending_balance"] == 1000000


class TestGapIncomeGeneralizesToWhicheverSpouseIsLaterRetiree:
    """Section 37.3: single-axis only ever gated gap income on
    `deceased != "justin"`, since only Justin could be the still-
    working spouse there. Two-age generalizes to `deceased !=
    later_retiree` -- verified here with JASON as the later, still-
    working retiree (impossible in single-axis)."""

    def test_gap_income_disappears_when_the_still_working_spouse_dies(self):
        """Jason is later_retiree (retires at 65, 4 years after Justin's
        61) -- if JASON is the one who dies, his own gap income must
        stop entirely (not taper at his own retirement age), matching
        the existing single-axis rule generalized to whichever spouse
        it actually is."""
        inputs = base_inputs(retirement_income_today_dollars=50000, w2_salary=100000, retirement_end_age=66)
        accounts = [TAXABLE(500000, "joint")]
        r = run_survivor_scenario(inputs, accounts, deceased="jason", death_age=61,
                                   jason_ret_age=65, justin_ret_age=61)
        # $50,000 today's-dollars * 0.75 survivor_need_factor (default)
        # = $37,500 -- WITHOUT any further gap-income offset, since gap
        # income must be zero here (Jason, the still-working spouse, is
        # the one who died). If the old single-axis-only `deceased !=
        # "justin"` gate were still in effect (it would incorrectly
        # evaluate to True for deceased="jason"), this would show LESS
        # than $37,500.
        assert r["schedule"][0]["draw"] == 37500

    def test_gap_income_continues_when_the_still_working_spouse_survives(self):
        """Mirror -- Justin (not the still-working spouse) is the one
        who dies; Jason (later_retiree, still earning $100,000 -> a
        $65,000 net gap-income offset) survives. The $37,500 need
        (50000*0.75) is more than covered by that $65,000 offset -- draw
        is $0, not $37,500."""
        inputs = base_inputs(retirement_income_today_dollars=50000, w2_salary=100000, retirement_end_age=66)
        accounts = [TAXABLE(500000, "joint")]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=61,
                                   jason_ret_age=65, justin_ret_age=61)
        assert r["schedule"][0]["draw"] == 0


class TestTwoAgeSurvivorModeRequiresBothAges:
    def test_only_jason_ret_age_raises(self):
        with pytest.raises(ValueError):
            run_survivor_scenario(base_inputs(), [TAXABLE(100000, "joint")], jason_ret_age=61)

    def test_only_justin_ret_age_raises(self):
        with pytest.raises(ValueError):
            run_survivor_scenario(base_inputs(), [TAXABLE(100000, "joint")], justin_ret_age=61)


class TestSingleAgeModeUnaffected:
    def test_default_call_has_no_two_age_fields(self, sample_inputs, sample_accounts):
        r = run_survivor_scenario(sample_inputs, sample_accounts, ret_age=60, deceased="jason")
        assert "mode" not in r
        assert "jason_ret_age" not in r
