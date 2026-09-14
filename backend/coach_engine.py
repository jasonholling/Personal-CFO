"""
Portfolio Coach recommendation engine (codex/portfolio-coach-
recommendations). Pure, deterministic — no I/O, no randomness, no
network calls. Produces prioritized action cards from already-computed
holdings/allocation/planning data; main.py owns all database reads/
writes and calls into projection_engine.py/simulation_engine.py
directly for any retirement-planning number (this module never
re-derives a calculation those engines already own).

Decision support only. Nothing here places a trade, connects to a
brokerage, or presents a recommendation as guaranteed or fiduciary
advice — see every card's own `assumptions`/`confidence`/
`conditions_that_would_invalidate` fields.
"""
import hashlib
import json
from datetime import date
from typing import Dict, List, Optional, Set

from holdings_engine import (
    TAXABLE_GAIN_TYPES, concentration_flags, expense_ratio_flags,
    duplicate_exposure_flags, ASSET_CLASSES,
    estimate_taxable_gain_warning,
    evaluate_tax_lots, is_lot_loss_candidate, detect_wash_sale_conflicts,
    DEFAULT_TAX_LOT_LOSS_REVIEW_THRESHOLD_PCT,
    PRETAX_RMD_TYPES, ROTH_TYPES, HSA_TYPES, resolve_portfolio_account_type,
)

# ── Decision lifecycle ────────────────────────────────────────────────────
STATUSES = ("proposed", "reviewing", "accepted", "deferred", "rejected", "completed", "invalidated")

# The controlling product clarification's explicit 8-tier priority order
# (verbatim, most urgent first):
#   1) Missing/unreliable data preventing valid analysis.
#   2) Dangerous concentration or liquidity problem.
#   3) Material investment-policy violation.
#   4) High-cost or materially redundant holding.
#   5) New-money direction.
#   6) Tax-advantaged rebalance.
#   7) Taxable rebalance.
#   8) Minor optimization.
# Supersedes an earlier internal 5-category draft (data_quality/
# allocation_drift/rebalance/fund_quality/goal_aware) that didn't
# distinguish urgent concentration from routine fund-quality review, or
# new-money direction from an in-place rebalance, or a tax-advantaged
# exchange from a taxable sale.
CATEGORIES = (
    "missing_data", "concentration_or_liquidity_risk", "policy_violation",
    "high_cost_or_redundant", "new_money", "tax_advantaged_rebalance",
    "taxable_rebalance", "minor_optimization",
)
CATEGORY_BASE_PRIORITY = {name: i + 1 for i, name in enumerate(CATEGORIES)}

# The brief's separate classification axis ("The engine must distinguish:
# data-quality problem / policy violation / permitted exception /
# optimization opportunity / urgent risk / ordinary review item") is NOT
# the same thing as priority order -- two cards can share a
# classification while sitting at different priority tiers. Every card's
# `classification` field is derived from its category below.
CATEGORY_CLASSIFICATION = {
    "missing_data": "data_quality_problem",
    "concentration_or_liquidity_risk": "urgent_risk",
    "policy_violation": "policy_violation",
    "high_cost_or_redundant": "optimization_opportunity",
    "new_money": "optimization_opportunity",
    "tax_advantaged_rebalance": "optimization_opportunity",
    "taxable_rebalance": "optimization_opportunity",
    "minor_optimization": "ordinary_review_item",
}


# Household-review-workflow integration (item 3): categories whose
# recommendations are urgent/foundational enough that accepting OR
# deferring one should be able to create/link a household Task, so it
# surfaces in the same Action Tracker every other financial task uses
# -- not just in the Coach's own review-summary widget. Tiers 1-3
# (missing_data / concentration_or_liquidity_risk / policy_violation),
# per section 77's own priority ordering.
HIGH_PRIORITY_TASK_CATEGORIES = {"missing_data", "concentration_or_liquidity_risk", "policy_violation"}


def stable_hash(obj) -> str:
    """Deterministic hash of whatever inputs produced a recommendation
    card — used as `assumptions_hash` so the sync logic below can tell
    "nothing changed" from "the underlying facts moved" without
    re-running the whole recommendation generation just to compare."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def recommendation_key(category: str, account_id=None, holding_id=None, asset_class=None, extra: str = "") -> str:
    """Stable identity, independent of any one generation run — a
    rejected/deferred recommendation is matched against a freshly
    regenerated candidate by this key, not by row id."""
    return ":".join([category, str(account_id), str(holding_id), str(asset_class), extra])


# External review finding #11 (2026-09-12, commit 4812d84): the
# frontend used to guess a value's display unit from its magnitude
# (<=100 -> percent), which misrenders a $50 cash balance as "50%".
# Every card now carries its own explicit value_unit for current_value/
# target_value so the frontend never has to guess.
VALUE_UNITS = ("currency", "percent", "age", "count", "text")


def _card(category, key, priority_offset, title, action_text, accounts, holdings,
          current_value, target_value, proposed_change, expected_effect, tax_impact,
          assumptions, confidence, review_date=None, invalidates_on=None, assumptions_hash_input=None,
          value_unit="currency"):
    """Builds one recommendation card with every field the brief
    requires: id (assigned by the caller/DB), category, priority, title,
    plain-English action, affected accounts/holdings, current/target
    value, proposed change, expected effect, tax impact, assumptions,
    data timestamp (via assumptions_hash's own inputs, which include
    whatever "as of" data went into the card), confidence, review date,
    and invalidation conditions. `value_unit` (default "currency")
    tells the caller how to DISPLAY current_value/target_value --
    "currency" / "percent" / "age" / "count" / "text" -- never inferred
    from the number's magnitude."""
    if value_unit not in VALUE_UNITS:
        raise ValueError(f"Unknown value_unit '{value_unit}' -- must be one of {VALUE_UNITS}")
    return {
        "recommendation_key": key,
        "category": category,
        "classification": CATEGORY_CLASSIFICATION[category],
        "priority": CATEGORY_BASE_PRIORITY[category] * 1000 + priority_offset,
        "title": title,
        "action_text": action_text,
        "affected_accounts": accounts,
        "affected_holdings": holdings,
        "current_value": current_value,
        "target_value": target_value,
        "value_unit": value_unit,
        "proposed_change": proposed_change,
        "expected_effect": expected_effect,
        "tax_impact": tax_impact,
        "assumptions": assumptions,
        "confidence": confidence,
        "review_date": review_date,
        "conditions_that_would_invalidate": invalidates_on or [],
        "assumptions_hash": stable_hash(assumptions_hash_input if assumptions_hash_input is not None else key),
    }


