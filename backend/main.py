from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import json
from datetime import datetime
from projection_engine import run_retirement_projection, run_education_projection
from task_engine import sync_auto_tasks
from quicken_importer import parse_quicken_networth_csv, get_net_worth_summary
from db import init_db, get_db
import auth

app = FastAPI(title="Personal CFO API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths reachable without a session — everything else under /api requires
# one once setup has run (see auth.py). Before first-run setup completes,
# every other /api route is blocked (auth.setup_required()), not just the
# ones behind a passphrase — a fresh clone starts locked, not open.
_AUTH_EXEMPT_PATHS = {
    "/api/auth/status",
    "/api/auth/setup",
    "/api/auth/login",
    "/api/auth/webauthn/login-options",
    "/api/auth/webauthn/login-verify",
}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not request.url.path.startswith("/api") or request.url.path in _AUTH_EXEMPT_PATHS:
        return await call_next(request)
    if auth.setup_required():
        return JSONResponse(status_code=401, content={"detail": "Setup required", "setup_required": True})
    if not auth.auth_enabled():
        return await call_next(request)
    token = request.cookies.get("cfo_session")
    if not auth.is_valid_session(token):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    return await call_next(request)

init_db()

# ── Auth ─────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    passphrase: str

class WebAuthnCredentialBody(BaseModel):
    credential: dict

class SetupRequest(BaseModel):
    passphrase: Optional[str] = None
    skip: bool = False

@app.get("/api/auth/status")
def auth_status(request: Request):
    if auth.setup_required():
        return {"auth_enabled": False, "authenticated": False, "webauthn_registered": False, "setup_required": True}
    if not auth.auth_enabled():
        return {"auth_enabled": False, "authenticated": True, "webauthn_registered": False, "setup_required": False}
    token = request.cookies.get("cfo_session")
    return {
        "auth_enabled": True,
        "authenticated": auth.is_valid_session(token),
        "webauthn_registered": auth.webauthn_registered(),
        "setup_required": False,
    }

@app.post("/api/auth/setup")
def auth_setup(body: SetupRequest, response: Response):
    """First-run only: either pick a passphrase or explicitly skip auth.
    No-op (403) once setup has already happened, so this can't be used to
    reset an existing passphrase — that's what /api/auth/logout + the
    passphrase itself are for."""
    if not auth.setup_required():
        raise HTTPException(status_code=403, detail="Setup already completed")
    if body.skip:
        auth.disable_auth_explicitly()
        return {"ok": True, "auth_enabled": False}
    if not body.passphrase or not body.passphrase.strip():
        raise HTTPException(status_code=400, detail="Passphrase cannot be empty")
    auth.set_passphrase(body.passphrase)
    token = auth.create_session()
    response.set_cookie(key="cfo_session", value=token, httponly=True, samesite="lax", secure=False)
    return {"ok": True, "auth_enabled": True}

@app.post("/api/auth/login")
def auth_login(body: LoginRequest, response: Response):
    if not auth.auth_enabled():
        return {"ok": True}
    if not auth.verify_passphrase(body.passphrase):
        raise HTTPException(status_code=401, detail="Incorrect passphrase")
    # Existing local installs used a plaintext APP_PASSPHRASE. Replace it
    # with a salted hash after its first successful login.
    if auth.legacy_passphrase_needs_migration():
        auth.set_passphrase(body.passphrase)
    token = auth.create_session()
    # No max_age/expires -> browser session cookie, cleared when the
    # browser fully closes. httponly so page JS (and any injected script)
    # can't read the token; secure=False because this only ever runs over
    # plain http://localhost.
    response.set_cookie(key="cfo_session", value=token, httponly=True, samesite="lax", secure=False)
    return {"ok": True}

@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    token = request.cookies.get("cfo_session")
    if token:
        auth.invalidate_session(token)
    response.delete_cookie("cfo_session")
    return {"ok": True}

@app.get("/api/auth/webauthn/register-options")
def webauthn_register_options():
    # No explicit auth check needed here — this path isn't in
    # _AUTH_EXEMPT_PATHS, so the middleware already required a valid
    # session before this function runs.
    return json.loads(auth.start_registration())

@app.post("/api/auth/webauthn/register-verify")
def webauthn_register_verify(body: WebAuthnCredentialBody):
    if not auth.verify_registration(json.dumps(body.credential)):
        raise HTTPException(status_code=400, detail="Touch ID registration failed")
    return {"ok": True}

@app.get("/api/auth/webauthn/login-options")
def webauthn_login_options():
    options = auth.start_authentication()
    if options is None:
        raise HTTPException(status_code=400, detail="Touch ID isn't set up yet")
    return json.loads(options)

@app.post("/api/auth/webauthn/login-verify")
def webauthn_login_verify(body: WebAuthnCredentialBody, response: Response):
    if not auth.verify_authentication(json.dumps(body.credential)):
        raise HTTPException(status_code=401, detail="Touch ID verification failed")
    token = auth.create_session()
    response.set_cookie(key="cfo_session", value=token, httponly=True, samesite="lax", secure=False)
    return {"ok": True}

class Account(BaseModel):
    id: Optional[int] = None
    name: str
    account_type: str
    owner: str
    institution: str
    balance: float
    notes: Optional[str] = None
    interest_rate: float = 0       # annual rate as a fraction, e.g. 0.1899 for 18.99% APR — debt accounts only
    minimum_payment: float = 0     # monthly — debt accounts only
    term_months: int = 0           # original loan term — debt accounts only, optional
    stock_allocation_pct: Optional[float] = None  # 0-100 — investment accounts only, unset means "assume default"
    expense_ratio: float = 0       # annual fraction, e.g. 0.0004 for 0.04% — investment accounts only
    monthly_rental_income: float = 0    # real_estate accounts only, rental properties
    monthly_rental_expenses: float = 0  # real_estate accounts only, rental properties (taxes, insurance, maintenance, etc — not the mortgage payment, which lives on the separate mortgage liability account)

class PlanningInputs(BaseModel):
    model_config = {"extra": "allow"}
    person1_name: str = "Person 1"
    person2_name: str = "Person 2"
    kid1_name: str = "Child 1"
    kid2_name: str = "Child 2"
    kid1_age: int = 0
    kid2_age: int = 0
    primary_residence_key: str = ""
    rental_property_key: str = ""
    jason_age: int
    justin_age: int
    retirement_income_today_dollars: float
    inflation_rate: float
    expected_return_pre_retirement: float
    expected_return_post_retirement: float
    jason_social_security: float
    justin_social_security: float
    jason_ss_age: int
    justin_ss_age: int
    annual_401k_contribution: float = 0
    annual_roth_contribution: float = 0
    annual_hsa_contribution: float = 0
    annual_rsu_value: float = 0
    mortgage_balance: float = 0

class SnapshotNote(BaseModel):
    note: str

class InsurancePolicy(BaseModel):
    id: Optional[int] = None
    who: str
    policy_type: str
    benefit: Optional[str] = None
    premium: Optional[str] = None
    notes: Optional[str] = None
    sort_order: int = 0

class PropertyPolicy(BaseModel):
    id: Optional[int] = None
    item: str
    coverage: Optional[str] = None
    renewal: Optional[str] = None
    sort_order: int = 0

# Accounts
@app.get("/api/accounts")
def get_accounts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM accounts ORDER BY account_type, owner").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/accounts")
def create_account(account: Account):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO accounts (name, account_type, owner, institution, balance, notes, interest_rate, minimum_payment, term_months, stock_allocation_pct, expense_ratio, monthly_rental_income, monthly_rental_expenses) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (account.name, account.account_type, account.owner, account.institution, account.balance, account.notes,
         account.interest_rate, account.minimum_payment, account.term_months, account.stock_allocation_pct, account.expense_ratio,
         account.monthly_rental_income, account.monthly_rental_expenses)
    )
    conn.commit()
    account.id = cur.lastrowid
    conn.close()
    return account

