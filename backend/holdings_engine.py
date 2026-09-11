"""
Portfolio holdings, allocation, and rebalancing — pure calculation module
(codex/portfolio-holdings-allocation, Milestone 3).

Decision support only. Nothing here places a trade or presents a
recommendation as guaranteed financial advice — every recommendation
carries an explicit reason, an assumption/confidence note, and (for a
taxable sale) a tax warning when cost basis is missing rather than
assuming it.

This module is deliberately independent of the existing allocation_engine.
py (account-level stock_allocation_pct guess, still used by the Net Worth
page's own allocation/fee/concentration cards — untouched by this branch).
Per the brief: "Never silently use the existing guessed 80/20 allocation
when real holdings are available." When real holdings exist for an
account, THIS module's per-holding asset_class data is authoritative for
that account; allocation_engine.py's account-level guess is never
consulted here and this module never writes back to it.
"""
import csv
import io
from typing import Dict, List, Optional, Set

# ── Account-type model ──────────────────────────────────────────────────
# Deliberately separate from net_worth_engine.VALID_ACCOUNT_TYPES / the
# existing `accounts.account_type` column — see db.py's
# init_holdings_tables() docstring for the full rationale. This is the
# Holdings feature's OWN 12-value enum, read from the additive, nullable
# `accounts.portfolio_account_type` override column, or derived from the
# legacy account_type when that override is unset.
PORTFOLIO_ACCOUNT_TYPES = frozenset({
    "brokerage", "traditional_401k", "roth_401k", "traditional_ira",
    "roth_ira", "hsa", "529", "custodial", "checking", "savings",
    "trust", "other",
})

# Legacy accounts.account_type -> this module's own enum. Deliberately a
# many-to-one, lossy mapping in one direction: this app has never
# distinguished a standalone Roth 401k from a Traditional 401k at the
# account level (see db.py's docstring) -- both live under the single
# legacy "401k" account_type, split only by a HOUSEHOLD-WIDE pretax/Roth
# percentage (planning_inputs.pretax_401k_pct) that projection_engine.py/
# simulation_engine.py already use for retirement-withdrawal math. A
# household with a genuine separate Roth 401k account needs to set
# portfolio_account_type="roth_401k" explicitly via the override column --
# this mapping alone can't tell the two apart, so it defaults every plain
# "401k" row to "traditional_401k" (the majority case, matching the
# pretax_401k_pct default of 0.75) rather than guessing. Documented as a
# known limitation, not silently papered over.
_LEGACY_TO_PORTFOLIO_TYPE = {
    "taxable":   "brokerage",
    "401k":      "traditional_401k",
    "ira":       "traditional_ira",
    "roth_ira":  "roth_ira",
    "hsa":       "hsa",
    "529":       "529",
    "custodial": "custodial",
    "checking":  "checking",
    "savings":   "savings",
}


def resolve_portfolio_account_type(account: Dict) -> str:
    """The account's portfolio_account_type override if explicitly set
    (must be one of PORTFOLIO_ACCOUNT_TYPES -- validated at the API layer,
    not re-validated here), else derived from the legacy account_type via
    _LEGACY_TO_PORTFOLIO_TYPE, else "other" (real_estate/business/
    insurance/daf/debt/mortgage/an unrecognized legacy type, or an account
    with no account_type at all -- every one of these BLOCKS allocation
    recommendations for that account per the brief, exactly like a
    missing type would)."""
    override = account.get("portfolio_account_type")
    if override:
        return override
    return _LEGACY_TO_PORTFOLIO_TYPE.get(account.get("account_type"), "other")


# Behavior groups, built directly from the brief's own "Account-type
# behavior" section -- one named set per rule, so each rule reads as a
# single membership test everywhere it's used below, not a repeated
# inline tuple that could drift between call sites.
TAXABLE_GAIN_TYPES        = {"brokerage"}
PRETAX_RMD_TYPES          = {"traditional_401k", "traditional_ira"}
ROTH_TYPES                = {"roth_401k", "roth_ira"}
HSA_TYPES                 = {"hsa"}
CHILD_SPECIFIC_TYPES      = {"529", "custodial"}
LIQUIDITY_TYPES           = {"checking", "savings"}
REVIEW_REQUIRED_TYPES     = {"trust"}
BLOCKED_TYPES             = {"other"}

