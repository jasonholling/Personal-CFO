"""
Debt payoff calculations — avalanche/snowball simulation, single-debt
payoff calculators, refinance break-even, and a personalized payoff
recommendation that picks a strategy and explains why.

Debts are ordinary `accounts` rows with account_type in DEBT_TYPES; they
carry interest_rate (annual %, e.g. 0.1899 for 18.99% APR), minimum_payment,
and optionally term_months in addition to the usual balance.
"""
from datetime import date
from typing import List, Dict, Optional

DEBT_TYPES = {"mortgage", "credit_card", "student_loan", "car_loan", "personal_loan"}

MAX_MONTHS = 600  # 50-year safety cap so a too-small payment can't infinite-loop

# Below this, the interest-rate spread between debts isn't worth the extra
# rigor of avalanche — snowball's momentum (fewer, sooner wins) is a fair
# trade for a small dollar difference.
AVALANCHE_ADVANTAGE_THRESHOLD = 200


def amortized_payment(principal: float, annual_rate: float, term_months: int) -> float:
    """Standard fixed-payment amortization formula — used both for the
    refinance calculator and to suggest a minimum payment when a debt has a
    known term but no stated minimum payment yet."""
    if term_months <= 0:
        return 0.0
    r = annual_rate / 12
    if r == 0:
        return principal / term_months
    return principal * (r * (1 + r) ** term_months) / ((1 + r) ** term_months - 1)


def get_debt_accounts(accounts: List[Dict]) -> List[Dict]:
    return [a for a in accounts if a.get("account_type") in DEBT_TYPES and a.get("balance", 0) > 0]


def find_negative_amortization_debts(debts: List[Dict]) -> List[Dict]:
    """Debts whose stated minimum payment doesn't even cover a month's
    interest — the balance would grow forever at that payment level."""
    flagged = []
    for d in debts:
        monthly_interest = d.get("balance", 0) * (d.get("interest_rate", 0) / 12)
        min_pay = d.get("minimum_payment", 0)
        if min_pay > 0 and min_pay <= monthly_interest:
            flagged.append({
                "id": d["id"], "name": d["name"],
                "monthly_interest": round(monthly_interest),
                "minimum_payment": min_pay,
            })
    return flagged


def _simulate_payoff(debts: List[Dict], extra_monthly: float, order_key, one_time_payments: Optional[List[Dict]] = None) -> Dict:
    """Simulate paying minimums on every debt, with `extra_monthly` applied
    each month to whichever debt sorts first under `order_key` (avalanche:
    highest rate; snowball: smallest balance). Once a debt is paid off, its
    minimum payment rolls into the extra pool for the rest ("snowballing").

    one_time_payments: optional list of {"account_id": int,
    "months_from_now": int, "amount": float} dicts — a one-time lump sum
    (amount as a positive number) applied directly against that SPECIFIC
    debt's balance in the month it lands, on top of (not instead of) its
    minimum payment and any avalanche/snowball extra_monthly already
    flowing to it that month. Unlike a freed-up minimum payment (which
    rolls into extra_pool for the snowball above), a lump sum that exceeds
    the debt's remaining balance does NOT roll over to other debts — it's
    simply capped at that balance and the rest is left on the table; that
    debt is just extinguished early. Defaults to None/empty so every
    existing caller (run_avalanche_snowball, credit_card_payoff_calculator,
    etc.) is completely unaffected."""
    balances = {d["id"]: d["balance"] for d in debts}
    minimums = {d["id"]: max(d.get("minimum_payment", 0), 0) for d in debts}
    rates    = {d["id"]: max(d.get("interest_rate", 0), 0) for d in debts}
    names    = {d["id"]: d["name"] for d in debts}
    order    = sorted(debts, key=order_key)

    # Index one-time payments by the month they land in, ignoring anything
    # that doesn't target one of these debts (e.g. a household with more
    # than one debt account, only some of them ever targeted).
    payments_by_month: Dict[int, List[Dict]] = {}
    for p in (one_time_payments or []):
        if p["account_id"] not in balances:
            continue
        payments_by_month.setdefault(int(p["months_from_now"]), []).append(p)

    total_interest = 0.0
    interest_by_debt = {did: 0.0 for did in balances}
    payoff_month = {}
    schedule = []
    balance_history = {did: [] for did in balances}
    month = 0
    extra_pool = extra_monthly

    while any(b > 0.005 for b in balances.values()) and month < MAX_MONTHS:
        month += 1
        month_interest = 0.0

        for did, bal in balances.items():
            if bal <= 0:
                continue
            interest = bal * (rates[did] / 12)
            month_interest += interest
            interest_by_debt[did] += interest
            balances[did] = bal + interest

        available_extra = extra_pool
        for d in order:
            did = d["id"]
            if balances[did] <= 0:
                continue
            pay = min(minimums[did], balances[did])
            balances[did] -= pay

        for d in order:
            did = d["id"]
            if balances[did] <= 0 or available_extra <= 0:
                continue
            pay = min(available_extra, balances[did])
            balances[did] -= pay
            available_extra -= pay

        # One-time lump-sum payments landing this month, targeted at a
        # SPECIFIC debt rather than avalanche/snowball-ordered — applied on
        # top of the above. Capped at the remaining balance; no rollover to
        # other debts if the lump sum is bigger than what's owed.
        for p in payments_by_month.get(month, []):
            did = p["account_id"]
            if balances[did] <= 0:
                continue
            pay = min(float(p["amount"]), balances[did])
            balances[did] -= pay

        for did, bal in balances.items():
            if bal <= 0.005 and did not in payoff_month:
                payoff_month[did] = month
                extra_pool += minimums[did]  # roll the freed-up minimum into the snowball

        total_interest += month_interest
        schedule.append({
            "month": month,
            "total_balance": round(sum(max(b, 0) for b in balances.values())),
        })
        for did, bal in balances.items():
            balance_history[did].append({"month": month, "balance": round(max(bal, 0))})

    return {
        "months_to_debt_free": month if month < MAX_MONTHS else None,
        "total_interest_paid": round(total_interest),
        "payoff_order": [
            {"id": did, "name": names[did], "payoff_month": payoff_month.get(did)}
            for did in sorted(payoff_month, key=lambda k: payoff_month[k])
        ],
        "schedule": schedule[::3],  # every 3rd month keeps the response small
        # Per-debt breakdowns — additive, only consumed by
        # project_debt_schedule below; existing callers ignore these keys.
        "interest_by_debt": {did: round(v) for did, v in interest_by_debt.items()},
        "balance_history": {did: hist[::3] for did, hist in balance_history.items()},
    }


