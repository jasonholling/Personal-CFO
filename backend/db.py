import sqlite3
import os

DB_PATH = os.environ.get("CFO_DB_PATH") or os.path.join(os.path.dirname(__file__), "cfo.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            account_type TEXT NOT NULL,
            owner TEXT NOT NULL,
            institution TEXT NOT NULL,
            balance REAL NOT NULL DEFAULT 0,
            notes TEXT,
            interest_rate REAL DEFAULT 0,
            minimum_payment REAL DEFAULT 0,
            term_months INTEGER DEFAULT 0,
            stock_allocation_pct REAL,
            expense_ratio REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS planning_inputs (
            id INTEGER PRIMARY KEY DEFAULT 1,
            person1_name TEXT DEFAULT 'Person 1',
            person2_name TEXT DEFAULT 'Person 2',
            kid1_name TEXT DEFAULT 'Child 1',
            kid2_name TEXT DEFAULT 'Child 2',
            kid1_age INTEGER DEFAULT 0,
            kid2_age INTEGER DEFAULT 0,
            jason_age INTEGER DEFAULT 0,
            justin_age INTEGER DEFAULT 0,
            retirement_income_today_dollars REAL DEFAULT 0,
            inflation_rate REAL DEFAULT 0.02,
            expected_return_pre_retirement REAL DEFAULT 0.07,
            expected_return_post_retirement REAL DEFAULT 0.06,
            jason_social_security REAL DEFAULT 0,
            jason_ss_delayed REAL DEFAULT 0,
            unl_annual_cost REAL DEFAULT 0,
            justin_social_security REAL DEFAULT 0,
            jason_ss_age INTEGER DEFAULT 62,
            justin_ss_age INTEGER DEFAULT 67,
            annual_401k_contribution REAL DEFAULT 0,
            annual_roth_contribution REAL DEFAULT 0,
            annual_hsa_contribution REAL DEFAULT 0,
            annual_rsu_value REAL DEFAULT 0,
            annual_bonus_pct REAL DEFAULT 0,
            mortgage_balance REAL DEFAULT 0,
            pretax_401k_pct REAL DEFAULT 0.75,
            employee_401k_pct REAL DEFAULT 0.06,
            employer_401k_pct REAL DEFAULT 0.03,
            abby_529_monthly REAL DEFAULT 0,
            cooper_529_monthly REAL DEFAULT 0,
            kids_roth_monthly REAL DEFAULT 0,
            kids_custodial_monthly REAL DEFAULT 0,
            w2_salary REAL DEFAULT 0,
            jason_life_basic REAL DEFAULT 0,
            jason_life_supplemental REAL DEFAULT 0,
            jason_life_term REAL DEFAULT 0,
            justin_life_ul REAL DEFAULT 0,
            justin_life_whole REAL DEFAULT 0,
            person2_life_employer REAL DEFAULT 0,
            justin_life_term REAL DEFAULT 0,
            justin_life_kids REAL DEFAULT 0,
            disability_monthly REAL DEFAULT 0,
            ltc_daily REAL DEFAULT 200,
            ltc_max REAL DEFAULT 0,
            home_insured REAL DEFAULT 0,
            umbrella REAL DEFAULT 0,
            pension_55 REAL DEFAULT 0,
            pension_60 REAL DEFAULT 0,
            pension_65 REAL DEFAULT 0,
            healthcare_kids REAL DEFAULT 0,
            kids_annual_cost REAL DEFAULT 0,
            bridge_income_55 REAL DEFAULT 0,
            bridge_years_55 REAL DEFAULT 0,
            kids_years_at_home_55 REAL DEFAULT 0,
            asset1_label TEXT DEFAULT 'Asset 1',
            asset1_sale_age REAL DEFAULT 0,
            asset1_sale_net REAL DEFAULT 0,
            asset1_appreciation REAL DEFAULT 0.03,
            asset2_label TEXT DEFAULT 'Asset 2',
            asset2_sale_age REAL DEFAULT 0,
            asset2_sale_net REAL DEFAULT 0,
            primary_residence_key TEXT DEFAULT '',
            rental_property_key TEXT DEFAULT '',
            retirement_end_age INTEGER DEFAULT 99,
            state_income_tax_rate REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS insurance_policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            who TEXT NOT NULL,
            policy_type TEXT NOT NULL,
            benefit TEXT,
            premium TEXT,
            notes TEXT,
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS property_policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item TEXT NOT NULL,
            coverage TEXT,
            renewal TEXT,
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS kids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            age INTEGER NOT NULL DEFAULT 0,
            monthly_529 REAL NOT NULL DEFAULT 0,
            display_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            total_assets REAL,
            liabilities REAL,
            net_worth REAL,
            accounts_json TEXT,
            note TEXT
        );
    """)
    conn.commit()

    existing = conn.execute("SELECT id FROM planning_inputs WHERE id=1").fetchone()
    if not existing:
        conn.execute("INSERT INTO planning_inputs (id) VALUES (1)")
        conn.commit()

    # Migrate accounts: add new columns if they don't exist (e.g. an
    # existing cfo.db created before the debt-payoff fields were added)
    accounts_cols = [r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
    accounts_migrations = [
        ("interest_rate",    "REAL DEFAULT 0"),
        ("minimum_payment",  "REAL DEFAULT 0"),
        ("term_months",      "INTEGER DEFAULT 0"),
        ("stock_allocation_pct", "REAL"),
        ("expense_ratio",    "REAL DEFAULT 0"),
        ("monthly_rental_income",   "REAL DEFAULT 0"),
        ("monthly_rental_expenses", "REAL DEFAULT 0"),
    ]
    for col, typedef in accounts_migrations:
        if col not in accounts_cols:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} {typedef}")
    conn.commit()

    # Migrate: add new columns if they don't exist
    existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(planning_inputs)").fetchall()]
    migrations = [
        ("person1_name",           "TEXT DEFAULT 'Person 1'"),
        ("person2_name",           "TEXT DEFAULT 'Person 2'"),
        ("kid1_name",              "TEXT DEFAULT 'Child 1'"),
        ("kid2_name",              "TEXT DEFAULT 'Child 2'"),
        ("kid1_age",               "INTEGER DEFAULT 0"),
        ("kid2_age",               "INTEGER DEFAULT 0"),
        ("pretax_401k_pct",        "REAL DEFAULT 0.75"),
        ("employee_401k_pct",      "REAL DEFAULT 0.06"),
        ("employer_401k_pct",      "REAL DEFAULT 0.03"),
        ("abby_529_monthly",       "REAL DEFAULT 0"),
        ("cooper_529_monthly",     "REAL DEFAULT 0"),
        ("kids_roth_monthly",      "REAL DEFAULT 0"),
        ("kids_custodial_monthly", "REAL DEFAULT 0"),
        ("w2_salary",              "REAL DEFAULT 0"),
        ("jason_life_basic",       "REAL DEFAULT 0"),
        ("jason_life_supplemental","REAL DEFAULT 0"),
        ("jason_life_term",        "REAL DEFAULT 0"),
        ("justin_life_ul",         "REAL DEFAULT 0"),
        ("justin_life_whole",      "REAL DEFAULT 0"),
        ("person2_life_employer",  "REAL DEFAULT 0"),
        ("justin_life_term",       "REAL DEFAULT 0"),
        ("justin_life_kids",       "REAL DEFAULT 0"),
        ("disability_monthly",     "REAL DEFAULT 0"),
        ("ltc_daily",              "REAL DEFAULT 200"),
        ("ltc_max",                "REAL DEFAULT 0"),
        ("home_insured",           "REAL DEFAULT 0"),
        ("umbrella",               "REAL DEFAULT 0"),
        ("pension_55",             "REAL DEFAULT 0"),
        ("pension_60",             "REAL DEFAULT 0"),
        ("pension_65",             "REAL DEFAULT 0"),
        ("healthcare_pre_medicare",  "REAL DEFAULT 0"),  # annual cost age 60-65
        ("healthcare_post_medicare", "REAL DEFAULT 0"),   # annual Medicare + supplement
        ("healthcare_kids",         "REAL DEFAULT 0"),
        ("kids_annual_cost",        "REAL DEFAULT 0"),
        ("bridge_income_55",        "REAL DEFAULT 0"),
        ("bridge_years_55",         "REAL DEFAULT 0"),
        ("kids_years_at_home_55",   "REAL DEFAULT 0"),
        ("asset1_label",            "TEXT DEFAULT 'Asset 1'"),
        ("asset1_sale_age",         "REAL DEFAULT 0"),
        ("asset1_sale_net",         "REAL DEFAULT 0"),
        ("asset1_appreciation",     "REAL DEFAULT 0.03"),
        ("asset2_label",            "TEXT DEFAULT 'Asset 2'"),
        ("asset2_sale_age",         "REAL DEFAULT 0"),
        ("asset2_sale_net",         "REAL DEFAULT 0"),
        ("primary_residence_key",   "TEXT DEFAULT ''"),
        ("rental_property_key",     "TEXT DEFAULT ''"),
        ("current_monthly_expenses", "REAL DEFAULT 0"),  # for the Emergency Fund check — actual current spending, not the retirement income target
        ("retirement_end_age",       "INTEGER DEFAULT 99"),
        ("state_income_tax_rate",    "REAL DEFAULT 0"),
        ("annual_bonus_pct",         "REAL DEFAULT 0"),  # recurring annual bonus, as a fraction of w2_salary (e.g. 0.20 for 20%) — scales with salary_growth_pct like 401k contributions, unlike the flat-dollar annual_rsu_value
        # Justin's own pre-retirement earnings profile (2026-09-08) — mirrors
        # w2_salary/employee_401k_pct/employer_401k_pct/annual_bonus_pct/
        # annual_rsu_value above, for a household where both spouses work
        # full-time instead of one combined/breadwinner-shaped income. All
        # default to 0/unset so an existing single-earner household sees no
        # behavior change: justin_w2_salary=0 means Justin's contribution
        # terms are all 0 regardless of the pct fields, and justin_ret_age=0
        # falls back to the pre-existing age-gap-derived retirement age
        # (same as every consumer already computed before this existed).
        ("justin_w2_salary",           "REAL DEFAULT 0"),
        ("justin_employee_401k_pct",   "REAL DEFAULT 0.06"),
        ("justin_employer_401k_pct",   "REAL DEFAULT 0.03"),
        ("justin_annual_bonus_pct",    "REAL DEFAULT 0"),
        ("justin_annual_rsu_value",    "REAL DEFAULT 0"),
        ("justin_ret_age",             "INTEGER DEFAULT 0"),  # 0 = not independently set; see run_retirement_projection
        # Social Security claiming age 62-70 (2026-09-08,
        # CALCULATION_CONTRACT.md section 44) — jason_ss_claim_age/
        # justin_ss_claim_age default to NULL (not 62), meaning "not set,
        # use the existing early/delayed ss_timing toggle" — an existing
        # household with these unset sees NO behavior change (the engine's
        # own resolve_ss_benefits only switches to continuous-claim-age
        # mode when a claim age is explicitly provided, never inferred
        # from a default). jason_ss_70/justin_ss_early/justin_ss_70 are
        # the real dollar anchors a household's own SSA.gov statement
        # shows at age 70 (and, for Justin, at 62 — Jason already has
        # jason_social_security/jason_ss_delayed for 62/67); default 0,
        # same "no assumption without real inputs" convention as every
        # other SS dollar field.
        ("jason_ss_claim_age",         "INTEGER"),
        ("justin_ss_claim_age",        "INTEGER"),
        ("jason_ss_70",                "REAL DEFAULT 0"),
        ("justin_ss_early",            "REAL DEFAULT 0"),
        ("justin_ss_70",               "REAL DEFAULT 0"),
        # Kids-variable-count (2026-09-09) — set to 1 the first time
        # migrate_legacy_kids below actually runs its conversion (whether
        # it finds signal and creates kids, or finds none and creates
        # zero), so it never runs a second time. Without this, a
        # household that migrates and then deletes back down to 0 kids
        # would have those kids resurrected on the NEXT backend restart —
        # migrate_legacy_kids is invoked from init_kids_table(), which
        # (like init_tasks_table()/init_cash_flow_table() above) runs
        # unconditionally at db.py's own import time, i.e. EVERY process
        # start, not once ever. Checking "does the kids table have any
        # row" alone can't distinguish "never migrated" from "migrated,
        # then deliberately emptied" — this flag can.
        ("kids_migrated",              "INTEGER DEFAULT 0"),
        # Insurance page follow-up (2026-09-09) — three fields the
        # Insurance page displayed but had nowhere to edit: rental_insured
        # was hardcoded to 0 in run_insurance_analysis (the Rental
        # Property card always said "Coverage unknown / Verify current
        # policy" no matter what, since there was never a real value
        # behind it), and disability_funded_by/ltc_premium_annual were
        # hardcoded strings/numbers ("Employer group policy", 369)
        # unconditionally rendered with a green checkmark regardless of
        # whether that was ever true for this household. All three now
        # mirror the existing home_insured/umbrella pattern -- a real
        # editable Settings field feeding the actual displayed value.
        ("rental_insured",             "REAL DEFAULT 0"),
        ("disability_funded_by",       "TEXT DEFAULT 'Employer group policy'"),
        ("disability_to_age",          "INTEGER DEFAULT 65"),
        ("ltc_premium_annual",         "REAL DEFAULT 369"),
    ]
    for col, typedef in migrations:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE planning_inputs ADD COLUMN {col} {typedef}")
    # Preserve a value from an earlier local-only schema whose column name
    # exposed an employer. The legacy column remains only in existing local
    # databases; new/public schemas use the neutral field above.
    if "justin_life_conagra" in existing_cols:
        conn.execute("UPDATE planning_inputs SET person2_life_employer=justin_life_conagra WHERE person2_life_employer=0")
    conn.commit()

    conn.close()

def init_tasks_table():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            task_type TEXT DEFAULT 'manual',
            recurrence TEXT DEFAULT 'once',
            auto_key TEXT,
            completed INTEGER DEFAULT 0,
            completed_date TEXT,
            due_year INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()

init_tasks_table()

def init_cash_flow_table():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cash_flow_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            cash_flow_type TEXT NOT NULL CHECK (cash_flow_type IN ('income', 'expense')),
            category TEXT NOT NULL DEFAULT 'Other',
            amount REAL NOT NULL DEFAULT 0 CHECK (amount >= 0),
            essential INTEGER NOT NULL DEFAULT 0 CHECK (essential IN (0, 1)),
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()

init_cash_flow_table()

def init_kids_table():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            age INTEGER NOT NULL DEFAULT 0,
            monthly_529 REAL NOT NULL DEFAULT 0,
            display_order INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.commit()
    migrate_legacy_kids(conn)
    repair_dangling_legacy_education_goals(conn)
    conn.close()

def migrate_legacy_kids(conn):
    """One-time conversion from the old fixed-2-kids model (planning_inputs'
    kid1_name/kid2_name/kid1_age/kid2_age/abby_529_monthly/cooper_529_monthly
    columns, plus the literal 'abby'/'cooper' account owner strings) to the
    variable-count (0-5) `kids` table, where each kid's stable identity is
    its own row id and account owner becomes f"kid_{id}" instead of a name-
    derived string -- see CALCULATION_CONTRACT.md's kids-variable-count
    section for the full design.

    Exposed as its own function (not inlined into init_kids_table below)
    so a test can call it directly against a fixture it sets up itself --
    init_kids_table()'s own module-level call at the bottom of this file
    only ever fires once per process, at db.py's own import time, before
    any test's planning_inputs/accounts rows exist yet.

    ONE-TIME conversion, not an ongoing sync -- guarded by planning_inputs'
    own kids_migrated flag (set at the end of this function, whether or
    not any kid actually got created), NOT by "does the kids table
    currently have a row." A household that migrates and then deletes
    back down to 0 kids must STAY at 0 on the next backend restart --
    checking only "kids table is empty" can't tell that apart from
    "never migrated," and this function is invoked from
    init_kids_table() below, which (like init_tasks_table()/
    init_cash_flow_table() above) runs unconditionally at db.py's own
    import time -- i.e. EVERY process start, not once ever.

    Also exposed as its own function (not inlined into init_kids_table)
    so a test can call it directly against a fixture it sets up itself --
    init_kids_table()'s own module-level call at the bottom of this file
    only ever fires once per process, before any test's planning_inputs/
    accounts rows exist yet.
    """
    if conn.execute("SELECT 1 FROM kids LIMIT 1").fetchone():
        return  # the user has already configured kids for real -- never touch this

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "planning_inputs" not in tables:
        return  # fresh DB -- init_db() hasn't created it yet, nothing to migrate yet

    # This function can run BEFORE init_db()'s own column migrations,
    # on the very first import of this code against an existing real
    # cfo.db (db.py's module-level init_kids_table() call fires during
    # `from db import ...`, before main.py's explicit init_db() call a
    # few lines later) -- so kids_migrated may not exist as a column
    # yet. Ensured here directly rather than relying on call order.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(planning_inputs)").fetchall()]
    if "kids_migrated" not in cols:
        conn.execute("ALTER TABLE planning_inputs ADD COLUMN kids_migrated INTEGER DEFAULT 0")
        conn.commit()

    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    if not inputs_row:
        return
    inputs = dict(inputs_row)
    if inputs.get("kids_migrated"):
        return  # already ran once (created 0+ kids) -- never re-check legacy signal again

    accounts_owners = set()
    if "accounts" in tables:
        accounts_owners = {r[0] for r in conn.execute("SELECT DISTINCT owner FROM accounts").fetchall()}

    # Each legacy slot only becomes a real kid row if there's actual
    # signal it was used -- a brand-new install also has kid1_name=
    # 'Child 1'/kid1_age=0/no 'abby' accounts, indistinguishable from
    # "never configured." Migrating that as a phantom kid would silently
    # give a fresh install 2 kids by default, defeating the whole point
    # of "could have zero kids."
    legacy_slots = [
        ("kid1_name", "kid1_age", "abby_529_monthly",   "abby",   "Child 1", "Education funding - Abby"),
        ("kid2_name", "kid2_age", "cooper_529_monthly", "cooper", "Child 2", "Education funding - Cooper"),
    ]
    order = 0
    for name_col, age_col, monthly_col, legacy_owner, default_name, legacy_goal in legacy_slots:
        name    = inputs.get(name_col) or default_name
        age     = inputs.get(age_col) or 0
        monthly = inputs.get(monthly_col) or 0
        has_signal = (name != default_name) or bool(age) or bool(monthly) or (legacy_owner in accounts_owners)
        if not has_signal:
            continue
        cur = conn.execute(
            "INSERT INTO kids (name, age, monthly_529, display_order) VALUES (?,?,?,?)",
            (name, age, monthly, order),
        )
        new_owner = f"kid_{cur.lastrowid}"
        if "accounts" in tables:
            conn.execute("UPDATE accounts SET owner=? WHERE owner=?", (new_owner, legacy_owner))
        # External audit follow-up, 2026-09-09: the account-owner remap
        # above was the whole story for accounts, but a household could
        # ALSO have an existing surplus_allocations row earmarking money
        # to this kid's education fund under the OLD fixed goal key
        # ("Education funding - Abby"/"Cooper" -- see SurplusPlan.jsx's
        # and main.py's _get_kids_surplus_529_monthly's own history).
        # That row's goal string was never touched by anything else in
        # this migration, so a household with e.g. $500/mo already
        # earmarked would have it silently become invisible: the new
        # UI/backend only ever look up "Education funding - kid_<id>",
        # never the old literal name, so that $500/mo would vanish from
        # every education projection while still sitting in the table
        # under a goal string nothing reads anymore. Remap it here,
        # using the exact new id just assigned above, same as accounts.
        if "surplus_allocations" in tables:
            conn.execute(
                "UPDATE surplus_allocations SET goal=? WHERE goal=?",
                (f"Education funding - {new_owner}", legacy_goal),
            )
        order += 1
    # Set unconditionally, even when neither slot had signal (0 kids is a
    # valid, real outcome) -- this is what makes the conversion run only
    # once ever, instead of re-checking legacy signal on every restart.
    conn.execute("UPDATE planning_inputs SET kids_migrated=1 WHERE id=1")
    conn.commit()


def repair_dangling_legacy_education_goals(conn):
    """Independently versioned repair (external audit, 2026-09-09, P2)
    for a real gap in migrate_legacy_kids above: its own goal-key remap
    (the "surplus_allocations SET goal=..." block just above) is only
    reachable while migrate_legacy_kids's OWN one-time kids_migrated
    guard hasn't fired yet. A household whose kids were already
    migrated (kids_migrated=1) BEFORE that remap code existed -- i.e.
    ran the first version of this migration, back when it only remapped
    account owners -- would keep a dangling "Education funding - Abby"/
    "Cooper" surplus_allocations row forever: migrate_legacy_kids
    returns immediately on every later call and never runs its body
    again, so that row's $/mo would never reach any projection.

    Runs unconditionally, on its OWN separate flag
    (legacy_surplus_goal_repair_done) and its OWN separate guard, so it
    reaches a database in exactly that stuck state regardless of
    whatever migrate_legacy_kids already did or didn't do.

    Identity mapping: matches by NAME against planning_inputs' own
    still-present kid1_name/kid2_name columns (migrate_legacy_kids
    never clears them). This is "reliable" in the specific sense asked
    for -- it either correctly identifies today's kids-table row for
    that legacy slot (the household hasn't renamed that kid since
    migrating), or explicitly leaves the dangling row alone (the name
    no longer matches anything). It deliberately does NOT fall back to
    guessing by row id/creation order, which could confidently remap to
    the WRONG kid instead of just failing to find the right one.
    """
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if not {"planning_inputs", "kids", "surplus_allocations"} <= tables:
        return

    cols = [r[1] for r in conn.execute("PRAGMA table_info(planning_inputs)").fetchall()]
    if "legacy_surplus_goal_repair_done" not in cols:
        conn.execute("ALTER TABLE planning_inputs ADD COLUMN legacy_surplus_goal_repair_done INTEGER DEFAULT 0")
        conn.commit()

    inputs_row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    if not inputs_row:
        return
    inputs = dict(inputs_row)
    if inputs.get("legacy_surplus_goal_repair_done"):
        return

    legacy_pairs = [
        ("kid1_name", "Education funding - Abby"),
        ("kid2_name", "Education funding - Cooper"),
    ]
    for name_col, legacy_goal in legacy_pairs:
        name = inputs.get(name_col)
        if not name:
            continue
        if not conn.execute("SELECT 1 FROM surplus_allocations WHERE goal=?", (legacy_goal,)).fetchone():
            continue  # nothing dangling under this legacy key
        kid_row = conn.execute("SELECT id FROM kids WHERE name=?", (name,)).fetchone()
        if not kid_row:
            continue  # can't reliably identify which current kid this was -- leave it rather than guess
        new_goal = f"Education funding - kid_{kid_row['id']}"
        # Don't clobber a real allocation the household already made
        # under the new key (e.g. they noticed the $0 and re-entered it
        # manually) -- only migrate if nothing exists there yet.
        if conn.execute("SELECT 1 FROM surplus_allocations WHERE goal=?", (new_goal,)).fetchone():
            continue
        conn.execute("UPDATE surplus_allocations SET goal=? WHERE goal=?", (new_goal, legacy_goal))

    conn.execute("UPDATE planning_inputs SET legacy_surplus_goal_repair_done=1 WHERE id=1")
    conn.commit()


init_kids_table()

def init_surplus_allocation_table():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS surplus_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal TEXT NOT NULL,
            monthly_amount REAL NOT NULL DEFAULT 0 CHECK (monthly_amount >= 0),
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_surplus_allocations_goal
        ON surplus_allocations(goal);
    """)
    conn.commit()
    conn.close()

