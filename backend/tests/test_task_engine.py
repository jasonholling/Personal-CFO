"""Tests for task_engine.py — auto-generated task rules."""
from task_engine import generate_tasks, sync_auto_tasks, CURRENT_YEAR
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