def no_policy_recommendation() -> Dict:
    """The brief: "Do not manufacture a target allocation solely from
    age. If no policy exists, the first Coach recommendation is to
    establish or review the investment policy." Tier 1 (missing_data,
    priority offset -1 so it sorts ahead of every other missing_data
    card) -- no drift/new-money/rebalance recommendation is valid
    without a saved policy to measure against."""
    return _card(
        "missing_data", recommendation_key("no_investment_policy"),
        -1, "Establish your investment policy",
        "No investment policy is saved yet. Set a target allocation, drift band, and rebalance preferences before "
        "the Coach can evaluate drift, new-money direction, or rebalancing -- nothing here is manufactured from age "
        "or a generic model.",
        [], [], current_value=None, target_value=None,
        proposed_change="Create an investment policy", expected_effect="Unlocks drift/new-money/rebalance recommendations.",
        tax_impact=None, assumptions=[], confidence="high",
        assumptions_hash_input={"no_policy": True},
    )


# ── Tier 1: missing/unreliable data ─────────────────────────────────────

def data_quality_recommendations(classified: Dict, reconciliations: List[Dict]) -> List[Dict]:
    cards = []
    for h in classified["blocked"]:
        cards.append(_card(
            "missing_data", recommendation_key("missing_account_type", account_id=h.get("account_id"), holding_id=h.get("id")),
            0, "Account type needs classification",
            f"Set a real account type for this account before its holdings can be included in any allocation recommendation.",
            [h.get("account_id")], [h.get("id")], current_value=None, target_value=None,
            proposed_change="Classify the account type", expected_effect="Unblocks this account for drift/rebalance recommendations.",
            tax_impact=None, assumptions=["Account type is unresolvable from the data on file."], confidence="high",
            assumptions_hash_input={"account_id": h.get("account_id"), "holding_id": h.get("id")},
        ))
    for recon in reconciliations:
        if recon["has_warning"]:
            cards.append(_card(
                "missing_data", recommendation_key("unreconciled_remainder", account_id=recon["account_id"]),
                1, "Holdings don't reconcile to the account balance",
                recon["warning"], [recon["account_id"]], [],
                current_value=recon["holdings_total"], target_value=recon["account_balance"],
                proposed_change="Add or correct holdings until they sum to the account balance",
                expected_effect="Accurate allocation totals for this account.",
                tax_impact=None, assumptions=["Account balance is current as of the last update."], confidence="high",
                assumptions_hash_input={"account_id": recon["account_id"], "remainder": recon["unreconciled_remainder"]},
                value_unit="currency",
            ))
    for h in classified["household"] + classified["hsa"] + classified["child_specific"]:
        # A target-date portfolio is intentionally a managed multi-asset
        # holding.  Its internal mix changes on the fund's own glide path,
        # so treating its single `unclassified` placeholder as missing data
        # would ask the household to invent a static allocation.  It remains
        # tracked by value, but is not a direct Coach trade or fee-audit
        # candidate until look-through data is deliberately supplied.
        is_managed_target_date = (
            h.get("management_mode") == "externally_managed"
            and h.get("security_type") == "target_date_fund"
        )
        reported_date = h.get("as_of_date") or h.get("updated_at") or h.get("created_at")
        try:
            reported_day = date.fromisoformat(str(reported_date)[:10]) if reported_date else None
        except ValueError:
            reported_day = None
        days_old = (date.today() - reported_day).days if reported_day else None
        if days_old is not None and days_old > 35:
            cards.append(_card(
                "missing_data", recommendation_key("stale_holding_value", account_id=h.get("account_id"), holding_id=h.get("id")),
                1, f"Refresh the value of {h.get('security_name') or h.get('ticker') or 'this holding'}",
                f"Its latest recorded value is {days_old} days old. Refresh the market value from a statement or quote before relying on allocation and trade guidance.",
                [h.get("account_id")], [h.get("id")], current_value=days_old, target_value=35,
                proposed_change="Refresh the holding value and statement date", expected_effect="Keeps allocation and Coach actions based on current values.",
                tax_impact=None, assumptions=[f"Latest holding date: {reported_day.isoformat()}"], confidence="medium",
                assumptions_hash_input={"holding_id": h.get("id"), "reported_date": reported_day.isoformat()},
                value_unit="count",
            ))
        if not is_managed_target_date and ((h.get("asset_class") or "unclassified") == "unclassified" or h.get("asset_class") not in ASSET_CLASSES):
            cards.append(_card(
                "missing_data", recommendation_key("unclassified_security", account_id=h.get("account_id"), holding_id=h.get("id")),
                2, f"{h.get('security_name') or h.get('ticker') or 'This holding'} needs an asset class",
                "Set an asset class for this holding so it counts toward your real allocation instead of the unclassified bucket.",
                [h.get("account_id")], [h.get("id")], current_value=h.get("asset_class"), target_value=None,
                proposed_change="Classify the holding's asset class", expected_effect="Included correctly in drift/rebalance math.",
                tax_impact=None, assumptions=[], confidence="high",
                assumptions_hash_input={"holding_id": h.get("id"), "asset_class": h.get("asset_class")},
                value_unit="text",
            ))
        # Cash has no unrealized capital gain to track; its tax basis is its
        # dollar value.  Do not ask for a tax lot or cost basis on it.
        is_cash_position = h.get("security_type") == "cash" or h.get("asset_class") == "cash"
        if (h.get("_portfolio_account_type") in TAXABLE_GAIN_TYPES and not is_cash_position
                and h.get("cost_basis") is None and (h.get("market_value", 0) or 0) > 0):
            cards.append(_card(
                "missing_data", recommendation_key("missing_cost_basis", account_id=h.get("account_id"), holding_id=h.get("id")),
                3, f"Missing cost basis on {h.get('security_name') or 'a taxable holding'}",
                "Add cost basis so any future sale of this taxable holding can show a real estimated gain/loss instead of an unknown.",
                [h.get("account_id")], [h.get("id")], current_value=None, target_value=None,
                proposed_change="Enter cost basis", expected_effect="Accurate tax-impact estimates on any future sale.",
                tax_impact=None, assumptions=[], confidence="high",
                assumptions_hash_input={"holding_id": h.get("id"), "has_cost_basis": False},
            ))
        # An expense ratio is a fund-level charge.  Do not create a bogus
        # data-quality task for direct shares, cash, or a stable-value option;
        # none has a fund expense ratio to enter in this field.
        is_non_fund_position = (
            h.get("security_type") in {"stock", "cash", "stable_value"}
            or h.get("asset_class") == "cash"
        )
        if (not is_managed_target_date and not is_non_fund_position
                and h.get("expense_ratio") is None and (h.get("market_value", 0) or 0) > 0):
            cards.append(_card(
                "missing_data", recommendation_key("missing_expense_ratio", account_id=h.get("account_id"), holding_id=h.get("id")),
                4, f"Missing expense ratio on {h.get('security_name') or 'a holding'}",
                "Add the expense ratio so fee-drag comparisons include this holding.",
                [h.get("account_id")], [h.get("id")], current_value=None, target_value=None,
                proposed_change="Enter expense ratio", expected_effect="Complete fee-audit coverage.",
                tax_impact=None, assumptions=[], confidence="medium",
                assumptions_hash_input={"holding_id": h.get("id"), "has_expense_ratio": False},
            ))
        if h.get("data_source") == "manual" or h.get("confidence") == "low":
            cards.append(_card(
                "missing_data", recommendation_key("low_confidence_entry", account_id=h.get("account_id"), holding_id=h.get("id")),
                5, f"{h.get('security_name') or 'A holding'} was entered manually",
                "Confirm this manually-entered holding against a real statement, or resolve it against the security lookup for higher-confidence data.",
                [h.get("account_id")], [h.get("id")], current_value=h.get("confidence"), target_value="high",
                proposed_change="Confirm via security lookup", expected_effect="Higher-confidence data for this holding.",
                tax_impact=None, assumptions=[], confidence="low",
                assumptions_hash_input={"holding_id": h.get("id"), "data_source": h.get("data_source"), "confidence": h.get("confidence")},
                value_unit="text",
            ))
    return cards