# Household retirement/investment allocation includes every type EXCEPT
# the three carve-outs the brief calls out explicitly: HSA (reported
# separately, never silently folded into ordinary taxable/retirement),
# 529/custodial (child-specific, excluded from household retirement
# allocation), and checking/savings (a liquidity reserve, excluded from
# stock/bond allocation). "other"/missing-type accounts are excluded too
# (blocked until classified) but that's enforced by is_allocation_blocked
# below, not by this membership set, since a trust account IS eligible
# (flagged for review, not excluded) and needs to stay in this set.
HOUSEHOLD_ALLOCATION_TYPES = PORTFOLIO_ACCOUNT_TYPES - HSA_TYPES - CHILD_SPECIFIC_TYPES - LIQUIDITY_TYPES - BLOCKED_TYPES


def is_allocation_blocked(portfolio_type: str) -> bool:
    """"other" or an unresolvable type blocks allocation recommendations
    for that account until it's classified -- per the brief verbatim."""
    return portfolio_type not in PORTFOLIO_ACCOUNT_TYPES or portfolio_type in BLOCKED_TYPES


ASSET_CLASSES = (
    "us_stock", "international_stock", "bonds", "cash",
    "real_estate", "alternatives", "unclassified",
)


# ── Milestone 1: reconciliation ─────────────────────────────────────────

def reconcile_account_holdings(account: Dict, account_holdings: List[Dict]) -> Dict:
    """Compares an account's own balance against the sum of its holdings'
    market_value. Never invents missing holdings data -- a mismatch is
    surfaced as an explicit "unclassified remainder" (positive: holdings
    under-total the account; negative: they over-total it, e.g. stale
    holdings data after a withdrawal) plus a warning, not silently
    absorbed or hidden."""
    account_balance = account.get("balance", 0) or 0
    holdings_total = sum(h.get("market_value", 0) or 0 for h in account_holdings)
    remainder = round(account_balance - holdings_total, 2)
    # A tiny remainder is just floating-point/rounding noise from
    # independently-entered share prices, not a real gap worth a warning.
    RECONCILIATION_TOLERANCE = 1.0
    has_warning = abs(remainder) > RECONCILIATION_TOLERANCE
    return {
        "account_id": account.get("id"),
        "account_balance": round(account_balance, 2),
        "holdings_total": round(holdings_total, 2),
        "unreconciled_remainder": remainder,
        "has_warning": has_warning,
        "warning": (
            f"Holdings total ${holdings_total:,.0f} does not match the account balance "
            f"${account_balance:,.0f} -- ${remainder:,.0f} is shown as an unclassified remainder "
            f"rather than assumed." if has_warning else None
        ),
    }


# ── Milestone 1/3: classification ───────────────────────────────────────

def classify_holdings(accounts: List[Dict], holdings: List[Dict]) -> Dict:
    """Groups every holding by its parent account's resolved portfolio
    account type into the behavior buckets the brief defines, WITHOUT
    inventing any allocation decision yet -- pure grouping, consumed by
    compute_current_allocation/recommend_* below. Returns holdings
    augmented with `_portfolio_account_type` and `_account` for the
    caller's convenience (original dicts untouched -- new dicts, not
    mutated in place)."""
    accounts_by_id = {a["id"]: a for a in accounts}
    household, hsa, child_specific, liquidity, blocked, review_required = [], [], [], [], [], []
    for h in holdings:
        account = accounts_by_id.get(h.get("account_id"))
        if account is None:
            blocked.append({**h, "_portfolio_account_type": None, "_account": None})
            continue
        ptype = resolve_portfolio_account_type(account)
        enriched = {**h, "_portfolio_account_type": ptype, "_account": account}
        if is_allocation_blocked(ptype):
            blocked.append(enriched)
        elif ptype in HSA_TYPES:
            hsa.append(enriched)
        elif ptype in CHILD_SPECIFIC_TYPES:
            child_specific.append(enriched)
        elif ptype in LIQUIDITY_TYPES:
            liquidity.append(enriched)
        else:
            household.append(enriched)
            if ptype in REVIEW_REQUIRED_TYPES:
                review_required.append(enriched)
    return {
        "household": household,
        "hsa": hsa,
        "child_specific": child_specific,
        "liquidity": liquidity,
        "blocked": blocked,
        "review_required": review_required,
    }