def run_avalanche_snowball(accounts: List[Dict], extra_monthly: float = 0) -> Dict:
    debts = get_debt_accounts(accounts)
    if not debts:
        return {"has_debt": False}

    avalanche = _simulate_payoff(debts, extra_monthly, order_key=lambda d: -d.get("interest_rate", 0))
    snowball  = _simulate_payoff(debts, extra_monthly, order_key=lambda d: d.get("balance", 0))

    return {
        "has_debt": True,
        "total_balance": round(sum(d["balance"] for d in debts)),
        "total_minimum_payment": round(sum(d.get("minimum_payment", 0) for d in debts)),
        "avalanche": avalanche,
        "snowball": snowball,
        "interest_saved_with_avalanche": round(snowball["total_interest_paid"] - avalanche["total_interest_paid"]),
    }


def project_debt_schedule(accounts: List[Dict], extra_monthly: float = 0,
                           one_time_payments: Optional[List[Dict]] = None) -> Dict:
    """Same avalanche/snowball plan as run_avalanche_snowball, extended
    with one-time lump-sum extra payments landing in specific months and
    targeted at a SPECIFIC debt (e.g. a life event's sale proceeds applied
    toward paying down a mortgage) — not spread across debts by
    avalanche/snowball ordering the way `extra_monthly` is.

    one_time_payments: list of {"account_id": int, "months_from_now": int,
    "amount": float} dicts. Defaults to None/empty, in which case this
    returns exactly what run_avalanche_snowball would (plus the additive
    "one_time_payment_effect" key below, which is just empty lists).

    Response shape is run_avalanche_snowball's shape (has_debt,
    total_balance, total_minimum_payment, avalanche, snowball,
    interest_saved_with_avalanche) plus one new key:

      "one_time_payment_effect": {"avalanche": [...], "snowball": [...]}

    Each list has one entry per targeted debt (order depends on the
    strategy's own tie-breaking, not insertion order), each entry:
      {account_id, name, original_payoff_month, new_payoff_month,
       months_saved, interest_saved, balance_history}
    comparing a baseline run (same extra_monthly, no one-time payments)
    against the run with the one-time payments applied — isolating the
    one-time-payment effect alone from whatever extra_monthly is already
    doing. `balance_history` is that debt's own balance trajectory (same
    every-3rd-month cadence as the existing `schedule`), taken from the
    run WITH the one-time payments.

    Design note: if two life events target the SAME debt in different
    years, both land in one_time_payments and both get applied in
    `_simulate_payoff` in whichever month they fall — order doesn't matter
    since each is independently capped at the balance remaining when it
    lands. Callers aren't expected to merge/dedupe them."""
    debts = get_debt_accounts(accounts)
    if not debts:
        return {"has_debt": False}
    one_time_payments = one_time_payments or []
    names = {d["id"]: d["name"] for d in debts}
    valid_ids = set(names)
    targeted_ids = sorted({p["account_id"] for p in one_time_payments if p["account_id"] in valid_ids})

    def _run(order_key):
        baseline = _simulate_payoff(debts, extra_monthly, order_key)
        with_payments = _simulate_payoff(debts, extra_monthly, order_key, one_time_payments=one_time_payments)
        effects = []
        for did in targeted_ids:
            base_month = next((p["payoff_month"] for p in baseline["payoff_order"] if p["id"] == did), None)
            new_month  = next((p["payoff_month"] for p in with_payments["payoff_order"] if p["id"] == did), None)
            base_interest = baseline["interest_by_debt"].get(did, 0)
            new_interest  = with_payments["interest_by_debt"].get(did, 0)
            effects.append({
                "account_id": did,
                "name": names[did],
                "original_payoff_month": base_month,
                "new_payoff_month": new_month,
                "months_saved": (base_month - new_month) if (base_month is not None and new_month is not None) else None,
                "interest_saved": round(base_interest - new_interest),
                "balance_history": with_payments["balance_history"].get(did, []),
            })
        return with_payments, effects

    avalanche, avalanche_effects = _run(lambda d: -d.get("interest_rate", 0))
    snowball, snowball_effects   = _run(lambda d: d.get("balance", 0))

    return {
        "has_debt": True,
        "total_balance": round(sum(d["balance"] for d in debts)),
        "total_minimum_payment": round(sum(d.get("minimum_payment", 0) for d in debts)),
        "avalanche": avalanche,
        "snowball": snowball,
        "interest_saved_with_avalanche": round(snowball["total_interest_paid"] - avalanche["total_interest_paid"]),
        "one_time_payment_effect": {"avalanche": avalanche_effects, "snowball": snowball_effects},
    }


