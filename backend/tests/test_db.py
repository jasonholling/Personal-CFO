"""
Tests for db.py — schema creation and migrations.

Every test here uses the temp_db fixture (see conftest.py), never the real
cfo.db. The _guard_never_touch_real_db autouse fixture fails loudly if that
ever stops being true.
"""
import sqlite3

import db as db_module


def test_init_db_creates_expected_tables(temp_db):
    conn = db_module.get_db()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert {"accounts", "planning_inputs", "insurance_policies",
            "property_policies", "snapshots", "tasks"} <= tables


def test_init_db_seeds_one_planning_inputs_row(temp_db):
    conn = db_module.get_db()
    row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    conn.close()
    assert row is not None
    assert row["person1_name"] == "Person 1"


def test_init_db_is_idempotent(temp_db):
    """Calling init_db() again must not error or duplicate the seed row."""
    db_module.init_db()
    db_module.init_db()
    conn = db_module.get_db()
    count = conn.execute("SELECT COUNT(*) c FROM planning_inputs").fetchone()["c"]
    conn.close()
    assert count == 1


def test_migration_adds_missing_column_to_existing_table(temp_db):
    """Simulate an older db missing a newer column, then confirm init_db()
    backfills it via ALTER TABLE without losing existing data."""
    conn = db_module.get_db()
    conn.execute("UPDATE planning_inputs SET w2_salary=999 WHERE id=1")
    conn.execute("ALTER TABLE planning_inputs DROP COLUMN asset1_label")
    conn.commit()
    conn.close()

    db_module.init_db()

    conn = db_module.get_db()
    row = conn.execute("SELECT * FROM planning_inputs WHERE id=1").fetchone()
    conn.close()
    assert row["asset1_label"] == "Asset 1"  # column restored with default
    assert row["w2_salary"] == 999  # pre-existing data untouched


def test_get_db_returns_row_factory_connection(temp_db):
    conn = db_module.get_db()
    assert conn.row_factory == sqlite3.Row
    conn.close()


def test_accounts_table_accepts_insert(temp_db):
    conn = db_module.get_db()
    conn.execute(
        "INSERT INTO accounts (name, account_type, owner, institution, balance) VALUES (?,?,?,?,?)",
        ("Test Checking", "checking", "joint", "Test Bank", 1234.56),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM accounts WHERE name='Test Checking'").fetchone()
    conn.close()
    assert row["balance"] == 1234.56