# ── Milestone 3: current allocation ─────────────────────────────────────

def compute_current_allocation(classified_holdings: List[Dict]) -> Dict:
    """Current dollar/percentage allocation by asset class for a list of
    already-classified holdings (household, hsa, or any other group from
    classify_holdings). An unclassified holding (asset_class ==
    "unclassified", or any value outside ASSET_CLASSES -- defensive
    against a stale/hand-edited row) is counted in its own "unclassified"
    bucket rather than silently dropped from the total or guessed into a
    class it was never assigned."""
    by_class = {c: 0.0 for c in ASSET_CLASSES}
    total = 0.0
    for h in classified_holdings:
        value = h.get("market_value", 0) or 0
        asset_class = h.get("asset_class") or "unclassified"
        if asset_class not in by_class:
            asset_class = "unclassified"
        by_class[asset_class] += value
        total += value
    pct_by_class = {c: (round(v / total * 100, 2) if total > 0 else 0.0) for c, v in by_class.items()}
    return {
        "total": round(total, 2),
        "by_class": {c: round(v, 2) for c, v in by_class.items()},
        "pct_by_class": pct_by_class,
    }


# ── Milestone 2/3: policy comparison ────────────────────────────────────

# Maps investment_policies' target_* columns to ASSET_CLASSES -- kept as
# one explicit table rather than a naming convention (target_us_stock_pct
# -> "us_stock") so a future asset class doesn't have to fit a string-
# munging scheme to be added.
POLICY_TARGET_FIELD_TO_ASSET_CLASS = {
    "target_us_stock_pct":            "us_stock",
    "target_international_stock_pct": "international_stock",
    "target_bonds_pct":               "bonds",
    "target_cash_pct":                "cash",
    "target_real_estate_pct":         "real_estate",
    "target_alternatives_pct":        "alternatives",
}


def policy_targets_by_class(policy: Dict) -> Dict[str, float]:
    """Extracts {asset_class: target_pct} from a raw investment_policies
    row. "unclassified" never has a target -- there's no such thing as a
    deliberate allocation to "we don't know what this is.\""""
    targets = {c: 0.0 for c in ASSET_CLASSES}
    for field, asset_class in POLICY_TARGET_FIELD_TO_ASSET_CLASS.items():
        targets[asset_class] = policy.get(field, 0) or 0
    return targets


def compare_to_target(current_allocation: Dict, policy: Dict) -> Dict:
    """Current vs. target allocation, drift-band-aware. deviation_pct is
    signed: positive means OVERWEIGHT (current > target), negative means
    UNDERWEIGHT. within_drift_band uses the policy's own drift_band_pct
    (a household with no policy never reaches this function -- callers
    must check for an active policy first and render the explicit setup
    state the brief requires instead)."""
    targets = policy_targets_by_class(policy)
    drift_band = policy.get("drift_band_pct", 5) or 5
    total = current_allocation["total"]
    deviations = {}
    for asset_class in ASSET_CLASSES:
        current_pct = current_allocation["pct_by_class"].get(asset_class, 0.0)
        target_pct = targets.get(asset_class, 0.0)
        deviation_pct = round(current_pct - target_pct, 2)
        deviation_dollars = round(deviation_pct / 100 * total, 2)
        deviations[asset_class] = {
            "current_pct": current_pct,
            "target_pct": round(target_pct, 2),
            "deviation_pct": deviation_pct,
            "deviation_dollars": deviation_dollars,
            "within_drift_band": abs(deviation_pct) <= drift_band,
        }
    return {
        "drift_band_pct": drift_band,
        "by_class": deviations,
        "any_outside_band": any(not d["within_drift_band"] for d in deviations.values()),
    }


