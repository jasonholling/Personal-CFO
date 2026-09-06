"""
Projection engine — retirement with proper bucket tracking and RMDs.

Buckets:
  pretax   — Traditional 401k + Traditional IRA — RMDs start at 73 or 75,
             see rmd_start_age() below (SECURE Act 2.0's birth-year rule)
  roth     — Roth 401k + Roth IRA — no RMDs
  taxable  — Brokerage — drawn first, most flexible
  hsa      — HSA — no RMDs during lifetime

RMD factors from IRS Uniform Lifetime Table (SECURE Act 2.0, age 73+).
"""

from typing import List, Dict
import datetime
import math

COLLEGE_COST_INFLATION = 0.04
COLLEGE_YEARS          = 4
UNL_CURRENT_ANNUAL     = 0  # fallback only — read from inputs at runtime

# 529 contributions are assumed to stop once you hit this age — no more
# earned income funding them after retirement. Matches the "Retire 60"
# scenario used as the default assumption elsewhere in the app.
PARENT_RETIREMENT_AGE_ASSUMPTION = 60

# Real EE/I savings bonds stop earning interest at final maturity — 30
# years from issue. We only track a bond's current balance, not its issue
# date, so this caps projected growth at a reasonable stand-in instead of
# compounding indefinitely (which would otherwise run 40+ years straight
# through to age 60 for a young kid).
BOND_MAX_GROWTH_YEARS = 30

# Pension / SS defaults — these are fallbacks only; real values always come
# from planning_inputs (set per-user in Settings), never hardcoded here.
PENSION_100J_S_DEFAULT   = {55: 0, 60: 0, 65: 0}
JASON_SS_EARLY_DEFAULT   = 0
JASON_SS_DELAYED_DEFAULT = 0
JASON_SS_DELAYED_RATIO   = 1.0  # fallback ratio only — no assumption without real inputs

# Justin spousal benefit = 50% of Jason FRA benefit, claimed at his FRA (67).
# Reduced if claimed before 67 — we model claiming at 67 for full amount.
JUSTIN_SPOUSAL_ANNUAL = 0
JUSTIN_SPOUSAL_AGE    = 67          # full benefit at his FRA

# Kids contribution defaults — overridden by planning_inputs at runtime
ABBY_MONTHLY_529_DEFAULT   = 0
COOPER_MONTHLY_529_DEFAULT = 0
KIDS_ROTH_MONTHLY_DEFAULT  = 0
KIDS_CUST_MONTHLY_DEFAULT  = 0

# IRS Uniform Lifetime Table — age: distribution period
RMD_TABLE = {
    73:26.5, 74:25.5, 75:24.6, 76:23.7, 77:22.9, 78:22.0, 79:21.1,
    80:20.2, 81:19.4, 82:18.5, 83:17.7, 84:16.8, 85:16.0, 86:15.2,
    87:14.4, 88:13.7, 89:12.9, 90:12.2, 91:11.5, 92:10.8, 93:10.1,
    94:9.5,  95:8.9,  96:8.4,  97:7.8,  98:7.3,  99:6.8,
}

# 401k constants — now read from accounts + planning_inputs at runtime
# These are kept as fallbacks only if planning_inputs not available
PRETAX_401K_BALANCE_DEFAULT  = 0
ROTH_401K_BALANCE_DEFAULT    = 0
ANNUAL_401K_PRETAX_DEFAULT   = 0
ANNUAL_401K_ROTH_DEFAULT     = 0


def _fv(pv, r, n):
    if n <= 0: return pv
    return pv * ((1 + r) ** n)

def _fv_annuity(pmt, r, n):
    if n <= 0: return 0.0
    if r == 0: return pmt * n
    return pmt * (((1 + r) ** n - 1) / r)

def _fv_growing_annuity(pmt, r, g, n):
    """Future value of a series of deposits that itself grows at rate g
    each year (e.g. a 401k contribution tied to a salary that gets annual
    raises), each deposit then compounding at r for the years remaining.
    `pmt` is the FIRST year's deposit, not scaled by g yet. Reduces to
    _fv_annuity when g == 0."""
    if n <= 0: return 0.0
    if r == g:
        # The (r - g) denominator below would divide by zero — every
        # deposit grows exactly as fast as the return compounds it, so
        # each of the n deposits is worth pmt * (1+r)^(n-1) at the end.
        return pmt * n * ((1 + r) ** (n - 1))
    return pmt * (((1 + r) ** n - (1 + g) ** n) / (r - g))

def _fv_annuity_monthly(pmt, r, n):
    if n <= 0: return 0.0
    mr = r / 12
    months = n * 12
    if mr == 0: return pmt * months
    return pmt * (((1 + mr) ** months - 1) / mr)

def _pv_annuity(pmt, r, n):
    if n <= 0: return 0.0
    if r == 0: return pmt * n
    return pmt * (1 - (1 + r) ** -n) / r

def rmd_start_age(current_age: int) -> int:
    """SECURE Act 2.0's RMD start age depends on birth year, not a single
    fixed age: 73 for anyone born 1951-1959 (the window in force through
    2032), stepping up to 75 for anyone born 1960 or later (reaching 74
    after 2032). Previously hardcoded to 73 everywhere — caught by
    external audit 2026-09-05. Approximates birth year from current age
    and today's date since the app only tracks whole-year ages, not exact
    birthdates; that's consistent with this engine's precision elsewhere."""
    birth_year = datetime.date.today().year - current_age
    return 75 if birth_year >= 1960 else 73


def _rmd(balance, age, start_age=73):
    """Calculate required minimum distribution for a given age. Pass the
    caller's own rmd_start_age(current_age) as start_age — the 73 default
    here only covers legacy callers that haven't been updated."""
    if age < start_age or balance <= 0:
        return 0.0
    factor = RMD_TABLE.get(age, RMD_TABLE[99])
    return balance / factor

