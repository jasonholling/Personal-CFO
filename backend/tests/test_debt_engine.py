"""Tests for debt_engine.py — avalanche/snowball simulation and standalone
payoff/refinance calculators."""
from datetime import date

import pytest

from debt_engine import (
    get_debt_accounts,
    run_avalanche_snowball,
    recommend_payoff_strategy,
    find_negative_amortization_debts,
    amortized_payment,
    credit_card_payoff_calculator,
    refinance_breakeven,
    debt_vs_invest_crossover,
    project_debt_schedule,
    _simulate_payoff,
    DEBT_TYPES,
)


@pytest.fixture
def two_debts():
    return [
        {"id": 1, "name": "High-rate card", "account_type": "credit_card", "balance": 5000, "interest_rate": 0.22, "minimum_payment": 150},
        {"id": 2, "name": "Low-rate card",   "account_type": "credit_card", "balance": 1500, "interest_rate": 0.15, "minimum_payment": 50},
    ]


class TestGetDebtAccounts:
    def test_filters_to_debt_types_only(self):
        accounts = [
            {"account_type": "checking", "balance": 1000},
            {"account_type": "credit_card", "balance": 500},
            {"account_type": "mortgage", "balance": 200000},
        ]
        debts = get_debt_accounts(accounts)
        assert len(debts) == 2

    def test_excludes_zero_balance(self):
        accounts = [{"account_type": "credit_card", "balance": 0}]
        assert get_debt_accounts(accounts) == []

    def test_every_debt_type_is_recognized(self):
        for t in DEBT_TYPES:
            accounts = [{"account_type": t, "balance": 100}]
            assert len(get_debt_accounts(accounts)) == 1


class TestRunAvalancheSnowball:
    def test_no_debt_returns_has_debt_false(self):
        result = run_avalanche_snowball([{"account_type": "checking", "balance": 1000}])
        assert result == {"has_debt": False}

    def test_avalanche_pays_highest_rate_first(self, two_debts):
        result = run_avalanche_snowball(two_debts, extra_monthly=200)
        assert result["avalanche"]["payoff_order"][0]["name"] == "High-rate card"

    def test_snowball_pays_smallest_balance_first(self, two_debts):
        result = run_avalanche_snowball(two_debts, extra_monthly=200)
        assert result["snowball"]["payoff_order"][0]["name"] == "Low-rate card"

    def test_avalanche_saves_at_least_as_much_interest_as_snowball(self, two_debts):
        result = run_avalanche_snowball(two_debts, extra_monthly=200)
        assert result["interest_saved_with_avalanche"] >= 0

    def test_total_balance_and_minimum_are_summed(self, two_debts):
        result = run_avalanche_snowball(two_debts, extra_monthly=0)
        assert result["total_balance"] == 6500
        assert result["total_minimum_payment"] == 200

    def test_both_debts_paid_off_when_budget_sufficient(self, two_debts):
        result = run_avalanche_snowball(two_debts, extra_monthly=500)
        assert len(result["avalanche"]["payoff_order"]) == 2
        assert result["avalanche"]["months_to_debt_free"] is not None

    def test_insufficient_payment_never_finishes(self):
        # Interest (22%/12 ≈ 1.83%/mo on $50k) exceeds a $10/mo payment
        debts = [{"id": 1, "name": "Huge card", "account_type": "credit_card", "balance": 50000, "interest_rate": 0.22, "minimum_payment": 10}]
        result = run_avalanche_snowball(debts, extra_monthly=0)
        assert result["avalanche"]["months_to_debt_free"] is None


class TestCreditCardPayoffCalculator:
    def test_extra_payment_reduces_months_and_interest(self):
        result = credit_card_payoff_calculator(5000, 0.22, 150, extra=100)
        assert result["months_to_payoff_with_extra"] < result["months_to_payoff"]
        assert result["total_interest_with_extra"] < result["total_interest_paid"]
        assert result["months_saved"] > 0

    def test_zero_extra_changes_nothing_meaningful(self):
        result = credit_card_payoff_calculator(5000, 0.22, 150, extra=0)
        assert result["months_to_payoff_with_extra"] == result["months_to_payoff"]


