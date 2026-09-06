"""Tests for rental_engine.py — cap rate, cash-on-cash return, and
sell-vs-hold comparison for the flagged rental property."""
import pytest

from rental_engine import rental_property_analysis


class TestRentalPropertyAnalysis:
    def test_no_rental_key_configured(self):
        assert rental_property_analysis([], "") == {"has_data": False}

    def test_no_matching_account(self):
        accounts = [{"account_type": "real_estate", "name": "Primary Home", "balance": 400000}]
        assert rental_property_analysis(accounts, "Rental") == {"has_data": False}

    def test_no_income_entered_yet(self):
        accounts = [{"account_type": "real_estate", "name": "Rental Unit", "balance": 200000,
                     "monthly_rental_income": 0, "monthly_rental_expenses": 0}]
        result = rental_property_analysis(accounts, "Rental")
        assert result["has_data"] is True
        assert result["annual_income"] == 0
        assert "add the monthly rent" in result["recommendation"]

    def test_computes_cap_rate(self):
        accounts = [{"account_type": "real_estate", "name": "Rental Unit", "balance": 200000,
                     "monthly_rental_income": 2000, "monthly_rental_expenses": 500}]
        result = rental_property_analysis(accounts, "Rental")
        assert result["annual_cash_flow"] == 18000
        assert result["cap_rate"] == pytest.approx(0.09, abs=0.001)

    def test_beats_alternative_when_cap_rate_higher(self):
        accounts = [{"account_type": "real_estate", "name": "Rental Unit", "balance": 200000,
                     "monthly_rental_income": 2000, "monthly_rental_expenses": 500}]
        result = rental_property_analysis(accounts, "Rental", alternative_return=0.07)
        assert result["beats_alternative"] is True

    def test_does_not_beat_alternative_when_cap_rate_lower(self):
        accounts = [{"account_type": "real_estate", "name": "Rental Unit", "balance": 500000,
                     "monthly_rental_income": 1500, "monthly_rental_expenses": 800}]
        result = rental_property_analysis(accounts, "Rental", alternative_return=0.07)
        assert result["beats_alternative"] is False

    def test_mortgage_reduces_equity_and_boosts_cash_on_cash(self):
        accounts = [
            {"account_type": "real_estate", "name": "Rental Unit", "balance": 200000,
             "monthly_rental_income": 2000, "monthly_rental_expenses": 500},
            {"account_type": "mortgage", "name": "Rental Unit Mortgage", "balance": 100000},
        ]
        result = rental_property_analysis(accounts, "Rental Unit")
        assert result["equity"] == 100000
        assert result["cash_on_cash_return"] == pytest.approx(0.18, abs=0.001)  # 18000 / 100000

    def test_debt_service_reduces_cash_on_cash_return(self):
        """Regression test (external audit 2026-09-05): cash-on-cash used to
        divide pre-debt-service cash flow by equity, mixing an unlevered
        numerator with a levered denominator and overstating the return
        whenever there's a mortgage payment. minimum_payment on the
        mortgage account is its monthly P&I."""
        accounts = [
            {"account_type": "real_estate", "name": "Rental Unit", "balance": 200000,
             "monthly_rental_income": 2000, "monthly_rental_expenses": 500},
            {"account_type": "mortgage", "name": "Rental Unit Mortgage", "balance": 100000,
             "minimum_payment": 600},
        ]
        result = rental_property_analysis(accounts, "Rental Unit")
        # annual_cash_flow (18000) - annual_debt_service (600*12=7200) = 10800, / equity (100000)
        assert result["annual_debt_service"] == 7200
        assert result["levered_cash_flow"] == 10800
        assert result["cash_on_cash_return"] == pytest.approx(0.108, abs=0.001)

    def test_zero_equity_does_not_divide_by_zero(self):
        accounts = [
            {"account_type": "real_estate", "name": "Rental Unit", "balance": 200000,
             "monthly_rental_income": 2000, "monthly_rental_expenses": 500},
            {"account_type": "mortgage", "name": "Rental Unit Mortgage", "balance": 250000},
        ]
        result = rental_property_analysis(accounts, "Rental Unit")
        assert result["equity"] == 0
        assert result["cash_on_cash_return"] == 0