init_surplus_allocation_table()

def init_saved_scenarios_table():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS saved_scenarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            retirement_age INTEGER NOT NULL,
            summary_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    # Migrate: add ss_timing + assumptions_json (same PRAGMA table_info +
    # ALTER TABLE idiom used above for accounts/planning_inputs/life_events).
    # Originally a saved scenario stored only name + retirement_age and
    # ALWAYS projected against early SS claiming, with no way to tell later
    # what assumptions actually produced the saved numbers (external audit
    # 2026-09-07, finding #15). ss_timing defaults to 'early' so existing
    # rows keep meaning exactly what they always implicitly meant.
    # assumptions_json is nullable and only covers retirement_age +
    # ss_timing today — it does NOT capture What-If Builder overrides,
    # since those live as page-local React state in StressTestWhatIf.jsx
    # rather than the shared scenario module (utils/scenario.js) that
    # ss_timing/retAge live in, so there's nothing durable to read them
    # from at save time. A saved scenario is self-describing for
    # age/timing but still summary-only with respect to What-If overrides.
    saved_scenarios_cols = [r[1] for r in conn.execute("PRAGMA table_info(saved_scenarios)").fetchall()]
    if "ss_timing" not in saved_scenarios_cols:
        conn.execute("ALTER TABLE saved_scenarios ADD COLUMN ss_timing TEXT NOT NULL DEFAULT 'early'")
    if "assumptions_json" not in saved_scenarios_cols:
        conn.execute("ALTER TABLE saved_scenarios ADD COLUMN assumptions_json TEXT")
    conn.commit(); conn.close()