@app.put("/api/accounts/{account_id}")
def update_account(account_id: int, account: Account):
    conn = get_db()
    conn.execute(
        "UPDATE accounts SET name=?, account_type=?, owner=?, institution=?, balance=?, notes=?, interest_rate=?, minimum_payment=?, term_months=?, stock_allocation_pct=?, expense_ratio=?, monthly_rental_income=?, monthly_rental_expenses=? WHERE id=?",
        (account.name, account.account_type, account.owner, account.institution, account.balance, account.notes,
         account.interest_rate, account.minimum_payment, account.term_months, account.stock_allocation_pct, account.expense_ratio,
         account.monthly_rental_income, account.monthly_rental_expenses, account_id)
    )
    conn.commit()
    conn.close()
    return {**account.dict(), "id": account_id}

@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int):
    conn = get_db()
    conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
    conn.commit()
    conn.close()
    return {"deleted": account_id}

# Debt Payoff — operates on accounts whose account_type is a debt type
# (mortgage, credit_card, student_loan, car_loan, personal_loan)
@app.get("/api/debts/payoff-plan")
def get_debt_payoff_plan(extra_monthly: float = 0):
    conn = get_db()
    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    from debt_engine import run_avalanche_snowball
    return run_avalanche_snowball(accounts, extra_monthly)

