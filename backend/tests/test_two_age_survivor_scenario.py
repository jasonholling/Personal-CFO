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


class TestPensionResumesForSurvivingJasonAfterHisOwnRetirement:
    """Independent review, 2026-09-08, fourth follow-up, finding 2 (P1):
    previously pension_annual was frozen at the death row's own
    (possibly still-gated-to-$0) snapshot for the whole post-death
    schedule. Justin dies at 61, BEFORE Jason's own retirement at 65 --
    the pension must turn ON once Jason (the survivor) reaches his own
    retirement age, not stay frozen at $0 forever. Under the old bug,
    pension_annual == death_row["pension"] == 0 (Jason hadn't retired at
    the death year) for every subsequent year too, so age 66's
    ending_balance below would have stayed $0 forever instead of
    reflecting the pension that actually starts at 65."""

    def test_pension_starts_zero_then_turns_on_at_jasons_own_retirement(self):
        inputs = base_inputs(jason_age=61, justin_age=61, retirement_income_today_dollars=0,
                              pension_55=30000, pension_60=30000, pension_65=30000,
                              retirement_end_age=67)
        accounts = [TAXABLE(0, "joint")]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=61,
                                   jason_ret_age=65, justin_ret_age=61)
        rows_by_age = {row["age"]: row for row in r["schedule"]}
        assert 62 in rows_by_age and 66 in rows_by_age
        # Before Jason's own retirement (65): no pension yet, $0 need -> nothing swept in.
        assert rows_by_age[62]["ending_balance"] == 0
        # After Jason's own retirement (65): pension is now on, $0 need -> the
        # surplus is swept into savings as it accumulates.
        assert rows_by_age[66]["ending_balance"] > 0
        # The reported headline figure reflects the FIRST post-death year
        # (age 62, before Jason's own retirement) -- correctly $0, not a
        # frozen guess about the whole future.
        assert r["pension_annual"] == 0


class TestSurvivorSocialSecurityRespectsTimingAndCola:
    """Independent review, 2026-09-08, fourth follow-up, finding 3 (P1):
    previously survivor_ss_annual was a single flat max(raw
    jason_social_security, raw justin_social_security) input, ignoring
    ss_timing (early vs delayed) and never compounding COLA from each
    spouse's own claim age. Under the old bug this would have reported
    the flat $30,000 raw early-claim input, never the selected $45,000
    delayed benefit at all."""

    def test_delayed_timing_and_cola_are_preserved_through_the_transition(self):
        inputs = base_inputs(jason_age=61, justin_age=61,
                              jason_social_security=30000, jason_ss_delayed=45000,
                              justin_social_security=0,
                              retirement_income_today_dollars=0, inflation_rate=0.03,
                              retirement_end_age=70)
        accounts = [TAXABLE(0, "joint")]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=67,
                                   jason_ret_age=62, justin_ret_age=62, ss_timing="delayed")
        # Jason claims at 67 (delayed timing) -- the survivor phase (starting age
        # 68) continues that SAME $45,000-based trajectory with one more year of
        # COLA: 45000 * 1.03 = 46350, never the flat $30,000 raw early input.
        assert r["survivor_ss_annual"] == pytest.approx(46350, rel=1e-6)


class TestPostDeathLifeEventsApplyToBothScheduleAndInsuranceCalc:
    """Independent review, 2026-09-08, fourth follow-up, finding 5 (P1):
    previously life_event_cash=0.0 was hardcoded in the post-death loop,
    silently dropping any post-death expense or windfall from both the
    schedule and _minimum_survivor_funding's insurance-shortfall calc.
    Under the old bug this $100,000 windfall would never have shown up
    -- ending_balance would have stayed $0 throughout."""

    def test_a_post_death_windfall_shows_up_in_the_ending_balance(self):
        inputs = base_inputs(jason_age=61, justin_age=61, retirement_income_today_dollars=0,
                              retirement_end_age=65)
        accounts = [TAXABLE(0, "joint")]
        life_events = [{"event_year": 2028, "one_time_cash_delta": 100000,
                         "monthly_cash_flow_delta": 0, "duration_months": 0}]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=61,
                                   jason_ret_age=61, justin_ret_age=61, life_events=life_events)
        assert r["schedule"][-1]["ending_balance"] == 100000


