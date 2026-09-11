"""
Portfolio holdings and allocation — pure calculation module
(codex/portfolio-coach-recommendations).

Decision support only. Nothing here places a trade, connects to a
brokerage, or presents a recommendation as guaranteed or fiduciary
advice.

Deliberately independent of the existing allocation_engine.py
(account-level stock_allocation_pct guess, still used by the Net Worth
page's own allocation/fee/concentration cards — untouched by this
branch). When real holdings exist for an account, THIS module's
per-holding asset_class data is authoritative for that account;
allocation_engine.py's account-level guess is never consulted here.
"""
from typing import Dict, List, Optional, Set
import csv
import io

# ── Account-type model ──────────────────────────────────────────────────
# Deliberately separate from net_worth_engine.VALID_ACCOUNT_TYPES / the
# existing `accounts.account_type` column — see db.py's
# init_portfolio_coach_tables() docstring for the full rationale.
PORTFOLIO_ACCOUNT_TYPES = frozenset({
    "brokerage", "traditional_401k", "roth_401k", "traditional_ira",
    "roth_ira", "hsa", "529", "custodial", "checking", "savings",
    "trust", "other",
})

# Legacy accounts.account_type -> this module's own enum. Lossy in one
# direction: a single legacy "401k" account_type covers BOTH traditional
# and Roth 401k dollars, split only by a HOUSEHOLD-WIDE percentage
# (planning_inputs.pretax_401k_pct) that projection_engine.py/
# simulation_engine.py already use for retirement-withdrawal math —
# there is no per-account distinction to derive from. Defaults every
# plain "401k" row to "traditional_401k" (the majority case), NOT a
# guess masquerading as certainty — a household with a genuine separate
# Roth 401k account sets portfolio_account_type="roth_401k" explicitly
# via the override column.
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
    """account.portfolio_account_type override if explicitly set, else
    derived from the legacy account_type, else "other" (real_estate/
    business/insurance/daf/debt/an unrecognized legacy type, or no
    account_type at all — every one of these BLOCKS recommendations for
    that account per the brief, exactly like a missing type would)."""
    override = account.get("portfolio_account_type")
    if override:
        return override
    return _LEGACY_TO_PORTFOLIO_TYPE.get(account.get("account_type"), "other")


TAXABLE_GAIN_TYPES    = {"brokerage"}
PRETAX_RMD_TYPES      = {"traditional_401k", "traditional_ira"}
ROTH_TYPES            = {"roth_401k", "roth_ira"}
HSA_TYPES             = {"hsa"}
CHILD_SPECIFIC_TYPES  = {"529", "custodial"}
LIQUIDITY_TYPES       = {"checking", "savings"}
REVIEW_REQUIRED_TYPES = {"trust"}
BLOCKED_TYPES         = {"other"}

# Household allocation includes every type EXCEPT the three carve-outs
# the brief calls out explicitly (HSA reported separately, 529/
# custodial excluded as child-specific, checking/savings excluded as a
# liquidity reserve). "other"/unresolvable is excluded via
# is_allocation_blocked below, not this set, since trust IS eligible
# (flagged for review, not excluded).
HOUSEHOLD_ALLOCATION_TYPES = PORTFOLIO_ACCOUNT_TYPES - HSA_TYPES - CHILD_SPECIFIC_TYPES - LIQUIDITY_TYPES - BLOCKED_TYPES


def is_allocation_blocked(portfolio_type: str) -> bool:
    """"other" or an unresolvable type blocks recommendations for that
    account until it's classified — per the brief verbatim."""
    return portfolio_type not in PORTFOLIO_ACCOUNT_TYPES or portfolio_type in BLOCKED_TYPES


# 8 asset classes — the brief's own list, US stock split into large-cap
# and mid/small-cap (unlike a simpler prior internal draft that only
# had one "us_stock" bucket).
ASSET_CLASSES = (
    "us_large_cap", "us_mid_small_cap", "international_stock", "bonds",
    "cash", "real_estate", "alternatives", "unclassified",
)


# ── Reconciliation ───────────────────────────────────────────────────────