@app.get("/api/debts/recommendation")
def get_debt_recommendation(extra_monthly: float = 0):
    """The actual verdict — which strategy to use, why, debt-free date, and
    which debt to focus extra payments on first. Not just the raw
    avalanche-vs-snowball numbers from /payoff-plan."""
    conn = get_db()
    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    from debt_engine import recommend_payoff_strategy
    return recommend_payoff_strategy(accounts, extra_monthly)

class AmortizedPaymentRequest(BaseModel):
    balance: float
    interest_rate: float
    term_months: int

@app.post("/api/debts/suggest-minimum-payment")
def post_suggest_minimum_payment(body: AmortizedPaymentRequest):
    """Helper for the Debt Payoff add-debt form — if you know your balance,
    rate, and remaining term but not your exact minimum payment, compute it."""
    from debt_engine import amortized_payment
    return {"suggested_minimum_payment": round(amortized_payment(body.balance, body.interest_rate, body.term_months))}

class CreditCardPayoffRequest(BaseModel):
    balance: float
    apr: float
    monthly_payment: float
    extra: float = 100

@app.post("/api/debts/credit-card-payoff")
def post_credit_card_payoff(body: CreditCardPayoffRequest):
    from debt_engine import credit_card_payoff_calculator
    return credit_card_payoff_calculator(body.balance, body.apr, body.monthly_payment, body.extra)

class RefinanceRequest(BaseModel):
    balance: float
    current_rate: float
    new_rate: float
    term_years: int
    closing_costs: float = 0

@app.post("/api/debts/refinance-analysis")
def post_refinance_analysis(body: RefinanceRequest):
    from debt_engine import refinance_breakeven
    return refinance_breakeven(body.balance, body.current_rate, body.new_rate, body.term_years, body.closing_costs)

class DebtVsInvestRequest(BaseModel):
    debt_rate: float
    expected_return: float
    employer_match_pct: float = 0

@app.post("/api/debts/debt-vs-invest")
def post_debt_vs_invest(body: DebtVsInvestRequest):
    from debt_engine import debt_vs_invest_crossover
    return debt_vs_invest_crossover(body.debt_rate, body.expected_return, body.employer_match_pct)

# ── Retirement tools: RMD planning, pension vs lump sum, backdoor Roth ─────────
@app.get("/api/retirement-tools/rmd-planning")
def get_rmd_planning():
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from retirement_tools_engine import run_rmd_planning
    return run_rmd_planning(dict(inputs_row), accounts)

class PensionVsLumpSumRequest(BaseModel):
    monthly_pension: float
    lump_sum: float
    current_age: int
    pension_start_age: int
    life_expectancy_age: int = 90
    discount_rate: float = 0.06

@app.post("/api/retirement-tools/pension-vs-lump-sum")
def post_pension_vs_lump_sum(body: PensionVsLumpSumRequest):
    from retirement_tools_engine import pension_vs_lump_sum
    return pension_vs_lump_sum(body.monthly_pension, body.lump_sum, body.current_age,
                                body.pension_start_age, body.life_expectancy_age, body.discount_rate)

class BackdoorRothRequest(BaseModel):
    magi: float
    existing_traditional_ira_balance: float = 0
    existing_traditional_ira_basis: float = 0
    planned_contribution: float = 7500  # 2026 IRA contribution limit

@app.post("/api/retirement-tools/backdoor-roth")
def post_backdoor_roth(body: BackdoorRothRequest):
    from retirement_tools_engine import backdoor_roth_eligibility
    return backdoor_roth_eligibility(body.magi, body.existing_traditional_ira_balance,
                                      body.existing_traditional_ira_basis, body.planned_contribution)

class QcdRequest(BaseModel):
    age: float
    ira_balance: float
    rmd_amount: float = 0
    desired_qcd_amount: float
    other_taxable_income: float = 0