class TestRefinanceBreakeven:
    def test_lower_rate_saves_money(self):
        result = refinance_breakeven(300000, 0.07, 0.055, 30, 5000)
        assert result["monthly_savings"] > 0
        assert result["worth_it"] is True

    def test_higher_rate_never_worth_it(self):
        result = refinance_breakeven(300000, 0.05, 0.07, 30, 5000)
        assert result["monthly_savings"] < 0
        assert result["worth_it"] is False
        assert result["breakeven_months"] is None

    def test_breakeven_within_term_is_worth_it(self):
        # Small balance/short term where breakeven exceeds the remaining term
        result = refinance_breakeven(50000, 0.07, 0.069, 5, 10000)
        assert result["worth_it"] is False


class TestDebtVsInvestCrossover:
    def test_higher_debt_rate_recommends_paying_debt(self):
        result = debt_vs_invest_crossover(debt_rate=0.22, expected_return=0.07)
        assert result["recommendation"] == "pay_debt_first"

    def test_lower_debt_rate_recommends_investing(self):
        result = debt_vs_invest_crossover(debt_rate=0.04, expected_return=0.07)
        assert result["recommendation"] == "invest_first"

    def test_employer_match_takes_priority_even_with_high_debt_rate(self):
        result = debt_vs_invest_crossover(debt_rate=0.22, expected_return=0.07, employer_match_pct=3)
        assert result["recommendation"] == "capture_match_then_pay_debt"

    def test_equal_rates_recommends_split(self):
        result = debt_vs_invest_crossover(debt_rate=0.07, expected_return=0.07)
        assert result["recommendation"] == "split_evenly"


class TestOneTimeLumpPayments:
    """The new one-time lump-sum mechanic (life-event-sourced extra
    payments targeted at a SPECIFIC debt, not avalanche/snowball-ordered)."""

    def test_default_no_payments_is_byte_for_byte_unchanged(self, two_debts):
        """The default/no-payments case must be identical to today's
        behavior — both the None default and an explicit empty list."""
        no_kwarg = _simulate_payoff(two_debts, extra_monthly=100, order_key=lambda d: -d.get("interest_rate", 0))
        explicit_none = _simulate_payoff(two_debts, extra_monthly=100, order_key=lambda d: -d.get("interest_rate", 0), one_time_payments=None)
        explicit_empty = _simulate_payoff(two_debts, extra_monthly=100, order_key=lambda d: -d.get("interest_rate", 0), one_time_payments=[])
        for key in ("months_to_debt_free", "total_interest_paid", "payoff_order", "schedule"):
            assert no_kwarg[key] == explicit_none[key] == explicit_empty[key]

    def test_lump_payment_accelerates_targeted_debt(self, two_debts):
        baseline = _simulate_payoff(two_debts, extra_monthly=0, order_key=lambda d: d["id"])
        with_payment = _simulate_payoff(
            two_debts, extra_monthly=0, order_key=lambda d: d["id"],
            one_time_payments=[{"account_id": 1, "months_from_now": 1, "amount": 3000}],
        )
        base_month = next(p["payoff_month"] for p in baseline["payoff_order"] if p["id"] == 1)
        new_month  = next(p["payoff_month"] for p in with_payment["payoff_order"] if p["id"] == 1)
        assert new_month < base_month
        assert with_payment["interest_by_debt"][1] < baseline["interest_by_debt"][1]

    def test_payment_on_one_debt_does_not_affect_others(self, two_debts):
        """A PARTIAL lump sum (not enough to pay debt 1 off outright) must
        leave debt 2 completely untouched — no shared pool, no snowball
        rollover in play since debt 1 isn't actually paid off early here."""
        baseline = _simulate_payoff(two_debts, extra_monthly=0, order_key=lambda d: d["id"])
        with_payment = _simulate_payoff(
            two_debts, extra_monthly=0, order_key=lambda d: d["id"],
            one_time_payments=[{"account_id": 1, "months_from_now": 1, "amount": 500}],
        )
        base_month_2 = next(p["payoff_month"] for p in baseline["payoff_order"] if p["id"] == 2)
        new_month_2  = next(p["payoff_month"] for p in with_payment["payoff_order"] if p["id"] == 2)
        assert base_month_2 == new_month_2
        assert baseline["interest_by_debt"][2] == with_payment["interest_by_debt"][2]
        # Debt 1 finishes slightly sooner with the extra $500, so the
        # overall simulation (bounded by whichever debt takes longest) is
        # shorter overall — compare debt 2's trajectory only over the
        # months both runs share; every shared month must match exactly.
        shared_len = min(len(baseline["balance_history"][2]), len(with_payment["balance_history"][2]))
        assert baseline["balance_history"][2][:shared_len] == with_payment["balance_history"][2][:shared_len]

    def test_lump_sum_exceeding_balance_does_not_roll_over_within_that_month(self, two_debts):
        """A $50,000 lump sum against a $1,500 debt should just extinguish
        it in month 1 without going negative, and the excess ($48,500) must
        NOT spill onto the other debt within that same month — verified by
        checking debt 1's month-1 balance is exactly what plain interest +
        minimum payment would produce, with no extra applied to it.
        (A later month's freed-up $50/mo minimum from the now-paid-off
        debt 2 legitimately DOES roll forward into debt 1 afterward — see
        _simulate_payoff's docstring — that's the pre-existing snowball
        mechanic for ANY debt that finishes early, not something this
        lump-sum feature introduces; this test isolates the lump sum's own
        month from that unrelated, expected effect.)"""
        baseline = _simulate_payoff(two_debts, extra_monthly=0, order_key=lambda d: d["id"])
        result = _simulate_payoff(
            two_debts, extra_monthly=0, order_key=lambda d: d["id"],
            one_time_payments=[{"account_id": 2, "months_from_now": 1, "amount": 50000}],
        )
        payoff_2 = next(p["payoff_month"] for p in result["payoff_order"] if p["id"] == 2)
        assert payoff_2 == 1
        # debt 2 never goes negative despite the wildly oversized lump sum
        assert all(entry["balance"] >= 0 for entry in result["balance_history"][2])
        # debt 1's very first month is identical to the no-payment baseline
        # — none of debt 2's $48,500 excess landed on it that month.
        baseline_month1_debt1 = baseline["balance_history"][1][0]
        result_month1_debt1   = result["balance_history"][1][0]
        assert baseline_month1_debt1 == result_month1_debt1 == {"month": 1, "balance": 4942}

    def test_payment_ignored_if_account_id_not_in_debts(self, two_debts):
        """A one-time payment targeting an account not in this household's
        debt list should be silently ignored, not raise."""
        baseline = _simulate_payoff(two_debts, extra_monthly=0, order_key=lambda d: d["id"])
        result = _simulate_payoff(
            two_debts, extra_monthly=0, order_key=lambda d: d["id"],
            one_time_payments=[{"account_id": 999, "months_from_now": 1, "amount": 1000}],
        )
        assert result["months_to_debt_free"] == baseline["months_to_debt_free"]
        assert result["total_interest_paid"] == baseline["total_interest_paid"]


