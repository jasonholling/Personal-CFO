"""
Smaller retirement calculators that don't need their own full projection
run: RMD planning, pension-vs-lump-sum, and backdoor Roth eligibility.

RMD planning reuses projection_engine's RMD table/formula and 401k-bucket
math rather than duplicating it — see the earlier calc-consistency work
this session (that duplication is exactly what caused bugs before).
"""
from typing import Dict, List, Optional

from projection_engine import _fv, _pv_annuity, _rmd, rmd_start_age, run_retirement_projection, is_kid_owner

# 2026 MFJ ordinary brackets (IRS Rev. Proc. 2025-32) — keep in sync with
# frontend/src/pages/TaxPlanning.jsx's ORDINARY_2026. Two copies (one Python,
# one JS) because the backend needs this for the RMD bracket-jump check and
# the frontend needs it for the interactive capital-gains/bracket tool —
# genuinely different call sites, not the same duplication problem as the
# calc-engine bugs from earlier.
# NOTE: the previous figures here (23850/96950/206700/...) were actually the
# 2025 brackets mislabeled 2026 — caught by external audit 2026-09-05.
ORDINARY_BRACKETS_MFJ_2026 = [
    (0.10, 24800), (0.12, 100800), (0.22, 211400), (0.24, 403550),
    (0.32, 512450), (0.35, 768700), (0.37, float("inf")),
]

# Standard deduction, MFJ, 2026 — also the canonical copy backing
# simulation_engine.py's Roth-conversion bracket-fill math, so it can't
# drift out of sync the way it did before (that version hardcoded a stale
# 2024 figure). Was $30,000 (a 2025 figure); actual 2026 is $32,200.
STD_DEDUCTION_MFJ_2026 = 32200

# Roth IRA MAGI phase-out range, married filing jointly, per IRS Notice
# 2025-... 2026 COLA figures. Update annually — verify against the
# current-year IRS figures before relying on this for a real contribution
# decision. (Previous range here, $236,000-$246,000, was 2025's.)
ROTH_MAGI_PHASEOUT_MFJ = (242000, 252000)
ROTH_IRA_CONTRIBUTION_LIMIT_2026 = 7500

# Per-person annual QCD limit, inflation-adjusted from the IRC's $100,000
# base (2024: $105,000) — verify against the current-year IRS figure
# before relying on this for a real donation decision.
QCD_ANNUAL_LIMIT_2026 = 108000
QCD_MIN_AGE = 70.5


def marginal_rate(taxable_income: float) -> float:
    for rate, cap in ORDINARY_BRACKETS_MFJ_2026:
        if taxable_income <= cap:
            return rate
    return ORDINARY_BRACKETS_MFJ_2026[-1][0]