class TestDeathBeforeFirstRetirementIsExplicitlyRejected:
    """Independent review, 2026-09-08, fourth follow-up, finding 6 (P2):
    previously a death requested before either spouse retired silently
    snapped forward to the first available (post-retirement) row while
    still reporting the originally-requested death_age unchanged."""

    def test_death_before_phase2_start_is_rejected_not_silently_moved(self):
        inputs = base_inputs(jason_age=55, justin_age=55, retirement_end_age=70)
        accounts = [TAXABLE(100000, "joint")]
        r = run_survivor_scenario(inputs, accounts, deceased="jason", death_age=58,
                                   jason_ret_age=65, justin_ret_age=67)
        assert r["has_data"] is False
        assert r.get("error") == "death_before_first_retirement_unsupported"


class TestJointTrustPretaxPreservesAccountType:
    """Independent review, 2026-09-08, fourth follow-up, finding 7 (P2):
    previously joint/trust contributions were folded ENTIRELY into
    starting_other regardless of underlying account type, so a
    transferred pretax dollar lost its RMD-triggering status. Mirrors
    TestRealPostDeathRmdOnSurvivorsOwnAge above, but with a joint- and a
    trust-owned IRA instead of a Jason-owned one -- under the old bug
    required_minimum_distribution would have stayed $0 for every row."""

    def test_joint_pretax_still_produces_rmds_after_transfer(self):
        inputs = base_inputs(retirement_income_today_dollars=0, retirement_end_age=78)
        accounts = [IRA(1000000, "joint")]
        r = run_survivor_scenario(inputs, accounts, deceased="jason", death_age=61,
                                   jason_ret_age=61, justin_ret_age=61)
        assert r["schedule"][-1]["required_minimum_distribution"] > 0

    def test_trust_pretax_still_produces_rmds_when_trust_is_available(self):
        inputs = base_inputs(retirement_income_today_dollars=0, retirement_end_age=78)
        accounts = [IRA(1000000, "trust")]
        r = run_survivor_scenario(inputs, accounts, deceased="jason", death_age=61,
                                   jason_ret_age=61, justin_ret_age=61,
                                   trust_available_to_survivor=True)
        assert r["schedule"][-1]["required_minimum_distribution"] > 0


