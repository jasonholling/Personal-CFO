"""
Shared fixtures for the backend test suite.

Deliberately synthetic values (round numbers, fake account names) — never
copy real figures from cfo.db into a test file. Tests should exercise the
calculation *shape*, not validate against your actual finances.

IMPORTANT: db.py creates/touches its sqlite file at *import time*
(init_tasks_table() runs unconditionally at module load), and main.py calls
init_db() at import time too. Both default to the real cfo.db path. The
CFO_DB_PATH env var override below MUST be set before `db` or `main` is
imported anywhere in the test session, which is why it happens at the very
top of this file — pytest always loads conftest.py before collecting any
test_*.py module in the same directory.

Same reasoning for APP_PASSPHRASE/APP_PASSPHRASE_HASH: auth.py loads
backend/.env at import time. If you've set a real passphrase there (either
the plaintext APP_PASSPHRASE fallback or, after completing setup through
the running app, the persisted APP_PASSPHRASE_HASH), forcing both empty
here (before `main`/`auth` import) keeps the auth middleware off during
tests — auth.py only loads a key from .env if it isn't already in
os.environ, so this wins. Without this, every TestClient call in the suite
would need a session cookie, and pytest would start failing the moment you
configure the app lock for real, for reasons that have nothing to do with
the code changed. (APP_PASSPHRASE_HASH was missing from this override
until 2026-09-06 — it only surfaced once a real passphrase had actually
been set up through the running app, silently failing every auth-dependent
test in the suite until then.)
"""
import os
import tempfile
from pathlib import Path

_TEST_DB_DIR = tempfile.mkdtemp(prefix="personal_cfo_test_")
os.environ["CFO_DB_PATH"] = str(Path(_TEST_DB_DIR) / "conftest_import_time.db")
os.environ["APP_PASSPHRASE"] = ""
os.environ["APP_PASSPHRASE_HASH"] = ""

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import db as db_module
import auth as auth_module

_REAL_DB_PATH = str(Path(__file__).resolve().parent.parent / "cfo.db")


@pytest.fixture(autouse=True)
def _isolate_webauthn_credential(tmp_path, monkeypatch):
    """auth.py's WebAuthn credential file defaults to a real path on disk
    (backend/.webauthn_credential.json) — once Touch ID is actually
    registered for real, tests that assume 'nothing registered yet' would
    silently start reading that real file instead of a clean slate.
    Isolate every test the same way temp_db isolates cfo.db."""
    monkeypatch.setattr(auth_module, "_CREDENTIAL_PATH", str(tmp_path / "test_webauthn_credential.json"))


@pytest.fixture(autouse=True)
def _skip_first_run_auth_setup(tmp_path, monkeypatch):
    """With APP_PASSPHRASE forced empty above, auth.setup_required() would
    otherwise be True for every test (a fresh install that hasn't gone
    through first-run setup yet) and every /api route except the auth ones
    would 401 — not what any non-auth test wants. Simulate "user already
    clicked skip" by pointing the disabled-marker path at a tmp file that
    exists, the same way _isolate_webauthn_credential isolates its file.
    Tests that care about the real first-run flow (TestAuthEndpoints) point
    it elsewhere or delete it to exercise setup_required() for real."""
    marker = tmp_path / "test_auth_disabled"
    marker.write_text("skipped for tests\n")
    monkeypatch.setattr(auth_module, "_AUTH_DISABLED_PATH", str(marker))


