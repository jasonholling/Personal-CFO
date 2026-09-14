"""Tests for task_engine.py — auto-generated task rules."""
from datetime import date
import task_engine
from task_engine import generate_tasks, sync_auto_tasks, ensure_annual_review_task, CURRENT_YEAR
import db as db_module


def _base_args(sample_inputs, sample_accounts):
    education = {"goals": []}
    projections = {"scenarios": []}
    return sample_accounts, sample_inputs, projections, education


def test_includes_core_annual_tasks_and_current_tax_reviews(sample_inputs, sample_accounts):
    accounts, inputs, projections, education = _base_args(sample_inputs, sample_accounts)
    tasks = generate_tasks(accounts, inputs, projections, education)
    annual = [t for t in tasks if t["recurrence"] == "annual" and t["task_type"] == "annual"]
    assert len(annual) >= 10
    keys = {task["auto_key"] for task in annual}
    assert f"withholding_review_{CURRENT_YEAR}" in keys
    assert f"estimated_tax_check_{CURRENT_YEAR}" in keys


def test_generate_tasks_uses_the_live_year_not_an_import_time_snapshot(sample_inputs, sample_accounts, monkeypatch):
    # audit finding, 2026-09-14, P2: this app is a long-lived process
    # (start.sh) — generate_tasks must read the real current year on every
    # call, not bake in whatever year it was when task_engine was imported,
    # or annual tasks would stop rolling over after a Dec 31 -> Jan 1
    # boundary until the backend is restarted.
    class FrozenNextYearDate(date):
        @classmethod
        def today(cls):
            return date(CURRENT_YEAR + 1, 5, 1)

    monkeypatch.setattr(task_engine, "date", FrozenNextYearDate)
    accounts, inputs, projections, education = _base_args(sample_inputs, sample_accounts)
    tasks = generate_tasks(accounts, inputs, projections, education)
    keys = {t["auto_key"] for t in tasks}
    assert f"withholding_review_{CURRENT_YEAR + 1}" in keys
    assert f"withholding_review_{CURRENT_YEAR}" not in keys


def test_annual_review_task_is_due_after_april_first(sample_inputs, sample_accounts):
    before = generate_tasks(sample_accounts, sample_inputs, {"scenarios": []}, {"goals": []})
    assert not any(t["auto_key"] == f"annual_review_{CURRENT_YEAR}" for t in before) if date.today().month < 4 else True
    after = generate_tasks(sample_accounts, sample_inputs, {"scenarios": []}, {"goals": []})
    if (date.today().month, date.today().day) >= (4, 1):
        assert any(t["auto_key"] == f"annual_review_{CURRENT_YEAR}" for t in after)


def test_ensure_annual_review_task_is_insert_only_after_due(temp_db):
    conn = db_module.get_db()
    assert not ensure_annual_review_task(conn, date(CURRENT_YEAR, 3, 31))
    assert ensure_annual_review_task(conn, date(CURRENT_YEAR, 4, 1))
    assert not ensure_annual_review_task(conn, date(CURRENT_YEAR, 4, 2))
    row = conn.execute("SELECT title, due_date FROM tasks WHERE auto_key=?", (f"annual_review_{CURRENT_YEAR}",)).fetchone()
    conn.close()
    assert row["title"] == "Complete annual review checklist"
    assert row["due_date"] == f"{CURRENT_YEAR}-04-01"


def test_allocation_review_task_uses_saved_policy_not_a_hardcoded_split(sample_inputs, sample_accounts):
    # Regression (audit finding, 2026-09-14, P2): this description used to
    # hardcode "80% stock / 15% fixed / 5% real estate", disagreeing with
    # report_generator.py's own separately hardcoded "85%/10%/5%" and with
    # whatever the household actually configured.
    accounts, inputs, projections, education = _base_args(sample_inputs, sample_accounts)
    policy = {"target_us_large_cap_pct": 60, "target_us_bonds_pct": 30, "target_cash_pct": 10}
    tasks = generate_tasks(accounts, inputs, projections, education, policy=policy)
    review = next(t for t in tasks if t["auto_key"] == f"allocation_review_{CURRENT_YEAR}")
    assert "60% stock" in review["description"]
    assert "30% bonds" in review["description"]

    no_policy_tasks = generate_tasks(accounts, inputs, projections, education)
    no_policy_review = next(t for t in no_policy_tasks if t["auto_key"] == f"allocation_review_{CURRENT_YEAR}")
    assert "80%" not in no_policy_review["description"]
    assert "Portfolio Setup" in no_policy_review["description"]


def test_education_gap_task_created_when_underfunded(sample_inputs, sample_accounts):
    education = {"goals": [
        {"child": "Abby", "funding_percent": 60, "funding_gap": 10000, "monthly_savings_to_close_gap": 100},
    ]}
    tasks = generate_tasks(sample_accounts, sample_inputs, {"scenarios": []}, education)
    gap_tasks = [t for t in tasks if t["auto_key"].startswith("529_gap_")]
    assert len(gap_tasks) == 1
    assert "below 85%" in gap_tasks[0]["description"]