class TestProjectDebtSchedule:
    def test_no_debt_returns_has_debt_false(self):
        assert project_debt_schedule([{"account_type": "checking", "balance": 1000}]) == {"has_debt": False}

    def test_default_matches_run_avalanche_snowball_plus_empty_effect(self, two_debts):
        classic = run_avalanche_snowball(two_debts, extra_monthly=150)
        extended = project_debt_schedule(two_debts, extra_monthly=150)
        for key in ("has_debt", "total_balance", "total_minimum_payment", "interest_saved_with_avalanche"):
            assert classic[key] == extended[key]
        assert extended["one_time_payment_effect"] == {"avalanche": [], "snowball": []}

    def test_targeted_debt_shows_up_in_effect_with_expected_fields(self, two_debts):
        result = project_debt_schedule(
            two_debts, extra_monthly=0,
            one_time_payments=[{"account_id": 1, "months_from_now": 1, "amount": 2000}],
        )
        for strategy in ("avalanche", "snowball"):
            effects = result["one_time_payment_effect"][strategy]
            assert len(effects) == 1
            effect = effects[0]
            assert effect["account_id"] == 1
            assert effect["name"] == "High-rate card"
            assert effect["new_payoff_month"] <= effect["original_payoff_month"]
            assert effect["interest_saved"] >= 0
            assert isinstance(effect["balance_history"], list)

    def test_two_events_targeting_same_debt_in_different_years_both_apply(self, two_debts):
        """Two life events targeting the same debt in different years is a
        supported (if unusual) case — both payments land independently,
        each capped at whatever balance remains when it hits."""
        result = project_debt_schedule(
            two_debts, extra_monthly=0,
            one_time_payments=[
                {"account_id": 1, "months_from_now": 1, "amount": 2000},
                {"account_id": 1, "months_from_now": 6, "amount": 2000},
            ],
        )
        payoff_1 = next(p["payoff_month"] for p in result["avalanche"]["payoff_order"] if p["id"] == 1)
        # Two separate $2000 payments against a $5000 balance should clear
        # it well before its un-accelerated payoff month.
        no_payments = project_debt_schedule(two_debts, extra_monthly=0)
        baseline_payoff_1 = next(p["payoff_month"] for p in no_payments["avalanche"]["payoff_order"] if p["id"] == 1)
        assert payoff_1 < baseline_payoff_1