def reconcile_account_holdings(account: Dict, account_holdings: List[Dict]) -> Dict:
    """Compares an account's own balance against the sum of its
    holdings' market_value. Never invents missing holdings data — a
    mismatch is an explicit unclassified remainder (positive: holdings
    under-total the account; negative: over-total, e.g. stale data
    after a withdrawal) plus a warning."""
    account_balance = account.get("balance", 0) or 0
    holdings_total = sum(h.get("market_value", 0) or 0 for h in account_holdings)
    remainder = round(account_balance - holdings_total, 2)
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


# ── Classification ───────────────────────────────────────────────────────

def classify_holdings(accounts: List[Dict], holdings: List[Dict]) -> Dict:
    """Groups every holding by its parent account's resolved portfolio
    account type into the behavior buckets the brief defines. Returns
    holdings augmented with `_portfolio_account_type`/`_account` (new
    dicts, originals untouched). Holdings inside 529/custodial accounts
    retain their child owner (via `_account["owner"]`) and never land in
    `household`."""
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
        "household": household, "hsa": hsa, "child_specific": child_specific,
        "liquidity": liquidity, "blocked": blocked, "review_required": review_required,
    }


# ── Current allocation ───────────────────────────────────────────────────

def compute_current_allocation(classified_holdings: List[Dict]) -> Dict:
    """Current dollar/percentage allocation by asset class. An
    unclassified/unrecognized asset_class is counted in its own
    "unclassified" bucket, never dropped or guessed into a real class."""
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
    return {"total": round(total, 2), "by_class": {c: round(v, 2) for c, v in by_class.items()}, "pct_by_class": pct_by_class}


# ── Policy comparison ─────────────────────────────────────────────────────

POLICY_TARGET_FIELD_TO_ASSET_CLASS = {
    "target_us_large_cap_pct":        "us_large_cap",
    "target_us_mid_small_cap_pct":    "us_mid_small_cap",
    "target_international_stock_pct": "international_stock",
    "target_bonds_pct":               "bonds",
    "target_cash_pct":                "cash",
    "target_real_estate_pct":         "real_estate",
    "target_alternatives_pct":        "alternatives",
}


def policy_targets_by_class(policy: Dict) -> Dict[str, float]:
    targets = {c: 0.0 for c in ASSET_CLASSES}
    for field, asset_class in POLICY_TARGET_FIELD_TO_ASSET_CLASS.items():
        targets[asset_class] = policy.get(field, 0) or 0
    return targets


def compare_to_target(current_allocation: Dict, policy: Dict) -> Dict:
    """Current vs. target, drift-band-aware. deviation_pct is signed:
    positive = overweight, negative = underweight."""
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
            "current_pct": current_pct, "target_pct": round(target_pct, 2),
            "deviation_pct": deviation_pct, "deviation_dollars": deviation_dollars,
            "within_drift_band": abs(deviation_pct) <= drift_band,
        }
    return {"drift_band_pct": drift_band, "by_class": deviations,
            "any_outside_band": any(not d["within_drift_band"] for d in deviations.values())}


# ── Contribution-first destination ───────────────────────────────────────

def recommend_contribution_destination(comparison: Dict, contribution_amount: float) -> List[Dict]:
    """New money goes exclusively to the most-underweight classes first,
    up to each one's own dollar gap — never an already-at-or-above-
    target class."""
    if contribution_amount <= 0:
        return []
    underweight = [
        (asset_class, -d["deviation_dollars"])
        for asset_class, d in comparison["by_class"].items()
        if d["deviation_dollars"] < 0 and asset_class != "unclassified"
    ]
    underweight.sort(key=lambda pair: pair[1], reverse=True)
    remaining = contribution_amount
    actions = []
    for asset_class, gap in underweight:
        if remaining <= 0:
            break
        amount = min(remaining, gap)
        if amount <= 0:
            continue
        actions.append({
            "asset_class": asset_class, "amount": round(amount, 2),
            "reason": f"Underweight by ${gap:,.0f} against target -- directing new contributions here closes the gap without selling anything.",
        })
        remaining -= amount
    if remaining > 0.01 and actions:
        actions.append({
            "asset_class": None, "amount": round(remaining, 2),
            "reason": "Every underweight asset class is now fully funded to target -- the remainder has no drift to correct.",
        })
    elif not actions:
        actions.append({
            "asset_class": None, "amount": round(contribution_amount, 2),
            "reason": "No asset class is currently underweight -- this contribution doesn't need to be targeted for rebalancing purposes.",
        })
    return actions