def test_education_gap_task_not_created_when_fully_funded(sample_inputs, sample_accounts):
    education = {"goals": [
        {"child": "Abby", "funding_percent": 100, "funding_gap": 0, "monthly_savings_to_close_gap": 0},
    ]}
    tasks = generate_tasks(sample_accounts, sample_inputs, {"scenarios": []}, education)
    gap_tasks = [t for t in tasks if t["auto_key"].startswith("529_gap_")]
    assert len(gap_tasks) == 0


def test_retirement_underfunded_task(sample_inputs, sample_accounts):
    projections = {"scenarios": [{"label": "age_60_early", "percent_funded": 70}]}
    tasks = generate_tasks(sample_accounts, sample_inputs, projections, {"goals": []})
    assert any(t["auto_key"] == f"retirement_underfunded_{CURRENT_YEAR}" for t in tasks)


def test_retirement_surplus_task(sample_inputs, sample_accounts):
    projections = {"scenarios": [{"label": "age_60_early", "percent_funded": 100}]}
    tasks = generate_tasks(sample_accounts, sample_inputs, projections, {"goals": []})
    assert any(t["auto_key"] == f"retirement_surplus_check_{CURRENT_YEAR}" for t in tasks)


def test_no_retirement_task_in_healthy_middle_range(sample_inputs, sample_accounts):
    projections = {"scenarios": [{"label": "age_60_early", "percent_funded": 95}]}
    tasks = generate_tasks(sample_accounts, sample_inputs, projections, {"goals": []})
    assert not any(t["auto_key"].startswith("retirement_") for t in tasks)


def test_mortgage_refi_task_above_threshold(sample_inputs, sample_accounts):
    accounts, inputs, projections, education = _base_args(sample_inputs, sample_accounts)
    tasks = generate_tasks(accounts, inputs, projections, education)
    assert any(t["auto_key"] == f"mortgage_refi_{CURRENT_YEAR}" for t in tasks)


def test_no_mortgage_refi_task_below_threshold(sample_inputs):
    accounts = [{"account_type": "mortgage", "balance": 50000}]
    tasks = generate_tasks(accounts, sample_inputs, {"scenarios": []}, {"goals": []})
    assert not any(t["auto_key"].startswith("mortgage_refi_") for t in tasks)


def test_rsu_task_only_when_rsu_configured(sample_inputs, sample_accounts):
    no_rsu = generate_tasks(sample_accounts, sample_inputs, {"scenarios": []}, {"goals": []})
    assert not any(t["auto_key"].startswith("rsu_tax_strategy_") for t in no_rsu)

    with_rsu = {**sample_inputs, "annual_rsu_value": 50000}
    tasks = generate_tasks(sample_accounts, with_rsu, {"scenarios": []}, {"goals": []})
    assert any(t["auto_key"].startswith("rsu_tax_strategy_") for t in tasks)


def test_stock_unwind_task_above_threshold(sample_inputs, sample_accounts):
    accounts = [*sample_accounts, {"account_type": "taxable", "balance": 1}]  # push total taxable > 200000
    tasks = generate_tasks(accounts, sample_inputs, {"scenarios": []}, {"goals": []})
    assert any(t["auto_key"].startswith("stock_unwind_") for t in tasks)

def test_no_stock_unwind_task_below_threshold(sample_inputs):
    accounts = [{"account_type": "taxable", "balance": 100000}]
    tasks = generate_tasks(accounts, sample_inputs, {"scenarios": []}, {"goals": []})
    assert not any(t["auto_key"].startswith("stock_unwind_") for t in tasks)


class TestSyncAutoTasks:
    def test_inserts_new_tasks(self, temp_db, sample_inputs, sample_accounts):
        conn = db_module.get_db()
        inserted = sync_auto_tasks(conn, sample_accounts, sample_inputs, {"scenarios": []}, {"goals": []})
        conn.close()
        assert inserted > 0

    def test_does_not_duplicate_on_second_call(self, temp_db, sample_inputs, sample_accounts):
        conn = db_module.get_db()
        sync_auto_tasks(conn, sample_accounts, sample_inputs, {"scenarios": []}, {"goals": []})
        second = sync_auto_tasks(conn, sample_accounts, sample_inputs, {"scenarios": []}, {"goals": []})
        count = conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"]
        conn.close()
        assert second == 0
        assert count == len(generate_tasks(sample_accounts, sample_inputs, {"scenarios": []}, {"goals": []}))

    def test_does_not_touch_completed_tasks(self, temp_db, sample_inputs, sample_accounts):
        conn = db_module.get_db()
        sync_auto_tasks(conn, sample_accounts, sample_inputs, {"scenarios": []}, {"goals": []})
        conn.execute("UPDATE tasks SET completed=1 WHERE auto_key=?", (f"snapshot_{CURRENT_YEAR}",))
        conn.commit()
        sync_auto_tasks(conn, sample_accounts, sample_inputs, {"scenarios": []}, {"goals": []})
        row = conn.execute("SELECT completed FROM tasks WHERE auto_key=?", (f"snapshot_{CURRENT_YEAR}",)).fetchone()
        conn.close()
        assert row["completed"] == 1