def run_rmd_planning(inputs: Dict, accounts: List[Dict], ret_age: int = 60, ss_timing: str = "early",
                      life_events: List[Dict] = None, surplus_allocations: List[Dict] = None) -> Dict:
    """Projects your pretax (401k/IRA) balance to your RMD start age, then
    simulates RMDs year by year, flagging whether the first RMD pushes you
    into a higher bracket than your other retirement income already puts
    you in — that's the signal for whether Roth-converting before then is
    worth it.

    ret_age/ss_timing/life_events/surplus_allocations: same knobs every
    other retirement-tools call site takes (see get_roth_conversion in
    main.py) — ret_age defaults to 60, matching the rest of the app's
    "headline scenario" convention. Threaded through to
    run_retirement_projection below so the balance this tool projects to
    RMD age reflects the household's ACTUAL retirement withdrawals, not a
    balance nothing is ever spent from."""
    jason_age = inputs["jason_age"]
    pre_ret  = inputs["expected_return_pre_retirement"]
    post_ret = inputs["expected_return_post_retirement"]

    total_401k = sum(a["balance"] for a in accounts if a.get("account_type") == "401k")
    pretax_pct = inputs.get("pretax_401k_pct", 0.75)
    pretax_start = total_401k * pretax_pct + sum(
        a["balance"] for a in accounts
        if a.get("account_type") == "ira" and not is_kid_owner(a.get("owner"))
    )

    if pretax_start <= 0:
        return {"has_pretax_balance": False}

    # SECURE Act 2.0: 73 or 75 depending on birth year — was hardcoded to
    # 73 everywhere in this file, caught by external audit 2026-09-05.
    start_age = rmd_start_age(jason_age)
    years_to_start = max(0, start_age - jason_age)

    # Was: balance_at_start = _fv(pretax_start, pre_ret, years_to_start) —
    # pure growth, no withdrawals subtracted. That meant this tool's
    # headline "balance at RMD age" (and the RMD computed from it) could
    # describe money that the household's own retirement plan has already
    # spent down to zero years before RMD age arrives — e.g. a $1M pretax
    # balance with $100k/yr spending and 0% returns is fully depleted by
    # the real projection well before age 75, yet this used to still
    # report a $40,650 first RMD off an untouched $1M. Caught by external
    # audit 2026-09-07.
    #
    # Fix: read the SAME pretax trajectory run_retirement_projection
    # produces for this household (the pattern run_roth_conversion_analysis
    # in simulation_engine.py already follows) instead of independently
    # compounding today's raw balance. Simplification that remains: once
    # the real projection actually reaches RMD age it starts subtracting
    # its own RMDs from the pretax bucket before this function's own
    # schedule loop below even starts — we take the balance from the LAST
    # pre-RMD year (start_age - 1) as the base, which is exactly what this
    # function's own RMD-schedule loop expects to work from. If retirement
    # happens at or after RMD age, there is no pre-RMD year to read, so we
    # fall back to the pretax balance at retirement itself (the household
    # hasn't had any pre-retirement withdrawal years to deplete it in that
    # case).
    proj = run_retirement_projection(inputs, accounts, ret_ages=[ret_age],
                                      life_events=life_events, surplus_allocations=surplus_allocations)
    scenario = next((s for s in proj["scenarios"] if s["label"] == f"age_{ret_age}_{ss_timing}"), None)
    if scenario is None:
        balance_at_start = _fv(pretax_start, pre_ret, years_to_start)
    elif ret_age >= start_age:
        balance_at_start = scenario["pretax_at_retirement"]
    else:
        pre_rmd_year = next((y for y in scenario["yearly_detail"] if y["jason_age"] == start_age - 1), None)
        # Projection didn't reach that far (e.g. plan runs past
        # life_expectancy before start_age - 1) — the pretax bucket at the
        # household's actual retirement is the best available estimate
        # rather than falling back to unadjusted compounding.
        balance_at_start = pre_rmd_year["pretax_balance"] if pre_rmd_year is not None else scenario["pretax_at_retirement"]

    # "Other income" once RMDs start — pension + both SS benefits at their
    # steady-state (age-65 pension, since RMDs start well past any
    # early/delayed SS claiming window).
    other_income = (
        inputs.get("pension_65", 0)
        + inputs.get("jason_social_security", 0)
        + inputs.get("justin_social_security", 0)
    )
    pre_rmd_bracket = marginal_rate(other_income)

    schedule = []
    bal = balance_at_start
    cumulative_rmd = 0.0
    for age in range(start_age, 100):
        if bal <= 0:
            break
        rmd = _rmd(bal, age, start_age)
        taxable_income_this_year = other_income + rmd
        schedule.append({
            "age": age,
            "starting_balance": round(bal),
            "rmd_amount": round(rmd),
            "marginal_rate": marginal_rate(taxable_income_this_year),
        })
        cumulative_rmd += rmd
        bal = max(0, (bal - rmd) * (1 + post_ret))

    first_rmd = schedule[0] if schedule else None
    first_rmd_amount = first_rmd["rmd_amount"] if first_rmd else 0
    first_rmd_bracket = first_rmd["marginal_rate"] if first_rmd else pre_rmd_bracket
    bracket_jump = first_rmd_bracket > pre_rmd_bracket

    if bracket_jump:
        recommendation = (
            f"Your first RMD at {start_age} adds about ${first_rmd_amount:,.0f}/yr of forced taxable income, "
            f"pushing your marginal rate from {pre_rmd_bracket*100:.0f}% to {first_rmd_bracket*100:.0f}%. "
            f"Consider Roth-converting some of your pretax balance in the years before {start_age}, while you're "
            f"still in the {pre_rmd_bracket*100:.0f}% bracket, to shrink future RMDs — see the Roth Conversion page."
        )
    else:
        recommendation = (
            f"Your first RMD at {start_age} adds about ${first_rmd_amount:,.0f}/yr of forced taxable income, but "
            f"doesn't push you into a higher bracket ({first_rmd_bracket*100:.0f}%) given your other income. "
            f"No urgency to Roth-convert purely to avoid RMD bracket creep."
        )

    return {
        "has_pretax_balance": True,
        "pretax_balance_today": round(pretax_start),
        "projected_balance_at_start_age": round(balance_at_start),
        "first_rmd_age": start_age,
        "first_rmd_amount": first_rmd_amount,
        "lifetime_rmd_total": round(cumulative_rmd),
        "bracket_jump": bracket_jump,
        "pre_rmd_bracket": pre_rmd_bracket,
        "first_rmd_bracket": first_rmd_bracket,
        "recommendation": recommendation,
        "schedule": schedule[::2],  # every other year keeps the response small
    }


