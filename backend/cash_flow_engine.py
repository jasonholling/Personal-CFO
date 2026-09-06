"""Monthly cash-flow totals and guardrails for the local Personal CFO app."""
from typing import Dict, List


def summarize_cash_flow(items: List[Dict]) -> Dict:
    """Summarize user-entered recurring monthly income and spending.

    Cash flow is intentionally separate from account balances: a balance is a
    point in time, while a cash-flow line is an ongoing monthly commitment.
    """
    active = [item for item in items if item.get("amount", 0) > 0]
    income = sum(item["amount"] for item in active if item.get("cash_flow_type") == "income")
    expenses = sum(item["amount"] for item in active if item.get("cash_flow_type") == "expense")
    essential = sum(item["amount"] for item in active if item.get("cash_flow_type") == "expense" and item.get("essential"))
    categories = {}
    for item in active:
        if item.get("cash_flow_type") == "expense":
            category = item.get("category") or "Other"
            categories[category] = categories.get(category, 0) + item["amount"]
    surplus = income - expenses
    if not active:
        status, guidance = "not_started", "Add recurring take-home income and spending to create a reliable monthly funding plan."
    elif income <= 0:
        status, guidance = "missing_income", "Add monthly take-home income before using this plan to make allocation decisions."
    elif surplus < 0:
        status, guidance = "shortfall", "Monthly spending is above take-home income. Stabilize the shortfall before increasing investment or goal contributions."
    elif surplus == 0:
        status, guidance = "balanced", "Every dollar is currently assigned. Create room for savings, irregular expenses, and goal funding."
    else:
        status, guidance = "surplus", "You have an unassigned monthly surplus. Direct it intentionally after maintaining your emergency reserve and required debt payments."
    return {
        "has_data": bool(active), "monthly_income": round(income), "monthly_expenses": round(expenses),
        "monthly_essential_expenses": round(essential), "monthly_discretionary_expenses": round(max(0, expenses - essential)),
        "monthly_surplus": round(surplus), "annual_surplus": round(surplus * 12), "status": status, "guidance": guidance,
        "expense_categories": [{"category": k, "amount": round(v)} for k, v in sorted(categories.items(), key=lambda item: item[1], reverse=True)],
    }