@app.post("/api/retirement-tools/qcd")
def post_qcd(body: QcdRequest):
    from retirement_tools_engine import qcd_planner
    return qcd_planner(body.age, body.ira_balance, body.rmd_amount,
                        body.desired_qcd_amount, body.other_taxable_income)

class HsaStrategyRequest(BaseModel):
    oop_expense_this_year: float
    years_to_delay: int
    growth_rate: float = 0.07

@app.post("/api/retirement-tools/hsa-strategy")
def post_hsa_strategy(body: HsaStrategyRequest):
    from retirement_tools_engine import hsa_stealth_ira_strategy
    return hsa_stealth_ira_strategy(body.oop_expense_this_year, body.years_to_delay, body.growth_rate)

# ── Asset allocation/rebalancing + investment fee audit ────────────────────────
@app.get("/api/allocation/analysis")
def get_allocation_analysis():
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from allocation_engine import analyze_allocation
    return analyze_allocation(dict(inputs_row), accounts)

@app.get("/api/allocation/fees")
def get_fee_analysis():
    conn = get_db()
    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    from allocation_engine import analyze_fees
    return analyze_fees(accounts)

@app.get("/api/allocation/concentration")
def get_concentration_risk():
    conn = get_db()
    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    from allocation_engine import concentration_risk
    return concentration_risk(accounts)

# ── Estate tax exposure ─────────────────────────────────────────────────────
@app.get("/api/estate/tax-exposure")
def get_estate_tax_exposure(filing_as_couple: bool = True):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    inputs = dict(inputs_row)
    from net_worth_engine import compute_net_worth
    nw = compute_net_worth(accounts)
    life_insurance_total = (
        inputs.get("jason_life_basic", 0) + inputs.get("jason_life_supplemental", 0) + inputs.get("jason_life_term", 0)
        + inputs.get("justin_life_ul", 0) + inputs.get("justin_life_whole", 0) + inputs.get("justin_life_conagra", 0)
        + inputs.get("justin_life_term", 0) + inputs.get("justin_life_kids", 0)
    )
    from estate_engine import estate_tax_exposure
    return estate_tax_exposure(nw["net_worth"], life_insurance_total, filing_as_couple)

# Insurance Policies (life/disability/LTC — was hardcoded in Risk.jsx, now lives in cfo.db only)
@app.get("/api/insurance-policies")
def get_insurance_policies():
    conn = get_db()
    rows = conn.execute("SELECT * FROM insurance_policies ORDER BY sort_order, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/insurance-policies")
def create_insurance_policy(policy: InsurancePolicy):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO insurance_policies (who, policy_type, benefit, premium, notes, sort_order) VALUES (?,?,?,?,?,?)",
        (policy.who, policy.policy_type, policy.benefit, policy.premium, policy.notes, policy.sort_order)
    )
    conn.commit()
    policy.id = cur.lastrowid
    conn.close()
    return policy

@app.put("/api/insurance-policies/{policy_id}")
def update_insurance_policy(policy_id: int, policy: InsurancePolicy):
    conn = get_db()
    conn.execute(
        "UPDATE insurance_policies SET who=?, policy_type=?, benefit=?, premium=?, notes=?, sort_order=? WHERE id=?",
        (policy.who, policy.policy_type, policy.benefit, policy.premium, policy.notes, policy.sort_order, policy_id)
    )
    conn.commit()
    conn.close()
    return {**policy.dict(), "id": policy_id}

@app.delete("/api/insurance-policies/{policy_id}")
def delete_insurance_policy(policy_id: int):
    conn = get_db()
    conn.execute("DELETE FROM insurance_policies WHERE id=?", (policy_id,))
    conn.commit()
    conn.close()
    return {"deleted": policy_id}

# Property & Liability Policies (was hardcoded in Risk.jsx, now lives in cfo.db only)
@app.get("/api/property-policies")
def get_property_policies():
    conn = get_db()
    rows = conn.execute("SELECT * FROM property_policies ORDER BY sort_order, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/property-policies")
def create_property_policy(policy: PropertyPolicy):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO property_policies (item, coverage, renewal, sort_order) VALUES (?,?,?,?)",
        (policy.item, policy.coverage, policy.renewal, policy.sort_order)
    )
    conn.commit()
    policy.id = cur.lastrowid
    conn.close()
    return policy