# ── Milestone 3: contribution-first destination recommendation ─────────

def recommend_contribution_destination(comparison: Dict, contribution_amount: float) -> List[Dict]:
    """Where new money should go: exclusively the most-underweight asset
    classes first, up to closing each one's own dollar gap, never
    touching an already-at-or-above-target class. This is the "use
    contributions before sales" half of the brief -- called BEFORE
    recommend_rebalance_actions decides whether any sale is needed at
    all, since a big enough contribution can close the whole drift on its
    own."""
    if contribution_amount <= 0:
        return []
    # Only classes that are UNDERWEIGHT (negative deviation, i.e. below
    # target) are contribution destinations -- an overweight class never
    # receives new money just because it's "eligible."
    underweight = [
        (asset_class, -d["deviation_dollars"])  # dollars needed to reach target, positive
        for asset_class, d in comparison["by_class"].items()
        if d["deviation_dollars"] < 0 and asset_class != "unclassified"
    ]
    underweight.sort(key=lambda pair: pair[1], reverse=True)  # largest gap first
    remaining = contribution_amount
    actions = []
    for asset_class, gap in underweight:
        if remaining <= 0:
            break
        amount = min(remaining, gap)
        if amount <= 0:
            continue
        actions.append({
            "asset_class": asset_class,
            "amount": round(amount, 2),
            "reason": f"Underweight by ${gap:,.0f} against target -- directing new contributions here closes the gap without selling anything.",
        })
        remaining -= amount
    if remaining > 0.01 and actions:
        # Every underweight class is now at target and money is still
        # left over -- note it explicitly rather than silently dropping
        # it or overfunding an already-on-target class.
        actions.append({
            "asset_class": None,
            "amount": round(remaining, 2),
            "reason": "Every underweight asset class is now fully funded to target -- the remainder has no drift to correct and can go wherever the household's policy/liquidity needs direct it.",
        })
    elif not actions:
        actions.append({
            "asset_class": None,
            "amount": round(contribution_amount, 2),
            "reason": "No asset class is currently underweight -- this contribution doesn't need to be targeted for rebalancing purposes.",
        })
    return actions


# ── Milestone 3: rebalancing actions ────────────────────────────────────

def _account_taxable_gain_warning(holding: Dict, sell_amount: float) -> Optional[Dict]:
    """A taxable sale's tax impact can only be ESTIMATED when cost_basis
    is known -- missing cost basis is flagged explicitly, never assumed
    to be zero (which would overstate the gain and the tax) or equal to
    market value (which would understate it to zero)."""
    cost_basis = holding.get("cost_basis")
    market_value = holding.get("market_value", 0) or 0
    if cost_basis is None:
        return {
            "has_cost_basis": False,
            "message": "Cost basis is not on file for this holding -- the taxable gain/loss on this sale cannot be estimated. Set cost basis before relying on any tax figure here.",
        }
    if market_value <= 0:
        gain_fraction = 0.0
    else:
        gain_fraction = max(0.0, (market_value - cost_basis) / market_value)
    estimated_gain = round(sell_amount * gain_fraction, 2)
    return {
        "has_cost_basis": True,
        "estimated_gain": estimated_gain,
        "message": (
            f"Estimated taxable gain on this sale: ${estimated_gain:,.0f} (based on this holding's overall "
            f"unrealized-gain fraction, not the tax lot actually sold -- an approximation, not a brokerage-grade "
            f"lot calculation)." if estimated_gain > 0 else
            "This holding is at or below cost basis -- no taxable gain expected on this sale, possibly a "
            "deductible loss (consult a tax professional for loss-harvesting specifics)."
        ),
    }