# ── Tier 2: dangerous concentration or liquidity problem ────────────────

def concentration_and_liquidity_recommendations(household_holdings: List[Dict], policy: Optional[Dict] = None,
                                                  household_cash_value: Optional[float] = None) -> List[Dict]:
    """Only the SEVERE concentration tier belongs at tier 2 (a dangerous
    single-security risk) -- moderate concentration is an ordinary
    review item, handled at tier 8 by minor_optimization_recommendations
    below, to avoid two different urgency levels sharing one priority.
    Liquidity: flags only when household cash is measurably below the
    policy's own minimum_cash_reserve -- never invented from an age or
    a rule of thumb."""
    cards = []
    configured_limit = (policy or {}).get("max_single_security_pct")
    threshold = configured_limit if configured_limit is not None else 10
    severe_threshold = configured_limit if configured_limit is not None else 25
    for f in concentration_flags(household_holdings, threshold_pct=threshold,
                                 severe_threshold_pct=severe_threshold, policy=policy):
        if f["severity"] != "severe":
            continue
        holding = next((h for h in household_holdings if h.get("id") == f["holding_id"]), None)
        tax_impact = None
        if holding and holding.get("_portfolio_account_type") in TAXABLE_GAIN_TYPES:
            tax_impact = estimate_taxable_gain_warning(holding, holding.get("market_value", 0) or 0)
        cards.append(_card(
            "concentration_or_liquidity_risk", recommendation_key("severe_concentration", account_id=f["account_id"], holding_id=f["holding_id"]),
            0, f"{f['name']} is a dangerously concentrated position",
            f"{f['name']} is {f['pct_of_portfolio']}% of your investable assets — a single-security risk large enough "
            f"to materially affect household net worth on its own. Review whether this concentration is intentional "
            f"(e.g. employer stock) — this is evidence for a decision, not an automatic sell.",
            [f["account_id"]], [f["holding_id"]], current_value=f["pct_of_portfolio"], target_value=None,
            proposed_change="Review for a possible diversification plan", expected_effect="Reduced single-security risk, if acted on.",
            tax_impact=tax_impact,
            assumptions=[f"Concentration measured against total household investable assets; limit is {threshold}% from the saved policy."],
            confidence="high", assumptions_hash_input=f, value_unit="percent",
        ))
    if policy and policy.get("minimum_cash_reserve") and household_cash_value is not None:
        minimum = policy["minimum_cash_reserve"]
        if household_cash_value < minimum:
            cards.append(_card(
                "concentration_or_liquidity_risk", recommendation_key("liquidity_shortfall"),
                1, "Cash reserve is below your policy's minimum",
                f"Household investable cash is ${household_cash_value:,.0f}, below the "
                f"${minimum:,.0f} minimum cash reserve set in your investment policy.",
                [], [], current_value=household_cash_value, target_value=minimum,
                proposed_change="Rebuild the cash reserve before directing new money elsewhere",
                expected_effect="Restores the policy's own minimum liquidity buffer.",
                tax_impact=None, assumptions=["minimum_cash_reserve comes from the saved investment policy."],
                confidence="high",
                invalidates_on=["The saved policy's minimum_cash_reserve changes.", "Cash holdings change enough to close the shortfall."],
                assumptions_hash_input={"household_cash_value": household_cash_value, "minimum": minimum},
                value_unit="currency",
            ))
    return cards


# ── Tier 3: material investment-policy violation ─────────────────────────