@app.put("/api/property-policies/{policy_id}")
def update_property_policy(policy_id: int, policy: PropertyPolicy):
    conn = get_db()
    conn.execute(
        "UPDATE property_policies SET item=?, coverage=?, renewal=?, sort_order=? WHERE id=?",
        (policy.item, policy.coverage, policy.renewal, policy.sort_order, policy_id)
    )
    conn.commit()
    conn.close()
    return {**policy.dict(), "id": policy_id}

@app.delete("/api/property-policies/{policy_id}")
def delete_property_policy(policy_id: int):
    conn = get_db()
    conn.execute("DELETE FROM property_policies WHERE id=?", (policy_id,))
    conn.commit()
    conn.close()
    return {"deleted": policy_id}

# Planning Inputs
@app.get("/api/planning-inputs")
def get_planning_inputs():
    conn = get_db()
    row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else {}

@app.post("/api/planning-inputs")
@app.put("/api/planning-inputs")
def save_planning_inputs(inputs: PlanningInputs):
    conn = get_db()
    # PlanningInputs allows extra fields (Settings persists many more columns
    # than the model declares — pension_55, ltc_daily, etc.), so data.keys()
    # is influenced by whatever JSON keys the client sent. Whitelist against
    # the table's real columns (from the schema, not the request) before
    # interpolating into SQL — column names can't be parameterized with `?`,
    # so this is what stands between "extra: allow" and SQL injection via a
    # crafted key like `foo); DROP TABLE accounts; --`.
    valid_cols = {r[1] for r in conn.execute("PRAGMA table_info(planning_inputs)").fetchall()}
    data = {k: v for k, v in inputs.model_dump().items() if k in valid_cols}
    keys = ", ".join(data.keys())
    placeholders = ", ".join(["?" for _ in data])
    updates = ", ".join([f"{k}=?" for k in data.keys()])
    conn.execute(
        f"INSERT INTO planning_inputs (id, {keys}) VALUES (1, {placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}",
        list(data.values()) + list(data.values())
    )
    conn.commit()
    conn.close()
    return inputs

# Net Worth
@app.get("/api/net-worth")
def get_net_worth():
    conn = get_db()
    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    from net_worth_engine import compute_net_worth
    result = compute_net_worth(accounts)
    result["accounts"] = accounts
    return result

@app.get("/api/emergency-fund")
def get_emergency_fund():
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from net_worth_engine import emergency_fund_check
    return emergency_fund_check(accounts, dict(inputs_row).get("current_monthly_expenses", 0))

@app.get("/api/rental/analysis")
def get_rental_analysis():
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    inputs = dict(inputs_row)
    from rental_engine import rental_property_analysis
    return rental_property_analysis(accounts, inputs.get("rental_property_key", ""),
                                     inputs.get("expected_return_pre_retirement", 0.07))

# Projections
@app.get("/api/projections/retirement")
def get_retirement_projections():
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    # Full 55-67 range — Retirement.jsx's own buttons only ever look up a
    # subset of these (55-60 one year at a time, plus 65), so the extra
    # ages are harmless there; but this same response also serves as
    # WhatIf.jsx's baseline for its full 55-67 slider, where a narrower
    # range here silently broke the "Impact on Retire at X" comparison
    # for any age outside the original [55,56,57,58,59,60,65] set.
    return run_retirement_projection(dict(inputs_row), accounts, ret_ages=list(range(55, 68)))

@app.get("/api/projections/education")
def get_education_projections(continue_contributions_during_college: bool = False):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    return run_education_projection(dict(inputs_row), accounts, continue_contributions_during_college)

# Quicken Import
@app.post("/api/import/quicken")
async def import_quicken(file: UploadFile = File(...)):
    content = await file.read()
    try:
        text = content.decode('utf-8-sig')
    except Exception:
        text = content.decode('latin-1')
    accounts = parse_quicken_networth_csv(text)
    if not accounts:
        raise HTTPException(status_code=400, detail="No accounts found. Export the Net Worth Summary report from Quicken.")
    conn = get_db()
    updated = created = 0
    for acc in accounts:
        existing = conn.execute(
            "SELECT id FROM accounts WHERE name=? AND account_type=?",
            (acc['name'], acc['account_type'])
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE accounts SET balance=?, notes=?, updated_at=datetime('now') WHERE id=?",
                (acc['balance'], acc['notes'], existing['id'])
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO accounts (name, account_type, owner, institution, balance, notes) VALUES (?,?,?,?,?,?)",
                (acc['name'], acc['account_type'], acc['owner'], acc['institution'], acc['balance'], acc['notes'])
            )
            created += 1
    conn.commit()
    conn.close()
    summary = get_net_worth_summary(accounts)
    return {"status": "success", "accounts_created": created, "accounts_updated": updated,
            "total_accounts": len(accounts), **summary}