def recommend_rebalance_actions(
    classified_household_holdings: List[Dict],
    current_allocation: Dict,
    comparison: Dict,
    policy: Dict,
    pending_contribution: float = 0.0,
) -> Dict:
    """Full rebalancing recommendation: applies the contribution first
    (recommend_contribution_destination), then only recommends
    buy/sell/exchange ACTIONS on existing holdings for whatever drift the
    contribution didn't already close. Never recommends a taxable
    (brokerage) sale when the drift can instead be corrected by a
    tax-advantaged exchange (a trade inside traditional/Roth 401k/IRA,
    which has no tax consequence) UNLESS the policy's own
    use_contributions_before_sales is explicitly False (interpreted per
    the brief as "policy explicitly allows" skipping the
    contribution/exchange-first preference) or there simply isn't enough
    tax-advantaged balance in overweight classes to fund the correction.

    Returns {contribution_actions, rebalance_actions, projected_allocation}
    -- projected_allocation is the household allocation AFTER every
    recommended action, so a reader can see the plan's own effect instead
    of taking "this fixes it" on faith."""
    contribution_actions = recommend_contribution_destination(comparison, pending_contribution)
    contribution_by_class = {a["asset_class"]: a["amount"] for a in contribution_actions if a["asset_class"]}

    prefer_no_taxable_sale = policy.get("use_contributions_before_sales", True)
    if prefer_no_taxable_sale is None:
        prefer_no_taxable_sale = True

    # Remaining dollar deviation after crediting the contribution's own
    # effect -- an underweight class shrinks (or closes) by whatever the
    # contribution just funded; overweight classes are untouched by a
    # contribution (new money never corrects an overweight class).
    remaining_deviation = {}
    for asset_class, d in comparison["by_class"].items():
        dollars = d["deviation_dollars"]
        if dollars < 0:  # underweight
            dollars = min(0.0, dollars + contribution_by_class.get(asset_class, 0.0))
        remaining_deviation[asset_class] = dollars

    overweight = sorted(
        [(c, v) for c, v in remaining_deviation.items() if v > 0.01 and c != "unclassified"],
        key=lambda pair: -pair[1],
    )
    underweight = sorted(
        [(c, -v) for c, v in remaining_deviation.items() if v < -0.01 and c != "unclassified"],
        key=lambda pair: -pair[1],
    )

    rebalance_actions = []
    # Pair each overweight class with underweight classes to fund, an
    # "exchange" (sell overweight, buy underweight) inside the SAME
    # account whenever the account is tax-advantaged (no tax consequence
    # either way), preferring tax-advantaged accounts' own overweight
    # holdings before ever touching a brokerage (taxable) holding.
    holdings_by_class: Dict[str, List[Dict]] = {c: [] for c in ASSET_CLASSES}
    for h in classified_household_holdings:
        asset_class = h.get("asset_class") or "unclassified"
        if asset_class in holdings_by_class:
            holdings_by_class[asset_class].append(h)

    # Selling only makes sense to FUND an underweight class -- an
    # overweight class with nowhere for the proceeds to go (every
    # underweight gap already closed by the contribution above) isn't
    # sold down here at all. Capped at the total remaining underweight
    # need ACROSS every class combined, not each overweight class's own
    # full excess -- otherwise a household whose contribution already
    # closed the only underweight gap would still see a pointless sell
    # recommended for an overweight class with no destination.
    remaining_underweight_need = sum(gap for _, gap in underweight)

    for asset_class, excess_dollars in overweight:
        if remaining_underweight_need <= 0.01:
            break
        target_sell = min(excess_dollars, remaining_underweight_need)
        candidates = holdings_by_class.get(asset_class, [])
        # Tax-advantaged holdings (anything not brokerage) first, largest
        # position first within each tier -- minimizes both tax impact
        # and the number of separate trades recommended.
        candidates = sorted(
            candidates,
            key=lambda h: (h.get("_portfolio_account_type") in TAXABLE_GAIN_TYPES, -(h.get("market_value", 0) or 0)),
        )
        to_sell = target_sell
        for h in candidates:
            if to_sell <= 0.01:
                break
            is_taxable = h.get("_portfolio_account_type") in TAXABLE_GAIN_TYPES
            if is_taxable and prefer_no_taxable_sale:
                # Only touch a taxable holding if every tax-advantaged
                # overweight holding in this class has already been used
                # up and drift remains -- checked by simply letting the
                # sort above exhaust non-taxable candidates first; if we
                # get here for a taxable holding, it's because there was
                # no other option, so proceed (not skip).
                pass
            amount = min(to_sell, h.get("market_value", 0) or 0)
            if amount <= 0:
                continue
            action = {
                "account_id": h.get("account_id"),
                "holding_id": h.get("id"),
                "holding_name": h.get("name"),
                "action": "sell",
                "asset_class": asset_class,
                "amount": round(amount, 2),
                "pct_of_holding": round(amount / (h.get("market_value") or 1) * 100, 1),
                "reason": (
                    f"{asset_class.replace('_', ' ').title()} is overweight by ${excess_dollars:,.0f} against target; "
                    f"selling to fund the household's remaining underweight class(es)."
                ),
                "is_taxable_sale": is_taxable,
                "tax_warning": _account_taxable_gain_warning(h, amount) if is_taxable else None,
                "confidence_note": (
                    "Tax-advantaged account -- no tax consequence from this sale." if not is_taxable else
                    "Taxable brokerage account -- see tax_warning for the estimated gain."
                ),
            }
            rebalance_actions.append(action)
            to_sell -= amount
            remaining_underweight_need -= amount

    # The dollars freed by the sells above (or, if no sells were needed
    # because the contribution alone closed every gap, nothing) get
    # recommended as buys into the still-underweight classes, largest gap
    # first -- same contribution-first destination logic, just funded by
    # sale proceeds instead of new cash.
    proceeds = sum(a["amount"] for a in rebalance_actions if a["action"] == "sell")
    for asset_class, gap in underweight:
        if proceeds <= 0.01:
            break
        amount = min(proceeds, gap)
        if amount <= 0:
            continue
        rebalance_actions.append({
            "account_id": None, "holding_id": None, "holding_name": None,
            "action": "buy",
            "asset_class": asset_class,
            "amount": round(amount, 2),
            "pct_of_holding": None,
            "reason": f"Underweight by ${gap:,.0f} against target -- funded by proceeds from the overweight sale(s) above.",
            "is_taxable_sale": False,
            "tax_warning": None,
            "confidence_note": "Destination account left to the household's own preference among its tax-advantaged accounts; not account-specific.",
        })
        proceeds -= amount

    # Projected allocation after every action: apply each sell/buy dollar
    # delta to the current totals, expressed as new pct_by_class.
    projected_by_class = dict(current_allocation["by_class"])
    for a in contribution_actions:
        if a["asset_class"]:
            projected_by_class[a["asset_class"]] = projected_by_class.get(a["asset_class"], 0) + a["amount"]
    for a in rebalance_actions:
        delta = a["amount"] if a["action"] == "buy" else -a["amount"]
        projected_by_class[a["asset_class"]] = projected_by_class.get(a["asset_class"], 0) + delta
    new_total = sum(projected_by_class.values())
    projected_pct = {c: (round(v / new_total * 100, 2) if new_total > 0 else 0.0) for c, v in projected_by_class.items()}

    return {
        "contribution_actions": contribution_actions,
        "rebalance_actions": rebalance_actions,
        "projected_allocation": {
            "total": round(new_total, 2),
            "by_class": {c: round(v, 2) for c, v in projected_by_class.items()},
            "pct_by_class": projected_pct,
        },
    }