def policy_violation_recommendations(comparison: Dict, policy: Dict) -> List[Dict]:
    """Never recommends "invest more in X" without a target, a measured
    current exposure, and the size of the gap — per the brief's explicit
    guard. Requires a real policy (caller must check has_policy first;
    this function assumes one exists)."""
    cards = []
    for asset_class, d in sorted(comparison["by_class"].items(), key=lambda kv: -abs(kv[1]["deviation_dollars"])):
        if asset_class == "unclassified" or d["within_drift_band"]:
            continue
        direction = "overweight" if d["deviation_dollars"] > 0 else "underweight"
        cards.append(_card(
            "policy_violation", recommendation_key("drift", asset_class=asset_class),
            0, f"{asset_class.replace('_', ' ').title()} is {direction} vs. target",
            (
                f"Current {d['current_pct']}% vs. target {d['target_pct']}% "
                f"({policy.get('name', 'your saved policy')}) — a ${abs(d['deviation_dollars']):,.0f} "
                f"({abs(d['deviation_pct'])} point) {direction}, outside the ±{comparison['drift_band_pct']}% drift band."
            ),
            [], [], current_value=d["current_pct"], target_value=d["target_pct"],
            proposed_change=f"Bring {asset_class.replace('_', ' ')} back toward {d['target_pct']}%",
            expected_effect=f"Allocation drift for this class reduced to within the {comparison['drift_band_pct']}% band.",
            tax_impact=None,
            assumptions=[f"Target percentages come from the saved investment policy (\"{policy.get('name', 'Household Policy')}\")."],
            confidence="high",
            invalidates_on=["The saved policy's target changes.", "Real holdings change enough to close the drift on their own."],
            assumptions_hash_input={"asset_class": asset_class, "current_pct": d["current_pct"], "target_pct": d["target_pct"]},
            value_unit="percent",
        ))
    return cards

# Backwards-compatible alias -- earlier internal draft's name.
allocation_drift_recommendations = policy_violation_recommendations


# ── Tier 4: high-cost or materially redundant holding ────────────────────

def high_cost_or_redundant_recommendations(household_holdings: List[Dict]) -> List[Dict]:
    """Deliberately does NOT re-flag unclassified holdings -- that's
    already a tier-1 missing_data card (data_quality_recommendations'
    "needs an asset class" card, keyed on the same holding_id) and
    re-surfacing it here would be a duplicate recommendation for the
    same underlying problem, which the brief explicitly says to avoid."""
    cards = []
    for f in expense_ratio_flags(household_holdings):
        cards.append(_card(
            "high_cost_or_redundant", recommendation_key("high_expense_ratio", account_id=f["account_id"], holding_id=f["holding_id"]),
            0, f"{f['name']} has a high expense ratio",
            f"{f['name']} charges {f['expense_ratio_pct']}% (~${f['annual_fee_dollars']:,.0f}/yr). "
            f"Review for a lower-cost equivalent — this is evidence for a decision, not an automatic sell.",
            [f["account_id"]], [f["holding_id"]], current_value=f["expense_ratio_pct"], target_value=None,
            proposed_change="Review for a lower-cost equivalent fund", expected_effect="Reduced fee drag, if acted on.",
            tax_impact=None, assumptions=[], confidence="high", assumptions_hash_input=f, value_unit="percent",
        ))
    for f in duplicate_exposure_flags(household_holdings):
        cards.append(_card(
            "high_cost_or_redundant", recommendation_key("duplicate_exposure", extra=f["name"]),
            1, f"{f['name']} held across {len(f['accounts'])} accounts",
            f"${f['total_market_value']:,.0f} of {f['name']} is spread across {len(f['accounts'])} accounts — not necessarily "
            f"wrong, but worth reviewing for redundant exposure or a consolidation opportunity.",
            f["accounts"], f["holding_ids"], current_value=f["total_market_value"], target_value=None,
            proposed_change="Review for consolidation", expected_effect="Simplified holdings, if acted on.",
            tax_impact=None, assumptions=[], confidence="medium", assumptions_hash_input=f,
        ))
    return cards

# Backwards-compatible alias -- earlier internal draft's name (dropped
# the unclassified-fund card, which duplicated a tier-1 missing_data
# card; see the docstring above).
fund_quality_recommendations = high_cost_or_redundant_recommendations


# ── Tier 5: new-money direction ──────────────────────────────────────────

def new_money_recommendations(contribution_actions: List[Dict]) -> List[Dict]:
    """Wraps holdings_engine.recommend_contribution_destination's own
    output (already the drift-minimizing destination choice) into full
    recommendation cards -- this only reformats it, never re-derives
    the allocation math."""
    cards = []
    for i, a in enumerate(contribution_actions):
        if not a.get("asset_class") or not a.get("amount"):
            continue
        cards.append(_card(
            "new_money", recommendation_key("new_money", asset_class=a["asset_class"]),
            i, f"Direct new contributions toward {a['asset_class'].replace('_', ' ')}",
            a.get("reason") or f"New money reduces the {a['asset_class'].replace('_', ' ')} underweight without any sale.",
            [], [], current_value=None, target_value=None,
            proposed_change=f"Direct ${a['amount']:,.0f} of new contributions to {a['asset_class'].replace('_', ' ')}",
            expected_effect="Reduces household allocation drift using new money, before any exchange or sale.",
            tax_impact=None, assumptions=["Assumes the pending contribution amount supplied for this run."],
            confidence="high",
            invalidates_on=["Holdings or the saved policy change before this is acted on."],
            assumptions_hash_input=a,
        ))
    return cards


# ── Asset location review ────────────────────────────────────────────────