# Snapshots
@app.post("/api/snapshot")
def take_snapshot(note_body: SnapshotNote):
    conn = get_db()
    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    from net_worth_engine import compute_net_worth
    nw = compute_net_worth(accounts)
    conn.execute(
        "INSERT INTO snapshots (snapshot_date, total_assets, liabilities, net_worth, accounts_json, note) VALUES (?,?,?,?,?,?)",
        (datetime.now().isoformat(), nw["total_assets"], nw["liabilities"], nw["net_worth"], json.dumps(accounts), note_body.note)
    )
    conn.commit()
    conn.close()
    return {"status": "snapshot saved", "date": datetime.now().isoformat()}

@app.get("/api/snapshots")
def get_snapshots():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, snapshot_date, total_assets, liabilities, net_worth, note FROM snapshots ORDER BY snapshot_date DESC LIMIT 24"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=["."],
        reload_excludes=["venv/*", "*.pyc", "__pycache__/*", "venv"],
    )

# ── Tasks ─────────────────────────────────────────────────────────────────────

class TaskCreate(BaseModel):
    section: str
    title: str
    description: Optional[str] = None

class TaskUpdate(BaseModel):
    completed: bool

@app.get("/api/tasks")
def get_tasks(section: Optional[str] = None):
    conn = get_db()
    if section:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE section=? ORDER BY completed ASC, created_at DESC", (section,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY section, completed ASC, created_at DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/tasks")
def create_task(task: TaskCreate):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO tasks (section, title, description, task_type, recurrence) VALUES (?,?,?,'manual','once')",
        (task.section, task.title, task.description)
    )
    conn.commit()
    task_id = cur.lastrowid
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    return dict(row)

