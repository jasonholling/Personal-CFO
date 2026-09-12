from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response as FastAPIResponse
from pydantic import BaseModel, field_validator
from typing import Optional, List, Dict
import sqlite3
import json
from datetime import datetime
from projection_engine import run_retirement_projection, run_education_projection, CALCULATION_ENGINE_VERSION
from task_engine import sync_auto_tasks
from cfo_briefing_engine import build_cfo_briefing
from cash_flow_engine import summarize_cash_flow
from life_event_engine import summarize_life_events
from confidence_engine import plan_confidence
from quicken_importer import parse_quicken_networth_csv, get_net_worth_summary
from db import init_db, get_db
import auth

app = FastAPI(title="Personal CFO API")

# "kids" added 2026-09-09 (external audit follow-up, kids-variable-count)
# -- a backup taken before this table existed omits it entirely, same as
# any other table added after export_backup/restore_backup shipped; the
# gap this closes is going forward, not retroactive to already-taken
# backups. Restoring a backup that predates this line will (correctly,
# per restore_backup's own "every table in _BACKUP_TABLES must be
# present" check below) reject with a missing-table error rather than
# silently restoring every table except kids and leaving whatever kids
# existed before the restore untouched -- which would have looked like
# a successful restore while actually leaving that one table stale.
# "estate_beneficiaries" added 2026-09-09 (external audit follow-up, P1)
# -- Estate.jsx's beneficiary-designation table was localStorage-only
# from the start; now backed by a real table (see its own schema
# comment in db.py), same "predates this line -> rejected, not silently
# incomplete" behavior as kids above applies to it too.
# "holdings"/"investment_policies"/"securities"/"security_snapshots"/
# "recommendations"/"recommendation_events" added 2026-09-11 (Portfolio
# Coach, codex/portfolio-coach-recommendations) -- same "predates this
# line -> rejected, not silently incomplete" precedent as every earlier
# table added to this list.
_BACKUP_TABLES = ("accounts", "planning_inputs", "insurance_policies", "property_policies", "snapshots", "tasks", "cash_flow_items", "surplus_allocations", "saved_scenarios", "life_events", "estate_documents", "estate_beneficiaries", "assumption_reviews", "kids", "holdings", "investment_policies", "securities", "security_snapshots", "recommendations", "recommendation_events")

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
    # Portfolio Coach (codex/portfolio-coach-recommendations) — nullable
    # override of holdings_engine.resolve_portfolio_account_type's own
    # legacy-account_type mapping. None (every existing account) means
    # "derive it from account_type."
    portfolio_account_type: Optional[str] = None

    @field_validator("portfolio_account_type")
    @classmethod
    def _portfolio_account_type_must_be_known(cls, v):
        if v is None:
            return v
        from holdings_engine import PORTFOLIO_ACCOUNT_TYPES
        if v not in PORTFOLIO_ACCOUNT_TYPES:
            raise ValueError(f"Unknown portfolio_account_type '{v}' — must be one of {sorted(PORTFOLIO_ACCOUNT_TYPES)} or unset")
        return v

    # account_type used to be an unchecked str: a value outside
    # net_worth_engine.VALID_ACCOUNT_TYPES (a typo, a legacy value, a name
    # that reads as plausible but isn't a real dropdown option — e.g.
    # "pretax_401k" instead of "401k") wasn't rejected here, it just went
    # on to be silently invisible to net worth, and to every retirement/
    # Monte Carlo/stress-test number (projection_engine.py's bucketing has
    # no fallback at all), while still showing up normally on the Accounts
    # page — found via a bug-hunt sandbox where exactly that mistake
    # dropped $650K from both net worth and a retirement projection with
    # no error anywhere. Reject it at the door instead.
    @field_validator("account_type")
    @classmethod
    def _account_type_must_be_known(cls, v):
        from net_worth_engine import VALID_ACCOUNT_TYPES
        if v not in VALID_ACCOUNT_TYPES:
            raise ValueError(f"Unknown account_type '{v}' — must be one of {sorted(VALID_ACCOUNT_TYPES)}")
        return v

class CashFlowItem(BaseModel):
    id: Optional[int] = None
    name: str
    cash_flow_type: str
    category: str = "Other"
    amount: float = 0
    essential: bool = False
    notes: Optional[str] = None

class SurplusAllocation(BaseModel):
    goal: str
    monthly_amount: float = 0
    notes: Optional[str] = None

# Portfolio Coach (codex/portfolio-coach-recommendations). Decision
# support only.
class Holding(BaseModel):
    id: Optional[int] = None
    account_id: int
    ticker: Optional[str] = None
    security_name: str
    provider_identifier: Optional[str] = None
    exchange: Optional[str] = None
    security_type: Optional[str] = None
    shares: Optional[float] = None
    market_value: float
    asset_class: str = "unclassified"
    expense_ratio: Optional[float] = None
    cost_basis: Optional[float] = None
    as_of_date: Optional[str] = None
    data_source: str = "manual"
    confidence: str = "low"
    notes: Optional[str] = None

    @field_validator("asset_class")
    @classmethod
    def _asset_class_must_be_known(cls, v):
        from holdings_engine import ASSET_CLASSES
        if v not in ASSET_CLASSES:
            raise ValueError(f"Unknown asset_class '{v}' — must be one of {ASSET_CLASSES}")
        return v

    @field_validator("market_value")
    @classmethod
    def _market_value_non_negative(cls, v):
        if v < 0:
            raise ValueError("market_value cannot be negative")
        return v

class HoldingImportRow(BaseModel):
    """Deliberately NOT the Holding model — asset_class isn't validated
    here so an invalid preview row can round-trip through preview ->
    edit -> commit without a 422 before the user gets a chance to fix
    it (commit re-validates server-side before writing)."""
    account_id: int
    ticker: Optional[str] = None
    security_name: str
    shares: Optional[float] = None
    market_value: float
    asset_class: str
    expense_ratio: Optional[float] = None
    cost_basis: Optional[float] = None
    notes: Optional[str] = None

class InvestmentPolicy(BaseModel):
    id: Optional[int] = None
    name: str = "Household Policy"
    target_us_large_cap_pct: float = 0
    target_us_mid_cap_pct: float = 0
    target_us_small_cap_pct: float = 0
    target_international_developed_pct: float = 0
    target_emerging_markets_pct: float = 0
    target_us_bonds_pct: float = 0
    target_international_bonds_pct: float = 0
    target_cash_pct: float = 0
    target_real_estate_pct: float = 0
    target_alternatives_pct: float = 0
    drift_band_pct: float = 5
    max_single_security_pct: Optional[float] = None
    minimum_cash_reserve: float = 0
    rebalance_cadence: str = "annual"
    use_contributions_before_sales: bool = True
    taxable_sale_preference: Optional[str] = None
    excluded_accounts: List[int] = []
    excluded_holdings: List[int] = []
    employer_stock_exceptions: List[Dict] = []
    legacy_holding_exceptions: List[Dict] = []
    risk_profile: Optional[str] = None
    account_constraints: List[Dict] = []
    effective_date: Optional[str] = None
    review_date: Optional[str] = None
    notes: Optional[str] = None

class PortfolioContributionRequest(BaseModel):
    amount: float = 0

class SecurityConfirmRequest(BaseModel):
    provider_identifier: Optional[str] = None
    ticker: Optional[str] = None
    security_name: str
    exchange: Optional[str] = None
    currency: Optional[str] = None
    security_type: Optional[str] = None
    status: str = "active"
    asset_class: Optional[str] = None
    asset_class_source: Optional[str] = None  # "provider" | "user_supplied"

class RecommendationDecision(BaseModel):
    status: str  # reviewing | accepted | deferred | rejected | completed
    notes: Optional[str] = None
    reason: Optional[str] = None
    resulting_allocation: Optional[Dict] = None

class ScenarioSave(BaseModel):
    name: str
    retirement_age: int = 60
    # Previously unset — save_scenario() always projected against the
    # "early" SS scenario regardless of what the user had selected
    # elsewhere in the app, with no field to say otherwise (external audit
    # 2026-09-07, finding #15). Matches the "early"/"delayed" vocabulary
    # used everywhere else (SS_OPTS in Simulation.jsx, utils/scenario.js).
    ss_timing: str = "early"
    # Milestone 1 (2026-09-09): optional explicit claim-age overrides, the
    # same 3-tier concept as everywhere else in the app (CALCULATION_CONTRACT
    # section 54) — when a caller has one active (e.g. the Retirement page's
    # useScenario() state), pass it through so the saved scenario's
    # resolved assumptions match what was actually on screen, not just the
    # early/delayed pair every scenario always had.
    jason_ss_claim_age: Optional[int] = None
    justin_ss_claim_age: Optional[int] = None
    # Optional — only meaningful for a scenario saved from a stochastic
    # result (Monte Carlo). Not required for the point-in-time projection
    # this endpoint runs itself; present so a future Monte Carlo "save this
    # scenario" call site can pass through what actually produced its
    # numbers, per Milestone 1's reproducibility requirement.
    seed: Optional[int] = None
    trial_count: Optional[int] = None
    # Milestone 1 acceptance follow-up (2026-09-09): "must not silently
    # rerun against different current Settings." When the caller already
    # has a live displayed result (Retirement.jsx does), it passes both of
    # these straight from what's on screen -- summary is stored verbatim
    # (no recompute), and household_data is the exact resolved_assumptions
    # bundle GET /api/projections/retirement returned for that same
    # result. Omitting both falls back to the original recompute-from-
    # current-DB-state behavior, for callers with no live result to pin to
    # (e.g. SavedScenarios.jsx's own "create by parameters" form).
    summary: Optional[Dict] = None
    household_data: Optional[Dict] = None

class LifeEvent(BaseModel):
    name: str
    event_type: str = "other"
    event_year: int
    one_time_cash_delta: float = 0
    monthly_cash_flow_delta: float = 0
    duration_months: int = 0
    notes: Optional[str] = None
    included_in_projection: bool = True
    # Nullable — when set, this event models a one-time lump-sum extra
    # payment toward a SPECIFIC debt account (see POST /api/life-events'
    # validation) instead of cash landing in the taxable investment
    # bucket. None/absent is the historical behavior.
    target_debt_account_id: Optional[int] = None

class EstateDocument(BaseModel):
    document_type: str
    status: str = "verify"
    reviewed_on: Optional[str] = None
    next_review_on: Optional[str] = None
    location_hint: Optional[str] = None
    notes: Optional[str] = None

# External audit follow-up, 2026-09-09 (P1) — the beneficiary-designation
# side of Estate.jsx (account -> primary/contingent beneficiary) was
# localStorage-only, never backed by any table, so a fresh browser or a
# restored backup lost those records entirely (a Backup export only ever
# covers real database tables). account_key is the same kind of stable,
# non-display identifier as document_type above (e.g. "kid_7roth", not
# the kid's renameable display name) -- see Estate.jsx's own comment on
# why beneficiary rows must survive a kid rename.
class EstateBeneficiary(BaseModel):
    account_key: str
    primary_beneficiary: Optional[str] = None
    contingent_beneficiary: Optional[str] = None

class AssumptionReview(BaseModel):
    label: str = "Assumption review"
    assumptions: dict

@app.get("/api/backup/export")
def export_backup():
    """Download only Personal CFO's local planning data as portable JSON."""
    conn = get_db()
    payload = {"format": "personal-cfo-backup", "version": 1, "exported_at": datetime.now().isoformat(),
               "tables": {table: [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()] for table in _BACKUP_TABLES}}
    conn.close()
    return FastAPIResponse(content=json.dumps(payload), media_type="application/json",
                           headers={"Content-Disposition": "attachment; filename=personal-cfo-backup.json"})