def _implied_discount_rate(annual_pension: float, years_receiving: int, lump_sum: float,
                            years_until_start: int) -> Optional[float]:
    """The discount rate at which the pension's present value equals the
    lump-sum offer — bisection since _pv_annuity is monotonic in rate."""
    if annual_pension <= 0 or years_receiving <= 0 or lump_sum <= 0:
        return None

    def pv_at_rate(rate):
        pv_at_start = _pv_annuity(annual_pension, rate, years_receiving)
        return pv_at_start / ((1 + rate) ** years_until_start)

    lo, hi = 0.0001, 0.30
    if pv_at_rate(lo) < lump_sum:
        return None  # even a near-zero discount rate can't make the pension worth this little
    if pv_at_rate(hi) > lump_sum:
        return hi  # implied rate is above our search ceiling
    for _ in range(60):
        mid = (lo + hi) / 2
        if pv_at_rate(mid) > lump_sum:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def pension_vs_lump_sum(monthly_pension: float, lump_sum: float, current_age: int,
                         pension_start_age: int, life_expectancy_age: int = 90,
                         discount_rate: float = 0.06) -> Dict:
    annual_pension = monthly_pension * 12
    years_receiving = max(0, life_expectancy_age - pension_start_age)
    years_until_start = max(0, pension_start_age - current_age)

    pv_at_start = _pv_annuity(annual_pension, discount_rate, years_receiving)
    pv_today = pv_at_start / ((1 + discount_rate) ** years_until_start)

    implied_rate = _implied_discount_rate(annual_pension, years_receiving, lump_sum, years_until_start)
    favors_lump_sum = lump_sum > pv_today

    # _implied_discount_rate returns None from its "even a near-zero
    # discount rate can't make the pension worth this little" branch
    # whenever the pension's own nominal total is already below the lump
    # sum (e.g. $100/mo * 10yr = $12,000 nominal vs. a $20,000 lump sum) —
    # no positive rate satisfies PV(pension) = lump_sum because the pension
    # is structurally the worse deal, full stop. That failure used to be
    # mislabeled as "implied rate is very high — hard to beat", the exact
    # opposite conclusion: a search that can't find ANY rate making the
    # pension worth the lump sum is a signal the lump sum wins, not that
    # the pension has a great return. Caught by external audit 2026-09-07.
    if implied_rate is not None:
        rate_note = (
            f"The pension is equivalent to investing the lump sum at a guaranteed "
            f"{implied_rate*100:.1f}%/yr — take the lump sum only if you're confident you can beat that "
            f"investing it yourself, after accounting for the guarantee you'd be giving up."
        )
    elif favors_lump_sum:
        rate_note = (
            "No positive discount rate makes the pension worth as much as the lump sum — its total nominal "
            "payments over the payout period are already less than what's being offered up front, so the lump "
            "sum is the clear choice here."
        )
    else:
        rate_note = (
            "The implied break-even rate couldn't be pinned down for these inputs, but the pension is still "
            "worth more than the lump sum at your assumed discount rate."
        )

    return {
        "present_value_of_pension": round(pv_today),
        "lump_sum_offer": round(lump_sum),
        "favors": "lump_sum" if favors_lump_sum else "pension",
        "implied_discount_rate_pct": round(implied_rate * 100, 2) if implied_rate is not None else None,
        "years_receiving_assumed": years_receiving,
        "recommendation": (
            (f"At a {discount_rate*100:.0f}% discount rate and life expectancy of {life_expectancy_age}, the pension is "
             f"worth about ${pv_today:,.0f} in today's dollars — {'less' if favors_lump_sum else 'more'} than the "
             f"${lump_sum:,.0f} lump-sum offer. {rate_note}")
        ),
    }