@pytest.fixture(autouse=True)
def _guard_never_touch_real_db():
    """Hard safety net: if this ever fails, STOP — it means some test or
    fixture pointed the app at your real financial data instead of a
    throwaway temp file."""
    assert db_module.DB_PATH != _REAL_DB_PATH, (
        "A test is pointed at the real cfo.db! This must never happen — "
        "check that CFO_DB_PATH / the temp_db fixture is wired correctly."
    )
    yield
    assert db_module.DB_PATH != _REAL_DB_PATH


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """A fresh, isolated sqlite db for a single test — never the real cfo.db."""
    db_path = str(tmp_path / "test_cfo.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module.init_db()
    db_module.init_tasks_table()
    db_module.init_cash_flow_table()
    db_module.init_surplus_allocation_table()
    db_module.init_saved_scenarios_table()
    db_module.init_life_events_table()
    db_module.init_cfo_operating_tables()
    db_module.init_kids_table()
    yield db_path


@pytest.fixture
def client(temp_db):
    """A FastAPI TestClient wired to the same isolated temp_db."""
    from fastapi.testclient import TestClient
    import main as main_module
    return TestClient(main_module.app)


@pytest.fixture
def sample_inputs():
    """A complete planning_inputs-shaped dict with sane synthetic defaults.
    Individual tests should override specific keys via `{**sample_inputs, "key": val}`
    rather than mutating this fixture."""
    return {
        "person1_name": "Alex", "person2_name": "Sam",
        "kid1_name": "Kid A", "kid2_name": "Kid B",
        "jason_age": 50, "justin_age": 48,
        "kid1_age": 10, "kid2_age": 8,
        "retirement_income_today_dollars": 100000,
        "inflation_rate": 0.02,
        "expected_return_pre_retirement": 0.07,
        "expected_return_post_retirement": 0.06,
        "jason_social_security": 30000,
        "jason_ss_delayed": 45000,
        "justin_social_security": 15000,
        "jason_ss_age": 62,
        "justin_ss_age": 67,
        "annual_401k_contribution": 20000,
        "annual_roth_contribution": 0,
        "annual_hsa_contribution": 8000,
        "annual_rsu_value": 0,
        "mortgage_balance": 200000,
        "pretax_401k_pct": 0.75,
        "employee_401k_pct": 0.06,
        "employer_401k_pct": 0.03,
        "w2_salary": 150000,
        "abby_529_monthly": 100,
        "cooper_529_monthly": 100,
        "kids_roth_monthly": 50,
        "kids_custodial_monthly": 50,
        "unl_annual_cost": 20000,
        "healthcare_pre_medicare": 20000,
        "healthcare_post_medicare": 5000,
        "healthcare_kids": 0,
        "kids_annual_cost": 0,
        "bridge_income_55": 0,
        "bridge_years_55": 0,
        "kids_years_at_home_55": 0,
        "pension_55": 20000,
        "pension_60": 30000,
        "pension_65": 35000,
        "jason_life_basic": 0, "jason_life_supplemental": 0, "jason_life_term": 0,
        "justin_life_ul": 0, "justin_life_whole": 0, "justin_life_conagra": 0,
        "justin_life_term": 0, "justin_life_kids": 0,
        "disability_monthly": 0, "ltc_daily": 200, "ltc_max": 0,
        "home_insured": 0, "umbrella": 0,
        "asset1_label": "", "asset1_sale_age": 0, "asset1_sale_net": 0, "asset1_appreciation": 0.03,
        "asset2_label": "", "asset2_sale_age": 0, "asset2_sale_net": 0,
        "primary_residence_key": "", "rental_property_key": "",
    }


@pytest.fixture
def sample_kids():
    """Kids-variable-count (2026-09-09) — replaces the old fixed
    kid1_age/kid2_age/abby_529_monthly/cooper_529_monthly fields
    sample_inputs above still carries (left there harmlessly for tests
    that don't touch kids at all). id 1/2 deliberately mirror the
    values sample_inputs used to hardcode for kid1/kid2, so tests
    written against those numbers don't need their expected values to
    change, only how they pass them in (kids=sample_kids instead of
    reading kid1_age/kid1_name off inputs). Account fixtures owned by a
    kid should use "kid_1"/"kid_2" to match these ids."""
    return [
        {"id": 1, "name": "Kid A", "age": 10, "monthly_529": 100, "display_order": 0},
        {"id": 2, "name": "Kid B", "age": 8,  "monthly_529": 100, "display_order": 1},
    ]


@pytest.fixture
def sample_accounts():
    """A minimal but representative set of accounts across bucket types."""
    return [
        {"id": 1, "name": "401k Test",        "account_type": "401k",      "owner": "jason", "balance": 500000, "institution": "", "notes": ""},
        {"id": 2, "name": "Roth IRA Test",    "account_type": "roth_ira",  "owner": "jason", "balance": 100000, "institution": "", "notes": ""},
        {"id": 3, "name": "Traditional IRA",  "account_type": "ira",       "owner": "justin","balance": 50000,  "institution": "", "notes": ""},
        {"id": 4, "name": "Brokerage Test",   "account_type": "taxable",   "owner": "joint", "balance": 200000, "institution": "", "notes": ""},
        {"id": 5, "name": "HSA Test",         "account_type": "hsa",      "owner": "jason", "balance": 20000,  "institution": "", "notes": ""},
        {"id": 6, "name": "Mortgage Test",    "account_type": "mortgage",  "owner": "joint", "balance": 200000, "institution": "", "notes": ""},
    ]
