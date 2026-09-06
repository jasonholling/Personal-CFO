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