def _months_to_date_str(months: Optional[int], today: Optional[date] = None) -> Optional[str]:
    if months is None:
        return None
    today = today or date.today()
    total = today.month - 1 + months
    year = today.year + total // 12
    month = total % 12 + 1
    return date(year, month, 1).strftime("%B %Y")


def recommend_payoff_strategy(accounts: List[Dict], extra_monthly: float = 0, today: Optional[date] = None,
                               one_time_payments: Optional[List[Dict]] = None) -> Dict:
    """The actual recommendation, not just two calculators side by side:
    picks avalanche or snowball, explains why in plain English, flags any
    debt whose minimum payment can't even keep up with interest, and gives
    a concrete next action.

    one_time_payments: optional life-event-sourced lump sums targeted at
    specific debts (see project_debt_schedule) — defaults to None/empty so
    every existing caller is unaffected. When present, adds a
    "life_event_debt_payments" key (the chosen strategy's slice of
    project_debt_schedule's one_time_payment_effect) on top of every field
    this function already returned, so existing readers of this response
    are unaffected either way.
    """
    debts = get_debt_accounts(accounts)
    if not debts:
        return {"has_debt": False}

    plan = project_debt_schedule(accounts, extra_monthly, one_time_payments=one_time_payments)
    avalanche, snowball = plan["avalanche"], plan["snowball"]
    negative_amortization = find_negative_amortization_debts(debts)

    savings = plan["interest_saved_with_avalanche"]
    many_small_debts = len(debts) >= 3
    use_snowball = savings < AVALANCHE_ADVANTAGE_THRESHOLD and many_small_debts
    strategy = "snowball" if use_snowball else "avalanche"
    chosen = snowball if use_snowball else avalanche

    if use_snowball:
        reason = (
            f"The interest-rate spread between your debts only costs you about ${savings:,.0f} extra "
            f"with the snowball method — small enough that clearing your smallest balances first for quick "
            f"wins is worth it with {len(debts)} separate debts to stay motivated on."
        )
    else:
        reason = (
            f"Paying off your highest-rate debt first saves you ${savings:,.0f} in interest compared to "
            f"snowball, without meaningfully changing how long it takes — there's no real tradeoff here."
            if savings > 0 else
            "Your debts are already close in cost, so tackling the highest rate first is the safe default."
        )

    focus_first = chosen["payoff_order"][0] if chosen["payoff_order"] else None

    return {
        "has_debt": True,
        "strategy": strategy,
        "reason": reason,
        "total_balance": plan["total_balance"],
        "total_minimum_payment": plan["total_minimum_payment"],
        "extra_monthly": extra_monthly,
        "months_to_debt_free": chosen["months_to_debt_free"],
        "debt_free_date": _months_to_date_str(chosen["months_to_debt_free"], today),
        "total_interest_paid": chosen["total_interest_paid"],
        "interest_saved_vs_other_strategy": savings if not use_snowball else -savings,
        "focus_first": focus_first,
        "payoff_order": chosen["payoff_order"],
        "negative_amortization_debts": negative_amortization,
        "avalanche_summary": {"months_to_debt_free": avalanche["months_to_debt_free"], "total_interest_paid": avalanche["total_interest_paid"]},
        "snowball_summary": {"months_to_debt_free": snowball["months_to_debt_free"], "total_interest_paid": snowball["total_interest_paid"]},
        # Additive — the chosen strategy's slice of project_debt_schedule's
        # one_time_payment_effect, so the frontend/user can tell what came
        # from life-event lump sums vs. the manual extra_monthly input
        # above. Empty list when no debt-targeted life events are active.
        "life_event_debt_payments": plan["one_time_payment_effect"]["snowball" if use_snowball else "avalanche"],
    }