# ── Rebalance actions ─────────────────────────────────────────────────────

def _account_taxable_gain_warning(holding: Dict, sell_amount: float) -> Optional[Dict]:
    """A taxable sale's tax impact can only be ESTIMATED when cost_basis
    is known -- missing cost basis is flagged explicitly, never assumed
    to be zero or equal to market value."""
    cost_basis = holding.get("cost_basis")
    market_value = holding.get("market_value", 0) or 0
    if cost_basis is None:
        return {
            "has_cost_basis": False,
            "message": "Cost basis is not on file for this holding -- the taxable gain/loss on this sale cannot be estimated. Set cost basis before relying on any tax figure here.",
        }
    gain_fraction = 0.0 if market_value <= 0 else max(0.0, (market_value - cost_basis) / market_value)
    estimated_gain = round(sell_amount * gain_fraction, 2)
    return {
        "has_cost_basis": True, "estimated_gain": estimated_gain,
        "message": (
            f"Estimated taxable gain on this sale: ${estimated_gain:,.0f} (based on this holding's overall "
            f"unrealized-gain fraction, not the tax lot actually sold)." if estimated_gain > 0 else
            "This holding is at or below cost basis -- no taxable gain expected on this sale."
        ),
    }


def recommend_rebalance_actions(
    classified_household_holdings: List[Dict],
    current_allocation: Dict,
    comparison: Dict,
    policy: Dict,
    pending_contribution: float = 0.0,
) -> Dict:
    """Contribution-first, then exchange inside tax-advantaged accounts,
    then taxable sales only when the tax-advantaged supply in an
    overweight class is exhausted and drift remains. Only sells enough
    to fund the household's REMAINING underweight need (after crediting
    the contribution's own effect) -- never an overweight class's own
    full excess in isolation, which would recommend a sale with no
    destination once a contribution already closed every gap."""
    contribution_actions = recommend_contribution_destination(comparison, pending_contribution)
    contribution_by_class = {a["asset_class"]: a["amount"] for a in contribution_actions if a["asset_class"]}

    prefer_no_taxable_sale = policy.get("use_contributions_before_sales", True)
    if prefer_no_taxable_sale is None:
        prefer_no_taxable_sale = True

    remaining_deviation = {}
    for asset_class, d in comparison["by_class"].items():
        dollars = d["deviation_dollars"]
        if dollars < 0:
            dollars = min(0.0, dollars + contribution_by_class.get(asset_class, 0.0))
        remaining_deviation[asset_class] = dollars

    overweight = sorted(
        [(c, v) for c, v in remaining_deviation.items() if v > 0.01 and c != "unclassified"], key=lambda p: -p[1])
    underweight = sorted(
        [(c, -v) for c, v in remaining_deviation.items() if v < -0.01 and c != "unclassified"], key=lambda p: -p[1])

    rebalance_actions = []
    holdings_by_class: Dict[str, List[Dict]] = {c: [] for c in ASSET_CLASSES}
    for h in classified_household_holdings:
        asset_class = h.get("asset_class") or "unclassified"
        if asset_class in holdings_by_class:
            holdings_by_class[asset_class].append(h)

    remaining_underweight_need = sum(gap for _, gap in underweight)

    for asset_class, excess_dollars in overweight:
        if remaining_underweight_need <= 0.01:
            break
        target_sell = min(excess_dollars, remaining_underweight_need)
        candidates = sorted(
            holdings_by_class.get(asset_class, []),
            key=lambda h: (h.get("_portfolio_account_type") in TAXABLE_GAIN_TYPES, -(h.get("market_value", 0) or 0)),
        )
        to_sell = target_sell
        for h in candidates:
            if to_sell <= 0.01:
                break
            is_taxable = h.get("_portfolio_account_type") in TAXABLE_GAIN_TYPES
            amount = min(to_sell, h.get("market_value", 0) or 0)
            if amount <= 0:
                continue
            rebalance_actions.append({
                "account_id": h.get("account_id"), "holding_id": h.get("id"), "holding_name": h.get("security_name"),
                "action": "sell", "asset_class": asset_class, "amount": round(amount, 2),
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
            })
            to_sell -= amount
            remaining_underweight_need -= amount

    proceeds = sum(a["amount"] for a in rebalance_actions if a["action"] == "sell")
    for asset_class, gap in underweight:
        if proceeds <= 0.01:
            break
        amount = min(proceeds, gap)
        if amount <= 0:
            continue
        rebalance_actions.append({
            "account_id": None, "holding_id": None, "holding_name": None,
            "action": "buy", "asset_class": asset_class, "amount": round(amount, 2), "pct_of_holding": None,
            "reason": f"Underweight by ${gap:,.0f} against target -- funded by proceeds from the overweight sale(s) above.",
            "is_taxable_sale": False, "tax_warning": None,
            "confidence_note": "Destination account left to the household's own preference among its tax-advantaged accounts.",
        })
        proceeds -= amount

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