def asset_location_recommendations(classified: Dict, options_by_account: Optional[Dict[int, List[Dict]]] = None,
                                     accounts: Optional[List[Dict]] = None) -> List[Dict]:
    """Return review-only tax-location opportunities from recorded facts.

    The calculation uses the same resolved account taxonomy as the rest of
    Portfolio Coach -- it never falls back to account-name guesses or legacy
    labels.  A card appears only when a holding in a brokerage account and at
    least one tax-sheltered account are both on file.  It identifies a useful
    *future-placement* question; it does not estimate tax savings, select a
    replacement fund, or recommend selling the taxable holding.
    """
    options_by_account = options_by_account or {}
    tax_sheltered_types = PRETAX_RMD_TYPES | ROTH_TYPES | HSA_TYPES
    # These categories often distribute ordinary income, interest, or other
    # income that may be less convenient in taxable.  This is deliberately a
    # small, conservative set -- broad equity holdings are not inferred to be
    # incorrectly located from their asset class alone.
    tax_sensitive_classes = {"us_bonds", "international_bonds", "real_estate"}

    account_by_id = {}
    for bucket in ("household", "hsa", "child_specific", "liquidity", "blocked", "review_required"):
        for holding in classified.get(bucket, []):
            account = holding.get("_account")
            if account:
                account_by_id[account.get("id")] = account
    for account in accounts or []:
        account_by_id[account.get("id")] = account

    sheltered_accounts = [
        account for account in account_by_id.values()
        if resolve_portfolio_account_type(account) in tax_sheltered_types
    ]
    if not sheltered_accounts:
        return []

    cards = []
    for holding in classified.get("household", []):
        account = holding.get("_account") or {}
        account_type = holding.get("_portfolio_account_type") or resolve_portfolio_account_type(account)
        asset_class = holding.get("asset_class")
        if account_type not in TAXABLE_GAIN_TYPES or asset_class not in tax_sensitive_classes:
            continue

        destinations = []
        for candidate in sheltered_accounts:
            matching_options = [
                option for option in options_by_account.get(candidate.get("id"), [])
                if option.get("asset_class") == asset_class
                and (option.get("available_for_new_contributions") or option.get("available_for_exchange"))
            ]
            destinations.append({
                "account_id": candidate.get("id"),
                "account_name": candidate.get("name") or "tax-advantaged account",
                "portfolio_account_type": resolve_portfolio_account_type(candidate),
                "matching_options": sorted({option.get("option_name") or option.get("ticker") for option in matching_options if option.get("option_name") or option.get("ticker")}),
            })

        recorded_matches = [
            f"{destination['account_name']}: {', '.join(destination['matching_options'][:2])}"
            for destination in destinations if destination["matching_options"]
        ]
        destination_note = (
            " Recorded same-class choices: " + "; ".join(recorded_matches[:3]) + "."
            if recorded_matches else
            " No same-class option has been recorded in those accounts, so confirm the menu before changing anything."
        )
        name = holding.get("security_name") or holding.get("ticker") or "This holding"
        cards.append(_card(
            "minor_optimization", recommendation_key("asset_location_review", account_id=holding.get("account_id"), holding_id=holding.get("id")),
            5, f"Review future tax location for {name}",
            f"{asset_class.replace('_', ' ').title()} is recorded in a taxable brokerage account. For future purchases or a separately justified rebalance, compare placing this exposure in one of the tax-sheltered accounts already on file." + destination_note,
            [holding.get("account_id")] + [destination["account_id"] for destination in destinations], [holding.get("id")],
            current_value=holding.get("market_value"), target_value=None,
            proposed_change="Use this as a future-placement review; do not sell solely to change location.",
            expected_effect="May reduce taxable distributions from future holdings. Coach does not estimate a dollar tax benefit.",
            tax_impact="Selling in taxable may realize a gain or loss; Coach does not calculate taxes, fees, or a replacement trade here.",
            assumptions=[
                "This is a location heuristic for future purchases or separately justified rebalancing, not a sell recommendation.",
                "Recorded account tax types and asset classifications are correct.",
                "It does not model tax brackets, state tax, fund distributions, contribution limits, or withdrawal timing.",
            ],
            confidence="medium", invalidates_on=["Account type, available menu, holdings, or tax circumstances change."],
            assumptions_hash_input={
                "holding_id": holding.get("id"), "account_type": account_type,
                "asset_class": asset_class, "market_value": holding.get("market_value"),
                "destinations": destinations,
            },
        ))
    return cards


def taxable_loss_review_recommendations(household_holdings: List[Dict], holding_ids_with_lots: Optional[Set] = None) -> List[Dict]:
    """Aggregate holding-level loss review -- the FALLBACK for a taxable
    holding with no recorded tax lots. `holding_ids_with_lots` (when
    supplied) excludes holdings already covered by the lot-aware
    tax_lot_loss_review_recommendations above, so the same holding never
    gets both an aggregate and a lot-level card for the same loss."""
    holding_ids_with_lots = holding_ids_with_lots or set()
    cards = []
    for h in household_holdings:
        if h.get("_portfolio_account_type") not in TAXABLE_GAIN_TYPES:
            continue
        if h.get("id") in holding_ids_with_lots:
            continue
        value, basis = h.get("market_value") or 0, h.get("cost_basis")
        if basis is None or value >= basis:
            continue
        loss = round(basis - value, 2)
        cards.append(_card("minor_optimization", recommendation_key("tax_loss_review", account_id=h.get("account_id"), holding_id=h.get("id")), 6,
            f"Review unrealized loss on {h.get('security_name') or h.get('ticker')}",
            f"Recorded value is ${loss:,.0f} below cost basis. Review tax-loss harvesting only after checking wash-sale rules and your replacement investment.",
            [h.get("account_id")], [h.get("id")], loss, None, "Review, do not automatically sell", "Potential tax-loss opportunity.", None,
            ["Uses aggregate cost basis, not tax lots."], "medium", assumptions_hash_input={"id":h.get("id"),"value":value,"basis":basis}))
    return cards