def pension_for_age(inputs: Dict, age: int) -> float:
    """Pension is defined at 55/60/65 in Settings; interpolate linearly between
    those anchor points for any other retirement age (e.g. a sensitivity sweep
    or Monte Carlo run at an in-between age). Shared by every module that needs
    a pension figure for an arbitrary retirement age — do not reimplement this
    interpolation inline elsewhere."""
    p55 = inputs.get("pension_55", PENSION_100J_S_DEFAULT[55])
    p60 = inputs.get("pension_60", PENSION_100J_S_DEFAULT[60])
    p65 = inputs.get("pension_65", PENSION_100J_S_DEFAULT[65])
    if age <= 55:   return p55
    elif age <= 60: return p55 + (p60 - p55) * (age - 55) / 5
    elif age <= 65: return p60 + (p65 - p60) * (age - 60) / 5
    else:           return p65


def run_retirement_projection(inputs: Dict, accounts: List[Dict], ret_ages: List[int] = None,
                               salary_growth_pct: float = 0.0) -> Dict:
    """salary_growth_pct: assumed annual raise rate applied to the 401k
    contribution base (annual_401k_pretax/roth, both derived from salary)
    every year until retirement, compounding — e.g. 0.03 for 3%/yr raises.
    Defaults to 0 (flat contributions, the historical behavior) so every
    other caller is unaffected; only the What-If tool currently sets it."""
    jason_age  = inputs["jason_age"]
    justin_age = inputs["justin_age"]
    inflation  = inputs["inflation_rate"]
    pre_ret    = inputs["expected_return_pre_retirement"]
    post_ret   = inputs["expected_return_post_retirement"]
    income_today = inputs["retirement_income_today_dollars"]
    annual_hsa   = inputs["annual_hsa_contribution"]
    annual_rsu   = inputs["annual_rsu_value"]
    # Justin takes spousal benefit = 50% of Jason FRA benefit at his FRA 67
    # Cannot claim until Jason has filed, so start age = max(Justin FRA, Jason SS age)
    justin_ss_annual = inputs.get("justin_social_security", JUSTIN_SPOUSAL_ANNUAL)
    justin_ss_age    = inputs.get("justin_ss_age", JUSTIN_SPOUSAL_AGE)

    # ── Starting balances by bucket ───────────────────────────────────────────
    # Get 401k total from accounts, apply split % from planning_inputs
    total_401k = sum(a["balance"] for a in accounts if a["account_type"] == "401k")
    pretax_pct = inputs.get("pretax_401k_pct", 0.75)
    roth_pct   = 1.0 - pretax_pct
    pretax_401k = total_401k * pretax_pct if total_401k > 0 else PRETAX_401K_BALANCE_DEFAULT
    roth_401k   = total_401k * roth_pct   if total_401k > 0 else ROTH_401K_BALANCE_DEFAULT

    # Get annual contribution rates from planning_inputs
    salary      = inputs.get("w2_salary", 0)
    emp_pct     = inputs.get("employee_401k_pct", 0.06)
    er_pct      = inputs.get("employer_401k_pct", 0.09)
    annual_401k_roth   = inputs.get("annual_401k_roth_employee",  salary * emp_pct)
    annual_401k_pretax = inputs.get("annual_401k_pretax_employer", salary * er_pct)

    # Pre-tax: traditional 401k slice + Justin IRA
    pretax_start = pretax_401k + sum(
        a["balance"] for a in accounts
        if a["account_type"] == "ira" and a["owner"] not in ("abby","cooper")
    )
    # Roth: roth 401k slice + all Roth IRAs (excluding kids)
    roth_start = roth_401k + sum(
        a["balance"] for a in accounts
        if a["account_type"] == "roth_ira" and a["owner"] not in ("abby","cooper")
    )
    # Taxable brokerage
    taxable_start = sum(
        a["balance"] for a in accounts
        if a["account_type"] == "taxable" and a["owner"] not in ("abby","cooper")
    )  # DAF excluded — charitable money, not investable
    # HSA
    hsa_start = sum(
        a["balance"] for a in accounts
        if a["account_type"] == "hsa"
    )

    scenarios = []

    jason_ss_early   = inputs.get("jason_social_security", JASON_SS_EARLY_DEFAULT)
    jason_ss_delayed = inputs.get("jason_ss_delayed", jason_ss_early * JASON_SS_DELAYED_RATIO)

    for ret_age in (ret_ages if ret_ages is not None else [55, 60, 65]):
        years_to_retire = max(0, ret_age - jason_age)
        pension_annual  = pension_for_age(inputs, ret_age)

        for ss_label, jason_ss_annual, jason_ss_age in [
            ("early",   jason_ss_early,   62),
            ("delayed", jason_ss_delayed, 67),
        ]:
            # ── Project each bucket to retirement ─────────────────────────────
            # Contributions run every year up to retirement regardless of
            # retirement age — this used to stop entirely for the 55
            # scenario even when retirement was years away (bug found by
            # external audit 2026-09-05: someone currently 40 planning to
            # retire at 55 got 0 years of modeled 401k/Roth/HSA/RSU
            # contributions, understating their projected balance). The
            # annuity functions already return 0 when years_to_retire is 0
            # (already at/past that age), so this is correct for every case.
            #
            # 401k contributions are salary-derived (salary * pct) — if an
            # annual raise rate is assumed, grow them at that rate each year
            # rather than holding them flat to retirement.
            pretax_contrib_fv = (
                _fv_growing_annuity(annual_401k_pretax, pre_ret, salary_growth_pct, years_to_retire)
                if salary_growth_pct else _fv_annuity(annual_401k_pretax, pre_ret, years_to_retire)
            )
            roth_contrib_fv = (
                _fv_growing_annuity(annual_401k_roth, pre_ret, salary_growth_pct, years_to_retire)
                if salary_growth_pct else _fv_annuity(annual_401k_roth, pre_ret, years_to_retire)
            )
            pretax_at_ret = _fv(pretax_start, pre_ret, years_to_retire) + pretax_contrib_fv
            roth_at_ret   = _fv(roth_start,   pre_ret, years_to_retire) + roth_contrib_fv
            taxable_at_ret = _fv(taxable_start, pre_ret, years_to_retire)
            hsa_at_ret = (
                _fv(hsa_start, pre_ret, years_to_retire) +
                _fv_annuity(annual_hsa, pre_ret, years_to_retire)
            )
            if annual_rsu > 0:
                taxable_at_ret += _fv_annuity(annual_rsu * 0.65, pre_ret, years_to_retire)

            if ret_age == 55:
                # Sale proceeds for the bridge-to-55 scenario — both sales
                # happen before/at 55.
                asset1_sale_age = inputs.get("asset1_sale_age", 0)
                asset1_sale_net = inputs.get("asset1_sale_net", 0)
                asset1_app      = inputs.get("asset1_appreciation", 0.03)
                if asset1_sale_age and asset1_sale_age <= ret_age:
                    yrs_asset1 = max(0, asset1_sale_age - jason_age)
                    yrs_to_grow = years_to_retire - yrs_asset1
                    asset1_proceeds = asset1_sale_net * ((1 + asset1_app) ** yrs_asset1)
                    taxable_at_ret  += asset1_proceeds * ((1 + pre_ret) ** yrs_to_grow)
                asset2_sale_age = inputs.get("asset2_sale_age", 0)
                asset2_sale_net = inputs.get("asset2_sale_net", 0)
                if asset2_sale_age and asset2_sale_age <= ret_age:
                    yrs_asset2    = max(0, asset2_sale_age - jason_age)
                    yrs_to_grow    = years_to_retire - yrs_asset2
                    taxable_at_ret += asset2_sale_net * ((1 + pre_ret) ** yrs_to_grow)

            portfolio_at_ret = pretax_at_ret + roth_at_ret + taxable_at_ret + hsa_at_ret

            # ── Capitalization for summary ────────────────────────────────────
            income_at_ret = income_today * ((1 + inflation) ** years_to_retire)
            # The planning horizon belongs to the household, not a hidden
            # engine constant. Keep a reasonable guardrail so a typo cannot
            # produce a negative/implausible drawdown period.
            mort_age      = max(ret_age + 1, min(110, int(inputs.get("retirement_end_age") or 99)))
            retire_years  = mort_age - ret_age
            real_rate     = ((1 + post_ret) / (1 + inflation) - 1) if post_ret != inflation else 0.0001

            healthcare_pre       = inputs.get("healthcare_pre_medicare", 0)
            healthcare_post      = inputs.get("healthcare_post_medicare", 0)
            healthcare_kids      = inputs.get("healthcare_kids", 0)
            kids_annual_cost     = inputs.get("kids_annual_cost", 0)
            bridge_income        = inputs.get("bridge_income_55", 0)
            bridge_years         = inputs.get("bridge_years_55", 0)
            kids_years           = inputs.get("kids_years_at_home_55", 0)

            if ret_age == 55:
                # Phase 1 (55-60): bridge job covers healthcare, net draw = base+kids - bridge
                p1_years   = bridge_years
                p1_need    = (income_at_ret + kids_annual_cost * ((1+inflation)**years_to_retire) - bridge_income * ((1+inflation)**years_to_retire))
                cap_p1     = _pv_annuity(max(0, p1_need), real_rate, p1_years)
                # Phase 2 (60-63): fully retired, kids still home, family healthcare
                p2_years   = max(0, kids_years - bridge_years)
                p2_need    = (income_at_ret * ((1+inflation)**p1_years) +
                              kids_annual_cost * ((1+inflation)**(years_to_retire+p1_years)) +
                              healthcare_kids  * ((1+inflation)**(years_to_retire+p1_years)))
                cap_p2     = _pv_annuity(p2_need, real_rate, p2_years) / ((1+real_rate)**p1_years)
                # Phase 3 (63-65): empty nest, pre-Medicare
                p3_years   = max(0, 65 - (ret_age + kids_years))
                p3_need    = (income_at_ret * ((1+inflation)**(kids_years)) +
                              healthcare_pre * ((1+inflation)**(years_to_retire+kids_years)))
                cap_p3     = _pv_annuity(p3_need, real_rate, p3_years) / ((1+real_rate)**kids_years) if p3_years > 0 else 0
                # Phase 4 (65+): Medicare
                p4_years   = mort_age - 65
                p4_need    = (income_at_ret * ((1+inflation)**(65-ret_age)) +
                              healthcare_post * ((1+inflation)**(years_to_retire+(65-ret_age))))
                cap_p4     = _pv_annuity(p4_need, real_rate, p4_years) / ((1+real_rate)**(65-ret_age))
                total_cap_need = cap_p1 + cap_p2 + cap_p3 + cap_p4
            else:
                healthcare_gap_yrs = max(0, 65 - ret_age)
                cap_income_only  = _pv_annuity(income_at_ret, real_rate, retire_years)
                cap_hc_pre       = _pv_annuity(healthcare_pre  * ((1 + inflation) ** years_to_retire), real_rate, healthcare_gap_yrs)
                cap_hc_post      = _pv_annuity(healthcare_post * ((1 + inflation) ** years_to_retire), real_rate, retire_years - healthcare_gap_yrs)
                total_cap_need   = cap_income_only + cap_hc_pre + cap_hc_post

            cap_pension = _pv_annuity(pension_annual, post_ret, mort_age - ret_age)  # frozen pension, no COLA, nominal rate

            years_ss_wait_jason  = max(0, jason_ss_age - ret_age)
            jason_ss_inflated    = jason_ss_annual * ((1 + inflation) ** years_ss_wait_jason)
            cap_jason_ss         = _pv_annuity(jason_ss_inflated, real_rate, mort_age - max(ret_age, jason_ss_age))

            justin_ret_age       = ret_age - (jason_age - justin_age)
            years_ss_wait_justin = max(0, justin_ss_age - justin_ret_age)
            justin_ss_inflated   = justin_ss_annual * ((1 + inflation) ** years_ss_wait_justin)
            cap_justin_ss        = _pv_annuity(justin_ss_inflated, real_rate, mort_age - max(ret_age, justin_ss_age))

            total_cap_income       = cap_pension + cap_jason_ss + cap_justin_ss
            cap_needed_from_assets = max(0, total_cap_need - total_cap_income)
            surplus                = portfolio_at_ret - cap_needed_from_assets
            pct_funded             = min(100, round(
                (portfolio_at_ret / cap_needed_from_assets * 100) if cap_needed_from_assets > 0 else 100
            ))

            # ── Healthcare costs ─────────────────────────────────────────────
            healthcare_pre  = inputs.get("healthcare_pre_medicare", 0)
            healthcare_post = inputs.get("healthcare_post_medicare", 0)

            # ── Year-by-year with buckets and RMDs ───────────────────────────
            pretax  = pretax_at_ret
            roth    = roth_at_ret
            taxable = taxable_at_ret
            hsa     = hsa_at_ret
            jason_rmd_start_age = rmd_start_age(jason_age)
            # Lazy import to avoid a circular import (retirement_tools_engine
            # imports from this module at load time); cheap after the first
            # call since Python caches the module.
            from retirement_tools_engine import marginal_rate as _marginal_rate, STD_DEDUCTION_MFJ_2026 as _STD_DED

            yearly = []
            for yr in range(retire_years):
                age = ret_age + yr

                # Income need this year (includes healthcare, phased for age 55)
                if ret_age == 55:
                    kids_still_home = yr < kids_years
                    bridge_active   = yr < bridge_years
                    if bridge_active:
                        # Phase 1: bridge job covers healthcare, net of bridge income
                        healthcare_this_year = 0
                        kids_cost = kids_annual_cost * ((1 + inflation) ** yr)
                        bridge    = bridge_income    * ((1 + inflation) ** yr)
                        year_need = max(0, income_at_ret * ((1 + inflation) ** yr) + kids_cost - bridge)
                    elif kids_still_home and age < 65:
                        # Phase 2: retired, kids home, family healthcare
                        healthcare_this_year = healthcare_kids
                        kids_cost = kids_annual_cost * ((1 + inflation) ** yr)
                        year_need = income_at_ret * ((1 + inflation) ** yr) + kids_cost + healthcare_kids * ((1 + inflation) ** yr)
                    elif age < 65:
                        # Phase 3: empty nest, pre-Medicare
                        healthcare_this_year = healthcare_pre
                        year_need = income_at_ret * ((1 + inflation) ** yr) + healthcare_pre * ((1 + inflation) ** yr)
                    else:
                        # Phase 4: Medicare
                        healthcare_this_year = healthcare_post
                        year_need = income_at_ret * ((1 + inflation) ** yr) + healthcare_post * ((1 + inflation) ** yr)
                    healthcare_inflated = healthcare_this_year * ((1 + inflation) ** yr)
                else:
                    healthcare_this_year = healthcare_pre if age < 65 else healthcare_post
                    healthcare_inflated  = healthcare_this_year * ((1 + inflation) ** yr)
                    year_need = income_at_ret * ((1 + inflation) ** yr) + healthcare_inflated

                # Fixed income sources
                year_pen = pension_annual  # frozen pension, no COLA
                year_jss = (jason_ss_annual * ((1 + inflation) ** max(0, age - jason_ss_age))
                            if age >= jason_ss_age else 0)
                year_uss = (justin_ss_annual * ((1 + inflation) ** max(0, age - justin_ss_age))
                            if age >= justin_ss_age else 0)
                fixed_income = year_pen + year_jss + year_uss

                # RMD on pre-tax bucket
                rmd = _rmd(pretax, age, jason_rmd_start_age)

                # Net need after fixed income
                net_need = max(0, year_need - fixed_income)

                # Estimated tax on pretax withdrawals — previously
                # withdrawal_pretax and reinvested RMD excess were treated
                # as tax-free, overstating both "after-tax spending" and the
                # taxable bucket (RMD excess was reinvested at its full
                # pretax amount, though you never actually get to keep the
                # untaxed dollars). Approximated with the same MFJ bracket
                # table used elsewhere in the app and the same
                # 85%-of-SS-is-taxable convention as simulation_engine.py.
                # Still approximate — no NIIT, no itemizing, no tax lots or
                # credits, single blended rate per year — but far closer than
                # treating distributions as tax-free. State tax is an
                # explicit optional planning input, not an implicit guess.
                taxable_income_est = max(0, year_pen + (year_jss + year_uss) * 0.85 + rmd - _STD_DED)
                pretax_tax_rate = min(0.90, _marginal_rate(taxable_income_est) + max(0, float(inputs.get("state_income_tax_rate") or 0)))

                # Draw order: taxable first, then pretax (satisfies RMD minimum),
                # then roth last (let it compound)
                withdrawal_taxable = 0.0
                withdrawal_pretax  = 0.0
                withdrawal_roth    = 0.0
                rmd_reinvested     = 0.0
                pretax_tax_owed    = 0.0

                remaining_need = net_need

                # 1. Must take RMD from pretax regardless — its after-tax
                # value (not the gross amount) is what's actually available
                # to cover spending or get reinvested.
                if rmd > 0:
                    actual_rmd = min(rmd, pretax)
                    pretax -= actual_rmd
                    withdrawal_pretax = actual_rmd
                    rmd_tax = actual_rmd * pretax_tax_rate
                    pretax_tax_owed += rmd_tax
                    after_tax_rmd = actual_rmd - rmd_tax
                    if after_tax_rmd <= remaining_need:
                        remaining_need -= after_tax_rmd
                    else:
                        # RMD exceeds need — after-tax excess reinvested in taxable
                        rmd_reinvested = after_tax_rmd - remaining_need
                        taxable += rmd_reinvested
                        remaining_need = 0

                # 2. Draw from taxable next
                if remaining_need > 0 and taxable > 0:
                    draw = min(remaining_need, taxable)
                    taxable -= draw
                    withdrawal_taxable += draw
                    remaining_need -= draw

                # 3. Draw more from pretax if needed — gross up the
                # withdrawal so its after-tax proceeds (not the gross
                # amount) cover the remaining need.
                if remaining_need > 0 and pretax > 0 and rmd == 0:
                    gross_needed = remaining_need / (1 - pretax_tax_rate) if pretax_tax_rate < 1 else remaining_need
                    draw = min(gross_needed, pretax)
                    pretax -= draw
                    withdrawal_pretax += draw
                    draw_tax = draw * pretax_tax_rate
                    pretax_tax_owed += draw_tax
                    remaining_need -= (draw - draw_tax)

                # 4. Draw from HSA (tax-free for medical, eventually anything)
                if remaining_need > 0 and hsa > 0:
                    draw = min(remaining_need, hsa)
                    hsa -= draw
                    remaining_need -= draw

                # 5. Roth last resort
                if remaining_need > 0 and roth > 0:
                    draw = min(remaining_need, roth)
                    roth -= draw
                    withdrawal_roth += draw
                    remaining_need -= draw

                total_withdrawal = withdrawal_taxable + withdrawal_pretax + withdrawal_roth

                # Grow remaining balances
                pretax  = max(0, pretax  * (1 + post_ret))
                roth    = max(0, roth    * (1 + post_ret))
                taxable = max(0, taxable * (1 + post_ret))
                hsa     = max(0, hsa     * (1 + post_ret))

                total_portfolio = pretax + roth + taxable + hsa

                yearly.append({
                    "jason_age":        age,
                    "justin_age":       age - (jason_age - justin_age),
                    "year":             2026 + yr + years_to_retire,
                    "income_need":      round(year_need),
                    "healthcare_cost":   round(healthcare_inflated),
                    "pension":          round(year_pen),
                    "social_security":  round(year_jss + year_uss),
                    "bridge_income":    round(bridge_income * ((1+inflation)**yr)) if ret_age == 55 and yr < bridge_years else 0,
                    "rmd":              round(rmd),
                    "rmd_reinvested":   round(rmd_reinvested),
                    "estimated_tax":    round(pretax_tax_owed),  # approximate — see comment above
                    "withdrawal_pretax":round(withdrawal_pretax),
                    "withdrawal_taxable":round(withdrawal_taxable),
                    "withdrawal_roth":  round(withdrawal_roth),
                    "withdrawal":       round(total_withdrawal),
                    "pretax_balance":   round(pretax),
                    "roth_balance":     round(roth),
                    "taxable_balance":  round(taxable),
                    "hsa_balance":      round(hsa),
                    "portfolio_balance":round(total_portfolio),
                })

            scenarios.append({
                "label":                         f"age_{ret_age}_{ss_label}",
                "retirement_age":                ret_age,
                "ss_timing":                     ss_label,
                "years_to_retirement":           years_to_retire,
                "income_first_year":             round(income_at_ret),
                "income_today_dollars":          round(income_today),
                "pension_annual":                round(pension_annual),
                "jason_ss_annual":               round(jason_ss_annual),
                "jason_ss_start_age":            jason_ss_age,
                "justin_ss_annual":              round(justin_ss_annual),
                "justin_ss_start_age":           justin_ss_age,
                "healthcare_pre_annual":         inputs.get("healthcare_pre_medicare", 0),
                "healthcare_post_annual":        inputs.get("healthcare_post_medicare", 0),
                "healthcare_gap_years":          max(0, 65 - ret_age),
                "healthcare_gap_total":          inputs.get("healthcare_pre_medicare", 0) * max(0, 65 - ret_age),
                "total_capitalized_need":        round(total_cap_need),
                "capitalized_income_sources":    round(total_cap_income),
                "capitalized_needed_from_assets":round(cap_needed_from_assets),
                "portfolio_at_retirement":       round(portfolio_at_ret),
                "pretax_at_retirement":          round(pretax_at_ret),
                "roth_at_retirement":            round(roth_at_ret),
                "taxable_at_retirement":         round(taxable_at_ret),
                "hsa_at_retirement":             round(hsa_at_ret),
                "current_investable_assets":     round(pretax_start + roth_start + taxable_start + hsa_start),
                "projected_surplus":             round(surplus),
                "on_track":                      surplus >= 0,
                "percent_funded":                pct_funded,
                "retirement_end_age":            mort_age,
                "state_income_tax_rate":         inputs.get("state_income_tax_rate", 0),
                "yearly_detail":                 yearly,
            })

    return {"scenarios": scenarios, "generated_at": datetime.datetime.now().isoformat()}