class TestDeceasedsFinalYearRmdIsSatisfied:
    """Independent review, 2026-09-08, fourth follow-up, finding 8 (P2):
    previously the deceased's own final-year RMD obligation was never
    independently checked (the pre-death walk's RMD is always Jason-
    anchored). Reproduced exactly per the review: Jason 61, Justin 75,
    Justin's own $1,000,000 IRA -- this repository's own RMD table gives
    _rmd(1000000, 75, 75) == 1000000/24.6 == $40,650.41, versus the old
    bug's $0.

    Independent review, 2026-09-08, sixth follow-up, finding 2 (P1):
    the shortfall must be TAXED (at that year's own pretax_tax_rate,
    same as any other pretax distribution) before landing in taxable --
    $0 other income here means the lowest bracket, 10% -- leaving
    $995,935 total, not the untaxed $1,000,000 the original version of
    this catch-up reported."""

    def test_justins_own_final_rmd_moves_from_pretax_to_taxable_at_death(self):
        inputs = base_inputs(jason_age=61, justin_age=75, retirement_income_today_dollars=0,
                              retirement_end_age=76)
        accounts = [IRA(1000000, "justin")]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=75,
                                   jason_ret_age=61, justin_ret_age=61)
        assert r["starting_balance_after_payout"] == pytest.approx(995935, abs=2)
        assert r["starting_pretax_after_payout"] == pytest.approx(1000000 - 40650, abs=1)
        assert r["starting_other_after_payout"] == pytest.approx(40650 * 0.9, abs=2)

    def test_no_double_draw_when_the_normal_death_year_already_satisfies_it(self):
        """Independent review, 2026-09-08, fifth follow-up, finding 1
        (P1): the ORIGINAL version of this catch-up computed Justin's
        full RMD against his END-of-death-year pretax balance (already
        net of whatever the normal death-year draw/RMD had already
        taken), then forced that WHOLE amount out again. Both spouses
        75, Justin's $1,000,000 sole pretax IRA -- the normal death
        year already withdraws $40,650 (pooled RMD, Jason also 75); the
        bug then pulled ANOTHER ~$38,998 from the remaining ~$959,350.
        Fixed: only the shortfall (if any) between Justin's own full
        obligation and what his own pretax was already reduced by this
        year is forced -- here, that's ~$0 (the normal year's draw
        already fully satisfies it)."""
        inputs = base_inputs(jason_age=75, justin_age=75, retirement_income_today_dollars=0,
                              retirement_end_age=76)
        accounts = [IRA(1000000, "justin")]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=75,
                                   jason_ret_age=61, justin_ret_age=61)
        # Pretax dropped by exactly ONE $40,650 RMD, not two -- the
        # normal year's own marginal tax on that RMD (unrelated to this
        # fix) is what keeps starting_other_after_payout below the full
        # pre-tax $40,650, so only the pretax side is asserted exactly.
        assert r["starting_pretax_after_payout"] == pytest.approx(1000000 - 40650, abs=2)
        assert r["starting_other_after_payout"] > 0
        # A double draw would leave ~$920,352 total (1,000,000 - 40,650 -
        # 38,998, each also net of tax) -- comfortably above that floor
        # confirms the second draw never happened.
        assert r["starting_balance_after_payout"] > 950000

    def test_shortfall_is_taxed_and_grows_with_the_death_year(self):
        """Independent review, 2026-09-08, sixth follow-up, finding 2
        (P1): with 10% growth on top of the same $0-spending/$0-other-
        income scenario, the correctly-taxed-then-grown shortfall
        ($40,650.41 taxed at 10% = $36,585.37 after-tax, both figures
        then grown 10% right along with everything else that year)
        gives $1,095,528.46 total, not the original bug's untaxed,
        ungrown $1,100,000."""
        inputs = base_inputs(jason_age=61, justin_age=75, retirement_income_today_dollars=0,
                              retirement_end_age=76, expected_return_post_retirement=0.10)
        accounts = [IRA(1000000, "justin")]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=75,
                                   jason_ret_age=61, justin_ret_age=61)
        assert r["starting_balance_after_payout"] == pytest.approx(1095528, abs=2)

    def test_jasons_own_shortfall_is_also_enforced_not_only_justins(self):
        """Independent review, 2026-09-08, sixth follow-up, finding 2
        (P1), part 2: the original catch-up only ever checked Justin --
        but the pooled RMD draws from JOINT's pretax first
        (WITHDRAWAL_OWNER_ORDER), so when Jason is deceased and his own
        pretax is untouched (all the year's RMD came out of a joint
        IRA instead), his own individual RMD obligation could go
        completely unenforced. Both spouses 75, Jason's OWN $1,000,000
        IRA plus a $1,000,000 joint IRA. With the eighth-follow-up
        integration (section 42), Jason's own $1,000,000/24.6 =
        $40,650.41 is forced from HIS OWN account FIRST, as part of the
        SAME aggregate $81,300.81 RMD (not layered on top of it) -- the
        remaining $40,650.40 comes from joint, so joint ends at
        $959,349.60, not the $918,699.19 it would be if Jason's own
        account were never touched at all. Declining rollover isolates
        this (excludes Jason's own now-irrelevant balance from the
        survivor, leaving only joint's own reduced contribution
        visible) -- if Jason's own account had gone untouched, joint
        alone would have absorbed the full $81,300.81."""
        inputs = base_inputs(jason_age=75, justin_age=75, retirement_income_today_dollars=0,
                              retirement_end_age=76)
        accounts = [IRA(1000000, "jason"), IRA(1000000, "joint")]
        r = run_survivor_scenario(inputs, accounts, deceased="jason", death_age=75,
                                   jason_ret_age=61, justin_ret_age=61,
                                   spousal_rollover_election=False)
        assert r["starting_pretax_after_payout"] == pytest.approx(959350, abs=5)