def tax_lot_loss_review_recommendations(
    household_holdings: List[Dict],
    lots_by_holding_id: Optional[Dict[int, List[Dict]]] = None,
    household_lots_for_wash_sale: Optional[List[Dict]] = None,
    today: Optional[str] = None,
    threshold_pct: float = DEFAULT_TAX_LOT_LOSS_REVIEW_THRESHOLD_PCT,
) -> List[Dict]:
    """Lot-aware taxable-loss review -- a SEPARATE recommendation type
    from taxable_loss_review_recommendations' aggregate-holding version
    above (distinct recommendation_key prefix, one card per candidate
    lot), for holdings that actually have recorded tax lots. Holdings
    without lots are not touched here -- the caller keeps running the
    aggregate version for those so no taxable holding silently loses its
    loss review just because lots were never entered.

    Never claims a loss "should" be harvested, never claims a sale is
    "wash-sale safe", and never invents a wash-sale-free result: the
    household's own recorded lots are the only purchases this function
    can see, which is stated in every card's assumptions."""
    lots_by_holding_id = lots_by_holding_id or {}
    household_lots_for_wash_sale = household_lots_for_wash_sale or []
    cards = []
    for h in household_holdings:
        if h.get("_portfolio_account_type") not in TAXABLE_GAIN_TYPES:
            continue
        lots = lots_by_holding_id.get(h.get("id"))
        if not lots:
            continue
        evaluations = evaluate_tax_lots(h, lots)
        name = h.get("security_name") or h.get("ticker") or "This holding"
        ticker_or_name = h.get("ticker") or h.get("security_name")
        for entry in evaluations:
            if not is_lot_loss_candidate(entry, threshold_pct):
                continue
            conflicts = detect_wash_sale_conflicts(
                ticker_or_name, entry["lot_id"], household_lots_for_wash_sale, potential_sale_date=today,
            )
            term_label = {"short_term": "short-term", "long_term": "long-term", "unknown": "unknown-term (needs review)"}[entry["term"]]
            loss = abs(entry["gain_loss"])
            pct_text = f" ({entry['gain_loss_pct']:+.1f}%)" if entry["gain_loss_pct"] is not None else ""
            assumptions = [
                "This is a review candidate, not an instruction to sell or harvest this lot.",
                f"Priced using this holding's own recorded value as of {entry['current_price_date'] or 'an unknown date'} -- not a live quote.",
                "This app cannot see purchases made in outside accounts or in retirement accounts unless those holdings/lots are recorded here.",
            ]
            action_text = (
                f"Lot acquired {entry['acquired_date']} ({entry['shares']} shares, {term_label}) is worth an estimated "
                f"${entry['current_value']:,.0f} against a cost basis of ${entry['cost_basis']:,.0f} -- an unrealized loss of "
                f"${loss:,.0f}{pct_text} as of {entry['current_price_date'] or 'an unrecorded price date'}. "
                "Review wash-sale exposure and your intended replacement investment before acting."
            )
            confidence = "medium"
            if conflicts:
                conflict_accounts = sorted({c.get("account_name") or f"account {c.get('account_id')}" for c in conflicts})
                assumptions.append(
                    "Possible wash-sale conflict -- review before acting: this app found a recorded purchase of "
                    f"{ticker_or_name} within 30 days of the potential sale date in {', '.join(conflict_accounts)}."
                )
                confidence = "low"
            else:
                assumptions.append(
                    "No matching same-ticker purchase was found in this app's own recorded accounts within the 30-day "
                    "wash-sale window -- this is NOT a wash-sale-safe determination, only the absence of a conflict this app can see."
                )
            cards.append(_card(
                "minor_optimization",
                recommendation_key("tax_lot_loss_review", account_id=h.get("account_id"), holding_id=h.get("id"), extra=f"lot{entry['lot_id']}"),
                7,
                f"Review lot-level loss on {name} ({entry['acquired_date']})",
                action_text,
                [h.get("account_id")], [h.get("id")],
                current_value=entry["current_value"], target_value=entry["cost_basis"],
                proposed_change="Review, do not automatically sell",
                expected_effect="Potential tax-loss review opportunity at the individual tax-lot level.",
                tax_impact={"has_cost_basis": True, "estimated_gain": entry["gain_loss"]},
                assumptions=assumptions,
                confidence=confidence,
                invalidates_on=["Holding value, this lot, or the household's recorded purchases change before this is acted on."],
                assumptions_hash_input={
                    "lot_id": entry["lot_id"], "holding_id": h.get("id"), "current_value": entry["current_value"],
                    "cost_basis": entry["cost_basis"], "conflicts": [c.get("lot_id") for c in conflicts],
                },
            ))
    return cards


# ── Tiers 6/7: account-aware rebalancing (tax-advantaged vs. taxable) ────

def rebalance_recommendations(rebalance_result: Dict) -> List[Dict]:
    """Wraps holdings_engine.recommend_rebalance_actions' own output
    into full recommendation cards (that function already implements
    the contribution-first/idle-cash/tax-advantaged-first/taxable-last
    priority order and the missing-cost-basis warning — this only
    reformats it, never re-derives it). Each action is tagged tier 6
    (tax_advantaged_rebalance) unless it is a taxable sale, which is
    tier 7 (taxable_rebalance) — the two are never the same tier, since
    the brief lists them as separate priority levels."""
    cards = []
    for i, a in enumerate(rebalance_result["rebalance_actions"]):
        is_taxable = bool(a.get("is_taxable_sale"))
        category = "taxable_rebalance" if is_taxable else "tax_advantaged_rebalance"
        destination = a.get("destination") or {}
        destination_text = ""
        if destination:
            account_label = destination.get("account_name") or "account {}".format(destination.get("account_id"))
            destination_text = f" in {account_label} using {destination.get('option_name')}"
        cards.append(_card(
            category, recommendation_key("rebalance_action", account_id=a.get("account_id"), holding_id=a.get("holding_id"), asset_class=a["asset_class"], extra=a["action"]),
            i, f"{a['action'].title()} {a.get('holding_name') or a['asset_class'].replace('_', ' ')}",
            a["reason"], [a.get("account_id")] if a.get("account_id") else [], [a.get("holding_id")] if a.get("holding_id") else [],
            current_value=None, target_value=None,
            proposed_change=f"{a['action']} ${a['amount']:,.0f}{destination_text}", expected_effect="Reduces household allocation drift toward target.",
            tax_impact=a.get("tax_warning"), assumptions=[a.get("confidence_note")] if a.get("confidence_note") else [],
            confidence="high" if not is_taxable else ("medium" if (a.get("tax_warning") or {}).get("has_cost_basis") else "low"),
            invalidates_on=["Holdings or the saved policy change before this is acted on."],
            assumptions_hash_input=a,
        ))
    return cards