init_saved_scenarios_table()

def init_life_events_table():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS life_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT 'other',
            event_year INTEGER NOT NULL,
            one_time_cash_delta REAL NOT NULL DEFAULT 0,
            monthly_cash_flow_delta REAL NOT NULL DEFAULT 0,
            duration_months INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_life_events_event_year ON life_events(event_year);
    """)
    # Migrate: add included_in_projection if this table predates it (same
    # PRAGMA table_info + ALTER TABLE pattern used above for accounts/
    # planning_inputs). Defaults to 1 (on) so existing rows keep affecting
    # the projection/simulation the same way they did before this flag
    # existed — the user opts a specific event OUT, not back in.
    life_events_cols = [r[1] for r in conn.execute("PRAGMA table_info(life_events)").fetchall()]
    if "included_in_projection" not in life_events_cols:
        conn.execute("ALTER TABLE life_events ADD COLUMN included_in_projection INTEGER NOT NULL DEFAULT 1")
    # Migrate: add target_debt_account_id (nullable — most events don't
    # target a debt) for the "apply this life event's cash as a one-time
    # extra payment on a specific debt" feature. Same PRAGMA table_info +
    # ALTER TABLE idiom as included_in_projection above. NULL/absent means
    # "not debt-targeted" — the historical behavior (cash flows into the
    # taxable investment bucket in the retirement projection).
    life_events_cols = [r[1] for r in conn.execute("PRAGMA table_info(life_events)").fetchall()]
    if "target_debt_account_id" not in life_events_cols:
        conn.execute("ALTER TABLE life_events ADD COLUMN target_debt_account_id INTEGER")
    conn.execute("PRAGMA optimize")
    conn.commit(); conn.close()

init_life_events_table()

def init_cfo_operating_tables():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS estate_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'not_started',
            reviewed_on TEXT,
            next_review_on TEXT,
            location_hint TEXT,
            notes TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_estate_documents_type ON estate_documents(document_type);
        -- External audit follow-up, 2026-09-09 (P1): Estate.jsx's
        -- beneficiary-designation table (account -> primary/contingent
        -- beneficiary) was localStorage-only from the start, never
        -- backed by any table -- unlike estate_documents just above,
        -- which HAD a real table + API the whole time but the frontend
        -- never actually called it either. Both are now wired up
        -- (main.py's /api/estate-beneficiaries, Estate.jsx) and in
        -- _BACKUP_TABLES, so a fresh browser or a restored backup
        -- recovers these records instead of losing them.
        CREATE TABLE IF NOT EXISTS estate_beneficiaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_key TEXT NOT NULL,
            primary_beneficiary TEXT,
            contingent_beneficiary TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_estate_beneficiaries_key ON estate_beneficiaries(account_key);
        CREATE TABLE IF NOT EXISTS assumption_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            assumptions_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.execute("PRAGMA optimize")
    conn.commit(); conn.close()

init_cfo_operating_tables()