def run_education_projection(inputs: Dict, accounts: List[Dict],
                              continue_contributions_during_college: bool = False) -> Dict:
    edu_return = 0.07

    abby_balance   = sum(a["balance"] for a in accounts if a["account_type"]=="529" and a["owner"]=="abby")
    cooper_balance = sum(a["balance"] for a in accounts if a["account_type"]=="529" and a["owner"]=="cooper")
    abby_monthly   = inputs.get("abby_529_monthly",   ABBY_MONTHLY_529_DEFAULT)
    cooper_monthly = inputs.get("cooper_529_monthly", COOPER_MONTHLY_529_DEFAULT)
    kid1_age = inputs.get("kid1_age", 0)
    kid2_age = inputs.get("kid2_age", 0)

    # Contributions can't outlast your earning years — assume they stop once
    # you hit PARENT_RETIREMENT_AGE_ASSUMPTION, regardless of whether a kid
    # is still in the saving phase or college by then. This is a hard cap
    # applied on top of whatever the college-window assumption below allows.
    jason_age = inputs.get("jason_age", 45)
    years_until_parent_retires = max(0, PARENT_RETIREMENT_AGE_ASSUMPTION - jason_age)

    goals = []
    # "child" stays a fixed internal key (abby/cooper) matching account owners;
    # display name comes from planning_inputs kid1_name/kid2_name at the API layer.
    child_names = {"Abby": inputs.get("kid1_name", "Child 1"), "Cooper": inputs.get("kid2_name", "Child 2")}
    for child, balance_529, current_age, monthly_contrib in [
        ("Abby",   abby_balance,   kid1_age, abby_monthly),
        ("Cooper", cooper_balance, kid2_age, cooper_monthly),
    ]:
        years_to_college  = 18 - current_age
        unl_base = inputs.get("unl_annual_cost", UNL_CURRENT_ANNUAL)
        annual_cost_start = unl_base * ((1 + COLLEGE_COST_INFLATION) ** years_to_college)
        total_cost        = sum(annual_cost_start * ((1+COLLEGE_COST_INFLATION)**yr) for yr in range(COLLEGE_YEARS))

        # How many years from today contributions actually continue — the
        # college-window assumption (stop at college start, or keep going
        # through all 4 years if continue_contributions_during_college),
        # capped by the parent's retirement cutoff above, whichever is
        # sooner. This single number drives both the chart simulation below
        # and the funding_percent/gap figures — previously those were two
        # separate calculations (a closed-form _fv/_fv_annuity_monthly pair
        # for funding math, a year-by-year loop for the chart) that agreed
        # only by coincidence of both assuming uninterrupted contributions;
        # once contributions can stop early or extend, keeping two parallel
        # implementations risks exactly the kind of calc-drift bug this
        # codebase has been bitten by before, so the chart simulation below
        # is now the single source of truth for both.
        contribution_window = years_to_college + (COLLEGE_YEARS if continue_contributions_during_college else 0)
        contribution_years  = min(contribution_window, years_until_parent_retires)

        # Saving-phase balance at each year boundary, computed in closed
        # form rather than iteratively adding monthly_contrib*12 once a
        # year: contributions actually land monthly and compound monthly
        # (same as _fv_annuity_monthly always assumed), so an annual lump-
        # sum step understates growth relative to that — a real regression
        # introduced when this was first rewritten to respect the
        # contribution cutoff below. This decomposition reproduces the
        # original monthly-compounding numbers exactly whenever the cutoff
        # isn't binding, and only changes the number when contributions
        # genuinely do stop early: contributions compound monthly for
        # whichever years they're actually active, then that accumulated
        # sum sits and compounds normally for any years left before college.
        yearly = []
        for yr in range(years_to_college):
            years_elapsed        = yr + 1
            contrib_years_so_far = min(contribution_years, years_elapsed)
            years_dormant        = years_elapsed - contrib_years_so_far
            fv_contrib_at_stop   = _fv_annuity_monthly(monthly_contrib, edu_return, contrib_years_so_far)
            fv_contrib_now       = _fv(fv_contrib_at_stop, edu_return, years_dormant)
            bal = _fv(balance_529, edu_return, years_elapsed) + fv_contrib_now
            yearly.append({"year_label": f"Age {current_age+yr+1}", "balance": round(bal), "phase": "saving"})

        projected_529 = bal if years_to_college > 0 else balance_529  # balance at the moment college starts

        # College-years drawdown still needs a year-by-year loop (costs are
        # withdrawn annually, so there's no clean closed form once
        # withdrawals and any continuing contributions overlap) — but a
        # still-active year's contribution now gets one year's worth of
        # monthly compounding credit (_fv_annuity_monthly for n=1) instead
        # of a flat, uncompounded monthly_contrib*12 add, for the same
        # reason as above.
        # Track the UNCLAMPED balance alongside the displayed (floored-at-0)
        # one, so a real shortfall shows up as a negative number we can
        # measure instead of vanishing into the floor. This is what the
        # "gap"/recommendation below is now based on — the actual worst
        # point the account hits during the 4 college years, not a snapshot
        # taken at day 1 that ignores the growth still to come. That old
        # 90%-target-at-day-1 snapshot was routinely flagging a "gap" (and
        # recommending more savings) even for a trajectory that already
        # ends college with money left over, which made no sense.
        bal = projected_529
        bal_unclamped = projected_529
        worst_deficit = 0.0
        worst_deficit_years_out = years_to_college
        for yr in range(COLLEGE_YEARS):
            college_year_index = years_to_college + yr
            contributing = college_year_index < contribution_years
            contrib_fv = _fv_annuity_monthly(monthly_contrib, edu_return, 1) if contributing else 0
            cost = annual_cost_start * ((1+COLLEGE_COST_INFLATION)**yr)
            bal_unclamped = bal_unclamped*(1+edu_return) + contrib_fv - cost
            bal = max(0, bal*(1+edu_return) + contrib_fv - cost)
            if bal_unclamped < worst_deficit:
                worst_deficit = bal_unclamped
                worst_deficit_years_out = years_to_college + yr + 1
            yearly.append({"year_label": f"College yr {yr+1}", "balance": round(bal), "phase": "drawdown"})

        gap = -worst_deficit if worst_deficit < 0 else 0
        funding_pct = 100 if gap == 0 else max(0, min(100, round((1 - gap / total_cost) * 100) if total_cost > 0 else 0))

        # How much extra it'd take to actually prevent that real shortfall:
        # extra monthly contributions run for as long as contributions can
        # actually happen (capped by the retirement cutoff, same as the
        # real ones), accumulate to a lump sum, then that lump sum keeps
        # growing untouched until the year the deficit actually hits.
        years_saving  = max(0, min(years_to_college, contribution_years))
        years_growth_after_saving = max(0, worst_deficit_years_out - years_saving)
        monthly_rate  = edu_return / 12
        months_saving = years_saving * 12
        if gap > 0 and months_saving > 0 and monthly_rate > 0:
            fv_annuity_needed = gap / ((1+edu_return) ** years_growth_after_saving)
            monthly_needed = fv_annuity_needed * monthly_rate / (((1+monthly_rate)**months_saving) - 1)
        else:
            monthly_needed = 0
        lump_sum = gap / ((1+edu_return)**worst_deficit_years_out) if gap>0 else 0

        goals.append({
            "child": child, "child_name": child_names[child], "current_age": current_age,
            "years_to_college": years_to_college,
            "current_529_balance": round(balance_529),
            "monthly_contribution": round(monthly_contrib),
            "contributions_stop_in_years": contribution_years,
            "projected_529_at_college": round(projected_529),
            "current_annual_cost": round(unl_base),
            "projected_total_cost": round(total_cost),
            "funding_percent": funding_pct,
            "funding_gap": round(gap),
            "monthly_savings_to_close_gap": round(monthly_needed),
            "lump_sum_to_close_gap": round(lump_sum),
            # funding_percent/gap now come from the same drawdown simulation
            # as balance_after_college — the worst point the (unclamped)
            # balance ever hits during the 4 college years — so the toggle
            # affects all of these consistently instead of just this last one.
            "balance_after_college": round(bal),
            "depleted_during_college": worst_deficit < 0,
            "yearly_chart": yearly,
        })

    return {"goals": goals}