# ── Tier 8: minor optimization / ordinary review items ───────────────────

def minor_optimization_recommendations(household_holdings: List[Dict], goal_context: Optional[Dict] = None) -> List[Dict]:
    """Everything that's worth a look but isn't urgent, a policy
    violation, a cost/redundancy problem, or an actionable money move:
    moderate (non-severe) concentration, and goal-aware planning
    context built from ALREADY-COMPUTED figures (years to retirement,
    Monte Carlo success rate, downside depletion age) -- this function
    performs no retirement-math of its own; every number in
    `goal_context` comes from run_retirement_projection and
    run_monte_carlo in main.py, per the brief's "do not duplicate
    calculation formulas already present in the retirement engine.\""""
    cards = []
    policy = (goal_context or {}).get("policy") or {}
    configured_limit = policy.get("max_single_security_pct")
    threshold = configured_limit if configured_limit is not None else 10
    severe_threshold = configured_limit if configured_limit is not None else 25
    for f in concentration_flags(household_holdings, threshold_pct=threshold,
                                 severe_threshold_pct=severe_threshold, policy=policy):
        if f["severity"] == "severe":
            continue  # already surfaced at tier 2
        cards.append(_card(
            "minor_optimization", recommendation_key("moderate_concentration", account_id=f["account_id"], holding_id=f["holding_id"]),
            0, f"{f['name']} is a moderately large position",
            f"{f['name']} is {f['pct_of_portfolio']}% of your investable assets. Not urgent, but worth reviewing "
            f"for diversification — this is evidence for a decision, not an automatic sell.",
            [f["account_id"]], [f["holding_id"]], current_value=f["pct_of_portfolio"], target_value=None,
            proposed_change="Review for a possible diversification plan", expected_effect="Reduced single-security risk, if acted on.",
            tax_impact=None, assumptions=["Concentration measured against total household investable assets."],
            confidence="medium", assumptions_hash_input=f, value_unit="percent",
        ))
    goal_context = goal_context or {}
    years_to_retirement = goal_context.get("years_to_retirement")
    depletion_age = goal_context.get("median_depletion_age")
    retirement_age = goal_context.get("retirement_age")
    success_rate = goal_context.get("monte_carlo_success_rate")
    if years_to_retirement is not None and depletion_age is not None and retirement_age is not None:
        if years_to_retirement <= 10 and depletion_age < retirement_age + 30:
            cards.append(_card(
                "minor_optimization", recommendation_key("near_term_depletion_risk"),
                1, "Review near-term spending reserve before increasing equity exposure",
                (
                    f"Retirement is {years_to_retirement} years away and the downside Monte Carlo scenario "
                    f"depletes the portfolio at age {depletion_age}. Review whether your near-term spending "
                    f"reserve is large enough before increasing equity exposure."
                ),
                [], [], current_value=depletion_age, target_value=None,
                proposed_change="Review cash reserve / spending cushion before any equity-weighting increase",
                expected_effect="Better-informed near-term risk decision -- not a specific trade.",
                tax_impact=None,
                assumptions=[f"Retirement projection assumes age {retirement_age}.", "Monte Carlo downside scenario per the last run."],
                confidence="medium",
                invalidates_on=["Retirement age or the Monte Carlo assumptions change materially."],
                assumptions_hash_input=goal_context, value_unit="age",
            ))
    if success_rate is not None and success_rate < 80:
        cards.append(_card(
            "minor_optimization", recommendation_key("low_success_rate"),
            2, "Monte Carlo success rate is below 80%",
            f"Your plan's Monte Carlo success rate is {success_rate}%. Review spending, savings rate, or retirement "
            f"age assumptions before treating any allocation change as the primary fix.",
            [], [], current_value=success_rate, target_value=80,
            proposed_change="Review broader plan assumptions, not just allocation", expected_effect="Better-informed planning decision.",
            tax_impact=None, assumptions=["Success rate per the last Monte Carlo run."], confidence="medium",
            invalidates_on=["Monte Carlo is re-run with different assumptions."],
            assumptions_hash_input=goal_context, value_unit="percent",
        ))
    return cards

# Backwards-compatible alias -- earlier internal draft's name. Signature
# differs (household_holdings is now required, goal_context optional) so
# old call sites (goal_context only) must be updated, not silently
# reinterpreted.
def goal_aware_recommendations(goal_context: Dict) -> List[Dict]:
    return minor_optimization_recommendations([], goal_context)


# ── Priority ordering ──────────────────────────────────────────────────────

def prioritize(cards: List[Dict]) -> List[Dict]:
    """Lower priority number = shown first. Stable within a tier by
    insertion order (callers already sort within-category by dollar
    magnitude where that applies)."""
    return sorted(cards, key=lambda c: c["priority"])


# ── Decision lifecycle: sync candidates against existing DB rows ────────

def recommendation_provenance(planning_inputs: Optional[Dict], holdings: List[Dict], policy: Optional[Dict],
                               saved_scenario_id=None, assumption_review_id=None) -> Dict:
    """What actually generated one batch of recommendations, so a later
    change can be attributed by name rather than a single generic
    "something changed" sentence (item 3: "let a recommendation record
    which saved scenario, planning inputs version, holdings snapshot,
    and policy version generated it"). Pure -- main.py supplies the rows
    already read from the DB; this only hashes/packages them.

    `planning_inputs_hash`/`holdings_snapshot_hash`/`policy_hash` are
    content hashes, not row-version numbers -- this app's
    planning_inputs/holdings/investment_policies tables have no
    updated_at-style version column that changes only on a real edit, so
    a deterministic hash of the actual values used is the only way to
    detect "the facts moved" versus "the row was merely re-read."""
    return {
        "saved_scenario_id": saved_scenario_id,
        "assumption_review_id": assumption_review_id,
        "planning_inputs_hash": stable_hash(planning_inputs) if planning_inputs is not None else None,
        "holdings_snapshot_hash": stable_hash(sorted(holdings, key=lambda h: h.get("id") or 0)),
        "policy_hash": stable_hash(policy) if policy else None,
    }


