"""Turn the existing planning engines into a short, actionable CFO briefing.

This module deliberately does not change data or make investment decisions. It
only prioritizes the household's existing inputs, calculations, and open tasks
into a compact agenda for the dashboard.
"""
from datetime import datetime, timezone
from typing import Dict, List

from debt_engine import DEBT_TYPES
from net_worth_engine import compute_net_worth, emergency_fund_check


def _age_in_days(timestamp: str | None) -> int | None:
    if not timestamp:
        return None
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - value).days)
    except (TypeError, ValueError):
        return None


def build_cfo_briefing(
    accounts: List[Dict], inputs: Dict, snapshots: List[Dict], tasks: List[Dict],
    retirement: Dict | None = None, education: Dict | None = None,
) -> Dict:
    """Return a transparent, ranked household agenda.

    `amount` is included only when the existing calculation has a concrete
    dollar gap. The UI can safely mask it through its existing privacy mode.
    """
    net_worth = compute_net_worth(accounts)
    emergency = emergency_fund_check(accounts, inputs.get("current_monthly_expenses", 0))
    priorities = []

    def add(priority, title, detail, destination, amount=None):
        item = {"priority": priority, "title": title, "detail": detail, "destination": destination}
        if amount is not None:
            item["amount"] = round(amount)
        priorities.append(item)

    if not accounts:
        add(1, "Build your household balance sheet", "Add checking, investment, real-estate, and debt balances so every recommendation is based on current data.", "accounts")

    if emergency.get("has_data") and emergency.get("status") == "underfunded":
        add(1, "Fund the emergency reserve first", f"Liquid cash covers {emergency['months_covered']:.1f} months of expenses; the minimum target is {emergency['target_min_months']} months.", "settings", emergency["gap_to_min"])
    elif not emergency.get("has_data"):
        add(5, "Set current monthly spending", "A current spending number lets the CFO briefing measure your emergency reserve and cash runway.", "settings")

    high_rate_debt = [a for a in accounts if a.get("account_type") in DEBT_TYPES and a.get("balance", 0) > 0 and a.get("interest_rate", 0) >= 0.07]
    if high_rate_debt:
        balance = sum(a["balance"] for a in high_rate_debt)
        max_rate = max(a["interest_rate"] for a in high_rate_debt)
        add(2, "Review high-interest debt payoff", f"You have debt at up to {max_rate * 100:.1f}% APR. Compare the payoff sequence before adding risk to the investment plan.", "debt", balance)

    age_60 = next((s for s in (retirement or {}).get("scenarios", []) if s.get("label") == "age_60_early"), None)
    if inputs.get("retirement_income_today_dollars", 0) <= 0:
        add(4, "Set a retirement spending target", "A target in today's dollars is required before the retirement plan can evaluate funding progress.", "settings")
    elif age_60 and age_60.get("percent_funded", 100) < 100:
        gap = max(0, -age_60.get("projected_surplus", 0))
        add(3, "Close the retirement funding gap", f"The age-{age_60['retirement_age']} plan is {age_60['percent_funded']}% funded under the current assumptions. Test contribution and retirement-age tradeoffs.", "retirement", gap or None)

    for goal in (education or {}).get("goals", []):
        if goal.get("funding_gap", 0) > 0:
            add(3, f"Decide how to close {goal.get('child_name', goal.get('child', 'the child'))}'s education gap", f"The projection shows a shortfall during college. Review the monthly savings or lump-sum options before changing other goals.", "education", goal["funding_gap"])

    latest_snapshot = snapshots[0] if snapshots else None
    snapshot_age = _age_in_days(latest_snapshot.get("snapshot_date")) if latest_snapshot else None
    if snapshot_age is None:
        add(6, "Take a starting net-worth snapshot", "A monthly snapshot makes progress and major balance-sheet changes visible over time.", "dashboard")
    elif snapshot_age > 35:
        add(6, "Refresh the monthly net-worth snapshot", f"Your most recent snapshot is {snapshot_age} days old. Refresh balances, then save a new baseline.", "dashboard")

    open_tasks = [task for task in tasks if not task.get("completed")]
    return {
        "net_worth": round(net_worth["net_worth"]),
        "emergency_fund": emergency,
        "priorities": sorted(priorities, key=lambda item: item["priority"])[:5],
        "open_tasks": open_tasks[:8],
        "data_health": {
            "account_count": len(accounts),
            "snapshot_age_days": snapshot_age,
            "open_task_count": len(open_tasks),
            "planning_ready": bool(inputs.get("retirement_income_today_dollars", 0) and inputs.get("current_monthly_expenses", 0)),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