# ── Milestone 3: portfolio health flags ─────────────────────────────────
# Same threshold precedent as allocation_engine.py's own concentration_
# risk/analyze_fees (CONCENTRATION_THRESHOLD_PCT/LOW_COST_INDEX_EXPENSE_
# RATIO), reused here at the per-HOLDING level instead of per-account,
# now that real holdings data makes that possible.

CONCENTRATION_THRESHOLD_PCT = 10
CONCENTRATION_SEVERE_PCT = 25
LOW_COST_INDEX_EXPENSE_RATIO = 0.0003   # ~0.03%, typical broad-market index fund
HIGH_EXPENSE_RATIO_THRESHOLD = 0.01     # 1.00% -- the level advisors typically flag as high


def concentration_flags(household_holdings: List[Dict], threshold_pct: float = CONCENTRATION_THRESHOLD_PCT) -> List[Dict]:
    """Flags any single HOLDING (not account, now that real per-holding
    data exists) that's an outsized share of household investable assets
    -- a real single-security concentration check, not allocation_engine.
    py's account-level proxy."""
    total = sum(h.get("market_value", 0) or 0 for h in household_holdings)
    if total <= 0:
        return []
    flagged = []
    for h in household_holdings:
        value = h.get("market_value", 0) or 0
        pct = value / total * 100
        if pct >= threshold_pct:
            flagged.append({
                "holding_id": h.get("id"), "account_id": h.get("account_id"),
                "name": h.get("name"), "market_value": round(value, 2),
                "pct_of_portfolio": round(pct, 1),
                "severity": "severe" if pct >= CONCENTRATION_SEVERE_PCT else "moderate",
            })
    flagged.sort(key=lambda f: -f["pct_of_portfolio"])
    return flagged