_INVALIDATION_REASON_LABELS = (
    ("holdings_snapshot_hash", "recorded holdings changed"),
    ("policy_hash", "the investment policy changed"),
    ("planning_inputs_hash", "planning inputs/assumptions changed"),
    ("saved_scenario_id", "a newer saved scenario was created"),
    ("assumption_review_id", "a newer assumption review was recorded"),
)


def invalidation_reason(old_row: Dict, new_provenance: Dict) -> str:
    """Human-readable reason a recommendation was marked invalidated/
    stale, built by comparing the provenance recorded on the row being
    invalidated against the provenance of the batch that triggered the
    invalidation. Falls back to a generic sentence when neither row
    carries provenance (e.g. rows created before this field existed) or
    when nothing in the compared provenance actually differs (the
    invalidation came from a candidate that disappeared for a reason
    provenance hashing can't see, such as a contribution amount input)."""
    reasons = [label for field, label in _INVALIDATION_REASON_LABELS
               if old_row.get(field) is not None and old_row.get(field) != new_provenance.get(field)]
    if not reasons:
        return "Underlying holdings, policy, or contribution input changed before this was acted on."
    return "Invalidated because " + " and ".join(reasons) + "."


def partition_annual_review_items(rows: List[Dict]) -> Dict[str, List[Dict]]:
    """Buckets an already-generated/persisted recommendation list into
    the annual-portfolio-review sections item 3 requires: stale holding
    values, unreconciled accounts, allocation drift, concentrated
    positions, and taxable-loss candidates -- by the same
    `recommendation_key` prefixes coach_engine's own card generators
    above already use. Pure partitioning only; it never re-derives a
    number the underlying cards don't already carry, and any
    recommendation_key that doesn't match one of the review's named
    buckets is kept under "other_open" rather than silently dropped."""
    sections = {
        "stale_holding_values": [], "unreconciled_accounts": [], "allocation_drift": [],
        "concentrated_positions": [], "taxable_loss_candidates": [], "other_open": [],
    }
    prefix_to_section = {
        "stale_holding_value": "stale_holding_values",
        "unreconciled_remainder": "unreconciled_accounts",
        "drift": "allocation_drift",
        "severe_concentration": "concentrated_positions",
        "moderate_concentration": "concentrated_positions",
        "liquidity_shortfall": "concentrated_positions",
        "tax_loss_review": "taxable_loss_candidates",
        "tax_lot_loss_review": "taxable_loss_candidates",
    }
    for row in rows:
        key = row.get("recommendation_key") or (row.get("payload") or {}).get("recommendation_key") or ""
        prefix = key.split(":", 1)[0]
        sections[prefix_to_section.get(prefix, "other_open")].append(row)
    return sections


def reconcile_recommendation_queue(candidates: List[Dict], existing_by_key: Dict[str, List[Dict]], today: Optional[str] = None) -> Dict:
    """Pure sync logic (main.py does the actual DB reads/writes). For
    each freshly-generated candidate:
    - No existing row for this key -> insert a new "proposed" row.
    - Deferred rows resurface on their review date when today is supplied.
    - Latest existing row is rejected/deferred:
        - SAME assumptions_hash -> suppress (do not resurface;
          "never silently disappear" is satisfied by the row itself
          staying in the table with its full history, just not shown
          in the active queue).
        - DIFFERENT hash -> the underlying facts changed; insert a
          fresh "proposed" row (the old row's own history is untouched).
    - Latest existing row is proposed/reviewing/accepted:
        - SAME hash -> reuse the existing row as-is (no duplicate row
          for unchanged state).
        - DIFFERENT hash -> the facts moved out from under an active
          recommendation; invalidate the old row and insert a fresh one.
    - Latest existing row is completed:
        - SAME hash -> nothing to do (already handled, no active card).
        - DIFFERENT hash -> the condition recurred after completion;
          insert a fresh "proposed" row.
    - Latest existing row is invalidated -> always insert a fresh row.
    - A key in `existing_by_key` has NO matching candidate this round at
      all (the underlying condition fully resolved -- e.g. a policy
      change eliminated the drift that produced it) and its latest row
      is still proposed/reviewing/accepted -> invalidate it. This is
      what makes reference test #20 ("policy change invalidates prior
      recommendations") hold even when the new policy produces no
      replacement candidate for that exact key, not only when it
      produces one with a different hash.

    Returns {"to_insert": [...], "reuse_ids": [...], "invalidate_ids": [...]}."""
    to_insert, reuse_ids, invalidate_ids = [], [], []
    candidate_keys = {c["recommendation_key"] for c in candidates}
    for key, rows in existing_by_key.items():
        if key in candidate_keys or not rows:
            continue
        latest = rows[0]
        if latest["status"] in ("proposed", "reviewing", "accepted"):
            invalidate_ids.append(latest["id"])
    for c in candidates:
        existing_rows = existing_by_key.get(c["recommendation_key"], [])
        latest = existing_rows[0] if existing_rows else None
        if latest is None:
            to_insert.append(c)
            continue
        same_hash = latest["assumptions_hash"] == c["assumptions_hash"]
        status = latest["status"]
        if status in ("rejected", "deferred"):
            due = status == "deferred" and today and latest.get("review_date") and latest["review_date"] <= today
            if due:
                to_insert.append(c)
            elif same_hash:
                pass  # suppressed -- not resurfaced, not reinserted
            else:
                to_insert.append(c)
        elif status in ("proposed", "reviewing", "accepted"):
            if same_hash:
                reuse_ids.append(latest["id"])
            else:
                invalidate_ids.append(latest["id"])
                to_insert.append(c)
        elif status == "completed":
            if not same_hash:
                to_insert.append(c)
        elif status == "invalidated":
            to_insert.append(c)
    return {"to_insert": to_insert, "reuse_ids": reuse_ids, "invalidate_ids": invalidate_ids}