class TestDeathYearIsOneIntegratedCalculationNotAPostHocPatch:
    """Independent review, 2026-09-08, eighth follow-up, P1: every
    earlier round patched the deceased's own final-RMD catch-up onto
    death_row's balances AFTER that year's spending, tax, and growth
    were already finished -- which kept reintroducing new errors
    (double-withdrawal, untaxed, ungrown). The catch-up must instead be
    incorporated into the death year's SINGLE calculation from the
    start: before the tax rate is estimated, before spending is funded,
    before ownership is allocated, before growth is applied."""

    def test_rmd_funds_spending_before_joint_taxable_is_drawn(self):
        """Jason 61, Justin 75, Justin's $1,000,000 IRA, $100,000 joint
        taxable, $50,000 spending, $0 growth, joint survivorship
        disabled. Patching the RMD in AFTER the year's spending was
        already funded (entirely from joint taxable, since the normal
        Jason-anchored aggregate RMD is $0 at Jason's age 61) leaves too
        much Justin-owned cash and drains joint further than necessary.
        Integrated, the RMD funds spending FIRST, exactly as it would
        for a real household. Correct: $1,002,642 (was $1,020,935)."""
        inputs = base_inputs(jason_age=61, justin_age=75, retirement_income_today_dollars=50000,
                              retirement_end_age=62)
        accounts = [IRA(1000000, "justin"), TAXABLE(100000, "joint")]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=75,
                                   jason_ret_age=61, justin_ret_age=61,
                                   joint_accounts_survivorship=False)
        assert r["starting_balance_after_payout"] == pytest.approx(1002642, abs=2)

    def test_the_added_rmd_moves_the_marginal_rate_in_the_same_calculation(self):
        """Jason 61, Justin 75, Justin's $1,000,000 IRA, a $100,000
        pension exactly funding $100,000 spending, $0 growth. Reusing a
        tax rate computed BEFORE the deceased's own RMD was added left
        the estimate stuck at a stale, too-low bracket (12%); folding
        the RMD into the SAME taxable-income estimate the rate is
        derived from correctly pushes it to 22%. Correct: $991,057
        (was $995,122)."""
        inputs = base_inputs(jason_age=61, justin_age=75, retirement_income_today_dollars=100000,
                              pension_55=100000, pension_60=100000, pension_65=100000,
                              retirement_end_age=62)
        accounts = [IRA(1000000, "justin")]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=75,
                                   jason_ret_age=61, justin_ret_age=61)
        assert r["starting_balance_after_payout"] == pytest.approx(991057, abs=2)