def expense_ratio_flags(household_holdings: List[Dict], threshold: float = HIGH_EXPENSE_RATIO_THRESHOLD) -> List[Dict]:
    """Flags holdings with an expense ratio at/above threshold, with an
    estimated annual dollar cost -- a holding with no expense_ratio on
    file is skipped (not assumed to be free), matching "do not invent
    missing holdings data.\""""
    flagged = []
    for h in household_holdings:
        er = h.get("expense_ratio")
        if er is None or er < threshold:
            continue
        value = h.get("market_value", 0) or 0
        flagged.append({
            "holding_id": h.get("id"), "account_id": h.get("account_id"),
            "name": h.get("name"), "expense_ratio_pct": round(er * 100, 3),
            "annual_fee_dollars": round(value * er, 2),
        })
    flagged.sort(key=lambda f: -f["annual_fee_dollars"])
    return flagged


def duplicate_exposure_flags(household_holdings: List[Dict]) -> List[Dict]:
    """Flags the same holding name held across more than one account --
    not necessarily wrong (a 401k and an IRA both holding a total-market
    index fund is normal), but worth surfacing since it can mean
    redundant exposure a household didn't realize it had, or an
    opportunity to consolidate for simplicity. Name comparison is
    case-insensitive/whitespace-trimmed so "VTI" and "vti " match."""
    by_name: Dict[str, List[Dict]] = {}
    for h in household_holdings:
        name = (h.get("name") or "").strip().lower()
        if not name:
            continue
        by_name.setdefault(name, []).append(h)
    flagged = []
    for name, group in by_name.items():
        account_ids = {h.get("account_id") for h in group}
        if len(account_ids) > 1:
            flagged.append({
                "name": group[0].get("name"),
                "total_market_value": round(sum(h.get("market_value", 0) or 0 for h in group), 2),
                "accounts": sorted(account_ids),
                "holding_ids": [h.get("id") for h in group],
            })
    flagged.sort(key=lambda f: -f["total_market_value"])
    return flagged


