"""Evidence labels for planning outputs, not a misleading precision score."""
from datetime import datetime, timezone
from typing import Dict, List


def _days_since(value):
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - timestamp).days)
    except (TypeError, ValueError):
        return None


def plan_confidence(accounts: List[Dict], inputs: Dict, cash_flow: Dict) -> Dict:
    """Return specific evidence and limitations rather than a magic score."""
    checks = []
    latest_update = max((_days_since(a.get("updated_at")) for a in accounts), default=None)
    stale = [a for a in accounts if (_days_since(a.get("updated_at")) if _days_since(a.get("updated_at")) is not None else 10_000) > 35]
    checks.append({
        "key": "balances", "label": "Account balances",
        "status": "ready" if accounts and not stale else "attention",
        "detail": "Balances are current." if accounts and not stale else ("Add account balances to establish the household balance sheet." if not accounts else f"Refresh {len(stale)} account balance{'s' if len(stale) != 1 else ''} older than 35 days."),
    })
    checks.append({
        "key": "cash_flow", "label": "Recurring cash flow",
        "status": "ready" if cash_flow.get("has_data") else "attention",
        "detail": "Recurring income and spending are entered." if cash_flow.get("has_data") else "Add recurring take-home income and core spending; transactions are not required.",
    })
    has_retirement = bool(inputs.get("retirement_income_today_dollars", 0) and inputs.get("jason_age", 0) and inputs.get("expected_return_pre_retirement", 0))
    checks.append({
        "key": "retirement", "label": "Retirement assumptions",
        "status": "ready" if has_retirement else "attention",
        "detail": "Age, spending target, and return assumptions are set." if has_retirement else "Set current age, retirement spending, and return assumptions before relying on retirement results.",
    })
    checks.append({
        "key": "tax_precision", "label": "Tax precision",
        "status": "limited",
        "detail": "Uses a simplified income-tax estimate. It does not model transaction-level tax lots, deductions, credits, or investment gains.",
    })
    attention = sum(1 for check in checks if check["status"] == "attention")
    return {
        "label": "Strong" if attention == 0 else "Needs input" if attention == 1 else "Early draft",
        "status": "ready" if attention == 0 else "attention",
        "checks": checks,
        "account_data_as_of_days": latest_update,
        "method": "Account-level planning only — no transaction uploads or transaction-level tax assumptions.",
    }
