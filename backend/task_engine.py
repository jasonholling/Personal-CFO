"""
Auto-generates tasks based on financial data and calendar rules.
Each auto task has a unique auto_key so we never duplicate.
"""
from datetime import datetime
from typing import List, Dict

CURRENT_YEAR = datetime.now().year


def generate_tasks(accounts: List[Dict], inputs: Dict, projections: Dict, education: Dict) -> List[Dict]:
    tasks = []

    def task(section, title, description, task_type, recurrence, auto_key, due_year=None):
        tasks.append({
            "section": section,
            "title": title,
            "description": description,
            "task_type": task_type,
            "recurrence": recurrence,
            "auto_key": auto_key,
            "due_year": due_year or CURRENT_YEAR,
        })

    # ── Annual recurring tasks ────────────────────────────────────────────────
    task("estate", "Review beneficiary designations",
         "Verify all accounts have correct primary and contingent beneficiaries.",
         "annual", "annual", f"beneficiary_review_{CURRENT_YEAR}", CURRENT_YEAR)

    task("estate", "Review will and estate documents",
         "Confirm will, trust, power of attorney documents still reflect your wishes.",
         "annual", "annual", f"will_review_{CURRENT_YEAR}", CURRENT_YEAR)

    task("risk", "Review life insurance coverage",
         "Confirm coverage amounts are still appropriate given current asset levels and income.",
         "annual", "annual", f"life_insurance_review_{CURRENT_YEAR}", CURRENT_YEAR)

    task("risk", "Compare home and auto insurance quotes",
         "Get competing quotes to ensure you have best rates. Check umbrella policy renewal.",
         "annual", "annual", f"insurance_quotes_{CURRENT_YEAR}", CURRENT_YEAR)

    task("risk", "Review disability coverage",
         "Confirm your group disability policy is still in force and benefit is adequate.",
         "annual", "annual", f"disability_review_{CURRENT_YEAR}", CURRENT_YEAR)

    task("risk", "Review long term care policy",
         "Confirm your LTC policy is still in force. Review premium and benefit amounts.",
         "annual", "annual", f"ltc_review_{CURRENT_YEAR}", CURRENT_YEAR)

    task("investments", "Take monthly net worth snapshot",
         "Update Quicken, import to CFO app, save snapshot.",
         "annual", "annual", f"snapshot_{CURRENT_YEAR}", CURRENT_YEAR)

    task("investments", "Review asset allocation",
         "Check that current allocation matches target (80% stock / 15% fixed / 5% real estate).",
         "annual", "annual", f"allocation_review_{CURRENT_YEAR}", CURRENT_YEAR)

    task("estate", "Review credit freeze status",
         "Verify credit freeze is active at Equifax, Experian, and TransUnion.",
         "annual", "annual", f"credit_freeze_{CURRENT_YEAR}", CURRENT_YEAR)

    task("financial_independence", "Update retirement projection inputs",
         "Update ages, SS estimates, salary, contribution rates in Planning Inputs.",
         "annual", "annual", f"projection_update_{CURRENT_YEAR}", CURRENT_YEAR)

    # ── Tax operating calendar ───────────────────────────────────────────────
    # These are planning prompts, not tax advice or a filing calculation. They
    # put the decision points a household commonly misses into the same annual
    # action plan as the rest of the financial system.
    if inputs.get("w2_salary", 0) > 0:
        task("financial_independence", "Review federal and state withholding",
             "After raises, bonuses, or household income changes, compare payroll withholding with your current-year tax projection. Confirm any change with your tax professional.",
             "annual", "annual", f"withholding_review_{CURRENT_YEAR}", CURRENT_YEAR)
    if inputs.get("annual_rsu_value", 0) > 0:
        task("financial_independence", "Review RSU withholding before vesting",
             "Employer default withholding may not cover your marginal tax rate. Confirm withholding and a sale plan before the next vesting event.",
             "calculated", "annual", f"rsu_withholding_{CURRENT_YEAR}", CURRENT_YEAR)
    if sum(a.get("balance", 0) for a in accounts if a.get("account_type") == "taxable") > 0:
        task("investments", "Complete year-end tax-loss and gain review",
             "Before year-end, review taxable-account gains, losses, charitable stock gifts, and any planned concentrated-stock sales with your CPA.",
             "annual", "annual", f"year_end_tax_review_{CURRENT_YEAR}", CURRENT_YEAR)
    if inputs.get("annual_rsu_value", 0) > 0 or inputs.get("w2_salary", 0) > 0:
        task("financial_independence", "Confirm quarterly-tax obligation with CPA",
             "Use actual year-to-date withholding, investment income, and other income to confirm whether estimated payments are needed. Do not rely on a generic quarterly-payment rule.",
             "annual", "annual", f"estimated_tax_check_{CURRENT_YEAR}", CURRENT_YEAR)

    # ── Calculation-triggered tasks ───────────────────────────────────────────

    # 529 funding gaps
    if education and "goals" in education:
        for goal in education["goals"]:
            child = goal["child"]
            pct   = goal["funding_percent"]
            gap   = goal["funding_gap"]
            monthly = goal["monthly_savings_to_close_gap"]
            if pct < 100 and monthly > 0:
                threshold_label = "below 85%" if pct < 85 else "below 100%"
                task("education", f"Increase 529 contributions for {child}",
                     f"{child}'s 529 is {pct}% funded ({threshold_label}). "
                     f"Adding ${monthly:,.0f}/mo closes the ${gap:,.0f} gap by college.",
                     "calculated", "once", f"529_gap_{child.lower()}_{CURRENT_YEAR}", CURRENT_YEAR)

    # Retirement funding
    if projections and "scenarios" in projections:
        age60_early = next((s for s in projections["scenarios"] if s["label"] == "age_60_early"), None)
        if age60_early:
            pct = age60_early["percent_funded"]
            if pct < 90:
                task("financial_independence", "Retirement funding below 90% — review contributions",
                     f"Retire-at-60 scenario is only {pct}% funded. Consider increasing 401k or Roth contributions.",
                     "calculated", "once", f"retirement_underfunded_{CURRENT_YEAR}", CURRENT_YEAR)
            elif pct >= 100:
                task("financial_independence", "Confirm retire-at-55 feasibility",
                     f"Retire-at-60 scenario shows {pct}% funded with surplus. Run the age-55 scenario to see if early retirement is viable.",
                     "calculated", "once", f"retirement_surplus_check_{CURRENT_YEAR}", CURRENT_YEAR)

    # Mortgage refi check
    mortgage = sum(a["balance"] for a in accounts if a["account_type"] == "mortgage")
    if mortgage > 100000:
        task("investments", "Review mortgage refinance opportunity",
             f"Current mortgage balance is ${mortgage:,.0f}. Check if current rates warrant refinancing.",
             "calculated", "once", f"mortgage_refi_{CURRENT_YEAR}", CURRENT_YEAR)

    # RSU — if set in inputs
    rsu = inputs.get("annual_rsu_value", 0)
    if rsu > 0:
        task("financial_independence", "Review RSU vesting and tax strategy",
             f"RSUs vesting at ~${rsu:,.0f}/yr. Coordinate with CPA on timing of stock sales relative to capital gains.",
             "calculated", "annual", f"rsu_tax_strategy_{CURRENT_YEAR}", CURRENT_YEAR)

    # Concentrated stock unwind — if taxable brokerage is large
    taxable = sum(a["balance"] for a in accounts if a["account_type"] == "taxable")
    if taxable > 200000:
        task("investments", "Plan annual concentrated stock unwind",
             f"Taxable brokerage has ${taxable:,.0f}. Coordinate with CPA to determine optimal sale amount for this tax year.",
             "calculated", "annual", f"stock_unwind_{CURRENT_YEAR}", CURRENT_YEAR)

    return tasks


def sync_auto_tasks(conn, accounts, inputs, projections, education):
    """
    Upsert auto-generated tasks. Never overwrites completed status.
    Only inserts tasks that don't already exist (by auto_key).
    """
    generated = generate_tasks(accounts, inputs, projections, education)
    inserted  = 0
    for t in generated:
        existing = conn.execute(
            "SELECT id FROM tasks WHERE auto_key=?", (t["auto_key"],)
        ).fetchone()
        if not existing:
            conn.execute("""
                INSERT INTO tasks (section, title, description, task_type, recurrence, auto_key, due_year)
                VALUES (?,?,?,?,?,?,?)
            """, (t["section"], t["title"], t["description"],
                  t["task_type"], t["recurrence"], t["auto_key"], t["due_year"]))
            inserted += 1
    conn.commit()
    return inserted
