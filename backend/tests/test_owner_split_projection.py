"""
Owner-split year-by-year walk (CALCULATION_CONTRACT.md section 37.4,
Milestone 4 of 4). Core correctness property, per the explicit review
requirement ("reconcile each account type and each year, including the
death transition -- matching the starting household total alone is
insufficient"): run_owner_split_two_dimensional_projection's own
portfolio_balance and unmet_need must match run_two_dimensional_
retirement_projection's pooled yearly_detail EXACTLY, every single
year, not just at initialization -- verified directly against the
pooled function's own (already reviewed, sections 19-24) output.

A real bug was found and fixed against this exact test during
development (not a review finding): WITHDRAWAL_OWNER_ORDER originally
excluded `trust`, on the theory that trust assets are never
auto-included (section 37.1) -- but that rule is about the SURVIVOR's
post-death access, not the pre-death walk, which must reconcile
against the pooled engine's existing behavior of spending trust-owned
balances as part of its one pooled total (it only ever excludes
abby/cooper). Excluding trust from the pre-death draw order let trust
money grow unchecked while the pooled reference correctly drew it
down, diverging by hundreds of thousands of dollars by the later
years of a multi-decade projection. Fixed by including trust last in
WITHDRAWAL_OWNER_ORDER for the PRE-death walk specifically.
"""

import pytest

from projection_engine import run_two_dimensional_retirement_projection, run_owner_split_two_dimensional_projection

BASE_INPUTS = {
    "jason_age": 60, "justin_age": 58, "inflation_rate": 0.03,
    "expected_return_pre_retirement": 0.07, "expected_return_post_retirement": 0.05,
    "retirement_income_today_dollars": 90000, "annual_hsa_contribution": 2000, "annual_rsu_value": 10000,
    "jason_social_security": 28000, "justin_social_security": 14000,
    "healthcare_pre_medicare": 12000, "healthcare_post_medicare": 4000,
    "w2_salary": 150000, "employee_401k_pct": 0.06, "employer_401k_pct": 0.09, "annual_bonus_pct": 0.10,
    "justin_w2_salary": 100000, "justin_employee_401k_pct": 0.06, "justin_employer_401k_pct": 0.03,
    "justin_annual_bonus_pct": 0.05, "justin_annual_rsu_value": 5000,
    "pension_55": 20000, "pension_60": 22000, "pension_65": 24000,
    "retirement_end_age": 95,
}

ACCOUNTS = [
    {"name": "Jason 401k",    "account_type": "401k",     "owner": "jason",  "balance": 200000},
    {"name": "Justin IRA",    "account_type": "ira",      "owner": "justin", "balance": 150000},
    {"name": "Joint taxable", "account_type": "taxable",  "owner": "joint",  "balance": 100000},
    {"name": "Trust",         "account_type": "taxable",  "owner": "trust",  "balance": 50000},
    {"name": "Jason Roth",    "account_type": "roth_ira", "owner": "jason",  "balance": 30000},
    {"name": "HSA",           "account_type": "hsa",      "owner": "jason",  "balance": 20000},
]


def _assert_reconciles(inputs, accounts, jason_ret_age, justin_ret_age):
    pooled = run_two_dimensional_retirement_projection(inputs, accounts,
                                                         jason_ret_age=jason_ret_age, justin_ret_age=justin_ret_age)
    split = run_owner_split_two_dimensional_projection(inputs, accounts,
                                                         jason_ret_age=jason_ret_age, justin_ret_age=justin_ret_age)
    pyd, syd = pooled["yearly_detail"], split["yearly_detail"]
    assert len(pyd) == len(syd)
    for p, s in zip(pyd, syd):
        assert s["portfolio_balance"] == p["portfolio_balance"], f"age {p['jason_age']}"
        assert s["unmet_need"] == p["unmet_need"], f"age {p['jason_age']}"
    return pooled, split