def backdoor_roth_eligibility(magi: float, existing_traditional_ira_balance: float = 0,
                               existing_traditional_ira_basis: float = 0,
                               planned_contribution: float = ROTH_IRA_CONTRIBUTION_LIMIT_2026) -> Dict:
    low, high = ROTH_MAGI_PHASEOUT_MFJ
    if magi < low:
        direct_eligible, phaseout_pct = True, 0.0
    elif magi >= high:
        direct_eligible, phaseout_pct = False, 1.0
    else:
        direct_eligible, phaseout_pct = False, round((magi - low) / (high - low), 3)

    needs_backdoor = not direct_eligible

    # Pro-rata rule (Form 8606): the IRS treats all your traditional IRAs as
    # one pot. The nontaxable fraction of ANY conversion = total basis /
    # total year-end balance across all traditional IRAs — you can't
    # cherry-pick which dollars convert tax-free.
    total_balance = existing_traditional_ira_balance + planned_contribution
    total_basis   = existing_traditional_ira_basis + planned_contribution
    nontaxable_fraction = (total_basis / total_balance) if total_balance > 0 else 1.0
    taxable_fraction = 1 - nontaxable_fraction
    taxable_amount = round(planned_contribution * taxable_fraction)
    pro_rata_applies = existing_traditional_ira_balance > existing_traditional_ira_basis

    if not needs_backdoor:
        recommendation = f"Your MAGI (${magi:,.0f}) is under the phase-out — contribute directly to a Roth IRA, no backdoor needed."
    elif not pro_rata_applies:
        recommendation = (
            f"Your MAGI (${magi:,.0f}) is above the direct-contribution limit, but you have no other pretax "
            f"traditional IRA balance, so a backdoor Roth is clean: the full ${planned_contribution:,.0f} "
            f"converts tax-free."
        )
    else:
        recommendation = (
            f"Your MAGI (${magi:,.0f}) is above the direct-contribution limit, and because you already hold "
            f"${existing_traditional_ira_balance:,.0f} in pretax traditional IRA balance, the pro-rata rule "
            f"means only {nontaxable_fraction*100:.0f}% of any conversion is tax-free — converting "
            f"${planned_contribution:,.0f} would trigger about ${taxable_amount:,.0f} of taxable income. "
            f"Consider rolling existing pretax IRA balances into a 401k first (if your plan accepts incoming "
            f"rollovers) to clear the pro-rata problem before doing the backdoor conversion."
        )

    return {
        "magi": magi,
        "direct_roth_eligible": direct_eligible,
        "phaseout_pct": phaseout_pct,
        "needs_backdoor": needs_backdoor,
        "pro_rata_applies": pro_rata_applies,
        "nontaxable_fraction": round(nontaxable_fraction, 3),
        "taxable_amount_of_conversion": taxable_amount,
        "recommendation": recommendation,
    }