def credit_card_payoff_calculator(balance: float, apr: float, monthly_payment: float, extra: float = 100) -> Dict:
    """Single-debt payoff calc — Prompt 27 equivalent."""
    base = _simulate_payoff(
        [{"id": 1, "name": "card", "balance": balance, "interest_rate": apr, "minimum_payment": monthly_payment}],
        extra_monthly=0, order_key=lambda d: 0,
    )
    with_extra = _simulate_payoff(
        [{"id": 1, "name": "card", "balance": balance, "interest_rate": apr, "minimum_payment": monthly_payment}],
        extra_monthly=extra, order_key=lambda d: 0,
    )
    return {
        "months_to_payoff": base["months_to_debt_free"],
        "total_interest_paid": base["total_interest_paid"],
        "months_to_payoff_with_extra": with_extra["months_to_debt_free"],
        "total_interest_with_extra": with_extra["total_interest_paid"],
        "extra_payment_assumed": extra,
        "months_saved": (
            (base["months_to_debt_free"] - with_extra["months_to_debt_free"])
            if base["months_to_debt_free"] and with_extra["months_to_debt_free"] else None
        ),
    }


def refinance_breakeven(balance: float, current_rate: float, new_rate: float,
                         term_years: int, closing_costs: float) -> Dict:
    """Prompt 33 equivalent — standard amortized-payment comparison."""
    current_payment = amortized_payment(balance, current_rate, term_years * 12)
    new_payment      = amortized_payment(balance, new_rate, term_years * 12)
    monthly_savings  = current_payment - new_payment

    breakeven_months = (closing_costs / monthly_savings) if monthly_savings > 0 else None
    total_savings = (monthly_savings * term_years * 12) - closing_costs if monthly_savings > 0 else None

    return {
        "current_monthly_payment": round(current_payment),
        "new_monthly_payment": round(new_payment),
        "monthly_savings": round(monthly_savings),
        "breakeven_months": round(breakeven_months) if breakeven_months else None,
        "worth_it": bool(breakeven_months and breakeven_months < term_years * 12),
        "total_savings_over_term": round(total_savings) if total_savings is not None else None,
    }


def debt_vs_invest_crossover(debt_rate: float, expected_return: float, employer_match_pct: float = 0) -> Dict:
    """Prompt 32 equivalent — simple rate-comparison guidance, not
    personalized tax/behavioral modeling."""
    return {
        "debt_rate": debt_rate,
        "expected_investment_return": expected_return,
        "recommendation": (
            "capture_match_then_pay_debt" if employer_match_pct > 0 and debt_rate > expected_return
            else "pay_debt_first" if debt_rate > expected_return
            else "invest_first" if debt_rate < expected_return
            else "split_evenly"
        ),
        "note": "Employer 401k match is free money — always capture it before extra debt paydown, "
                "regardless of the rate comparison.",
    }