@app.post("/api/backup/restore")
async def restore_backup(file: UploadFile = File(...), confirm: bool = False):
    """Replace app planning data only after an explicit client confirmation."""
    if not confirm:
        raise HTTPException(status_code=400, detail="Restore requires explicit confirmation")
    try:
        payload = json.loads((await file.read()).decode("utf-8"))
        if payload.get("format") != "personal-cfo-backup" or not isinstance(payload.get("tables"), dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="This is not a valid Personal CFO backup")
    tables = payload["tables"]
    if any(table not in _BACKUP_TABLES or not isinstance(rows, list) for table, rows in tables.items()):
        raise HTTPException(status_code=400, detail="Backup contains an invalid table")
    # A legitimate backup (from this app's own /api/backup/export) always
    # includes every table in _BACKUP_TABLES, even ones with zero rows —
    # that's how the export is built. Validation used to only check that
    # tables *present* in the payload were known table names, so a
    # structurally-valid-but-incomplete payload (missing keys entirely, in
    # the extreme case an empty {}) passed straight through to a DELETE
    # over every real table followed by re-inserting only what little (or
    # nothing) the payload actually contained — a restore that "succeeds"
    # while silently erasing every table the backup didn't happen to
    # mention (external audit 2026-09-07, reproduced with literally
    # {"tables": {}} -> {"ok": true} and an empty accounts/planning_inputs
    # table afterward). Require every table to be present as a key
    # (an empty list for a genuinely-empty table is fine and expected —
    # only a MISSING key indicates a malformed/partial file) before
    # allowing anything to be deleted.
    missing = [t for t in _BACKUP_TABLES if t not in tables]
    if missing:
        raise HTTPException(status_code=400,
            detail=f"Backup is missing expected table(s): {', '.join(missing)} — refusing to restore an incomplete backup")
    conn = get_db()
    try:
        conn.execute("BEGIN")
        for table in _BACKUP_TABLES:
            conn.execute(f"DELETE FROM {table}")
        for table, rows in tables.items():
            for row in rows:
                if not isinstance(row, dict): raise ValueError
                columns = [key for key in row if key.replace("_", "").isalnum()]
                if columns:
                    conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [row[key] for key in columns])
        conn.commit()
    except Exception:
        conn.rollback(); raise HTTPException(status_code=400, detail="Backup could not be restored")
    finally:
        conn.close()
    return {"ok": True}

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
    annual_bonus_pct: float = 0
    mortgage_balance: float = 0
    # Second-earner support (2026-09-08) — see run_retirement_projection's
    # own docstring and docs/CALCULATION_CONTRACT.md section 13 (backlog
    # item 7): these persisted correctly before via extra:allow + the
    # save route's DB-column whitelist, same as most Settings fields, but
    # were never declared here, weakening request validation/API docs for
    # this specific feature. All default to 0/unset, matching db.py.
    justin_w2_salary: float = 0
    justin_employee_401k_pct: float = 0.06
    justin_employer_401k_pct: float = 0.03
    justin_annual_bonus_pct: float = 0
    justin_annual_rsu_value: float = 0
    justin_ret_age: int = 0
    # Social Security claiming age 62-70 (2026-09-08, CALCULATION_
    # CONTRACT.md section 44, milestone 6): jason_ss_claim_age/
    # justin_ss_claim_age are Optional and default to None -- "not set,
    # use the existing early(62)/delayed(67) ss_timing toggle" -- an
    # existing household sees no behavior change until it explicitly
    # sets one. jason_ss_70/justin_ss_early/justin_ss_70 are the real
    # dollar anchors from a household's own SSA.gov statement (Jason
    # already has jason_social_security/jason_ss_delayed for 62/67).
    jason_ss_claim_age: Optional[int] = None
    justin_ss_claim_age: Optional[int] = None
    jason_ss_70: float = 0
    justin_ss_early: float = 0
    justin_ss_70: float = 0

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

# Kids-variable-count (2026-09-09) — replaced the old fixed kid1_name/
# kid2_name/kid1_age/kid2_age/abby_529_monthly/cooper_529_monthly
# planning_inputs columns (always exactly 2 kids) with a real table, 0-5
# rows. See db.py's kids table + migrate_legacy_kids for how an existing
# household's real data converts over automatically, and
# projection_engine.py's is_kid_owner for how account ownership works
# now (f"kid_{id}" instead of a literal name like "abby").
class Kid(BaseModel):
    id: Optional[int] = None
    name: str
    age: int = 0
    monthly_529: float = 0
    display_order: int = 0

MAX_KIDS = 5

# Accounts
@app.get("/api/accounts")
def get_accounts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM accounts ORDER BY account_type, owner").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/accounts/freshness")
def get_account_freshness():
    """Expose balance freshness without changing account data."""
    conn = get_db()
    accounts = [dict(r) for r in conn.execute("SELECT id, name, account_type, owner, balance, updated_at FROM accounts").fetchall()]
    conn.close()
    now = datetime.now()
    stale = []
    for account in accounts:
        try:
            updated = datetime.fromisoformat(account["updated_at"])
            age_days = max(0, (now - updated).days)
        except (TypeError, ValueError):
            age_days = None
        account["age_days"] = age_days
        if age_days is None or age_days > 35:
            stale.append(account)

    # Possible-duplicate detection: /api/import/quicken matches an existing
    # account by (name, account_type) — if a re-import ever maps the same
    # real-world account to a different account_type than before (a mapping
    # change, a typo fix, etc.), the old row is never updated again and just
    # sits there aging instead of being replaced, while a fresh new row
    # quietly takes over. The >35-day staleness check above would eventually
    # catch the orphaned original, but only after 35 days of silently
    # showing a duplicated balance in net worth in the meantime. Surface
    # same-name groups immediately instead of waiting on staleness alone.
    by_name: Dict[str, List[dict]] = {}
    for account in accounts:
        key = account["name"].strip().lower()
        by_name.setdefault(key, []).append(account)
    possible_duplicates = [group for group in by_name.values() if len(group) > 1]

    return {"account_count": len(accounts), "stale_count": len(stale), "stale_accounts": stale,
            "fresh": len(stale) == 0 and not possible_duplicates,
            "possible_duplicate_accounts": possible_duplicates}

@app.post("/api/accounts")
def create_account(account: Account):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO accounts (name, account_type, owner, institution, balance, notes, interest_rate, minimum_payment, term_months, stock_allocation_pct, expense_ratio, monthly_rental_income, monthly_rental_expenses, portfolio_account_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (account.name, account.account_type, account.owner, account.institution, account.balance, account.notes,
         account.interest_rate, account.minimum_payment, account.term_months, account.stock_allocation_pct, account.expense_ratio,
         account.monthly_rental_income, account.monthly_rental_expenses, account.portfolio_account_type)
    )
    conn.commit()
    account.id = cur.lastrowid
    conn.close()
    return account