def run_kids_projection(accounts: List[Dict], inputs: Dict = None) -> Dict:
    edu_return  = 0.07
    roth_return = 0.07
    if inputs is None: inputs = {}

    abby_529_mo   = inputs.get("abby_529_monthly",       ABBY_MONTHLY_529_DEFAULT)
    cooper_529_mo = inputs.get("cooper_529_monthly",     COOPER_MONTHLY_529_DEFAULT)
    kids_roth_mo  = inputs.get("kids_roth_monthly",      KIDS_ROTH_MONTHLY_DEFAULT)
    kids_cust_mo  = inputs.get("kids_custodial_monthly", KIDS_CUST_MONTHLY_DEFAULT)

    kid1_age = inputs.get("kid1_age", 0)
    kid2_age = inputs.get("kid2_age", 0)
    child_names = {"Abby": inputs.get("kid1_name", "Child 1"), "Cooper": inputs.get("kid2_name", "Child 2")}

    kids = []
    for child, current_age, monthly_529 in [
        ("Abby",   kid1_age, abby_529_mo),
        ("Cooper", kid2_age, cooper_529_mo),
    ]:
        years_to_18 = 18 - current_age

        bal_529  = sum(a["balance"] for a in accounts if a["account_type"]=="529"      and a["owner"]==child.lower())
        bal_roth = sum(a["balance"] for a in accounts if a["account_type"]=="roth_ira" and a["owner"]==child.lower())
        bal_cust = sum(a["balance"] for a in accounts if a["account_type"]=="custodial" and a["owner"]==child.lower())
        bonds    = sum(a["balance"] for a in accounts if a["account_type"]=="other"    and a["owner"]==child.lower() and "bond" in a["name"].lower())

        proj_529_at_18 = _fv(bal_529, edu_return, years_to_18) + _fv_annuity_monthly(monthly_529, edu_return, years_to_18)

        # Drawdown 100% of annual costs
        unl_base_kid = inputs.get("unl_annual_cost", UNL_CURRENT_ANNUAL)
        annual_cost_at_18 = unl_base_kid * ((1 + COLLEGE_COST_INFLATION) ** years_to_18)
        bal_after = proj_529_at_18
        for yr in range(COLLEGE_YEARS):
            cost = annual_cost_at_18 * ((1 + COLLEGE_COST_INFLATION) ** yr)
            bal_after = max(0, bal_after * (1 + edu_return) - cost)
        proj_529_at_22 = round(bal_after)
        roth_rollover  = min(bal_after, 35000)

        proj_roth_at_18 = _fv(bal_roth, roth_return, years_to_18) + _fv_annuity_monthly(kids_roth_mo, roth_return, years_to_18)
        years_18_to_60  = 42
        proj_roth_at_22 = _fv(proj_roth_at_18, roth_return, 4) + roth_rollover
        proj_roth_at_60 = _fv(proj_roth_at_22, roth_return, years_18_to_60 - 4)

        proj_cust_at_18 = _fv(bal_cust, edu_return, years_to_18) + _fv_annuity_monthly(kids_cust_mo, edu_return, years_to_18)
        proj_cust_at_24 = _fv(bal_cust, edu_return, 24 - current_age) + _fv_annuity_monthly(kids_cust_mo, edu_return, 24 - current_age)
        proj_bonds_at_18 = _fv(bonds, 0.04, years_to_18) if bonds > 0 else 0
        # Nothing in this model actually spends the custodial account or
        # the savings bonds — unlike the 529 (drawn down for college) and
        # the Roth (its whole point is a value at 60), these just sat with
        # a projection that stopped at 18/24 and implied the money
        # vanished. Continue compounding both out to 60 — custodial picks
        # up from its already-computed age-24 value (contributions already
        # stopped at 18 in that number), bonds from today's balance at the
        # same 4% bond rate used for its age-18 projection.
        #
        # Bonds specifically stop earning interest at final maturity —
        # unlike custodial, which can keep compounding indefinitely — so
        # bond growth is additionally capped at BOND_MAX_GROWTH_YEARS.
        years_to_60 = max(0, 60 - current_age)
        bond_growth_years = min(years_to_60, BOND_MAX_GROWTH_YEARS)
        proj_cust_at_60 = _fv(proj_cust_at_24, edu_return, 60 - 24)
        proj_bonds_at_60 = _fv(bonds, 0.04, bond_growth_years) if bonds > 0 else 0

        # Timeline to 24
        timeline = []
        r_bal = bal_roth
        c_529 = bal_529
        c_cust = bal_cust
        for yr in range(24 - current_age + 1):
            age = current_age + yr
            if age < 18:
                c_529 = c_529*(1+edu_return) + monthly_529*12
            elif age < 22:
                yr_in_college = age - 18
                unl_base = inputs.get('unl_annual_cost', UNL_CURRENT_ANNUAL)
                cost = unl_base*((1+COLLEGE_COST_INFLATION)**(years_to_18+yr_in_college))
                c_529 = max(0, c_529*(1+edu_return) - cost)
            else:
                c_529 = c_529*(1+edu_return)
            if age < 18:
                r_bal = r_bal*(1+roth_return) + kids_roth_mo*12
            elif age == 22:
                r_bal = r_bal*(1+roth_return) + roth_rollover
            else:
                r_bal = r_bal*(1+roth_return)
            c_cust = c_cust*(1+edu_return) + (kids_cust_mo*12 if age < 18 else 0)
            timeline.append({"age": age, "roth": round(r_bal), "529": round(c_529), "custodial": round(c_cust)})

        roth_to_60 = []
        r = proj_roth_at_18
        for yr in range(years_18_to_60+1):
            age = 18 + yr
            r = r*(1+roth_return)
            if age == 22:
                r += roth_rollover
            if age % 5 == 0 or age == 18 or age == 22:
                roth_to_60.append({"age": age, "balance": round(r),
                                   "note": "529 rollover" if age == 22 and roth_rollover > 0 else None})

        kids.append({
            "child": child, "child_name": child_names[child], "current_age": current_age,
            "529":       {"current": round(bal_529), "at_18": round(proj_529_at_18), "at_22": proj_529_at_22, "monthly_contribution": round(monthly_529)},
            "roth":      {"current": round(bal_roth), "at_18": round(proj_roth_at_18), "at_22": round(proj_roth_at_22), "at_60": round(proj_roth_at_60), "monthly_contribution": kids_roth_mo, "529_rollover": round(roth_rollover)},
            "custodial": {"current": round(bal_cust), "at_18": round(proj_cust_at_18), "at_24": round(proj_cust_at_24), "at_60": round(proj_cust_at_60), "monthly_contribution": round(kids_cust_mo)},
            "bonds":     {"current": round(bonds), "at_18": round(proj_bonds_at_18), "at_60": round(proj_bonds_at_60)},
            "timeline":  timeline,
            "roth_to_60":roth_to_60,
        })

    return {"kids": kids}