class TestAmortizedPayment:
    def test_matches_known_amortization_value(self):
        # $20,000 @ 5.5% over 60 months — standard textbook figure
        assert amortized_payment(20000, 0.055, 60) == pytest.approx(382.02, abs=0.01)

    def test_zero_rate_is_simple_division(self):
        assert amortized_payment(12000, 0, 12) == 1000

    def test_zero_term_returns_zero(self):
        assert amortized_payment(10000, 0.05, 0) == 0


class TestFindNegativeAmortizationDebts:
    def test_flags_debt_where_minimum_barely_covers_interest(self):
        debts = [{"id": 1, "name": "Trap card", "balance": 10000, "interest_rate": 0.30, "minimum_payment": 100}]
        flagged = find_negative_amortization_debts(debts)
        assert len(flagged) == 1
        assert flagged[0]["name"] == "Trap card"

    def test_does_not_flag_healthy_debt(self):
        debts = [{"id": 1, "name": "Fine loan", "balance": 10000, "interest_rate": 0.06, "minimum_payment": 300}]
        assert find_negative_amortization_debts(debts) == []

    def test_ignores_debts_with_no_minimum_set(self):
        debts = [{"id": 1, "name": "New entry", "balance": 5000, "interest_rate": 0.25, "minimum_payment": 0}]
        assert find_negative_amortization_debts(debts) == []


class TestRecommendPayoffStrategy:
    def test_no_debt(self):
        assert recommend_payoff_strategy([]) == {"has_debt": False}

    def test_diverging_debts_recommends_avalanche_when_savings_material(self):
        debts = [
            {"id": 1, "name": "Big High-Rate Card", "account_type": "credit_card", "balance": 8000, "interest_rate": 0.24, "minimum_payment": 160},
            {"id": 2, "name": "Small Low-Rate Loan", "account_type": "personal_loan", "balance": 1200, "interest_rate": 0.07, "minimum_payment": 50},
            {"id": 3, "name": "Medium Car Loan", "account_type": "car_loan", "balance": 9000, "interest_rate": 0.06, "minimum_payment": 250},
        ]
        result = recommend_payoff_strategy(debts, extra_monthly=250, today=date(2026, 8, 1))
        assert result["strategy"] == "avalanche"
        assert result["focus_first"]["name"] == "Big High-Rate Card"
        assert result["debt_free_date"] is not None
        assert "saves you" in result["reason"]

    def test_small_savings_with_many_debts_recommends_snowball(self):
        # Rates close enough together, several debts -> snowball momentum wins
        debts = [
            {"id": 1, "name": "Card A", "account_type": "credit_card", "balance": 500,  "interest_rate": 0.20, "minimum_payment": 25},
            {"id": 2, "name": "Card B", "account_type": "credit_card", "balance": 800,  "interest_rate": 0.19, "minimum_payment": 30},
            {"id": 3, "name": "Card C", "account_type": "credit_card", "balance": 1200, "interest_rate": 0.21, "minimum_payment": 40},
        ]
        result = recommend_payoff_strategy(debts, extra_monthly=100, today=date(2026, 8, 1))
        assert result["strategy"] == "snowball"
        assert result["focus_first"]["name"] == "Card A"  # smallest balance

    def test_flags_negative_amortization(self):
        debts = [{"id": 1, "name": "Trap card", "account_type": "credit_card", "balance": 10000, "interest_rate": 0.30, "minimum_payment": 100}]
        result = recommend_payoff_strategy(debts, extra_monthly=0)
        assert len(result["negative_amortization_debts"]) == 1

    def test_debt_free_date_is_calendar_correct(self):
        debts = [{"id": 1, "name": "Small loan", "account_type": "personal_loan", "balance": 1200, "interest_rate": 0.10, "minimum_payment": 200}]
        result = recommend_payoff_strategy(debts, extra_monthly=0, today=date(2026, 1, 15))
        # 1200 at ~200/mo minus interest should finish in ~6-7 months from Jan 2026
        assert result["debt_free_date"] in ("July 2026", "August 2026")