@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, update: TaskUpdate):
    conn = get_db()
    completed_date = datetime.now().isoformat() if update.completed else None
    conn.execute(
        "UPDATE tasks SET completed=?, completed_date=?, updated_at=datetime('now') WHERE id=?",
        (1 if update.completed else 0, completed_date, task_id)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    return dict(row)

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE id=? AND task_type='manual'", (task_id,))
    conn.commit()
    conn.close()
    return {"deleted": task_id}

@app.post("/api/tasks/sync")
def sync_tasks():
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row:
        return {"inserted": 0, "message": "No planning inputs yet"}
    inputs = dict(inputs_row)
    try:
        projections = run_retirement_projection(inputs, accounts)
        education   = run_education_projection(inputs, accounts)
    except Exception:
        projections = {}
        education   = {}
    conn = get_db()
    inserted = sync_auto_tasks(conn, accounts, inputs, projections, education)
    conn.close()
    return {"inserted": inserted, "message": f"Synced — {inserted} new tasks added"}

@app.get("/api/projections/kids")
def get_kids_projections():
    conn = get_db()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    conn.close()
    from projection_engine import run_kids_projection
    inputs = dict(inputs_row) if inputs_row else {}
    return run_kids_projection(accounts, inputs)

@app.get("/api/projections/insurance")
def get_insurance_analysis():
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from projection_engine import run_insurance_analysis
    return run_insurance_analysis(dict(inputs_row), accounts)

# ── Annual Report ─────────────────────────────────────────────────────────────
from fastapi.responses import Response

@app.get("/api/report/annual")
def generate_annual_report():
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()

    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")

    inputs = dict(inputs_row)
    from projection_engine import (run_retirement_projection, run_education_projection,
                                   run_kids_projection, run_insurance_analysis)
    from report_generator import generate_annual_report
    from net_worth_engine import compute_net_worth

    # Build net worth summary — same shared bucketing as /api/net-worth and
    # /api/snapshot use, so the report's Net Worth Summary can't silently
    # diverge from the Dashboard/NetWorth pages the way it used to (this
    # used to be a separately-typed categories dict missing 529/DAF).
    nw = compute_net_worth(accounts)
    nw["accounts"] = accounts

    data = {
        "net_worth":  nw,
        "retirement": run_retirement_projection(inputs, accounts),
        "education":  run_education_projection(inputs, accounts),
        "kids":       run_kids_projection(accounts, inputs),
        "insurance":  run_insurance_analysis(inputs, accounts),
        "names": {
            "person1": inputs.get("person1_name", "Person 1"),
            "person2": inputs.get("person2_name", "Person 2"),
            "kid1": inputs.get("kid1_name", "Child 1"),
            "kid2": inputs.get("kid2_name", "Child 2"),
        },
    }

    pdf_bytes = generate_annual_report(data)
    filename  = f"Financial_Dashboard_{datetime.now().strftime('%Y_%m_%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

# ── Simulation endpoints ───────────────────────────────────────────────────────

@app.get("/api/simulation/monte-carlo")
def get_monte_carlo(ret_age: int = 60, ss_timing: str = "early"):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from simulation_engine import run_monte_carlo
    return run_monte_carlo(dict(inputs_row), accounts, ret_age, ss_timing)

@app.post("/api/projections/whatif")
def get_whatif(body: dict):
    """Run retirement projection with modified assumptions."""
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}

    from projection_engine import run_retirement_projection
    inputs = dict(inputs_row)

    # Apply what-if overrides
    ret_age      = body.get("ret_age", 60)
    salary_growth_pct = body.get("salary_growth_pct", 0.0)
    pension_mult = body.get("pension_mult", 1.0)
    ss_mult      = body.get("ss_mult", 1.0)

    inputs["expected_return_pre_retirement"]  = body.get("pre_return",  inputs["expected_return_pre_retirement"])
    inputs["expected_return_post_retirement"] = body.get("post_return", inputs["expected_return_post_retirement"])
    inputs["inflation_rate"]                  = body.get("inflation",   inputs["inflation_rate"])
    inputs["healthcare_pre_medicare"]         = body.get("healthcare_pre", inputs.get("healthcare_pre_medicare", 0))
    inputs["retirement_income_today_dollars"] = body.get("income_target",  inputs["retirement_income_today_dollars"])
    inputs["bridge_income_55"]                = body.get("bridge_income",  inputs.get("bridge_income_55", 0))

    if pension_mult != 1.0:
        inputs["pension_55"] = inputs.get("pension_55", 0) * pension_mult
        inputs["pension_60"] = inputs.get("pension_60", 0) * pension_mult
        inputs["pension_65"] = inputs.get("pension_65", 0) * pension_mult
    if ss_mult != 1.0:
        inputs["jason_social_security"] = inputs.get("jason_social_security", 0) * ss_mult
        inputs["jason_ss_delayed"]      = inputs.get("jason_ss_delayed", 0) * ss_mult
        inputs["justin_social_security"]= inputs.get("justin_social_security", 0) * ss_mult

    # Always include the 55/60/65 baseline ages (the "Surplus Across All
    # Retirement Ages" panel shows those three fixed cards regardless of
    # slider position) plus whichever age the slider is actually on — the
    # slider now covers the full 55-67 range, so a default-only [55,60,65]
    # silently left the "Impact on Retire at X" panel empty for any other
    # age (the reported "nothing appears" bug, same root cause as the
    # Monte Carlo income-sources gap fixed alongside this).
    ret_ages = sorted({55, 60, 65, ret_age})
    return run_retirement_projection(inputs, accounts, ret_ages=ret_ages, salary_growth_pct=salary_growth_pct)

@app.get("/api/simulation/swr-batch")
def get_swr_batch():
    """Compute SWR for all retirement ages 55-67 in one call."""
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from simulation_engine import run_swr_analysis
    results = {}
    for ret_age in range(55, 68):
        try:
            r = run_swr_analysis(dict(inputs_row), accounts, ret_age=ret_age, ss_timing="early")
            results[ret_age] = r
        except Exception as e:
            results[ret_age] = {"error": str(e)}
    return {"swr": results}