class TestOwnerSplitReconcilesEveryYear:
    def test_long_horizon_including_rmd_years(self):
        _assert_reconciles(BASE_INPUTS, ACCOUNTS, 61, 63)

    def test_simultaneous_retirement(self):
        _assert_reconciles(BASE_INPUTS, ACCOUNTS, 61, 61)

    def test_jason_retires_later_than_justin(self):
        _assert_reconciles(BASE_INPUTS, ACCOUNTS, 63, 61)

    def test_past_retirement_selection_and_large_age_gap(self):
        inputs = {**BASE_INPUTS, "jason_age": 70, "justin_age": 50}
        _assert_reconciles(inputs, ACCOUNTS, 65, 65)

    def test_depletion_and_unmet_need_with_no_accounts(self):
        pooled, split = _assert_reconciles(BASE_INPUTS, [], 60, 60)
        assert any(y["unmet_need"] > 0 for y in pooled["yearly_detail"])

    def test_owner_sub_buckets_sum_to_the_reported_total_every_year(self):
        """Not just the rounded portfolio_balance -- the owner_balances
        breakdown itself must sum to that same total, every year."""
        _, split = _assert_reconciles(BASE_INPUTS, ACCOUNTS, 61, 63)
        for row in split["yearly_detail"]:
            total = sum(row["owner_balances"][o][t]
                        for o in ("jason", "justin", "joint", "trust")
                        for t in ("pretax", "roth", "taxable", "hsa"))
            # Each owner bucket is individually rounded before summing
            # here, vs. portfolio_balance rounding the whole total once
            # -- a $1 rounding-order artifact is expected, not a real
            # discrepancy (the unrounded reconciliation is already
            # covered exactly by _assert_reconciles above).
            assert abs(total - row["portfolio_balance"]) <= 1

    def test_trust_money_is_spent_down_like_the_pooled_engine_does(self):
        """Regression test for the bug found during development: trust
        must NOT be excluded from the pre-death withdrawal order --
        it's part of the one pooled total the existing engine already
        draws from. A household with ONLY a trust account and no other
        assets, and spending well above its guaranteed income (a real,
        unavoidable shortfall every year, not a surplus), must actually
        fund that shortfall from trust -- there is nowhere else for the
        money to come from."""
        accounts = [{"name": "Trust", "account_type": "taxable", "owner": "trust", "balance": 2000000}]
        inputs = {**BASE_INPUTS, "retirement_income_today_dollars": 150000, "retirement_end_age": 70,
                   "pension_55": 0, "pension_60": 0, "pension_65": 0,
                   "jason_social_security": 0, "justin_social_security": 0,
                   "w2_salary": 0, "justin_w2_salary": 0, "annual_rsu_value": 0,
                   "justin_annual_rsu_value": 0, "annual_hsa_contribution": 0}
        pooled, split = _assert_reconciles(inputs, accounts, 61, 61)
        first_trust = split["yearly_detail"][0]["owner_balances"]["trust"]["taxable"]
        last_trust  = split["yearly_detail"][-1]["owner_balances"]["trust"]["taxable"]
        assert last_trust < first_trust
        # And the OTHER three owner buckets must stay at exactly $0 the
        # whole time -- there's nothing else to draw from or credit a
        # surplus to (no guaranteed income at all here), so if the
        # allocation logic leaked a draw or a spurious surplus into
        # jason/justin/joint, this would catch it.
        for row in split["yearly_detail"]:
            for owner in ("jason", "justin", "joint"):
                assert sum(row["owner_balances"][owner].values()) == 0


class TestSurplusCreditsItsOwnIncomeSourceNotAlwaysJoint:
    """Independent review, 2026-09-08, fourth follow-up, finding 4 (P1):
    every positive delta used to be credited entirely to joint
    regardless of source, violating section 37.4's own stated rule
    ("transfers land in the same owner-bucket the income source belongs
    to"). Both spouses retired now, $0 spending need (isolates the
    surplus), Jason's own $30,000 pension is the ONLY guaranteed income
    -- the resulting surplus must credit JASON's own taxable bucket, not
    joint. Under the old bug, owner_balances["joint"]["taxable"] would
    have been $30,000 and jason's own $0."""

    def test_pension_surplus_credits_the_pension_owner_not_joint(self):
        inputs = {**BASE_INPUTS, "jason_age": 65, "justin_age": 65,
                  "retirement_income_today_dollars": 0, "retirement_end_age": 66,
                  "pension_55": 30000, "pension_60": 30000, "pension_65": 30000,
                  "jason_social_security": 0, "justin_social_security": 0,
                  "w2_salary": 0, "justin_w2_salary": 0, "annual_rsu_value": 0,
                  "justin_annual_rsu_value": 0, "annual_hsa_contribution": 0}
        accounts = [{"name": "Joint taxable", "account_type": "taxable", "owner": "joint", "balance": 0}]
        split = run_owner_split_two_dimensional_projection(inputs, accounts, jason_ret_age=65, justin_ret_age=65)
        first_year = split["yearly_detail"][0]
        assert first_year["owner_balances"]["jason"]["taxable"] > 0
        assert first_year["owner_balances"]["joint"]["taxable"] == 0

    def test_justins_own_ss_surplus_credits_justin_not_joint(self):
        """Mirror -- Justin's own SS is the only guaranteed income; the
        surplus must credit Justin's own bucket."""
        inputs = {**BASE_INPUTS, "jason_age": 65, "justin_age": 65,
                  "retirement_income_today_dollars": 0, "retirement_end_age": 66,
                  "pension_55": 0, "pension_60": 0, "pension_65": 0,
                  "jason_social_security": 0, "justin_social_security": 20000, "justin_ss_age": 60,
                  "w2_salary": 0, "justin_w2_salary": 0, "annual_rsu_value": 0,
                  "justin_annual_rsu_value": 0, "annual_hsa_contribution": 0}
        accounts = [{"name": "Joint taxable", "account_type": "taxable", "owner": "joint", "balance": 0}]
        split = run_owner_split_two_dimensional_projection(inputs, accounts, jason_ret_age=65, justin_ret_age=65)
        first_year = split["yearly_detail"][0]
        assert first_year["owner_balances"]["justin"]["taxable"] > 0
        assert first_year["owner_balances"]["joint"]["taxable"] == 0