def run_insurance_analysis(inputs: Dict, accounts: List[Dict]) -> Dict:
    jason_age  = inputs["jason_age"]
    justin_age = inputs["justin_age"]
    inflation  = inputs["inflation_rate"]
    post_ret   = inputs["expected_return_post_retirement"]
    income_today = inputs["retirement_income_today_dollars"]

    from debt_engine import DEBT_TYPES
    total_debt    = sum(a["balance"] for a in accounts if a["account_type"] in DEBT_TYPES)
    abby_529      = sum(a["balance"] for a in accounts if a["account_type"]=="529" and a["owner"]=="abby")
    cooper_529    = sum(a["balance"] for a in accounts if a["account_type"]=="529" and a["owner"]=="cooper")
    abby_proj     = _fv(abby_529, 0.07, 7)   + _fv_annuity_monthly(inputs.get("abby_529_monthly", ABBY_MONTHLY_529_DEFAULT), 0.07, 7)
    cooper_proj   = _fv(cooper_529, 0.07, 11) + _fv_annuity_monthly(inputs.get("cooper_529_monthly", COOPER_MONTHLY_529_DEFAULT), 0.07, 11)
    unl_base      = inputs.get("unl_annual_cost", UNL_CURRENT_ANNUAL)
    abby_cost     = sum(unl_base*((1+COLLEGE_COST_INFLATION)**(7+yr)) for yr in range(4))
    cooper_cost   = sum(unl_base*((1+COLLEGE_COST_INFLATION)**(11+yr)) for yr in range(4))
    total_529_gap = max(0, abby_cost - abby_proj) + max(0, cooper_cost - cooper_proj)

    justin_years_to_ret = max(0, (60 - (jason_age - justin_age)) - justin_age)
    income_at_ret       = income_today * ((1+inflation)**justin_years_to_ret)
    real_rate           = ((1 + post_ret) / (1 + inflation) - 1) if post_ret != inflation else 0.0001
    retire_years        = 99 - (60 - (jason_age - justin_age))
    cap_income_need     = _pv_annuity(income_at_ret, real_rate, retire_years)

    investable = sum(
        a["balance"] for a in accounts
        if a["account_type"] in {"roth_ira","ira","hsa","taxable","401k"}
        and a["owner"] not in ("abby","cooper")
    )
    proj_assets  = _fv(investable, post_ret, justin_years_to_ret)
    income_gap   = max(0, cap_income_need - proj_assets)

    jason_total_need     = total_debt + total_529_gap + income_gap
    jason_current_coverage = inputs.get("jason_life_basic", 0) + inputs.get("jason_life_supplemental", 0) + inputs.get("jason_life_term", 0)
    jason_surplus        = jason_current_coverage - jason_total_need

    justin_total_need    = total_debt + total_529_gap
    justin_current_coverage = (inputs.get("justin_life_ul", 0) + inputs.get("justin_life_whole", 0)
                                + inputs.get("person2_life_employer", 0) + inputs.get("justin_life_term", 0)
                                + inputs.get("justin_life_kids", 0))
    justin_surplus       = justin_current_coverage - justin_total_need

    primary_key  = inputs.get("primary_residence_key", "")
    rental_key   = inputs.get("rental_property_key", "")
    home_value   = sum(a["balance"] for a in accounts if a["account_type"]=="real_estate" and primary_key and primary_key in a["name"])
    rental_value = sum(a["balance"] for a in accounts if a["account_type"]=="real_estate" and rental_key and rental_key in a["name"])

    # Umbrella adequacy: the standard advisor rule of thumb is coverage at
    # least equal to net worth (what a lawsuit could actually go after),
    # rounded up to the nearest $1M since that's how umbrella policies are
    # typically sold in $1M increments.
    from net_worth_engine import compute_net_worth
    net_worth = compute_net_worth(accounts)["net_worth"]
    umbrella_coverage = inputs.get("umbrella", 0)
    recommended_umbrella = max(1_000_000, math.ceil(max(0, net_worth) / 1_000_000) * 1_000_000)
    umbrella_gap = max(0, recommended_umbrella - umbrella_coverage)

    return {
        "jason":  {"total_need": round(jason_total_need), "debt_payoff": round(total_debt),
                   "college_funding": round(total_529_gap), "income_replacement": round(income_gap),
                   "current_coverage": jason_current_coverage, "surplus_gap": round(jason_surplus), "on_track": jason_surplus >= 0},
        "justin": {"total_need": round(justin_total_need), "debt_payoff": round(total_debt),
                   "college_funding": round(total_529_gap), "income_replacement": 0,
                   "current_coverage": justin_current_coverage, "surplus_gap": round(justin_surplus), "on_track": justin_surplus >= -100000},  # within $100k is acceptable since Jason keeps earning
        "property": {"primary_home_value": round(home_value), "primary_home_insured": inputs.get("home_insured", 0),
                     "primary_home_gap": round(max(0, home_value - inputs.get("home_insured", 0))),
                     "rental_value": round(rental_value), "rental_insured": 0, "umbrella": umbrella_coverage,
                     "net_worth": round(net_worth), "recommended_umbrella": recommended_umbrella,
                     "umbrella_gap": round(umbrella_gap), "umbrella_adequate": umbrella_gap <= 0},
        "disability": {"monthly_benefit": inputs.get("disability_monthly", 0), "to_age": 65, "funded_by": "Employer group policy"},
        "ltc": {"daily_benefit": inputs.get("ltc_daily", 200), "max_benefit": inputs.get("ltc_max", 0), "premium_annual": 369,
                "omaha_daily_cost_low": 134, "omaha_daily_cost_high": 248},
    }