@app.get("/api/projections/retirement-sensitivity")
def get_retirement_sensitivity():
    """Pre-compute retirement projections for all ages 55-67.

    Delegates entirely to run_retirement_projection() — the same engine every
    other page uses — instead of reimplementing the math. This used to be a
    separate calculation that had drifted from the canonical one (missing the
    bridge-job phasing at 55, and hardcoding Justin's SS claiming age instead
    of reading it from Settings). See CLAUDE.md / conversation history.
    """
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}

    from projection_engine import run_retirement_projection

    inputs = dict(inputs_row)
    jason_age = inputs["jason_age"]
    proj = run_retirement_projection(inputs, accounts, ret_ages=list(range(55, 68)))

    # This page has no early/delayed SS toggle (unlike Retirement.jsx), so we
    # show the "early" scenario at every age — same assumption the old code
    # made, but now derived from the real engine instead of duplicated math.
    results = [{
        "ret_age":            s["retirement_age"],
        "years_to_retire":    s["years_to_retirement"],
        "portfolio":          s["portfolio_at_retirement"],
        "cap_need":           s["total_capitalized_need"],
        "cap_income":         s["capitalized_income_sources"],
        "needed_from_assets": s["capitalized_needed_from_assets"],
        "surplus":            s["projected_surplus"],
        "on_track":           s["on_track"],
        "pension_annual":     s["pension_annual"],
        "healthcare_gap_yrs": s["healthcare_gap_years"],
    } for s in proj["scenarios"] if s["ss_timing"] == "early"]

    return {"sensitivity": results, "jason_current_age": jason_age}

@app.get("/api/retirement/income-sources")
def get_income_sources(ret_age: int = 60, ss_timing: str = "early"):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from projection_engine import run_retirement_projection
    # ret_ages defaults to [55, 60, 65] — must pass the requested age
    # explicitly, since the Monte Carlo/Historical Stress tabs now offer
    # the full 55-67 range and any age outside the default 3 would
    # otherwise silently compute nothing, leaving this chart empty.
    result = run_retirement_projection(dict(inputs_row), accounts, ret_ages=[ret_age])
    scenario = next((s for s in result["scenarios"] if s["label"] == f"age_{ret_age}_{ss_timing}"), None)
    if not scenario: return {"error": "Scenario not found"}
    # Return simplified chart data
    chart = []
    for y in scenario["yearly_detail"]:
        chart.append({
            "age":          y["jason_age"],
            "pension":      y["pension"],
            "social_security": y["social_security"],
            "bridge_income":   y.get("bridge_income", 0),
            "portfolio_draw":  y["withdrawal"],
            "total_need":      y["income_need"],
        })
    return {"chart": chart, "label": scenario["label"]}

@app.get("/api/simulation/tax-efficiency")
def get_tax_efficiency(ret_age: int = 60, ss_timing: str = "early"):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from simulation_engine import run_tax_efficiency_simulation
    return run_tax_efficiency_simulation(dict(inputs_row), accounts, ret_age, ss_timing)

@app.get("/api/simulation/contribution-sensitivity")
def get_contribution_sensitivity(ret_age: int = 60):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from simulation_engine import run_contribution_sensitivity
    return run_contribution_sensitivity(dict(inputs_row), accounts, ret_age)

@app.get("/api/simulation/survivor-scenario")
def get_survivor_scenario(ret_age: int = 60, deceased: str = "jason", death_age: int = None,
                           survivor_need_factor: float = 0.75):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from simulation_engine import run_survivor_scenario
    return run_survivor_scenario(dict(inputs_row), accounts, ret_age, deceased, death_age, survivor_need_factor)

@app.get("/api/simulation/sequence-risk")
def get_sequence_risk(ret_age: int = 55, ss_timing: str = "early"):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from simulation_engine import run_stress_tests
    result = run_stress_tests(dict(inputs_row), accounts, ret_age, ss_timing)
    # Return only the new scenarios
    return {
        "early_sequence":  result["scenarios"].get("early_sequence"),
        "bridge_job_loss": result["scenarios"].get("bridge_job_loss"),
        "ss_reduction":    result["scenarios"].get("ss_reduction"),
        "base":            result["scenarios"].get("base"),
    }

@app.get("/api/simulation/roth-conversion")
def get_roth_conversion(ret_age: int = 60, ss_timing: str = "early"):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from simulation_engine import run_roth_conversion_analysis
    return run_roth_conversion_analysis(dict(inputs_row), accounts, ret_age, ss_timing)

@app.get("/api/simulation/swr")
def get_swr(ret_age: int = 60, ss_timing: str = "early"):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from simulation_engine import run_swr_analysis
    return run_swr_analysis(dict(inputs_row), accounts, ret_age, ss_timing)

@app.get("/api/simulation/stress-tests")
def get_stress_tests(ret_age: int = 60, ss_timing: str = "early"):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from simulation_engine import run_stress_tests
    return run_stress_tests(dict(inputs_row), accounts, ret_age, ss_timing)