# ── Fund-quality flags ────────────────────────────────────────────────────

CONCENTRATION_THRESHOLD_PCT = 10
CONCENTRATION_SEVERE_PCT = 25
HIGH_EXPENSE_RATIO_THRESHOLD = 0.01


def concentration_flags(household_holdings: List[Dict], threshold_pct: float = CONCENTRATION_THRESHOLD_PCT) -> List[Dict]:
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
                "name": h.get("security_name"), "market_value": round(value, 2), "pct_of_portfolio": round(pct, 1),
                "severity": "severe" if pct >= CONCENTRATION_SEVERE_PCT else "moderate",
            })
    flagged.sort(key=lambda f: -f["pct_of_portfolio"])
    return flagged


def expense_ratio_flags(household_holdings: List[Dict], threshold: float = HIGH_EXPENSE_RATIO_THRESHOLD) -> List[Dict]:
    flagged = []
    for h in household_holdings:
        er = h.get("expense_ratio")
        if er is None or er < threshold:
            continue
        value = h.get("market_value", 0) or 0
        flagged.append({
            "holding_id": h.get("id"), "account_id": h.get("account_id"), "name": h.get("security_name"),
            "expense_ratio_pct": round(er * 100, 3), "annual_fee_dollars": round(value * er, 2),
        })
    flagged.sort(key=lambda f: -f["annual_fee_dollars"])
    return flagged


def duplicate_exposure_flags(household_holdings: List[Dict]) -> List[Dict]:
    by_name: Dict[str, List[Dict]] = {}
    for h in household_holdings:
        name = (h.get("security_name") or h.get("ticker") or "").strip().lower()
        if not name:
            continue
        by_name.setdefault(name, []).append(h)
    flagged = []
    for name, group in by_name.items():
        account_ids = {h.get("account_id") for h in group}
        if len(account_ids) > 1:
            flagged.append({
                "name": group[0].get("security_name") or group[0].get("ticker"),
                "total_market_value": round(sum(h.get("market_value", 0) or 0 for h in group), 2),
                "accounts": sorted(account_ids), "holding_ids": [h.get("id") for h in group],
            })
    flagged.sort(key=lambda f: -f["total_market_value"])
    return flagged


def unclassified_flags(household_holdings: List[Dict]) -> List[Dict]:
    flagged = []
    for h in household_holdings:
        asset_class = h.get("asset_class") or "unclassified"
        if asset_class not in ASSET_CLASSES or asset_class == "unclassified":
            flagged.append({
                "holding_id": h.get("id"), "account_id": h.get("account_id"),
                "name": h.get("security_name"), "market_value": round(h.get("market_value", 0) or 0, 2),
            })
    flagged.sort(key=lambda f: -f["market_value"])
    return flagged


