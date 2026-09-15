"""
The 4 Pillars executive summary — tax, estate, risk/insurance, and
investment management, the same four-pillar structure Creative Planning
and most comprehensive wealth managers organize a plan around (see
CALCULATION_CONTRACT-adjacent research, 2026-09-15) instead of an
investments-only view.

Like cfo_briefing_engine.py, this module deliberately adds NO new
financial math and makes NO new recommendations of its own — it only
classifies calculations this app already runs elsewhere (portfolio
recommendations, RMD/IRMAA projections, estate documents/beneficiaries/
tax exposure, insurance coverage) into a traffic-lighted status per
pillar, so the household gets one glance at "is anything on fire" before
diving into any single page.
"""
from typing import Dict, List, Optional

STATUS_RANK = {"good": 0, "attention": 1, "critical": 2}

# Estate.jsx's own document-template ids (documentDefaults) — duplicated
# here rather than imported since it's frontend JS, not Python; same
# accepted duplication tradeoff as ORDINARY_BRACKETS_MFJ_2026 having a
# Python and a JS copy (retirement_tools_engine.py). Update both together
# if Estate.jsx's template list ever changes.
REQUIRED_ESTATE_DOCUMENT_TYPES = ("trust", "wills", "fpoa", "hcpoa", "freeze")
GOOD_DOCUMENT_STATUSES = {"executed", "complete"}
CRITICAL_DOCUMENT_STATUSES = {"outdated"}

# coach_engine.py's CATEGORIES, split by how much attention they deserve
# in a one-glance rollup. Kept as a local copy rather than importing
# coach_engine.CATEGORIES itself -- this module only needs the
# classification, not the rest of that (large) engine's machinery.
INVESTMENT_CRITICAL_CATEGORIES = {"policy_violation", "concentration_or_liquidity_risk"}
INVESTMENT_ATTENTION_CATEGORIES = {
    "missing_data", "high_cost_or_redundant", "new_money",
    "tax_advantaged_rebalance", "taxable_rebalance",
}


def _worst(*statuses: str) -> str:
    return max(statuses, key=lambda s: STATUS_RANK[s])


def _pillar(key: str, label: str, status: str, headline: str, detail: str, destination: str) -> Dict:
    return {"key": key, "label": label, "status": status, "headline": headline, "detail": detail, "destination": destination}


def _tax_pillar(portfolio_recommendations: List[Dict], rmd_result: Optional[Dict],
                 tax_inputs_ready: bool) -> Dict:
    if not tax_inputs_ready:
        return _pillar("tax", "Tax Planning", "attention",
                        "Retirement income and expense inputs aren't set yet",
                        "Set them in Settings so RMD, Roth conversion, and IRMAA projections can run.",
                        "settings")

    loss_review_count = sum(
        1 for r in portfolio_recommendations or []
        if (r.get("category") or (r.get("payload") or {}).get("category")) == "minor_optimization"
        and str(r.get("recommendation_key") or (r.get("payload") or {}).get("recommendation_key") or "").startswith("tax_loss_review")
    )

    status = "good"
    notes = []
    if loss_review_count:
        status = _worst(status, "attention")
        notes.append(f"{loss_review_count} taxable holding{'s' if loss_review_count != 1 else ''} with an unrealized loss worth reviewing")

    if rmd_result and rmd_result.get("has_pretax_balance"):
        if rmd_result.get("irmaa_tier_jump"):
            status = _worst(status, "attention")
            notes.append(f"RMDs are projected to push Medicare premiums into IRMAA tier {rmd_result['first_rmd_irmaa']['tier_index']}")
        elif rmd_result.get("bracket_jump"):
            status = _worst(status, "attention")
            notes.append(f"first RMD is projected to raise your marginal bracket to {rmd_result['first_rmd_bracket']*100:.0f}%")

    headline = "No urgent tax-planning items" if status == "good" else "Tax planning needs a look"
    detail = "; ".join(notes) if notes else "RMD, IRMAA, and tax-loss review checks are all clear given current inputs."
    return _pillar("tax", "Tax Planning", status, headline, detail, "roth")