@app.put("/api/accounts/{account_id}")
def update_account(account_id: int, account: Account):
    conn = get_db()
    conn.execute(
        "UPDATE accounts SET name=?, account_type=?, owner=?, institution=?, balance=?, notes=?, interest_rate=?, minimum_payment=?, term_months=?, stock_allocation_pct=?, expense_ratio=?, monthly_rental_income=?, monthly_rental_expenses=?, portfolio_account_type=? WHERE id=?",
        (account.name, account.account_type, account.owner, account.institution, account.balance, account.notes,
         account.interest_rate, account.minimum_payment, account.term_months, account.stock_allocation_pct, account.expense_ratio,
         account.monthly_rental_income, account.monthly_rental_expenses, account.portfolio_account_type, account_id)
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

# ══════════════════════════════════════════════════════════════════════
# Portfolio Coach (codex/portfolio-coach-recommendations). Decision
# support only — see holdings_engine.py/coach_engine.py's own module
# docstrings. Holdings entry/import, the security lookup adapter, the
# investment policy, allocation reads, the two named workflows, the
# recommendation decision lifecycle, and planning integration.
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/holdings")
def get_holdings():
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM holdings ORDER BY account_id, security_name").fetchall()]
    conn.close()
    return rows

@app.get("/api/holdings/grouped")
def get_holdings_grouped():
    conn = get_db()
    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    holdings = [dict(r) for r in conn.execute("SELECT * FROM holdings ORDER BY security_name").fetchall()]
    conn.close()
    from holdings_engine import resolve_portfolio_account_type, reconcile_account_holdings, is_allocation_blocked
    by_account: Dict[int, List[Dict]] = {}
    for h in holdings:
        by_account.setdefault(h["account_id"], []).append(h)
    groups = []
    for acc in accounts:
        acc_holdings = by_account.get(acc["id"], [])
        ptype = resolve_portfolio_account_type(acc)
        groups.append({
            "account_id": acc["id"], "account_name": acc["name"], "account_type": acc["account_type"],
            "owner": acc["owner"], "portfolio_account_type": ptype, "allocation_blocked": is_allocation_blocked(ptype),
            "holdings": acc_holdings, **reconcile_account_holdings(acc, acc_holdings),
        })
    return {"groups": groups}

@app.post("/api/holdings")
def create_holding(holding: Holding):
    conn = get_db()
    acc = conn.execute("SELECT id FROM accounts WHERE id=?", (holding.account_id,)).fetchone()
    if not acc:
        conn.close()
        raise HTTPException(status_code=400, detail=f"account_id {holding.account_id} does not exist")
    cur = conn.execute(
        "INSERT INTO holdings (account_id, ticker, security_name, provider_identifier, exchange, security_type, shares, "
        "market_value, asset_class, expense_ratio, cost_basis, as_of_date, data_source, confidence, notes, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
        (holding.account_id, holding.ticker, holding.security_name, holding.provider_identifier, holding.exchange,
         holding.security_type, holding.shares, holding.market_value, holding.asset_class, holding.expense_ratio,
         holding.cost_basis, holding.as_of_date, holding.data_source, holding.confidence, holding.notes)
    )
    conn.commit()
    holding.id = cur.lastrowid
    conn.close()
    return holding

@app.put("/api/holdings/{holding_id}")
def update_holding(holding_id: int, holding: Holding):
    conn = get_db()
    acc = conn.execute("SELECT id FROM accounts WHERE id=?", (holding.account_id,)).fetchone()
    if not acc:
        conn.close()
        raise HTTPException(status_code=400, detail=f"account_id {holding.account_id} does not exist")
    conn.execute(
        "UPDATE holdings SET account_id=?, ticker=?, security_name=?, provider_identifier=?, exchange=?, security_type=?, "
        "shares=?, market_value=?, asset_class=?, expense_ratio=?, cost_basis=?, as_of_date=?, data_source=?, confidence=?, "
        "notes=?, updated_at=datetime('now') WHERE id=?",
        (holding.account_id, holding.ticker, holding.security_name, holding.provider_identifier, holding.exchange,
         holding.security_type, holding.shares, holding.market_value, holding.asset_class, holding.expense_ratio,
         holding.cost_basis, holding.as_of_date, holding.data_source, holding.confidence, holding.notes, holding_id)
    )
    conn.commit()
    conn.close()
    return {**holding.dict(), "id": holding_id}

@app.delete("/api/holdings/{holding_id}")
def delete_holding(holding_id: int):
    conn = get_db()
    conn.execute("DELETE FROM holdings WHERE id=?", (holding_id,))
    conn.commit()
    conn.close()
    return {"deleted": holding_id}

@app.post("/api/holdings/import/preview")
async def preview_holdings_import(file: UploadFile = File(...)):
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except Exception:
        text = content.decode("latin-1")
    conn = get_db()
    valid_account_ids = {r[0] for r in conn.execute("SELECT id FROM accounts").fetchall()}
    conn.close()
    from holdings_engine import parse_holdings_csv
    return parse_holdings_csv(text, valid_account_ids)

@app.post("/api/holdings/import/commit")
def commit_holdings_import(rows: List[HoldingImportRow]):
    from holdings_engine import ASSET_CLASSES
    conn = get_db()
    valid_account_ids = {r[0] for r in conn.execute("SELECT id FROM accounts").fetchall()}
    created = 0
    skipped = []
    for row in rows:
        if row.account_id not in valid_account_ids:
            skipped.append({"security_name": row.security_name, "reason": f"account_id {row.account_id} does not exist"})
            continue
        if row.asset_class not in ASSET_CLASSES:
            skipped.append({"security_name": row.security_name, "reason": f"asset_class '{row.asset_class}' is not one of {sorted(ASSET_CLASSES)}"})
            continue
        conn.execute(
            "INSERT INTO holdings (account_id, ticker, security_name, shares, market_value, asset_class, expense_ratio, "
            "cost_basis, notes, data_source, confidence, updated_at) VALUES (?,?,?,?,?,?,?,?,?,'manual','low',datetime('now'))",
            (row.account_id, row.ticker, row.security_name, row.shares, row.market_value, row.asset_class,
             row.expense_ratio, row.cost_basis, row.notes)
        )
        created += 1
    conn.commit()
    conn.close()
    return {"created": created, "skipped": skipped}

# ── Security lookup (Milestone 2) ────────────────────────────────────────
# Never called directly from the browser for the actual provider request
# — the frontend only ever talks to these backend endpoints, which hold
# any real provider credential server-side (an env var, never committed;
# no live provider is configured on this branch, see security_provider.py).

@app.get("/api/securities/search")
def search_securities(q: str = ""):
    from security_provider import get_active_provider
    from dataclasses import asdict
    candidates = get_active_provider().search(q)
    return {"candidates": [asdict(c) for c in candidates]}

@app.post("/api/securities/confirm")
def confirm_security(body: SecurityConfirmRequest):
    """User confirms one search candidate (or supplies metadata
    manually) — stores/updates the canonical securities row and returns
    its id. Does NOT create a holding; the frontend uses the returned
    security to populate a holding create/update call."""
    conn = get_db()
    existing = None
    if body.provider_identifier:
        existing = conn.execute("SELECT id FROM securities WHERE provider_identifier=?", (body.provider_identifier,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE securities SET ticker=?, security_name=?, exchange=?, currency=?, security_type=?, status=?, "
            "asset_class=?, asset_class_source=?, updated_at=datetime('now') WHERE id=?",
            (body.ticker, body.security_name, body.exchange, body.currency, body.security_type, body.status,
             body.asset_class, body.asset_class_source, existing["id"])
        )
        security_id = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO securities (provider_identifier, ticker, security_name, exchange, currency, security_type, "
            "status, asset_class, asset_class_source) VALUES (?,?,?,?,?,?,?,?,?)",
            (body.provider_identifier, body.ticker, body.security_name, body.exchange, body.currency,
             body.security_type, body.status, body.asset_class, body.asset_class_source)
        )
        security_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"security_id": security_id, **body.dict()}

@app.get("/api/securities/{security_id}/quote")
def get_security_quote(security_id: int, holding_id: Optional[int] = None):
    """Fetches a quote ONLY when requested (never automatically) and
    stores a DATED snapshot tied to the specific holding it was fetched
    for, so a later price change never silently rewrites what a past
    recommendation was actually based on."""
    conn = get_db()
    security = conn.execute("SELECT * FROM securities WHERE id=?", (security_id,)).fetchone()
    if not security:
        conn.close()
        raise HTTPException(status_code=404, detail="Security not found")
    from security_provider import get_active_provider
    from dataclasses import asdict
    quote = get_active_provider().get_quote(security["provider_identifier"]) if security["provider_identifier"] else None
    if quote is None:
        conn.close()
        return {"quote": None, "message": "No quote available for this security."}
    cur = conn.execute(
        "INSERT INTO security_snapshots (security_id, holding_id, price, as_of_date, data_source, raw_json) VALUES (?,?,?,?,?,?)",
        (security_id, holding_id, quote.price, quote.as_of, quote.data_source, json.dumps(asdict(quote)))
    )
    conn.commit()
    snapshot_id = cur.lastrowid
    conn.close()
    return {"quote": asdict(quote), "snapshot_id": snapshot_id}

# ── Investment policy (Milestone 3) ──────────────────────────────────────

_POLICY_JSON_LIST_FIELDS = (
    "account_constraints", "excluded_accounts", "excluded_holdings",
    "employer_stock_exceptions", "legacy_holding_exceptions",
)

def _policy_row_to_dict(row) -> Dict:
    d = dict(row)
    for field in _POLICY_JSON_LIST_FIELDS:
        d[field] = json.loads(d.pop(f"{field}_json") or "[]")
    d["use_contributions_before_sales"] = bool(d["use_contributions_before_sales"])
    return d

@app.get("/api/investment-policy")
def get_investment_policy():
    conn = get_db()
    row = conn.execute("SELECT * FROM investment_policies ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return {"has_policy": False}
    return {"has_policy": True, "policy": _policy_row_to_dict(row)}

@app.post("/api/investment-policy")
def save_investment_policy(policy: InvestmentPolicy):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO investment_policies (name, target_us_large_cap_pct, target_us_mid_cap_pct, "
        "target_us_small_cap_pct, target_international_developed_pct, target_emerging_markets_pct, "
        "target_us_bonds_pct, target_international_bonds_pct, target_cash_pct, target_real_estate_pct, "
        "target_alternatives_pct, drift_band_pct, max_single_security_pct, minimum_cash_reserve, "
        "rebalance_cadence, use_contributions_before_sales, taxable_sale_preference, "
        "excluded_accounts_json, excluded_holdings_json, employer_stock_exceptions_json, "
        "legacy_holding_exceptions_json, risk_profile, account_constraints_json, effective_date, "
        "review_date, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (policy.name, policy.target_us_large_cap_pct, policy.target_us_mid_cap_pct,
         policy.target_us_small_cap_pct, policy.target_international_developed_pct,
         policy.target_emerging_markets_pct, policy.target_us_bonds_pct,
         policy.target_international_bonds_pct, policy.target_cash_pct,
         policy.target_real_estate_pct, policy.target_alternatives_pct, policy.drift_band_pct,
         policy.max_single_security_pct, policy.minimum_cash_reserve, policy.rebalance_cadence,
         int(policy.use_contributions_before_sales), policy.taxable_sale_preference,
         json.dumps(policy.excluded_accounts), json.dumps(policy.excluded_holdings),
         json.dumps(policy.employer_stock_exceptions), json.dumps(policy.legacy_holding_exceptions),
         policy.risk_profile, json.dumps(policy.account_constraints), policy.effective_date,
         policy.review_date, policy.notes)
    )
    conn.commit()
    policy.id = cur.lastrowid
    conn.close()
    return policy

# ── Allocation reads + workflows (Milestone 4) ───────────────────────────

def _load_portfolio_context(conn):
    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    holdings = [dict(r) for r in conn.execute("SELECT * FROM holdings").fetchall()]
    policy_row = conn.execute("SELECT * FROM investment_policies ORDER BY id DESC LIMIT 1").fetchone()
    policy = _policy_row_to_dict(policy_row) if policy_row else None
    return accounts, holdings, policy

@app.get("/api/portfolio/allocation")
def get_portfolio_allocation():
    conn = get_db()
    accounts, holdings, policy = _load_portfolio_context(conn)
    conn.close()
    if not holdings:
        return {"has_holdings": False}
    from holdings_engine import (
        classify_holdings, compute_current_allocation, compare_to_target,
        concentration_flags, expense_ratio_flags, duplicate_exposure_flags, unclassified_flags,
    )
    classified = classify_holdings(accounts, holdings)
    current = compute_current_allocation(classified["household"])
    result = {
        "has_holdings": True, "current_allocation": current,
        "hsa_allocation": compute_current_allocation(classified["hsa"]),
        "child_specific_total": round(sum(h.get("market_value", 0) or 0 for h in classified["child_specific"]), 2),
        "liquidity_total": round(sum(h.get("market_value", 0) or 0 for h in classified["liquidity"]), 2),
        "blocked_holdings": [{"holding_id": h.get("id"), "account_id": h.get("account_id"), "name": h.get("security_name")} for h in classified["blocked"]],
        "review_required_accounts": sorted({h["account_id"] for h in classified["review_required"]}),
        "concentration_flags": concentration_flags(classified["household"]),
        "expense_ratio_flags": expense_ratio_flags(classified["household"]),
        "duplicate_exposure_flags": duplicate_exposure_flags(classified["household"] + classified["hsa"] + classified["child_specific"]),
        "unclassified_flags": unclassified_flags(classified["household"]),
    }
    if not policy:
        result["has_policy"] = False
        return result
    result["has_policy"] = True
    result["comparison"] = compare_to_target(current, policy)
    return result

@app.post("/api/portfolio/contribution-destination")
def portfolio_contribution_destination(req: PortfolioContributionRequest):
    """"Where should my next contribution go?" — the Available Funds
    workflow's own recommendation step."""
    conn = get_db()
    accounts, holdings, policy = _load_portfolio_context(conn)
    conn.close()
    if not holdings:
        raise HTTPException(status_code=400, detail="No holdings entered yet — add holdings before requesting a contribution recommendation.")
    if not policy:
        raise HTTPException(status_code=400, detail="No investment policy saved yet — set target allocation before requesting a contribution recommendation.")
    from holdings_engine import classify_holdings, compute_current_allocation, compare_to_target, recommend_contribution_destination
    classified = classify_holdings(accounts, holdings)
    current = compute_current_allocation(classified["household"])
    comparison = compare_to_target(current, policy)
    return {"actions": recommend_contribution_destination(comparison, req.amount),
            "current_allocation": current, "comparison": comparison}

@app.post("/api/portfolio/rebalance")
def portfolio_rebalance(req: PortfolioContributionRequest):
    """"I want to rebalance" — shows proposed trades/exchanges, flags
    taxable sales, and both account-level and household-level results.
    Marking an action complete happens via the recommendation decision
    endpoints below, not here (this endpoint is a pure preview)."""
    conn = get_db()
    accounts, holdings, policy = _load_portfolio_context(conn)
    conn.close()
    if not holdings:
        raise HTTPException(status_code=400, detail="No holdings entered yet — add holdings before requesting a rebalance plan.")
    if not policy:
        raise HTTPException(status_code=400, detail="No investment policy saved yet — set target allocation before requesting a rebalance plan.")
    from holdings_engine import classify_holdings, compute_current_allocation, compare_to_target, recommend_rebalance_actions
    classified = classify_holdings(accounts, holdings)
    current = compute_current_allocation(classified["household"])
    comparison = compare_to_target(current, policy)
    return recommend_rebalance_actions(classified["household"], current, comparison, policy, pending_contribution=req.amount)

# Monthly cash flow — recurring amounts, deliberately separate from balances.
@app.get("/api/cash-flow")
def get_cash_flow():
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM cash_flow_items ORDER BY cash_flow_type, category, name"
    ).fetchall()]
    conn.close()
    return {"items": rows, "summary": summarize_cash_flow(rows)}

@app.post("/api/cash-flow")
def create_cash_flow_item(item: CashFlowItem):
    if item.cash_flow_type not in ("income", "expense") or item.amount < 0 or not item.name.strip():
        raise HTTPException(status_code=400, detail="Enter a name, income or expense type, and a non-negative amount")
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO cash_flow_items (name, cash_flow_type, category, amount, essential, notes) VALUES (?,?,?,?,?,?)",
        (item.name.strip(), item.cash_flow_type, item.category.strip() or "Other", item.amount, int(item.essential), item.notes),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM cash_flow_items WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return dict(row)