class TestRmdReinvestmentCreditsThePretaxOwnerNotJoint:
    """Independent review, 2026-09-08, fifth follow-up, finding 2 (P1),
    part 1: a positive delta with no cash-INCOME basis (a pure RMD-
    reinvestment year -- $0 spending need, $0 guaranteed income, so the
    forced RMD has nothing to fund and gets swept back to savings)
    still defaulted to joint, even though the money being reinvested
    came directly out of a specific owner's own pretax account. Jason
    75 (his own $1,000,000 IRA), Justin 61, $0 spending -- the RMD-
    driven surplus must credit Jason's own taxable bucket, not joint."""

    def test_rmd_reinvestment_credits_the_pretax_owner_not_joint(self):
        inputs = {**BASE_INPUTS, "jason_age": 75, "justin_age": 61,
                  "retirement_income_today_dollars": 0, "retirement_end_age": 76,
                  "pension_55": 0, "pension_60": 0, "pension_65": 0,
                  "jason_social_security": 0, "justin_social_security": 0,
                  "w2_salary": 0, "justin_w2_salary": 0, "annual_rsu_value": 0,
                  "justin_annual_rsu_value": 0, "annual_hsa_contribution": 0}
        accounts = [{"name": "Jason IRA", "account_type": "ira", "owner": "jason", "balance": 1000000}]
        split = run_owner_split_two_dimensional_projection(inputs, accounts, jason_ret_age=61, justin_ret_age=61)
        first_year = split["yearly_detail"][0]
        assert first_year["owner_balances"]["jason"]["taxable"] > 0
        assert first_year["owner_balances"]["joint"]["taxable"] == 0


class TestNetOfNeedIncomeAttributionDoesNotDiluteAnUnrelatedRmdSurplus:
    """Independent review, 2026-09-08, sixth follow-up, finding 1 (P1):
    gross income sources must not dilute a same-year RMD-reinvestment
    surplus that isn't actually theirs -- only the portion of an
    income source that's genuinely left over after funding need counts
    toward the attribution basis, not its gross amount. Jason's
    $30,000 pension exactly funds $30,000 spending (net contribution
    to any surplus is $0); a trust-owned $1,000,000 IRA's forced RMD is
    reinvested the same year -- the reinvested RMD must credit the
    trust entirely, not get diluted by Jason's already-fully-consumed
    pension (the old gross-proportion split credited ~$15,190 of it to
    Jason instead)."""

    def test_pension_exactly_funding_need_does_not_dilute_trusts_own_rmd_surplus(self):
        inputs = {**BASE_INPUTS, "jason_age": 75, "justin_age": 75,
                  "retirement_income_today_dollars": 30000, "retirement_end_age": 76,
                  "pension_55": 30000, "pension_60": 30000, "pension_65": 30000,
                  "jason_social_security": 0, "justin_social_security": 0,
                  "w2_salary": 0, "justin_w2_salary": 0, "annual_rsu_value": 0,
                  "justin_annual_rsu_value": 0, "annual_hsa_contribution": 0,
                  "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0}
        accounts = [{"name": "Trust IRA", "account_type": "ira", "owner": "trust", "balance": 1000000}]
        split = run_owner_split_two_dimensional_projection(inputs, accounts, jason_ret_age=61, justin_ret_age=61)
        first_year = split["yearly_detail"][0]
        assert first_year["owner_balances"]["jason"]["taxable"] == 0
        assert first_year["owner_balances"]["trust"]["taxable"] > 0


class TestStillWorkingGapIncomeCreditsTheActualLaterRetiree:
    """Independent review, 2026-09-08, fifth follow-up, finding 3 (P2):
    the still-working spouse's own gap-income surplus was hardcoded to
    credit "justin", even though the gap-income mechanism itself is
    already generalized to whichever spouse is timeline.later_retiree.
    Jason retires at 65, Justin at 61, Jason earns $100,000, $0
    spending -- Jason is the one still working (later_retiree), so the
    resulting ~$65,000 net surplus must credit HIS OWN bucket, not
    Justin's."""

    def test_jasons_own_working_income_surplus_credits_jason_not_justin(self):
        inputs = {**BASE_INPUTS, "jason_age": 61, "justin_age": 61,
                  "retirement_income_today_dollars": 0, "retirement_end_age": 62,
                  "pension_55": 0, "pension_60": 0, "pension_65": 0,
                  "jason_social_security": 0, "justin_social_security": 0,
                  "w2_salary": 100000, "justin_w2_salary": 0,
                  "annual_rsu_value": 0, "justin_annual_rsu_value": 0, "annual_hsa_contribution": 0}
        accounts = [{"name": "Joint taxable", "account_type": "taxable", "owner": "joint", "balance": 0}]
        split = run_owner_split_two_dimensional_projection(inputs, accounts, jason_ret_age=65, justin_ret_age=61)
        first_year = split["yearly_detail"][0]
        assert first_year["owner_balances"]["jason"]["taxable"] > 0
        assert first_year["owner_balances"]["justin"]["taxable"] == 0