def _estate_pillar(estate_documents: List[Dict], estate_beneficiaries: List[Dict],
                    estate_tax_exposure: Optional[Dict]) -> Dict:
    docs_by_type = {d.get("document_type"): d for d in estate_documents or []}
    missing, outdated, needs_review = [], [], []
    for doc_type in REQUIRED_ESTATE_DOCUMENT_TYPES:
        doc = docs_by_type.get(doc_type)
        if doc is None:
            missing.append(doc_type)
        elif doc.get("status") in CRITICAL_DOCUMENT_STATUSES:
            outdated.append(doc_type)
        elif doc.get("status") not in GOOD_DOCUMENT_STATUSES:
            needs_review.append(doc_type)

    status = "good"
    notes = []
    if outdated:
        status = _worst(status, "critical")
        notes.append(f"{len(outdated)} document{'s' if len(outdated) != 1 else ''} marked outdated")
    if missing or needs_review:
        status = _worst(status, "attention")
        if missing:
            notes.append(f"{len(missing)} of {len(REQUIRED_ESTATE_DOCUMENT_TYPES)} core documents not yet recorded")
        if needs_review:
            notes.append(f"{len(needs_review)} document{'s' if len(needs_review) != 1 else ''} pending review")

    if not estate_beneficiaries:
        status = _worst(status, "attention")
        notes.append("no beneficiary designations recorded yet")
    else:
        blank = sum(1 for b in estate_beneficiaries if not (b.get("primary_beneficiary") or "").strip())
        if blank:
            status = _worst(status, "attention")
            notes.append(f"{blank} account{'s' if blank != 1 else ''} missing a primary beneficiary")

    if estate_tax_exposure and estate_tax_exposure.get("exposure", 0) > 0:
        status = _worst(status, "attention")
        notes.append(f"~${estate_tax_exposure['exposure']:,.0f} of projected federal estate tax exposure")

    headline = "Estate plan looks current" if status == "good" else "Estate plan needs attention"
    detail = "; ".join(notes) if notes else "Core documents, beneficiary designations, and estate tax exposure all look current."
    return _pillar("estate", "Estate Planning", status, headline, detail, "estate")


def _risk_pillar(insurance_policies: List[Dict], property_policies: List[Dict],
                  insurance_analysis: Optional[Dict]) -> Dict:
    status = "good"
    notes = []
    if not insurance_policies:
        status = _worst(status, "attention")
        notes.append("no insurance policies recorded")
    if not property_policies:
        status = _worst(status, "attention")
        notes.append("no property/umbrella coverage recorded")

    for person_key in ("jason", "justin"):
        person = (insurance_analysis or {}).get(person_key)
        if person and person.get("on_track") is False:
            status = _worst(status, "critical")
            notes.append(f"projected life-insurance shortfall of ~${abs(person.get('surplus_gap', 0)):,.0f}")

    headline = "Coverage looks adequate" if status == "good" else "Protection coverage needs a look"
    detail = "; ".join(notes) if notes else "Life, property, and liability coverage all look recorded and on track."
    return _pillar("risk", "Risk & Insurance", status, headline, detail, "protection")


def _investment_pillar(portfolio_recommendations: List[Dict]) -> Dict:
    categories = set()
    for r in portfolio_recommendations or []:
        category = r.get("category") or (r.get("payload") or {}).get("category")
        if category:
            categories.add(category)

    status = "good"
    if categories & INVESTMENT_CRITICAL_CATEGORIES:
        status = "critical"
    elif categories & INVESTMENT_ATTENTION_CATEGORIES:
        status = "attention"
    elif "minor_optimization" in categories:
        status = "attention"

    count = len(portfolio_recommendations or [])
    headline = "No open portfolio recommendations" if count == 0 else f"{count} open portfolio recommendation{'s' if count != 1 else ''}"
    detail = (
        "Allocation, fees, and concentration all look clear." if count == 0
        else "Review the Portfolio Coach queue — items are ranked by urgency there."
    )
    return _pillar("investment", "Investment Management", status, headline, detail, "coach")


def build_pillars_summary(
    portfolio_recommendations: List[Dict],
    rmd_result: Optional[Dict],
    tax_inputs_ready: bool,
    estate_documents: List[Dict],
    estate_beneficiaries: List[Dict],
    estate_tax_exposure: Optional[Dict],
    insurance_policies: List[Dict],
    property_policies: List[Dict],
    insurance_analysis: Optional[Dict],
) -> Dict:
    """Pure classifier over already-computed inputs — see module docstring.
    Every argument is the exact response another existing endpoint already
    returns; main.py's /api/pillars-summary route is the only place that
    gathers them."""
    pillars = [
        _tax_pillar(portfolio_recommendations, rmd_result, tax_inputs_ready),
        _estate_pillar(estate_documents, estate_beneficiaries, estate_tax_exposure),
        _risk_pillar(insurance_policies, property_policies, insurance_analysis),
        _investment_pillar(portfolio_recommendations),
    ]
    return {
        "pillars": pillars,
        "overall_status": _worst(*[p["status"] for p in pillars]),
    }