class TestOwnerCashFlowWaterfallReplacesGrossProportionalReweighting:
    """Independent review, 2026-09-08, seventh follow-up, finding 1
    (P1): reweighting the pooled ending-balance change by GROSS income
    shares mixes untaxed income surplus with gross (pre-tax) pretax
    distributions, and clamps negative life-event costs to $0, dropping
    them entirely. All three reproduced exactly per the review: spouses
    75, $0 growth/inflation, trust availability disabled."""

    def test_pension_exactly_leftover_after_need_trust_rmd_excluded(self):
        """$60,000 Jason pension / $30,000 spending / $1,000,000 trust
        IRA -- the pension alone funds need with a real $30,000 leftover
        that's entirely Jason's own (untaxed); the trust's forced RMD is
        separate money that's entirely the trust's own (excluded).
        Correct: $30,000. The old gross-proportional weighting reported
        $27,929 (diluting Jason's real leftover against the trust's
        gross, pre-tax RMD)."""
        inputs = base_inputs(jason_age=75, justin_age=75, retirement_income_today_dollars=30000,
                              pension_55=60000, pension_60=60000, pension_65=60000,
                              retirement_end_age=76)
        accounts = [IRA(1000000, "trust")]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=75,
                                   jason_ret_age=61, justin_ret_age=61,
                                   trust_available_to_survivor=False)
        assert r["starting_balance_after_payout"] == pytest.approx(30000, abs=2)

    def test_negative_life_event_is_not_dropped_no_phantom_surplus(self):
        """Same as above, plus a $40,000 one-time expense -- clamping
        it to $0 previously dropped it entirely, inventing a phantom
        $30,000 pension surplus that doesn't exist once the real
        expense is netted in. Correct: $0 (the expense consumes the
        pension entirely; only the trust's own excluded RMD proceeds
        remain). The old code reported $10,944."""
        inputs = base_inputs(jason_age=75, justin_age=75, retirement_income_today_dollars=30000,
                              pension_55=60000, pension_60=60000, pension_65=60000,
                              retirement_end_age=76)
        accounts = [IRA(1000000, "trust")]
        life_events = [{"event_year": 2026, "one_time_cash_delta": -40000,
                         "monthly_cash_flow_delta": 0, "duration_months": 0}]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=75,
                                   jason_ret_age=61, justin_ret_age=61,
                                   trust_available_to_survivor=False, life_events=life_events)
        assert r["starting_balance_after_payout"] == pytest.approx(0, abs=2)

    def test_fully_consumed_distribution_does_not_still_get_credited_extra(self):
        """No pension, $9,000 spending, $10,000 joint IRA + $990,000
        trust IRA -- the joint IRA's own $9,000 after-tax RMD proceeds
        already exactly fund spending; $0 should be left over for ANY
        owner. Correct: $0. The old proportional split still credited
        some of the trust's own proceeds as if joint's fully-consumed
        distribution hadn't used its own money up -- reported $6,786."""
        inputs = base_inputs(jason_age=75, justin_age=75, retirement_income_today_dollars=9000,
                              retirement_end_age=76)
        accounts = [IRA(10000, "joint"), IRA(990000, "trust")]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=75,
                                   jason_ret_age=61, justin_ret_age=61,
                                   trust_available_to_survivor=False)
        assert r["starting_balance_after_payout"] == pytest.approx(0, abs=2)


class TestRecurringLifeEventIncomeCreditsJointNotWhicheverSpouseWasCounted:
    """Independent review, 2026-09-08, fifth follow-up, finding 2 (P1),
    part 2: recurring monthly life-event income was missing from the
    surplus-source-attribution basis entirely (only the one-time
    component was counted), so it silently vanished into whichever
    OTHER source happened to be in the basis, over-crediting that
    source. Reproduced: $30,000 pension + $12,000/yr recurring
    household income, $0 spending, immediate death, joint survivorship
    disabled -- the recurring income has no individual owner and must
    be credited to joint (then halved by the disabled-survivorship
    assumption): $30,000 (Jason's own pension, unaffected) + $6,000
    (half of joint's $12,000) = $36,000, not the bug's $42,000 (the
    $12,000 silently becoming Jason's own money too, since it wasn't in
    the attribution basis at all)."""

    def test_recurring_income_is_halved_by_disabled_joint_survivorship_pensions_own_share_is_not(self):
        inputs = base_inputs(jason_age=65, justin_age=65, retirement_income_today_dollars=0,
                              pension_55=30000, pension_60=30000, pension_65=30000,
                              retirement_end_age=66)
        accounts = [TAXABLE(0, "joint")]
        life_events = [{"event_year": 2026, "one_time_cash_delta": 0,
                         "monthly_cash_flow_delta": 1000, "duration_months": 0}]
        r = run_survivor_scenario(inputs, accounts, deceased="justin", death_age=65,
                                   jason_ret_age=65, justin_ret_age=65,
                                   joint_accounts_survivorship=False, life_events=life_events)
        assert r["starting_balance_after_payout"] == 36000
