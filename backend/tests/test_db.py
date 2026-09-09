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
            "property_policies", "snapshots", "tasks", "estate_documents",
            "assumption_reviews", "life_events"} <= tables


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


def test_saved_scenarios_migration_adds_ss_timing_and_assumptions(temp_db):
    """Regression (external audit 2026-09-07, finding #15): an older
    saved_scenarios table (pre-dating ss_timing/assumptions_json) must be
    backfilled by init_saved_scenarios_table() without losing existing
    rows, and ss_timing must default to 'early' — the implicit behavior
    every pre-existing row always actually had."""
    conn = db_module.get_db()
    conn.execute(
        "INSERT INTO saved_scenarios (name, retirement_age, summary_json) VALUES (?,?,?)",
        ("Legacy row", 60, '{"retirement_age": 60}'),
    )
    conn.execute("ALTER TABLE saved_scenarios DROP COLUMN ss_timing")
    conn.execute("ALTER TABLE saved_scenarios DROP COLUMN assumptions_json")
    conn.commit()
    conn.close()

    db_module.init_saved_scenarios_table()

    conn = db_module.get_db()
    row = conn.execute("SELECT * FROM saved_scenarios WHERE name='Legacy row'").fetchone()
    conn.close()
    assert row["retirement_age"] == 60  # pre-existing data untouched
    assert row["ss_timing"] == "early"  # backfilled default
    assert row["assumptions_json"] is None


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


class TestMigrateLegacyKids:
    """Kids-variable-count (2026-09-09): converts the OLD fixed
    kid1_name/kid2_name/kid1_age/kid2_age/abby_529_monthly/
    cooper_529_monthly planning_inputs columns + literal "abby"/"cooper"
    account owners into the new `kids` table, exactly once. The
    temp_db fixture already calls init_kids_table() (which calls
    migrate_legacy_kids itself) against a fresh install with no legacy
    signal, so every test here re-invokes migrate_legacy_kids directly
    after seeding legacy-shaped data, mirroring what happens the first
    time an upgraded backend starts against a real pre-existing cfo.db."""

    def test_fresh_install_migrates_to_zero_kids(self, temp_db):
        # temp_db's own setup already ran this against all-default
        # planning_inputs with no accounts -- confirms it stayed at zero
        # rather than materializing two phantom kids.
        conn = db_module.get_db()
        count = conn.execute("SELECT COUNT(*) FROM kids").fetchone()[0]
        conn.close()
        assert count == 0

    def test_both_legacy_kids_migrate_with_accounts_reowned(self, temp_db):
        conn = db_module.get_db()
        # temp_db's own setup already ran migrate_legacy_kids once (against
        # an all-default install, setting kids_migrated=1) -- reset it to
        # simulate an existing real cfo.db upgrading for the first time,
        # which is the actual scenario this test is covering.
        conn.execute("UPDATE planning_inputs SET kids_migrated=0 WHERE id=1")
        conn.execute(
            "UPDATE planning_inputs SET kid1_name=?, kid1_age=?, abby_529_monthly=?, "
            "kid2_name=?, kid2_age=?, cooper_529_monthly=? WHERE id=1",
            ("Riley", 12, 150, "Sam", 8, 200),
        )
        conn.execute(
            "INSERT INTO accounts (name, account_type, owner, institution, balance) VALUES (?,?,?,?,?)",
            ("Riley 529", "529", "abby", "Test Bank", 5000),
        )
        conn.execute(
            "INSERT INTO accounts (name, account_type, owner, institution, balance) VALUES (?,?,?,?,?)",
            ("Sam Roth", "roth_ira", "cooper", "Test Bank", 1000),
        )
        conn.commit()

        db_module.migrate_legacy_kids(conn)

        kids = [dict(r) for r in conn.execute("SELECT * FROM kids ORDER BY display_order").fetchall()]
        assert len(kids) == 2
        assert kids[0]["name"] == "Riley" and kids[0]["age"] == 12 and kids[0]["monthly_529"] == 150
        assert kids[1]["name"] == "Sam"   and kids[1]["age"] == 8  and kids[1]["monthly_529"] == 200

        owners = {r["owner"] for r in conn.execute("SELECT owner FROM accounts").fetchall()}
        assert owners == {f"kid_{kids[0]['id']}", f"kid_{kids[1]['id']}"}
        conn.close()

    def test_only_one_legacy_kid_with_signal_migrates_alone(self, temp_db):
        """kid2 stays at every default (name 'Child 2', age 0, no
        'cooper' accounts) -- indistinguishable from "never configured,"
        so only kid1 should become a real row."""
        conn = db_module.get_db()
        conn.execute("UPDATE planning_inputs SET kids_migrated=0, kid1_name=?, kid1_age=? WHERE id=1", ("Riley", 12))
        conn.commit()

        db_module.migrate_legacy_kids(conn)

        kids = [dict(r) for r in conn.execute("SELECT * FROM kids").fetchall()]
        assert len(kids) == 1
        assert kids[0]["name"] == "Riley"
        conn.close()

    def test_is_a_one_time_conversion_not_an_ongoing_sync(self, temp_db):
        """A household that migrates, then deletes back down to 0 kids,
        must STAY at 0 on the next backend restart -- migrate_legacy_kids
        must not resurrect kid1/kid2 from the (now stale) legacy columns
        just because the kids table happens to be empty again."""
        conn = db_module.get_db()
        conn.execute("UPDATE planning_inputs SET kids_migrated=0, kid1_name=?, kid1_age=? WHERE id=1", ("Riley", 12))
        conn.commit()
        db_module.migrate_legacy_kids(conn)
        assert conn.execute("SELECT COUNT(*) FROM kids").fetchone()[0] == 1

        conn.execute("DELETE FROM kids")
        conn.commit()
        # Simulates a second backend restart: migrate_legacy_kids runs
        # again (as it would at the next init_kids_table() call) against
        # the SAME still-legacy planning_inputs row.
        db_module.migrate_legacy_kids(conn)
        assert conn.execute("SELECT COUNT(*) FROM kids").fetchone()[0] == 0
        conn.close()

    def test_noop_when_kids_table_already_has_a_real_row(self, temp_db):
        """Guards against re-running the conversion on top of a household
        that has already configured kids for real through the app (not
        via legacy migration) -- must never duplicate or overwrite."""
        conn = db_module.get_db()
        conn.execute("INSERT INTO kids (name, age, monthly_529, display_order) VALUES (?,?,?,?)",
                     ("Already Real", 5, 0, 0))
        conn.execute("UPDATE planning_inputs SET kid1_name=?, kid1_age=? WHERE id=1", ("Riley", 12))
        conn.commit()

        db_module.migrate_legacy_kids(conn)

        kids = [dict(r) for r in conn.execute("SELECT * FROM kids").fetchall()]
        assert len(kids) == 1
        assert kids[0]["name"] == "Already Real"
        conn.close()