# ── CSV import (preview/validate, no writes) ──────────────────────────────

REQUIRED_CSV_COLUMNS = {"account_id", "security_name", "market_value", "asset_class"}


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
    """Parses a holdings CSV into validated rows WITHOUT writing
    anything. Required: account_id, security_name, market_value,
    asset_class. Optional: ticker, description/notes, shares,
    expense_ratio, cost_basis."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        return {"rows": [], "errors": [{"row": 0, "message": "Empty file or no header row."}], "valid_count": 0, "invalid_count": 0}
    missing_cols = REQUIRED_CSV_COLUMNS - {c.strip() for c in reader.fieldnames if c}
    if missing_cols:
        return {"rows": [], "errors": [{"row": 0, "message": f"Missing required column(s): {', '.join(sorted(missing_cols))}"}],
                "valid_count": 0, "invalid_count": 0}

    rows, errors = [], []
    for i, raw in enumerate(reader, start=2):
        row_errors: List[str] = []
        account_id_raw = (raw.get("account_id") or "").strip()
        account_id = None
        try:
            account_id = int(account_id_raw)
            if account_id not in valid_account_ids:
                row_errors.append(f"account_id {account_id} does not match any existing account")
        except ValueError:
            row_errors.append("account_id must be a whole number")

        security_name = (raw.get("security_name") or "").strip()
        if not security_name:
            row_errors.append("security_name is required")

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

        rows.append({
            "row": i, "account_id": account_id, "ticker": (raw.get("ticker") or "").strip() or None,
            "security_name": security_name or None, "shares": shares, "market_value": market_value,
            "asset_class": asset_class or None, "expense_ratio": expense_ratio, "cost_basis": cost_basis,
            "notes": (raw.get("notes") or "").strip() or None,
            "valid": not row_errors, "errors": row_errors,
        })
        if row_errors:
            errors.append({"row": i, "message": "; ".join(row_errors)})

    return {"rows": rows, "errors": errors, "valid_count": sum(1 for r in rows if r["valid"]),
            "invalid_count": sum(1 for r in rows if not r["valid"])}


# ── Planning integration (Milestone 7) ────────────────────────────────────

ASSET_CLASS_EXPECTED_RETURNS = {
    "us_large_cap": 0.09, "us_mid_small_cap": 0.10, "international_stock": 0.08,
    "bonds": 0.045, "cash": 0.02, "real_estate": 0.07, "alternatives": 0.06, "unclassified": None,
}


def blended_expected_return(pct_by_class: Dict[str, float]) -> Optional[float]:
    """Weighted-average expected nominal return -- documented,
    conservative planning assumption, NOT a guarantee. Excludes
    "unclassified" from both the weighted sum and denominator, so a
    partially-unclassified allocation isn't dragged toward 0%."""
    known = [(c, pct) for c, pct in pct_by_class.items()
             if c != "unclassified" and ASSET_CLASS_EXPECTED_RETURNS.get(c) is not None and pct]
    total_known_weight = sum(pct for _, pct in known)
    if total_known_weight <= 0:
        return None
    weighted = sum(pct * ASSET_CLASS_EXPECTED_RETURNS[c] for c, pct in known)
    return round(weighted / total_known_weight, 4)


def blended_expense_ratio(household_holdings: List[Dict]) -> Optional[Dict]:
    """Real, holdings-weighted current expense ratio -- no proposed-side
    equivalent exists from asset-class targets alone."""
    total_value = sum(h.get("market_value", 0) or 0 for h in household_holdings)
    rated = [h for h in household_holdings if h.get("expense_ratio") is not None]
    if not rated or total_value <= 0:
        return None
    weighted_ratio = sum((h.get("market_value", 0) or 0) * h["expense_ratio"] for h in rated) / total_value
    annual_fee = sum((h.get("market_value", 0) or 0) * h["expense_ratio"] for h in rated)
    return {"blended_expense_ratio_pct": round(weighted_ratio * 100, 3), "annual_fee_dollars": round(annual_fee, 2),
            "holdings_with_expense_ratio": len(rated), "holdings_total": len(household_holdings)}