def unclassified_flags(household_holdings: List[Dict]) -> List[Dict]:
    """Flags holdings with asset_class == "unclassified" (or any value
    outside ASSET_CLASSES) -- these count toward compute_current_
    allocation's "unclassified" bucket already, but need their own
    explicit list so a user can see exactly WHICH holdings to go classify,
    not just that some dollar amount is sitting unclassified."""
    flagged = []
    for h in household_holdings:
        asset_class = h.get("asset_class") or "unclassified"
        if asset_class not in ASSET_CLASSES or asset_class == "unclassified":
            flagged.append({
                "holding_id": h.get("id"), "account_id": h.get("account_id"),
                "name": h.get("name"), "market_value": round(h.get("market_value", 0) or 0, 2),
            })
    flagged.sort(key=lambda f: -f["market_value"])
    return flagged


# ── Milestone 1: CSV import (preview/validate, no writes) ──────────────

REQUIRED_CSV_COLUMNS = {"account_id", "name", "market_value", "asset_class"}


def _parse_optional_float(raw_row: Dict, key: str, row_errors: List[str]) -> Optional[float]:
    value = (raw_row.get(key) or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        row_errors.append(f"{key} must be a number if provided")
        return None


def parse_holdings_csv(csv_text: str, valid_account_ids: Set[int]) -> Dict:
    """Parses a holdings CSV into validated rows WITHOUT writing anything
    -- the preview/validate step the brief requires before any holding is
    actually saved. Every row failing validation carries its own specific
    reason(s) and is marked invalid rather than silently dropped or
    coerced into something plausible-looking (a blank asset_class is
    never guessed as "us_stock", a missing account_id is never assumed
    to mean "the first account," etc). Required columns:
    account_id, name, market_value, asset_class. Optional: description,
    shares, expense_ratio, cost_basis, notes."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        return {"rows": [], "errors": [{"row": 0, "message": "Empty file or no header row."}],
                "valid_count": 0, "invalid_count": 0}
    missing_cols = REQUIRED_CSV_COLUMNS - {c.strip() for c in reader.fieldnames if c}
    if missing_cols:
        return {"rows": [], "errors": [{"row": 0, "message": f"Missing required column(s): {', '.join(sorted(missing_cols))}"}],
                "valid_count": 0, "invalid_count": 0}

    rows = []
    errors = []
    for i, raw in enumerate(reader, start=2):  # row 1 is the header
        row_errors: List[str] = []

        account_id_raw = (raw.get("account_id") or "").strip()
        account_id = None
        try:
            account_id = int(account_id_raw)
            if account_id not in valid_account_ids:
                row_errors.append(f"account_id {account_id} does not match any existing account")
        except ValueError:
            row_errors.append("account_id must be a whole number")

        name = (raw.get("name") or "").strip()
        if not name:
            row_errors.append("name is required")

        market_value_raw = (raw.get("market_value") or "").strip()
        market_value = None
        try:
            market_value = float(market_value_raw)
            if market_value < 0:
                row_errors.append("market_value cannot be negative")
        except ValueError:
            row_errors.append("market_value must be a number")

        asset_class = (raw.get("asset_class") or "").strip()
        if asset_class not in ASSET_CLASSES:
            row_errors.append(f"asset_class must be one of: {', '.join(ASSET_CLASSES)}")

        shares = _parse_optional_float(raw, "shares", row_errors)
        expense_ratio = _parse_optional_float(raw, "expense_ratio", row_errors)
        cost_basis = _parse_optional_float(raw, "cost_basis", row_errors)

        parsed = {
            "row": i,
            "account_id": account_id,
            "name": name or None,
            "description": (raw.get("description") or "").strip() or None,
            "shares": shares,
            "market_value": market_value,
            "asset_class": asset_class or None,
            "expense_ratio": expense_ratio,
            "cost_basis": cost_basis,
            "notes": (raw.get("notes") or "").strip() or None,
            "valid": not row_errors,
            "errors": row_errors,
        }
        rows.append(parsed)
        if row_errors:
            errors.append({"row": i, "message": "; ".join(row_errors)})

    return {
        "rows": rows,
        "errors": errors,
        "valid_count": sum(1 for r in rows if r["valid"]),
        "invalid_count": sum(1 for r in rows if not r["valid"]),
    }