@app.put("/api/cash-flow/{item_id}")
def update_cash_flow_item(item_id: int, item: CashFlowItem):
    if item.cash_flow_type not in ("income", "expense") or item.amount < 0 or not item.name.strip():
        raise HTTPException(status_code=400, detail="Enter a name, income or expense type, and a non-negative amount")
    conn = get_db()
    conn.execute(
        "UPDATE cash_flow_items SET name=?, cash_flow_type=?, category=?, amount=?, essential=?, notes=?, updated_at=datetime('now') WHERE id=?",
        (item.name.strip(), item.cash_flow_type, item.category.strip() or "Other", item.amount, int(item.essential), item.notes, item_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM cash_flow_items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Cash-flow item not found")
    return dict(row)

@app.delete("/api/cash-flow/{item_id}")
def delete_cash_flow_item(item_id: int):
    conn = get_db()
    conn.execute("DELETE FROM cash_flow_items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return {"deleted": item_id}

@app.get("/api/surplus-allocations")
def get_surplus_allocations():
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM surplus_allocations ORDER BY goal").fetchall()]
    cash_rows = [dict(r) for r in conn.execute("SELECT * FROM cash_flow_items").fetchall()]
    conn.close()
    summary = summarize_cash_flow(cash_rows)
    assigned = round(sum(row["monthly_amount"] for row in rows))
    return {"allocations": rows, "monthly_surplus": summary["monthly_surplus"], "assigned": assigned,
            "unassigned": round(summary["monthly_surplus"] - assigned)}

@app.put("/api/surplus-allocations/{goal}")
def save_surplus_allocation(goal: str, allocation: SurplusAllocation):
    if not goal.strip() or allocation.monthly_amount < 0:
        raise HTTPException(status_code=400, detail="Enter a goal and a non-negative monthly amount")
    conn = get_db()
    conn.execute("INSERT INTO surplus_allocations (goal, monthly_amount, notes) VALUES (?,?,?) ON CONFLICT(goal) DO UPDATE SET monthly_amount=excluded.monthly_amount, notes=excluded.notes, updated_at=datetime('now')",
                 (goal.strip(), allocation.monthly_amount, allocation.notes))
    conn.commit()
    row = conn.execute("SELECT * FROM surplus_allocations WHERE goal=?", (goal.strip(),)).fetchone()
    conn.close()
    return dict(row)

def _capture_household_data_bundle(conn) -> dict:
    """The household-data half of 'resolved assumptions actually used by
    the calculation' -- planning_inputs/accounts/kids/life_events/
    surplus_allocations, i.e. everything a projection call reads from the
    database. Shared by both the retirement-projection GET endpoint (so
    the frontend can echo back EXACTLY what it was shown, not let a save
    re-read the database a second time -- see _capture_resolved_assumptions'
    own docstring) and the save/recalculate endpoints' fallback path.
    """
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    inputs = dict(inputs_row) if inputs_row else None
    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    kids = [dict(r) for r in conn.execute("SELECT * FROM kids ORDER BY display_order").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    return {
        "planning_inputs": inputs,
        "accounts": accounts,
        "kids": kids,
        "life_events": life_events,
        "surplus_allocations": surplus_allocations,
    }

def _capture_resolved_assumptions(conn, ss_timing, jason_ss_claim_age=None, justin_ss_claim_age=None,
                                   seed=None, trial_count=None, household_data=None) -> dict:
    """Milestone 1: 'resolved assumptions actually used by the
    calculation—not merely visible controls or selected overrides.' A
    saved scenario needs enough here to be re-run byte-for-byte later, so
    this captures the full rows a projection actually reads from, not a
    hand-picked subset that will inevitably drift out of sync with the
    engine as new inputs get added.

    household_data: pass the EXACT bundle the frontend already displayed
    (echoed back from GET /api/projections/retirement's own
    resolved_assumptions, round-tripped through the save request) when
    one is available -- see save_scenario()'s docstring for why re-reading
    the database here instead would risk saving a plan the user never
    actually looked at. Only recompute-from-DB (conn is None-safe here
    because the caller already has a connection open) when the caller
    truly has no live displayed result to pin to, e.g. the standalone
    "create by parameters" form on SavedScenarios.jsx.
    """
    return {
        **(household_data if household_data is not None else _capture_household_data_bundle(conn)),
        "ss_timing": ss_timing,
        "jason_ss_claim_age": jason_ss_claim_age,
        "justin_ss_claim_age": justin_ss_claim_age,
        "seed": seed,
        "trial_count": trial_count,
    }

@app.get("/api/saved-scenarios")
def get_saved_scenarios():
    conn=get_db(); rows=conn.execute("SELECT * FROM saved_scenarios ORDER BY created_at DESC").fetchall(); conn.close()
    # assumptions_json is nullable (rows saved before finding #15's fix
    # won't have one) — surface it as `assumptions` when present so the
    # frontend can show what actually produced this scenario's numbers.
    # is_legacy (Milestone 1): true for every row saved before this
    # migration — those only ever captured retirement_age + ss_timing, so
    # the frontend must not present them as fully reproducible.
    return [{**dict(r), "summary":json.loads(r["summary_json"]),
             "assumptions": json.loads(r["assumptions_json"]) if r["assumptions_json"] else None,
             "is_legacy": bool(r["is_legacy"])} for r in rows]

@app.get("/api/saved-scenarios/{scenario_id}")
def get_saved_scenario(scenario_id: int):
    conn=get_db(); row=conn.execute("SELECT * FROM saved_scenarios WHERE id=?", (scenario_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(status_code=404, detail="Saved scenario not found")
    return {**dict(row), "summary":json.loads(row["summary_json"]),
            "assumptions": json.loads(row["assumptions_json"]) if row["assumptions_json"] else None,
            "is_legacy": bool(row["is_legacy"])}

@app.post("/api/saved-scenarios")
def save_scenario(body: ScenarioSave):
    if not body.name.strip() or body.retirement_age < 50 or body.retirement_age > 75: raise HTTPException(status_code=400,detail="Enter a name and retirement age from 50 to 75")
    if body.ss_timing not in ("early","delayed"): raise HTTPException(status_code=400,detail="ss_timing must be 'early' or 'delayed'")
    conn=get_db()
    # Milestone 1: a plain save used to overwrite any existing row with the
    # same name (ON CONFLICT DO UPDATE), silently destroying whatever had
    # been saved there before. A saved scenario is now insert-only — saving
    # under a name that's already taken is rejected so the user picks a
    # distinct name; recalculating an existing scenario against current
    # data goes through POST /api/saved-scenarios/{id}/recalculate instead,
    # which explicitly creates a new revision and never touches the original.
    existing = conn.execute("SELECT id FROM saved_scenarios WHERE name=?", (body.name.strip(),)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=409, detail="A saved scenario with this name already exists. Choose a different name, or use Recalculate on the existing one.")

    if body.summary is not None and body.household_data is not None:
        # Milestone 1 acceptance follow-up (2026-09-09): "must not silently
        # rerun against different current Settings." The caller already
        # has a live displayed result (Retirement.jsx passes its own `s`
        # scenario object as `summary` and the exact resolved_assumptions
        # bundle GET /api/projections/retirement returned as
        # `household_data`) -- store both VERBATIM, with zero recompute
        # and zero second DB read. If Settings changed in the exact gap
        # between page load and clicking Save, this still saves the plan
        # the user actually looked at, not whatever Settings say now.
        summary = {k: body.summary[k] for k in ("retirement_age", "percent_funded", "portfolio_at_retirement", "projected_surplus", "on_track")}
        assumptions = _capture_resolved_assumptions(None, body.ss_timing, body.jason_ss_claim_age, body.justin_ss_claim_age, body.seed, body.trial_count, household_data=body.household_data)
    else:
        # Fallback for a caller with no live displayed result to pin to
        # (e.g. SavedScenarios.jsx's own "create by parameters" form,
        # which only has a name + age + ss_timing, never fetched a
        # projection itself) -- best effort: read current household data
        # and recompute now. This path is inherently the exact "rerun
        # against current Settings" behavior the milestone's acceptance
        # criteria warn about for a DISPLAYED result, but there's no
        # displayed result here to contradict.
        assumptions = _capture_resolved_assumptions(conn, body.ss_timing, body.jason_ss_claim_age, body.justin_ss_claim_age, body.seed, body.trial_count)
        if not assumptions["planning_inputs"]: conn.close(); raise HTTPException(status_code=400,detail="Planning inputs not set")
        # Used to hardcode "early" here regardless of body.ss_timing, so a
        # scenario saved while "SS at 67" was selected everywhere else in
        # the app was silently projected as if early claiming had been
        # chosen instead (external audit 2026-09-07, finding #15).
        # run_retirement_projection only applies a continuous claim age
        # when jason_ss_claim_age/justin_ss_claim_age are passed as
        # explicit keyword args (CALCULATION_CONTRACT.md section 44,
        # ninth follow-up review) — pass through whatever override was
        # active, so the saved numbers match what was actually chosen.
        result=run_retirement_projection(assumptions["planning_inputs"],assumptions["accounts"],ret_ages=[body.retirement_age],
                                          life_events=assumptions["life_events"],surplus_allocations=assumptions["surplus_allocations"],
                                          jason_ss_claim_age=body.jason_ss_claim_age,justin_ss_claim_age=body.justin_ss_claim_age)
        # A jason_ss_claim_age override collapses run_retirement_projection's
        # scenario set down to a single "custom"-labeled entry instead of the
        # usual early/delayed pair (see the jason_ss_options branch in
        # projection_engine.py) — look that up instead of body.ss_timing
        # whenever an override is active, or this always returns None.
        wanted_label = "custom" if body.jason_ss_claim_age is not None else body.ss_timing
        scenario=next((s for s in result["scenarios"] if s["ss_timing"]==wanted_label),None)
        summary={k:scenario[k] for k in ("retirement_age","percent_funded","portfolio_at_retirement","projected_surplus","on_track")}

    cur = conn.execute(
        "INSERT INTO saved_scenarios (name,retirement_age,ss_timing,summary_json,assumptions_json,schema_version,calculation_version,seed,trial_count,revision_of,revision_number,is_legacy) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (body.name.strip(),body.retirement_age,body.ss_timing,json.dumps(summary),json.dumps(assumptions),
         2,CALCULATION_ENGINE_VERSION,body.seed,body.trial_count,None,1,0))
    conn.commit(); new_id = cur.lastrowid; conn.close()
    return {**summary, "id": new_id}

@app.post("/api/saved-scenarios/{scenario_id}/recalculate")
def recalculate_saved_scenario(scenario_id: int):
    """Milestone 1: 'an explicit Recalculate with current data action that
    creates a new revision and preserves the original.' Re-runs the exact
    same retirement_age/ss_timing/claim-age choices the original scenario
    used, but against whatever planning_inputs/accounts/kids/life_events/
    surplus_allocations are current right now — then inserts a NEW row
    linked back to the original via revision_of. The original row is never
    modified, so it stays exactly what it always was: the plan as it stood
    when it was first saved.
    """
    conn=get_db(); original=conn.execute("SELECT * FROM saved_scenarios WHERE id=?", (scenario_id,)).fetchone()
    if not original: conn.close(); raise HTTPException(status_code=404, detail="Saved scenario not found")
    old_assumptions = json.loads(original["assumptions_json"]) if original["assumptions_json"] else {}
    jason_claim = old_assumptions.get("jason_ss_claim_age")
    justin_claim = old_assumptions.get("justin_ss_claim_age")
    seed = old_assumptions.get("seed")
    trial_count = old_assumptions.get("trial_count")
    assumptions = _capture_resolved_assumptions(conn, original["ss_timing"], jason_claim, justin_claim, seed, trial_count)
    if not assumptions["planning_inputs"]: conn.close(); raise HTTPException(status_code=400,detail="Planning inputs not set")
    result=run_retirement_projection(assumptions["planning_inputs"],assumptions["accounts"],ret_ages=[original["retirement_age"]],
                                      life_events=assumptions["life_events"],surplus_allocations=assumptions["surplus_allocations"],
                                      jason_ss_claim_age=jason_claim,justin_ss_claim_age=justin_claim)
    wanted_label = "custom" if jason_claim is not None else original["ss_timing"]
    scenario=next((s for s in result["scenarios"] if s["ss_timing"]==wanted_label),None)
    summary={k:scenario[k] for k in ("retirement_age","percent_funded","portfolio_at_retirement","projected_surplus","on_track")}
    # Lineage root: if the original itself was already a revision, link the
    # new row to THAT chain's root (original["revision_of"]), not to the
    # immediate parent — keeps every revision of a scenario grouped under
    # one root no matter how many times it's been recalculated.
    root_id = original["revision_of"] if original["revision_of"] else original["id"]
    latest_revision = conn.execute(
        "SELECT MAX(revision_number) AS n FROM saved_scenarios WHERE id=? OR revision_of=?", (root_id, root_id)
    ).fetchone()["n"] or 1
    new_name = f"{original['name']} (recalculated, rev {latest_revision + 1})"
    cur = conn.execute(
        "INSERT INTO saved_scenarios (name,retirement_age,ss_timing,summary_json,assumptions_json,schema_version,calculation_version,seed,trial_count,revision_of,revision_number,is_legacy) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (new_name,original["retirement_age"],original["ss_timing"],json.dumps(summary),json.dumps(assumptions),
         2,CALCULATION_ENGINE_VERSION,seed,trial_count,root_id,latest_revision + 1,0))
    conn.commit(); new_id = cur.lastrowid; conn.close()
    return {**summary, "id": new_id, "revision_of": root_id, "revision_number": latest_revision + 1}

@app.get("/api/plan-confidence")
def get_plan_confidence():
    conn = get_db()
    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    cash_flow_items = [dict(r) for r in conn.execute("SELECT * FROM cash_flow_items").fetchall()]
    conn.close()
    return plan_confidence(accounts, dict(inputs_row) if inputs_row else {}, summarize_cash_flow(cash_flow_items))

def _get_active_life_events(conn) -> List[dict]:
    """Rows from life_events with included_in_projection true — the list
    fed into run_retirement_projection()/simulation_engine.py so a
    hypothetical/toggled-off event doesn't silently move the real numbers.
    GET /api/life-events (below) still shows every row regardless of the
    flag; only the actual projection/simulation endpoints filter here."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM life_events WHERE included_in_projection=1 ORDER BY event_year, id"
    ).fetchall()]

def _get_debt_targeted_life_events(conn) -> List[dict]:
    """Rows from life_events that target a specific debt account (a
    one-time lump-sum extra payment, not investable cash) — filtered to
    included_in_projection=1 same as _get_active_life_events above, so a
    toggled-off event has zero effect on the debt-payoff routes either.
    Fed into debt_engine.project_debt_schedule (via GET /api/debts/
    payoff-plan and /recommendation) as one_time_payments, after converting
    each event_year to a months-from-now offset — see the callers below for
    that conversion, which follows projection_engine.CURRENT_YEAR the same
    way every other "life-event year -> time offset" conversion in this
    codebase does."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM life_events WHERE included_in_projection=1 AND target_debt_account_id IS NOT NULL "
        "ORDER BY event_year, id"
    ).fetchall()]


def _debt_targeted_events_to_one_time_payments(events: List[dict]) -> List[dict]:
    """Convert life_events rows (event_year, one_time_cash_delta,
    target_debt_account_id) into the {"account_id","months_from_now",
    "amount"} shape debt_engine.project_debt_schedule expects.

    months_from_now follows the same CURRENT_YEAR convention
    projection_engine.py uses everywhere else to turn a life-event
    calendar year into a time offset (e.g. retirement_year_for_events =
    CURRENT_YEAR + years_to_retire) — we don't track a real "today" date
    anywhere in this app, just the assumed current year. An event dated in
    the current year or earlier (already-past relative to that
    assumption) is clamped to land in month 1 of the simulation — "apply
    it now" — rather than being dropped or landing at a negative/zero
    month index the simulator would never reach.

    one_time_cash_delta is validated <= 0 at write time (see POST
    /api/life-events), so the payment amount is its absolute value.
    """
    from projection_engine import CURRENT_YEAR
    payments = []
    for ev in events:
        months_from_now = max(1, (int(ev["event_year"]) - CURRENT_YEAR) * 12)
        payments.append({
            "account_id": ev["target_debt_account_id"],
            "months_from_now": months_from_now,
            "amount": abs(float(ev.get("one_time_cash_delta") or 0)),
        })
    return payments


def _get_relevant_surplus_allocations(conn) -> List[dict]:
    """Rows from surplus_allocations for the two goals that represent
    money actually invested toward retirement — "Retirement contributions"
    and "Taxable investing" (see projection_engine.py's module-level
    comment for why the other five fixed goals are excluded). Filtered here
    at the query rather than trusting projection_engine.py alone to filter,
    same defense-in-depth spirit as _get_active_life_events above. Fed into
    run_retirement_projection()/simulation_engine.py alongside life_events.
    GET /api/surplus-allocations (above) still returns every row regardless
    — this helper only feeds the real projection/simulation math."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM surplus_allocations WHERE goal IN ('Retirement contributions', 'Taxable investing')"
    ).fetchall()]


def _ss_claim_ages(inputs_row: dict, jason_override: int = None, justin_override: int = None):
    """CALCULATION_CONTRACT.md section 44, milestone 6: reads the
    persisted continuous SS claiming-age Settings fields
    (jason_ss_claim_age/justin_ss_claim_age), if the household has set
    them. Returns (None, None) for a household that hasn't -- every
    simulation-engine consumer's own resolve_ss_benefits treats None as
    "not set, use this call's own ss_timing early/delayed toggle
    unchanged," so an existing household sees no behavior change until
    it explicitly picks a claim age in Settings.

    jason_override/justin_override (2026-09-09, CALCULATION_CONTRACT.md
    section 54): an explicit PER-REQUEST claim age -- e.g. a page's own
    slider, not yet saved to Settings -- takes priority over the saved
    row, per spouse independently. This is the same explicit-override >
    saved-Settings > legacy-default layering resolve_ss_claim_ages
    already applies inside the engine layer (section 49, finding 1);
    this helper now does the identical thing one layer up, at the HTTP
    request itself, so a caller can try out a claim age without
    persisting it first."""
    jason  = jason_override  if jason_override  is not None else inputs_row.get("jason_ss_claim_age")
    justin = justin_override if justin_override is not None else inputs_row.get("justin_ss_claim_age")
    return jason, justin


def _get_kids_surplus_529_monthly(conn, kids: List[dict]) -> Dict[str, float]:
    """The per-kid education-funding surplus_allocations goals — money
    the household has explicitly earmarked (in Surplus Plan) for one
    specific kid's 529, on top of whatever flat monthly_529 rate lives
    on that kid's own row. Returns {f"kid_{id}": amount, ...} for each
    CURRENT kid, 0.0 if that goal has no row or a non-positive
    monthly_amount. Originally two fixed keys ("Education funding -
    Abby"/"Cooper"); generalized (kids-variable-count) to one key per
    row in `kids`, via projection_engine.surplus_goal_key_for_kid.

    Deliberately NOT part of _get_relevant_surplus_allocations' filter
    above — this money is invested toward college, not retirement (see
    projection_engine.py's module comment) — but IS exactly what should
    feed run_education_projection/run_kids_projection's surplus_529_monthly
    param, additively, mirroring how _get_debt_payoff_surplus_monthly feeds
    the debt-payoff routes below."""
    from projection_engine import surplus_goal_key_for_kid
    if not kids:
        return {}
    goal_keys = {surplus_goal_key_for_kid(k["id"]): f"kid_{k['id']}" for k in kids}
    placeholders = ",".join("?" * len(goal_keys))
    rows = {r["goal"]: r["monthly_amount"] for r in conn.execute(
        f"SELECT goal, monthly_amount FROM surplus_allocations WHERE goal IN ({placeholders})",
        list(goal_keys.keys())
    ).fetchall()}
    return {
        owner_key: max(0.0, float(rows.get(goal, 0) or 0))
        for goal, owner_key in goal_keys.items()
    }

@app.get("/api/life-events")
def get_life_events():
    # NOTE: this used to be a pure overlay that never touched the base
    # plan (see life_event_engine.py's own module docstring, also updated).
    # That's no longer true — /api/projections/retirement,
    # /api/simulation/monte-carlo, and the other simulation endpoints below
    # now fetch these same rows (filtered to included_in_projection=1 via
    # _get_active_life_events) and feed them into the real projection/
    # Monte Carlo math. This endpoint's own summarize_life_events() call is
    # left as-is: an isolated single-event future-value estimate, still
    # useful as a quick per-event lens, and it intentionally shows EVERY
    # event (including toggled-off ones) so the user can still see what a
    # toggled-off scenario would be worth if turned back on.
    conn = get_db()
    events = [dict(r) for r in conn.execute("SELECT * FROM life_events ORDER BY event_year, id").fetchall()]
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    conn.close()
    result = summarize_life_events(events, dict(inputs_row) if inputs_row else {})
    for summary, raw in zip(result["events"], events):
        summary["included_in_projection"] = bool(raw.get("included_in_projection", True))
    return result

@app.post("/api/life-events")
def create_life_event(event: LifeEvent):
    if not event.name.strip() or event.event_year < 2000 or event.event_year > 2200 or event.duration_months < 0:
        raise HTTPException(status_code=400, detail="Enter a name, a valid year, and a non-negative duration.")
    conn = get_db()
    monthly_cash_flow_delta = event.monthly_cash_flow_delta
    if event.target_debt_account_id is not None:
        from debt_engine import DEBT_TYPES
        account = conn.execute("SELECT account_type FROM accounts WHERE id=?", (event.target_debt_account_id,)).fetchone()
        if not account or account["account_type"] not in DEBT_TYPES:
            conn.close()
            raise HTTPException(status_code=400, detail="Target debt account not found, or isn't a debt account.")
        if event.one_time_cash_delta > 0:
            conn.close()
            raise HTTPException(
                status_code=400,
                detail="A debt-targeted life event must have a zero or negative one-time cash amount — it's a "
                       "payment toward that debt, not income. Use a positive amount (with no debt target) for "
                       "cash going into an investable bucket instead.",
            )
        # monthly_cash_flow_delta is silently zeroed rather than 400ing —
        # this feature models a single one-time lump payment landing in
        # event_year, not an ongoing monthly override of the debt's actual
        # minimum payment (which already lives on the account itself via
        # Account.minimum_payment). Rejecting outright would force the
        # frontend to strip the field before submitting whenever a user
        # flips the debt-target dropdown on/off; zeroing it here is the
        # same "ignore what doesn't apply" treatment already used
        # elsewhere in this file (e.g. surplus_allocations goal filtering).
        monthly_cash_flow_delta = 0
    cur = conn.execute(
        "INSERT INTO life_events (name,event_type,event_year,one_time_cash_delta,monthly_cash_flow_delta,duration_months,notes,included_in_projection,target_debt_account_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (event.name.strip(), event.event_type, event.event_year, event.one_time_cash_delta, monthly_cash_flow_delta,
         event.duration_months, event.notes, 1 if event.included_in_projection else 0, event.target_debt_account_id),
    )
    conn.commit(); conn.close()
    return {**event.model_dump(), "monthly_cash_flow_delta": monthly_cash_flow_delta, "id": cur.lastrowid}

@app.delete("/api/life-events/{event_id}")
def delete_life_event(event_id: int):
    conn = get_db(); conn.execute("DELETE FROM life_events WHERE id=?", (event_id,)); conn.commit(); conn.close()
    return {"deleted": event_id}

@app.patch("/api/life-events/{event_id}/toggle")
def toggle_life_event(event_id: int, body: dict = None):
    """Flip (or explicitly set, via {"included_in_projection": bool}) just
    the included_in_projection flag on one event — lighter-weight than
    requiring the frontend to resubmit the whole event payload."""
    conn = get_db()
    row = conn.execute("SELECT included_in_projection FROM life_events WHERE id=?", (event_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Life event not found")
    if body and "included_in_projection" in body:
        new_value = 1 if body["included_in_projection"] else 0
    else:
        new_value = 0 if row["included_in_projection"] else 1
    conn.execute("UPDATE life_events SET included_in_projection=?, updated_at=datetime('now') WHERE id=?", (new_value, event_id))
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM life_events WHERE id=?", (event_id,)).fetchone())
    conn.close()
    return updated

@app.get("/api/estate-documents")
def get_estate_documents():
    conn = get_db(); rows = [dict(r) for r in conn.execute("SELECT * FROM estate_documents ORDER BY document_type").fetchall()]; conn.close()
    return rows

@app.put("/api/estate-documents/{document_type}")
def save_estate_document(document_type: str, body: EstateDocument):
    # Status vocabulary widened 2026-09-09 -- corrected to
    # executed/verify/outdated/pending in the previous commit on the
    # (wrong) assumption that Estate.jsx was this endpoint's only
    # caller. It isn't: PlanOperatingSystem.jsx ALSO calls this same
    # endpoint, with its own disjoint document_type keys ("Will",
    # "Revocable trust", etc. vs. Estate.jsx's "trust"/"wills"/...) AND
    # its own disjoint status vocabulary (not_started/in_progress/
    # complete) -- the narrower fix would have 400'd every save from
    # that page. Accepts the union of both until the two estate-
    # tracking UIs are reconciled into one (Milestone 3 navigation
    # work) rather than silently regressing one of them in the
    # meantime.
    if document_type != body.document_type or not document_type.strip() or body.status not in {
        "executed", "verify", "outdated", "pending", "not_started", "in_progress", "complete",
    }:
        raise HTTPException(status_code=400, detail="Invalid estate-document status")
    conn = get_db()
    conn.execute("INSERT INTO estate_documents (document_type,status,reviewed_on,next_review_on,location_hint,notes,updated_at) VALUES (?,?,?,?,?,?,datetime('now')) ON CONFLICT(document_type) DO UPDATE SET status=excluded.status,reviewed_on=excluded.reviewed_on,next_review_on=excluded.next_review_on,location_hint=excluded.location_hint,notes=excluded.notes,updated_at=datetime('now')", (body.document_type,body.status,body.reviewed_on,body.next_review_on,body.location_hint,body.notes))
    conn.commit(); conn.close(); return body

@app.get("/api/estate-beneficiaries")
def get_estate_beneficiaries():
    conn = get_db(); rows = [dict(r) for r in conn.execute("SELECT * FROM estate_beneficiaries ORDER BY account_key").fetchall()]; conn.close()
    return rows

@app.put("/api/estate-beneficiaries/{account_key}")
def save_estate_beneficiary(account_key: str, body: EstateBeneficiary):
    if account_key != body.account_key or not account_key.strip():
        raise HTTPException(status_code=400, detail="Invalid estate-beneficiary account key")
    conn = get_db()
    conn.execute(
        "INSERT INTO estate_beneficiaries (account_key,primary_beneficiary,contingent_beneficiary,updated_at) VALUES (?,?,?,datetime('now')) "
        "ON CONFLICT(account_key) DO UPDATE SET primary_beneficiary=excluded.primary_beneficiary,contingent_beneficiary=excluded.contingent_beneficiary,updated_at=datetime('now')",
        (body.account_key, body.primary_beneficiary, body.contingent_beneficiary)
    )
    conn.commit(); conn.close(); return body

@app.get("/api/assumption-reviews")
def get_assumption_reviews():
    conn=get_db(); rows=[dict(r) for r in conn.execute("SELECT * FROM assumption_reviews ORDER BY created_at DESC LIMIT 12").fetchall()]; conn.close()
    for row in rows: row["assumptions"] = json.loads(row.pop("assumptions_json"))
    return rows

@app.post("/api/assumption-reviews")
def create_assumption_review(body: AssumptionReview):
    conn=get_db(); cur=conn.execute("INSERT INTO assumption_reviews (label,assumptions_json) VALUES (?,?)", (body.label.strip() or "Assumption review",json.dumps(body.assumptions))); conn.commit(); conn.close()
    return {"id":cur.lastrowid,**body.model_dump()}

@app.get("/api/financial-runway")
def financial_runway():
    from net_worth_engine import compute_net_worth, effective_monthly_expenses, emergency_fund_check
    conn=get_db(); inputs_row=conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone(); accounts=[dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]; cash=[dict(r) for r in conn.execute("SELECT * FROM cash_flow_items").fetchall()]; life_events=_get_active_life_events(conn); surplus_allocations=_get_relevant_surplus_allocations(conn); conn.close()
    inputs=dict(inputs_row) if inputs_row else {}; retirement=run_retirement_projection(inputs,accounts,ret_ages=[60],life_events=life_events,surplus_allocations=surplus_allocations) if inputs else {"scenarios":[]}; age60=next((s for s in retirement["scenarios"] if s["label"]=="age_60_early"),{}); flow=summarize_cash_flow(cash); emergency=emergency_fund_check(accounts,effective_monthly_expenses(inputs,flow)); networth=compute_net_worth(accounts)
    return {"net_worth":round(networth["net_worth"]),"emergency":emergency,"cash_flow":flow,"retirement":{"percent_funded":age60.get("percent_funded"),"projected_surplus":age60.get("projected_surplus"),"retirement_age":60},"account_count":len(accounts)}

@app.get("/api/calendar/export")
def export_calendar():
    """Portable calendar file for Google Calendar, Apple Calendar, and Outlook."""
    conn=get_db(); tasks=[dict(r) for r in conn.execute("SELECT * FROM tasks WHERE completed=0 ORDER BY due_year,title").fetchall()]; conn.close()
    year=datetime.now().year; lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Personal CFO//Planning Calendar//EN","CALSCALE:GREGORIAN"]
    for index, task in enumerate(tasks):
        due=f"{max(year, task.get('due_year') or year)}0101"
        summary=task['title'].replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;')
        description=(task.get('description') or '').replace('\\', '\\\\').replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')
        lines += ["BEGIN:VEVENT",f"UID:personal-cfo-{task['id']}-{year}@local",f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",f"DTSTART;VALUE=DATE:{due}",f"SUMMARY:{summary}",f"DESCRIPTION:{description}","END:VEVENT"]
    lines.append("END:VCALENDAR")
    return FastAPIResponse(content="\r\n".join(lines)+"\r\n",media_type="text/calendar",headers={"Content-Disposition":"attachment; filename=personal-cfo-planning-calendar.ics"})

def _get_debt_payoff_surplus_monthly(conn) -> float:
    """The "High-interest debt payoff" surplus_allocations goal's
    monthly_amount — money the user has explicitly earmarked (in Surplus
    Plan) for paying down debt, on top of whatever they type into the
    /debts/payoff-plan and /debts/recommendation extra_monthly query param.
    Excluded from run_retirement_projection/simulation_engine.py (it pays
    down debt, it doesn't grow investable assets — see projection_engine.py's
    module-level comment), but the debt engine is exactly where it SHOULD
    land, additively with any manually-entered extra_monthly, mirroring how
    _get_debt_targeted_life_events feeds one-time lump sums into the same
    routes below."""
    row = conn.execute(
        "SELECT monthly_amount FROM surplus_allocations WHERE goal='High-interest debt payoff'"
    ).fetchone()
    return float(row["monthly_amount"]) if row else 0.0


# Debt Payoff — operates on accounts whose account_type is a debt type
# (mortgage, credit_card, student_loan, car_loan, personal_loan)
@app.get("/api/debts/payoff-plan")
def get_debt_payoff_plan(extra_monthly: float = 0):
    conn = get_db()
    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    debt_events = _get_debt_targeted_life_events(conn)
    surplus_extra = _get_debt_payoff_surplus_monthly(conn)
    conn.close()
    from debt_engine import project_debt_schedule
    one_time_payments = _debt_targeted_events_to_one_time_payments(debt_events)
    return project_debt_schedule(accounts, extra_monthly + surplus_extra, one_time_payments=one_time_payments)

@app.get("/api/debts/recommendation")
def get_debt_recommendation(extra_monthly: float = 0):
    """The actual verdict — which strategy to use, why, debt-free date, and
    which debt to focus extra payments on first. Not just the raw
    avalanche-vs-snowball numbers from /payoff-plan."""
    conn = get_db()
    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    debt_events = _get_debt_targeted_life_events(conn)
    surplus_extra = _get_debt_payoff_surplus_monthly(conn)
    conn.close()
    from debt_engine import recommend_payoff_strategy
    one_time_payments = _debt_targeted_events_to_one_time_payments(debt_events)
    result = recommend_payoff_strategy(accounts, extra_monthly + surplus_extra, one_time_payments=one_time_payments)
    if result.get("has_debt"):
        # Breakdown of what makes up "extra_monthly" above — the manual
        # query-param the user typed in this page vs. the "High-interest
        # debt payoff" surplus goal set on the Surplus Plan page — so the
        # frontend doesn't have to (and can't accidentally) recompute
        # "Monthly Commitment" as total_minimum_payment + the manual amount
        # alone and silently under-report it.
        result["manual_extra_monthly"] = extra_monthly
        result["surplus_debt_payoff_monthly"] = surplus_extra
    return result

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
def get_rmd_planning(ret_age: int = 60, ss_timing: str = "early"):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from retirement_tools_engine import run_rmd_planning
    return run_rmd_planning(dict(inputs_row), accounts, ret_age, ss_timing,
                             life_events=life_events, surplus_allocations=surplus_allocations)

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
        + inputs.get("justin_life_ul", 0) + inputs.get("justin_life_whole", 0) + inputs.get("person2_life_employer", 0)
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

# Kids (variable count, 0-5 — see the Kid model's own comment above)
def _get_kids(conn) -> List[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM kids ORDER BY display_order, id").fetchall()]

@app.get("/api/kids")
def get_kids():
    conn = get_db()
    kids = _get_kids(conn)
    conn.close()
    return kids

@app.post("/api/kids")
def create_kid(kid: Kid):
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM kids").fetchone()[0]
    if count >= MAX_KIDS:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Already at the maximum of {MAX_KIDS} kids")
    # New kids append after whatever the highest display_order currently
    # is, rather than always 0 — otherwise a newly added kid would jump
    # to the front of the tab/card order every time.
    max_order = conn.execute("SELECT MAX(display_order) FROM kids").fetchone()[0]
    kid.display_order = (max_order + 1) if max_order is not None else 0
    cur = conn.execute(
        "INSERT INTO kids (name, age, monthly_529, display_order) VALUES (?,?,?,?)",
        (kid.name, kid.age, kid.monthly_529, kid.display_order)
    )
    conn.commit()
    kid.id = cur.lastrowid
    conn.close()
    return kid

@app.put("/api/kids/{kid_id}")
def update_kid(kid_id: int, kid: Kid):
    conn = get_db()
    conn.execute(
        "UPDATE kids SET name=?, age=?, monthly_529=?, display_order=? WHERE id=?",
        (kid.name, kid.age, kid.monthly_529, kid.display_order, kid_id)
    )
    conn.commit()
    conn.close()
    return {**kid.dict(), "id": kid_id}

@app.delete("/api/kids/{kid_id}")
def delete_kid(kid_id: int):
    # Deliberately does NOT touch any account currently owned by
    # f"kid_{kid_id}" -- those accounts keep that owner value (so they
    # stay correctly excluded from the parents' own net worth/retirement
    # totals via is_kid_owner, which only checks the "kid_" prefix, not
    # whether a kids row still exists for that id) but simply won't
    # appear under any kid's projection anymore, since the kid record
    # that would have surfaced them is gone. No balances are deleted or
    # reassigned -- reversible by re-adding a kid and manually editing
    # those accounts' owner back, if that's ever wanted.
    #
    # The per-kid education-funding surplus_allocations row IS deleted,
    # though (external audit follow-up, 2026-09-09) -- unlike an
    # account, it holds no real balance, only a monthly earmarking
    # instruction ("$X/mo toward this kid's 529"). Left in place, that
    # instruction becomes silently unreachable: SurplusPlan.jsx only
    # ever renders a row per CURRENT kid, so there's no UI left to see
    # or edit it, yet GET /api/surplus-allocations' own "assigned" total
    # sums every row unconditionally -- the deleted kid's dollars would
    # keep counting as "spoken for" and reducing "unassigned" forever,
    # with no way to ever reclaim or even see them again. Deleting the
    # row (rather than leaving it orphaned like the account is) returns
    # that money to "unassigned" where it's visible and re-allocatable.
    from projection_engine import surplus_goal_key_for_kid
    conn = get_db()
    conn.execute("DELETE FROM surplus_allocations WHERE goal=?", (surplus_goal_key_for_kid(kid_id),))
    conn.execute("DELETE FROM kids WHERE id=?", (kid_id,))
    conn.commit()
    conn.close()
    return {"deleted": kid_id}

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
    cash_flow_items = [dict(r) for r in conn.execute("SELECT * FROM cash_flow_items").fetchall()]
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from net_worth_engine import effective_monthly_expenses, emergency_fund_check
    inputs = dict(inputs_row)
    return emergency_fund_check(accounts, effective_monthly_expenses(inputs, summarize_cash_flow(cash_flow_items)))

@app.get("/api/cfo-briefing")
def get_cfo_briefing():
    """A read-only, prioritized summary of the existing household plan."""
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    snapshots = [dict(r) for r in conn.execute(
        "SELECT id, snapshot_date, total_assets, liabilities, net_worth, note FROM snapshots ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchall()]
    tasks = [dict(r) for r in conn.execute(
        "SELECT * FROM tasks WHERE completed=0 ORDER BY created_at DESC LIMIT 24"
    ).fetchall()]
    cash_flow_items = [dict(r) for r in conn.execute("SELECT * FROM cash_flow_items").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    kids = _get_kids(conn)
    surplus_529 = _get_kids_surplus_529_monthly(conn, kids)
    conn.close()
    inputs = dict(inputs_row) if inputs_row else {}
    try:
        retirement = run_retirement_projection(inputs, accounts, ret_ages=[60], life_events=life_events, surplus_allocations=surplus_allocations)
        education = run_education_projection(inputs, accounts, surplus_529_monthly=surplus_529, kids=kids)
    except (KeyError, ValueError, ZeroDivisionError):
        # Empty or partially completed setup should still receive useful
        # data-quality guidance instead of an unusable dashboard error.
        retirement, education = {}, {}
    return build_cfo_briefing(accounts, inputs, snapshots, tasks, retirement, education, summarize_cash_flow(cash_flow_items))

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
def get_retirement_projections(jason_ss_claim_age: int = None, justin_ss_claim_age: int = None):
    """jason_ss_claim_age/justin_ss_claim_age (2026-09-09,
    CALCULATION_CONTRACT.md section 54): unlike every other wired
    endpoint, this one does NOT fall back to the SAVED Settings claim
    age -- only an EXPLICIT query param switches this response to the
    single "custom" scenario. This endpoint's early/delayed PAIR is a
    hard dependency of both Retirement.jsx's own toggle and WhatIf.jsx's
    `age_X_early` label lookups (WhatIf.jsx never sends these params),
    so falling back to a saved Settings value the way the other 7
    endpoints do would silently break WhatIf.jsx the moment a household
    saves ANY claim age, even on a page that never touches this one.
    Retirement.jsx's own new slider passes these explicitly when the
    user turns it on; left unset (as WhatIf.jsx always does), behavior
    is 100% unchanged regardless of what's saved in Settings."""
    conn = get_db()
    household_data = _capture_household_data_bundle(conn)
    inputs_row = household_data["planning_inputs"]
    accounts   = household_data["accounts"]
    life_events = household_data["life_events"]
    surplus_allocations = household_data["surplus_allocations"]
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    # Full 55-67 range — Retirement.jsx's own buttons only ever look up a
    # subset of these (55-60 one year at a time, plus 65), so the extra
    # ages are harmless there; but this same response also serves as
    # WhatIf.jsx's baseline for its full 55-67 slider, where a narrower
    # range here silently broke the "Impact on Retire at X" comparison
    # for any age outside the original [55,56,57,58,59,60,65] set.
    result = run_retirement_projection(dict(inputs_row), accounts, ret_ages=list(range(55, 68)), life_events=life_events, surplus_allocations=surplus_allocations,
                                        jason_ss_claim_age=jason_ss_claim_age, justin_ss_claim_age=justin_ss_claim_age)
    # Milestone 1 acceptance follow-up (2026-09-09): "saving must not
    # silently rerun against different current Settings." The original
    # save_scenario() re-read planning_inputs/accounts fresh from the DB
    # at save time -- if Settings changed in the gap between this page
    # loading and the user clicking Save, the saved summary would reflect
    # THAT later state, not the plan actually on screen when they clicked.
    # Echoing the exact bundle used for THIS response lets the frontend
    # round-trip it back on save, so what gets saved is provably the same
    # data that produced what's displayed -- no second DB read involved.
    result["resolved_assumptions"] = household_data
    return result

@app.get("/api/projections/two-dimensional-retirement")
def get_two_dimensional_retirement_projection(jason_ret_age: int, justin_ret_age: int, ss_timing: str = "early"):
    """Explicit ages for both spouses -- run_two_dimensional_retirement_projection
    (docs/TWO_DIMENSIONAL_RETIREMENT_DESIGN.md section 7), v1 scope:
    Retirement Projection reference implementation only, one scenario at
    a time. Both ages are required query params (no defaults) -- this
    endpoint is deliberately not reachable by accident from a page that
    only knows about the existing single-age model."""
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from projection_engine import run_two_dimensional_retirement_projection
    return run_two_dimensional_retirement_projection(dict(inputs_row), accounts, jason_ret_age, justin_ret_age,
                                                       ss_timing=ss_timing, life_events=life_events,
                                                       surplus_allocations=surplus_allocations)

@app.get("/api/projections/education")
def get_education_projections(continue_contributions_during_college: bool = False):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    kids = _get_kids(conn)
    surplus_529 = _get_kids_surplus_529_monthly(conn, kids)
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    return run_education_projection(dict(inputs_row), accounts, continue_contributions_during_college, surplus_529_monthly=surplus_529, kids=kids)

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
    # quicken_account_map.local.json is hand-maintained and gitignored — a
    # typo'd account_type there (this import path builds accounts directly
    # from raw SQL, bypassing the Account model's validation entirely)
    # would otherwise import successfully and then be silently invisible to
    # net worth and every retirement/Monte Carlo number, with nothing in
    # this response to say so. Skip and report those explicitly instead.
    from net_worth_engine import VALID_ACCOUNT_TYPES
    invalid = [acc for acc in accounts if acc['account_type'] not in VALID_ACCOUNT_TYPES]
    accounts = [acc for acc in accounts if acc['account_type'] in VALID_ACCOUNT_TYPES]
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
            "total_accounts": len(accounts),
            "accounts_skipped_invalid_type": [{"name": a["name"], "account_type": a["account_type"]} for a in invalid],
            **summary}

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
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    kids = _get_kids(conn)
    surplus_529 = _get_kids_surplus_529_monthly(conn, kids)
    conn.close()
    if not inputs_row:
        return {"inserted": 0, "message": "No planning inputs yet"}
    inputs = dict(inputs_row)
    try:
        projections = run_retirement_projection(inputs, accounts, life_events=life_events, surplus_allocations=surplus_allocations)
        education   = run_education_projection(inputs, accounts, surplus_529_monthly=surplus_529, kids=kids)
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
    kids = _get_kids(conn)
    surplus_529 = _get_kids_surplus_529_monthly(conn, kids)
    conn.close()
    from projection_engine import run_kids_projection
    inputs = dict(inputs_row) if inputs_row else {}
    return run_kids_projection(accounts, inputs, surplus_529_monthly=surplus_529, kids=kids)

@app.get("/api/projections/insurance")
def get_insurance_analysis():
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    kids = _get_kids(conn)
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from projection_engine import run_insurance_analysis
    return run_insurance_analysis(dict(inputs_row), accounts, kids=kids)

# ── Annual Report ─────────────────────────────────────────────────────────────
from fastapi.responses import Response

@app.get("/api/report/annual")
def generate_annual_report():
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    kids = _get_kids(conn)
    surplus_529 = _get_kids_surplus_529_monthly(conn, kids)
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
        "retirement": run_retirement_projection(inputs, accounts, life_events=life_events, surplus_allocations=surplus_allocations),
        "education":  run_education_projection(inputs, accounts, surplus_529_monthly=surplus_529, kids=kids),
        "kids":       run_kids_projection(accounts, inputs, surplus_529_monthly=surplus_529, kids=kids),
        "insurance":  run_insurance_analysis(inputs, accounts, kids=kids),
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

def _apply_whatif_overrides(inputs: dict, body: dict) -> dict:
    """Shared by /api/projections/whatif and the What-If-aware Monte Carlo/
    Stress Test POST endpoints below — same override fields, same
    semantics, so the What-If Builder's modified assumptions actually
    carry into whichever tab you switch to next instead of being silently
    discarded in favor of saved Settings (external audit 2026-09-06:
    switching from What-If to Monte Carlo/Historical Stress used to re-run
    against Settings, only passing along retirement age and SS timing —
    every slider the user had just moved was thrown away)."""
    inputs = dict(inputs)
    inputs["_salary_growth_pct"] = body.get("salary_growth_pct", 0.0)
    inputs["_ss_timing"] = body.get("ss_timing", "early")
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
        # External audit review of commit 0c1a569, finding 3 (P1): the
        # SS claiming-age 62-70 anchors (jason_ss_70/justin_ss_early/
        # justin_ss_70, CALCULATION_CONTRACT.md section 44) didn't exist
        # yet when ss_mult was first written and were never added here.
        # A household with a saved claim age of 70 gets its benefit
        # ENTIRELY from jason_ss_70 (ss_benefit_for_claim_age returns
        # benefit_70 exactly at age>=70) -- leaving it unscaled meant
        # the SS multiplier slider had NO effect on that household's
        # What-If/Monte-Carlo/Stress-Test results at all. Scale all
        # three the same way as the original two fields.
        inputs["jason_ss_70"]     = inputs.get("jason_ss_70", 0) * ss_mult
        inputs["justin_ss_early"] = inputs.get("justin_ss_early", 0) * ss_mult
        inputs["justin_ss_70"]    = inputs.get("justin_ss_70", 0) * ss_mult
    return inputs

@app.get("/api/simulation/monte-carlo")
def get_monte_carlo(ret_age: int = 60, ss_timing: str = "early",
                     jason_ret_age: int = None, justin_ret_age: int = None,
                     jason_ss_claim_age: int = None, justin_ss_claim_age: int = None):
    """jason_ret_age/justin_ret_age (2026-09-08, CALCULATION_CONTRACT.md
    section 22): explicit two-age mode, both required together (a
    ValueError from run_monte_carlo becomes a 400, not a silent
    single-axis fallback) -- ret_age/ss_timing's existing single-axis
    behavior is unaffected when these are left unset.

    jason_ss_claim_age/justin_ss_claim_age (2026-09-09,
    CALCULATION_CONTRACT.md section 54): an explicit PER-REQUEST claim
    age -- e.g. a page's own slider -- takes priority over whatever's
    saved in Settings, per spouse independently, so a page can try out
    a claim age without persisting it first. Left unset, behavior is
    unchanged (falls back to the saved Settings value, then the legacy
    ss_timing toggle, exactly as before)."""
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from simulation_engine import run_monte_carlo
    _inputs = dict(inputs_row)
    _jason_ss_claim_age, _justin_ss_claim_age = _ss_claim_ages(_inputs, jason_ss_claim_age, justin_ss_claim_age)
    try:
        return run_monte_carlo(_inputs, accounts, ret_age, ss_timing, life_events=life_events,
                                surplus_allocations=surplus_allocations,
                                jason_ret_age=jason_ret_age, justin_ret_age=justin_ret_age,
                                jason_ss_claim_age=_jason_ss_claim_age, justin_ss_claim_age=_justin_ss_claim_age)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/simulation/monte-carlo")
def post_monte_carlo(body: dict):
    """What-If-aware variant of the GET endpoint above — accepts the same
    override fields as /api/projections/whatif (pre_return, post_return,
    inflation, healthcare_pre, income_target, bridge_income, pension_mult,
    ss_mult) so the Monte Carlo tab can actually reflect the scenario just
    built in the What-If Builder instead of silently re-running against
    saved Settings (external audit 2026-09-06 — see StressTestWhatIf.jsx).

    jason_ret_age/justin_ret_age (2026-09-08, CALCULATION_CONTRACT.md
    section 23): two-age mode, both required together, same as the GET
    endpoint — added here so switching into two-age mode doesn't also
    silently drop whatever What-If overrides/ss_timing were already
    selected (independent review finding: the frontend used to call the
    plain GET endpoint unconditionally in two-age mode, always reverting
    to early SS and saved Settings regardless of what the user had
    actually chosen)."""
    ret_age    = body.get("ret_age", 60)
    ss_timing  = body.get("ss_timing", "early")
    jason_ret_age  = body.get("jason_ret_age")
    justin_ret_age = body.get("justin_ret_age")
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    inputs = _apply_whatif_overrides(dict(inputs_row), body)
    _jason_ss_claim_age, _justin_ss_claim_age = _ss_claim_ages(inputs)
    _jason_ss_claim_age = body.get("jason_ss_claim_age", _jason_ss_claim_age)
    _justin_ss_claim_age = body.get("justin_ss_claim_age", _justin_ss_claim_age)
    from simulation_engine import run_monte_carlo
    try:
        return run_monte_carlo(inputs, accounts, ret_age, ss_timing, life_events=life_events,
                                surplus_allocations=surplus_allocations,
                                jason_ret_age=jason_ret_age, justin_ret_age=justin_ret_age,
                                jason_ss_claim_age=_jason_ss_claim_age, justin_ss_claim_age=_justin_ss_claim_age)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/projections/whatif")
def get_whatif(body: dict):
    """Run retirement projection with modified assumptions."""
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}

    from projection_engine import run_retirement_projection

    # Apply what-if overrides (shared with the Monte Carlo/Stress Test
    # POST endpoints below — see _apply_whatif_overrides).
    ret_age      = body.get("ret_age", 60)
    salary_growth_pct = body.get("salary_growth_pct", 0.0)
    inputs = _apply_whatif_overrides(dict(inputs_row), body)

    # Always include the 55/60/65 baseline ages (the "Surplus Across All
    # Retirement Ages" panel shows those three fixed cards regardless of
    # slider position) plus whichever age the slider is actually on — the
    # slider now covers the full 55-67 range, so a default-only [55,60,65]
    # silently left the "Impact on Retire at X" panel empty for any other
    # age (the reported "nothing appears" bug, same root cause as the
    # Monte Carlo income-sources gap fixed alongside this).
    ret_ages = sorted({55, 60, 65, ret_age})
    return run_retirement_projection(inputs, accounts, ret_ages=ret_ages, salary_growth_pct=salary_growth_pct, life_events=life_events, surplus_allocations=surplus_allocations)

@app.get("/api/simulation/swr-batch")
def get_swr_batch():
    """Compute SWR for all retirement ages 55-67 in one call."""
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from simulation_engine import run_swr_analysis
    results = {}
    for ret_age in range(55, 68):
        try:
            r = run_swr_analysis(dict(inputs_row), accounts, ret_age=ret_age, ss_timing="early", life_events=life_events, surplus_allocations=surplus_allocations)
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
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}

    from projection_engine import run_retirement_projection

    inputs = dict(inputs_row)
    jason_age = inputs["jason_age"]
    # This page has no early/delayed/claim-age SS control at all (unlike
    # Retirement.jsx) and always shows the "early" scenario below.
    # run_retirement_projection only applies a continuous claim age when
    # jason_ss_claim_age/justin_ss_claim_age are passed as explicit
    # keyword args (CALCULATION_CONTRACT.md section 44, ninth follow-up
    # review) -- this call site never passes them, so it always produces
    # the normal early+delayed pair regardless of what the household has
    # set in Settings, and the filter below always finds its scenario.
    proj = run_retirement_projection(inputs, accounts, ret_ages=list(range(55, 68)), life_events=life_events, surplus_allocations=surplus_allocations)

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

@app.post("/api/retirement/income-sources")
@app.get("/api/retirement/income-sources")
def get_income_sources(ret_age: int = 60, ss_timing: str = "early", body: dict = None,
                        jason_ss_claim_age: int = None, justin_ss_claim_age: int = None):
    if body is not None:
        ret_age = body.get("ret_age", ret_age)
        ss_timing = body.get("ss_timing", ss_timing)
        jason_ss_claim_age = body.get("jason_ss_claim_age", jason_ss_claim_age)
        justin_ss_claim_age = body.get("justin_ss_claim_age", justin_ss_claim_age)
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from projection_engine import run_retirement_projection
    # ret_ages defaults to [55, 60, 65] — must pass the requested age
    # explicitly, since the Monte Carlo/Historical Stress tabs now offer
    # the full 55-67 range and any age outside the default 3 would
    # otherwise silently compute nothing, leaving this chart empty.
    # External audit review of commit 0c1a569, finding 4 (P2): unlike
    # get_retirement_sensitivity/get_income_sources's OTHER caller
    # pages, THIS endpoint is Simulation.jsx's own companion chart to
    # Monte Carlo (see Simulation.jsx's income-sources fetch) -- Monte
    # Carlo's own simulation already honors a saved claim age via
    # _ss_claim_ages, so leaving this chart on the legacy ss_timing
    # toggle made them silently disagree: reproduced with Jason's saved
    # claim age of 70, Monte Carlo correctly paid $0 SS at 67 while this
    # chart showed $21,000 (the early/delayed toggle's own age-67
    # figure). Now resolves the same claim age Monte Carlo uses and, if
    # Jason has one set, looks up the resulting "custom" label instead
    # of the ss_timing-derived one -- matching
    # run_retirement_projection's own documented "custom" scenario
    # contract (CALCULATION_CONTRACT.md section 44).
    #
    # External audit review of commit aaa3cf5, finding 2 (P2): this
    # used to switch to the "custom" label whenever EITHER spouse had a
    # saved claim age, but run_retirement_projection's scenario label
    # is driven by jason_ss_claim_age ONLY -- Justin's claim age
    # changes his own benefit amount within whichever scenario Jason's
    # is in, but never creates its own scenario branch (there is no
    # per-Justin scenario sweep in this single-axis function). With
    # only Justin's claim age saved (Jason's left unset), the label
    # stayed "early"/"delayed" as normal, but this endpoint looked up
    # "custom" anyway and returned {"error": "Scenario not found"}.
    # Fixed to match the label jason_ss_claim_age alone actually
    # produces.
    _proj_inputs = _apply_whatif_overrides(dict(inputs_row), body or {})
    _jason_ss_claim_age, _justin_ss_claim_age = _ss_claim_ages(_proj_inputs, jason_ss_claim_age, justin_ss_claim_age)
    result = run_retirement_projection(_proj_inputs, accounts, ret_ages=[ret_age], life_events=life_events, surplus_allocations=surplus_allocations,
                                        jason_ss_claim_age=_jason_ss_claim_age, justin_ss_claim_age=_justin_ss_claim_age)
    _label = f"age_{ret_age}_custom" if _jason_ss_claim_age is not None else f"age_{ret_age}_{ss_timing}"
    scenario = next((s for s in result["scenarios"] if s["label"] == _label), None)
    if not scenario: return {"error": "Scenario not found"}
    # Return simplified chart data.
    #
    # "portfolio_draw" (== yearly_detail's "withdrawal") is a GROSS
    # cash-flow figure: once RMDs start, it includes the full forced
    # distribution even in years most of it is immediately reinvested
    # back into taxable rather than spent (simulate_withdrawal_year's
    # own surplus-sweep behavior, see annual_engine.py) — a household
    # can show a large "portfolio draw" here while its actual spending
    # need that year is much smaller. Auditor review (2026-09-10):
    # this chart's own tooltip only ever showed the single combined
    # "Portfolio Draw" number, inviting a reasonable reader to mistake
    # a large RMD year for a large DISCRETIONARY withdrawal. Splitting
    # rmd/discretionary_withdrawal out explicitly, and returning the
    # raw rmd/rmd_reinvested/withdrawal_pretax fields already computed
    # by yearly_detail (never re-derived here), lets the frontend show
    # both the split stacked areas and the raw figures in the tooltip
    # rather than only the conflated total. discretionary_withdrawal is
    # never negative: draws["pretax"] only ever grows from the RMD
    # baseline (annual_engine.simulate_withdrawal_year draws the full
    # RMD unconditionally, then optionally ADDS a further discretionary
    # pretax draw on top if spending need remains) and every other
    # bucket (taxable/roth/hsa) is fully discretionary by construction,
    # so subtracting the RMD-only portion from the full gross draw can't
    # go below zero.
    chart = []
    for y in scenario["yearly_detail"]:
        rmd = y.get("rmd", 0)
        total_draw = y["withdrawal"]
        chart.append({
            "age":          y["jason_age"],
            "pension":      y["pension"],
            "social_security": y["social_security"],
            "bridge_income":   y.get("bridge_income", 0),
            "portfolio_draw":  total_draw,
            "rmd":                    rmd,
            "rmd_reinvested":         y.get("rmd_reinvested", 0),
            "withdrawal_pretax":      y.get("withdrawal_pretax", 0),
            "discretionary_withdrawal": max(0, total_draw - rmd),
            "total_need":      y["income_need"],
        })
    return {"chart": chart, "label": scenario["label"]}

@app.get("/api/simulation/tax-efficiency")
def get_tax_efficiency(ret_age: int = 60, ss_timing: str = "early",
                        jason_ret_age: int = None, justin_ret_age: int = None,
                        jason_ss_claim_age: int = None, justin_ss_claim_age: int = None):
    """jason_ret_age/justin_ret_age (2026-09-08, CALCULATION_CONTRACT.md
    section 34/35, Milestone 3 of 4): two-age mode, both required
    together -- read from the query string, matching this endpoint's
    existing GET-only shape (no What-If overrides/POST body support
    exists here for single-axis either, so none is added for two-age).

    jason_ss_claim_age/justin_ss_claim_age (2026-09-09,
    CALCULATION_CONTRACT.md section 54): explicit per-request claim
    age, takes priority over saved Settings -- see get_monte_carlo's
    identical param."""
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from simulation_engine import run_tax_efficiency_simulation
    _inputs = dict(inputs_row)
    _jason_ss_claim_age, _justin_ss_claim_age = _ss_claim_ages(_inputs, jason_ss_claim_age, justin_ss_claim_age)
    try:
        return run_tax_efficiency_simulation(_inputs, accounts, ret_age, ss_timing,
                                              life_events=life_events, surplus_allocations=surplus_allocations,
                                              jason_ret_age=jason_ret_age, justin_ret_age=justin_ret_age,
                                              jason_ss_claim_age=_jason_ss_claim_age, justin_ss_claim_age=_justin_ss_claim_age)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/simulation/contribution-sensitivity")
@app.get("/api/simulation/contribution-sensitivity")
def get_contribution_sensitivity(ret_age: int = 60, body: dict = None):
    if body is not None:
        ret_age = body.get("ret_age", ret_age)
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from simulation_engine import run_contribution_sensitivity
    return run_contribution_sensitivity(_apply_whatif_overrides(dict(inputs_row), body or {}), accounts, ret_age, life_events=life_events, surplus_allocations=surplus_allocations)

@app.get("/api/simulation/survivor-scenario")
def get_survivor_scenario(ret_age: int = 60, deceased: str = "jason", death_age: int = None,
                           survivor_need_factor: float = 0.75,
                           jason_ret_age: int = None, justin_ret_age: int = None,
                           ss_timing: str = "early",
                           trust_available_to_survivor: bool = False,
                           joint_accounts_survivorship: bool = True,
                           spousal_rollover_election: bool = True,
                           jason_ss_claim_age: int = None, justin_ss_claim_age: int = None):
    """jason_ret_age/justin_ret_age (2026-09-08, CALCULATION_CONTRACT.md
    sections 36-37, Milestone 4 of 4): two-age mode with an owner-
    attributed account ledger, both ages required together. The three
    trailing booleans are two-age-only explicit scenario assumptions
    (section 37.1-37.3) with no single-axis equivalent -- ignored
    entirely when jason_ret_age/justin_ret_age are left at their None
    default.

    jason_ss_claim_age/justin_ss_claim_age (2026-09-09,
    CALCULATION_CONTRACT.md section 54): explicit per-request claim
    age, takes priority over saved Settings -- see get_monte_carlo's
    identical param."""
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from simulation_engine import run_survivor_scenario
    _inputs = dict(inputs_row)
    _jason_ss_claim_age, _justin_ss_claim_age = _ss_claim_ages(_inputs, jason_ss_claim_age, justin_ss_claim_age)
    try:
        return run_survivor_scenario(_inputs, accounts, ret_age, deceased, death_age, survivor_need_factor,
                                      life_events=life_events, surplus_allocations=surplus_allocations,
                                      jason_ret_age=jason_ret_age, justin_ret_age=justin_ret_age, ss_timing=ss_timing,
                                      trust_available_to_survivor=trust_available_to_survivor,
                                      joint_accounts_survivorship=joint_accounts_survivorship,
                                      spousal_rollover_election=spousal_rollover_election,
                                      jason_ss_claim_age=_jason_ss_claim_age, justin_ss_claim_age=_justin_ss_claim_age)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/simulation/sequence-risk")
def get_sequence_risk(ret_age: int = 55, ss_timing: str = "early",
                       jason_ss_claim_age: int = None, justin_ss_claim_age: int = None):
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from simulation_engine import run_stress_tests
    _inputs = dict(inputs_row)
    _jason_ss_claim_age, _justin_ss_claim_age = _ss_claim_ages(_inputs, jason_ss_claim_age, justin_ss_claim_age)
    result = run_stress_tests(_inputs, accounts, ret_age, ss_timing, life_events=life_events, surplus_allocations=surplus_allocations,
                               jason_ss_claim_age=_jason_ss_claim_age, justin_ss_claim_age=_justin_ss_claim_age)
    # Return only the new scenarios
    return {
        "early_sequence":  result["scenarios"].get("early_sequence"),
        "bridge_job_loss": result["scenarios"].get("bridge_job_loss"),
        "ss_reduction":    result["scenarios"].get("ss_reduction"),
        "base":            result["scenarios"].get("base"),
    }

@app.post("/api/simulation/roth-conversion")
@app.get("/api/simulation/roth-conversion")
def get_roth_conversion(ret_age: int = 60, ss_timing: str = "early", body: dict = None,
                         jason_ret_age: int = None, justin_ret_age: int = None,
                         jason_ss_claim_age: int = None, justin_ss_claim_age: int = None):
    """jason_ret_age/justin_ret_age (2026-09-08, CALCULATION_CONTRACT.md
    section 30/32, Milestone 2 of 4): two-age mode, both required
    together -- read from the query string (GET) or, same as
    ret_age/ss_timing above, from the POST body if present.

    jason_ss_claim_age/justin_ss_claim_age (2026-09-09,
    CALCULATION_CONTRACT.md section 54): explicit per-request claim
    age, takes priority over saved Settings -- see get_monte_carlo's
    identical param."""
    if body is not None:
        ret_age = body.get("ret_age", ret_age)
        ss_timing = body.get("ss_timing", ss_timing)
        jason_ret_age = body.get("jason_ret_age", jason_ret_age)
        justin_ret_age = body.get("justin_ret_age", justin_ret_age)
        jason_ss_claim_age = body.get("jason_ss_claim_age", jason_ss_claim_age)
        justin_ss_claim_age = body.get("justin_ss_claim_age", justin_ss_claim_age)
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from simulation_engine import run_roth_conversion_analysis
    inputs = _apply_whatif_overrides(dict(inputs_row), body or {})
    _jason_ss_claim_age, _justin_ss_claim_age = _ss_claim_ages(inputs, jason_ss_claim_age, justin_ss_claim_age)
    try:
        return run_roth_conversion_analysis(inputs, accounts, ret_age, ss_timing, life_events=life_events,
                                             surplus_allocations=surplus_allocations,
                                             jason_ret_age=jason_ret_age, justin_ret_age=justin_ret_age,
                                             jason_ss_claim_age=_jason_ss_claim_age, justin_ss_claim_age=_justin_ss_claim_age)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/simulation/swr")
@app.get("/api/simulation/swr")
def get_swr(ret_age: int = 60, ss_timing: str = "early", body: dict = None,
            jason_ret_age: int = None, justin_ret_age: int = None,
            jason_ss_claim_age: int = None, justin_ss_claim_age: int = None):
    """jason_ret_age/justin_ret_age (2026-09-08, CALCULATION_CONTRACT.md
    section 25/26, Milestone 1 of 4): two-age mode, both required
    together -- read from the query string (GET) or, same as
    ret_age/ss_timing above, from the POST body if present.

    jason_ss_claim_age/justin_ss_claim_age (2026-09-09,
    CALCULATION_CONTRACT.md section 54): explicit per-request claim
    age, takes priority over saved Settings -- see get_monte_carlo's
    identical param."""
    if body is not None:
        ret_age = body.get("ret_age", ret_age)
        ss_timing = body.get("ss_timing", ss_timing)
        jason_ret_age = body.get("jason_ret_age", jason_ret_age)
        justin_ret_age = body.get("justin_ret_age", justin_ret_age)
        jason_ss_claim_age = body.get("jason_ss_claim_age", jason_ss_claim_age)
        justin_ss_claim_age = body.get("justin_ss_claim_age", justin_ss_claim_age)
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs ORDER BY id DESC LIMIT 1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row: return {"error": "No planning inputs found"}
    from simulation_engine import run_swr_analysis
    inputs = _apply_whatif_overrides(dict(inputs_row), body or {})
    _jason_ss_claim_age, _justin_ss_claim_age = _ss_claim_ages(inputs, jason_ss_claim_age, justin_ss_claim_age)
    try:
        return run_swr_analysis(inputs, accounts, ret_age, ss_timing, life_events=life_events,
                                 surplus_allocations=surplus_allocations,
                                 jason_ret_age=jason_ret_age, justin_ret_age=justin_ret_age,
                                 jason_ss_claim_age=_jason_ss_claim_age, justin_ss_claim_age=_justin_ss_claim_age)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/simulation/stress-tests")
def get_stress_tests(ret_age: int = 60, ss_timing: str = "early",
                      jason_ret_age: int = None, justin_ret_age: int = None,
                      jason_ss_claim_age: int = None, justin_ss_claim_age: int = None):
    """jason_ret_age/justin_ret_age: see get_monte_carlo's identical
    params (CALCULATION_CONTRACT.md section 22). jason_ss_claim_age/
    justin_ss_claim_age (2026-09-09, section 54): explicit per-request
    claim age, takes priority over saved Settings."""
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    from simulation_engine import run_stress_tests
    _inputs = dict(inputs_row)
    _jason_ss_claim_age, _justin_ss_claim_age = _ss_claim_ages(_inputs, jason_ss_claim_age, justin_ss_claim_age)
    try:
        return run_stress_tests(_inputs, accounts, ret_age, ss_timing, life_events=life_events,
                                 surplus_allocations=surplus_allocations,
                                 jason_ret_age=jason_ret_age, justin_ret_age=justin_ret_age,
                                 jason_ss_claim_age=_jason_ss_claim_age, justin_ss_claim_age=_justin_ss_claim_age)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/simulation/stress-tests")
def post_stress_tests(body: dict):
    """What-If-aware variant of the GET endpoint above — see
    post_monte_carlo's docstring; same override fields, same reason
    (external audit 2026-09-06). jason_ret_age/justin_ret_age: see
    post_monte_carlo's identical params (CALCULATION_CONTRACT.md
    section 23)."""
    ret_age    = body.get("ret_age", 60)
    ss_timing  = body.get("ss_timing", "early")
    jason_ret_age  = body.get("jason_ret_age")
    justin_ret_age = body.get("justin_ret_age")
    jason_ss_claim_age  = body.get("jason_ss_claim_age")
    justin_ss_claim_age = body.get("justin_ss_claim_age")
    conn = get_db()
    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    accounts   = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    life_events = _get_active_life_events(conn)
    surplus_allocations = _get_relevant_surplus_allocations(conn)
    conn.close()
    if not inputs_row:
        raise HTTPException(status_code=400, detail="Planning inputs not set yet")
    inputs = _apply_whatif_overrides(dict(inputs_row), body)
    _jason_ss_claim_age, _justin_ss_claim_age = _ss_claim_ages(inputs, jason_ss_claim_age, justin_ss_claim_age)
    from simulation_engine import run_stress_tests
    try:
        return run_stress_tests(inputs, accounts, ret_age, ss_timing, life_events=life_events,
                                 surplus_allocations=surplus_allocations,
                                 jason_ret_age=jason_ret_age, justin_ret_age=justin_ret_age,
                                 jason_ss_claim_age=_jason_ss_claim_age, justin_ss_claim_age=_justin_ss_claim_age)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
