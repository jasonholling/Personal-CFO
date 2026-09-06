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
            justin_life_conagra REAL DEFAULT 0,
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
            rental_property_key TEXT DEFAULT ''
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
        ("justin_life_conagra",    "REAL DEFAULT 0"),
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
    ]
    for col, typedef in migrations:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE planning_inputs ADD COLUMN {col} {typedef}")
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
    conn.commit(); conn.close()
init_saved_scenarios_table()