def qcd_planner(age: float, ira_balance: float, rmd_amount: float,
                 desired_qcd_amount: float, other_taxable_income: float = 0) -> Dict:
    """Qualified Charitable Distribution — giving straight from a
    traditional IRA once you're 70½+. Unlike a DAF/itemized deduction,
    QCD dollars are excluded from AGI entirely (full marginal-rate value,
    not capped by the standard deduction), and up to the annual limit they
    count toward satisfying that year's RMD dollar-for-dollar."""
    if age < QCD_MIN_AGE:
        return {
            "eligible": False,
            "min_age": QCD_MIN_AGE,
            "recommendation": f"QCDs require the IRA owner to be at least {QCD_MIN_AGE} — not eligible yet.",
        }

    qcd_amount = max(0, min(desired_qcd_amount, QCD_ANNUAL_LIMIT_2026, ira_balance))
    rmd_satisfied_by_qcd = min(qcd_amount, rmd_amount)
    remaining_taxable_rmd = max(0, rmd_amount - rmd_satisfied_by_qcd)

    rate = marginal_rate(other_taxable_income + remaining_taxable_rmd)
    tax_savings = qcd_amount * rate

    if qcd_amount <= 0:
        recommendation = "Enter a donation amount to see the QCD impact."
    else:
        rmd_note = (
            f" This satisfies {rmd_satisfied_by_qcd:,.0f} of your ${rmd_amount:,.0f} RMD, leaving "
            f"${remaining_taxable_rmd:,.0f} still taxable."
            if rmd_amount > 0 else " You don't have an RMD yet, but the QCD still counts toward future giving without touching your return."
        )
        recommendation = (
            f"Donating ${qcd_amount:,.0f} via QCD instead of writing a check (or even instead of a DAF "
            f"contribution) keeps that money off your tax return entirely — an estimated ${tax_savings:,.0f} "
            f"in tax savings at your {rate*100:.0f}% marginal rate, since it's excluded from AGI rather than "
            f"just deducted.{rmd_note}"
        )

    return {
        "eligible": True,
        "qcd_amount": round(qcd_amount),
        "annual_limit": QCD_ANNUAL_LIMIT_2026,
        "rmd_amount": round(rmd_amount),
        "rmd_satisfied_by_qcd": round(rmd_satisfied_by_qcd),
        "remaining_taxable_rmd": round(remaining_taxable_rmd),
        "marginal_rate": rate,
        "estimated_tax_savings": round(tax_savings),
        "recommendation": recommendation,
    }


def hsa_stealth_ira_strategy(oop_expense_this_year: float, years_to_delay: int,
                              growth_rate: float = 0.07) -> Dict:
    """The 'stealth IRA' HSA move: pay a medical expense out of pocket now,
    save the receipt, and reimburse yourself from the HSA any time later —
    there's no deadline — letting that money compound tax-free in the
    meantime instead of pulling it out today."""
    if oop_expense_this_year <= 0:
        return {"has_expense": False}

    future_value_if_delayed = oop_expense_this_year * ((1 + growth_rate) ** years_to_delay)
    extra_value_from_delaying = future_value_if_delayed - oop_expense_this_year

    recommendation = (
        f"Pay the ${oop_expense_this_year:,.0f} out of pocket now and keep the receipt, rather than "
        f"reimbursing yourself from the HSA today. Reimbursing in {years_to_delay} years instead leaves that "
        f"money growing tax-free — worth an estimated ${future_value_if_delayed:,.0f} by then, about "
        f"${extra_value_from_delaying:,.0f} more than pulling it out now — as long as covering it out of "
        f"pocket doesn't strain your cash flow."
    )

    return {
        "has_expense": True,
        "oop_expense": round(oop_expense_this_year),
        "years_to_delay": years_to_delay,
        "growth_rate": growth_rate,
        "future_value_if_delayed": round(future_value_if_delayed),
        "extra_value_from_delaying": round(extra_value_from_delaying),
        "recommendation": recommendation,
    }
