"""Tests for net_worth_engine.py — the single shared net-worth bucketing
function used by /api/net-worth, /api/snapshot, and /api/report/annual.
Regression coverage for the "three pages showed three different net worth
numbers for the same accounts" bug this module was extracted to fix."""
import pytest

from net_worth_engine import compute_net_worth, emergency_fund_check, EMERGENCY_FUND_MIN_MONTHS, EMERGENCY_FUND_FULL_MONTHS


class TestComputeNetWorth:
    def test_basic_bucketing(self):
        accounts = [
            {"account_type": "checking", "owner": "joint", "balance": 10000},
            {"account_type": "401k", "owner": "jason", "balance": 200000},
            {"account_type": "credit_card", "owner": "joint", "balance": 3000},
        ]
        result = compute_net_worth(accounts)
        assert result["savings"] == 10000
        assert result["investment"] == 200000
        assert result["liabilities"] == 3000
        assert result["total_assets"] == 210000
        assert result["net_worth"] == 207000

    def test_unrecognized_account_type_falls_back_to_other(self):
        """Regression: an account_type matching none of CATEGORIES' lists
        (a stale/mistyped value from a hand-edited Quicken mapping, a direct
        API/DB write, or a type this list hasn't caught up with yet) used to
        just vanish — present in the plain accounts list with its balance
        intact, but silently missing from every category and from
        total_assets/net_worth, with nothing in the UI to say so. Found via
        a bug-hunt sandbox where a mistyped 401k type ("pretax_401k" instead
        of "401k") silently dropped $650K from a $1.05M total. It must land
        in "other" instead."""
        accounts = [
            {"account_type": "pretax_401k", "owner": "jason", "balance": 650000},
            {"account_type": "checking", "owner": "joint", "balance": 10000},
        ]
        result = compute_net_worth(accounts)
        assert result["other"] == 650000
        assert result["total_assets"] == 660000
        assert result["net_worth"] == 660000

    def test_education_and_daf_included_in_total(self):
        """Regression: the report's old copy of this logic had no
        'education'/'daf' bucket, so 529s and DAF accounts were silently
        dropped from total_assets/net_worth in the PDF report only."""
        accounts = [
            {"account_type": "529", "owner": "jason", "balance": 20000},
            {"account_type": "daf", "owner": "joint", "balance": 15000},
        ]
        result = compute_net_worth(accounts)
        assert result["education"] == 20000
        assert result["daf"] == 15000
        assert result["total_assets"] == 35000
        assert result["net_worth"] == 35000

    def test_custodial_in_investment_bucket(self):
        accounts = [{"account_type": "custodial", "owner": "jason", "balance": 5000}]
        result = compute_net_worth(accounts)
        assert result["investment"] == 5000

    def test_kids_accounts_excluded_from_totals(self):
        """Regression: /api/snapshot used to include kids' custodial/Roth/
        529 balances while /api/net-worth excluded them — a snapshot's
        net_worth could be higher than the live 'Net Worth Today' tile for
        the exact same accounts."""
        accounts = [
            {"account_type": "custodial", "owner": "abby", "balance": 8000},
            {"account_type": "roth_ira", "owner": "cooper", "balance": 3000},
            {"account_type": "529", "owner": "abby", "balance": 12000},
            {"account_type": "checking", "owner": "joint", "balance": 1000},
        ]
        result = compute_net_worth(accounts)
        assert result["kids_assets"] == 23000
        assert result["total_assets"] == 1000
        assert result["net_worth"] == 1000

    def test_kids_accounts_excluded_regardless_of_account_type(self):
        """Regression test for a real reported bug: the exclusion only
        checked account_type in {custodial, roth_ira, 529}, so a kid-owned
        savings bond ("other") or a kid's own checking/savings account
        silently leaked into the parents' net worth instead of
        kids_assets — inconsistent with emergency_fund_check, which
        already excludes kid-owned checking/savings. Any account owned by
        a kid should be excluded, not just the three most common types."""
        accounts = [
            {"account_type": "other", "owner": "abby", "balance": 2000},      # savings bond
            {"account_type": "savings", "owner": "cooper", "balance": 500},   # kid's own savings account
            {"account_type": "checking", "owner": "joint", "balance": 1000},
        ]
        result = compute_net_worth(accounts)
        assert result["kids_assets"] == 2500
        assert result["other"] == 0
        assert result["savings"] == 1000
        assert result["total_assets"] == 1000
        assert result["net_worth"] == 1000

    def test_parent_owned_529_not_treated_as_kids_asset(self):
        """A 529 owned by a parent (not abby/cooper) is a normal education
        asset, not a kids_assets exclusion."""
        accounts = [{"account_type": "529", "owner": "jason", "balance": 9000}]
        result = compute_net_worth(accounts)
        assert result["kids_assets"] == 0
        assert result["education"] == 9000

    def test_empty_accounts(self):
        result = compute_net_worth([])
        assert result["total_assets"] == 0
        assert result["net_worth"] == 0
        assert result["liabilities"] == 0

    def test_all_debt_types_counted_as_liabilities(self):
        accounts = [
            {"account_type": "mortgage", "owner": "joint", "balance": 300000},
            {"account_type": "student_loan", "owner": "jason", "balance": 20000},
            {"account_type": "car_loan", "owner": "justin", "balance": 15000},
            {"account_type": "personal_loan", "owner": "joint", "balance": 5000},
        ]
        result = compute_net_worth(accounts)
        assert result["liabilities"] == 340000
        assert result["net_worth"] == -340000


class TestEmergencyFundCheck:
    def test_no_monthly_expenses_returns_has_data_false(self):
        assert emergency_fund_check([{"account_type": "checking", "balance": 10000}], 0) == {"has_data": False}

    def test_underfunded_status(self):
        accounts = [{"account_type": "checking", "owner": "joint", "balance": 2000}]
        result = emergency_fund_check(accounts, monthly_expenses=5000)
        assert result["status"] == "underfunded"
        assert result["months_covered"] == pytest.approx(0.4, abs=0.01)
        assert result["gap_to_min"] > 0

    def test_adequate_status_between_min_and_full(self):
        accounts = [{"account_type": "savings", "owner": "joint", "balance": 20000}]
        result = emergency_fund_check(accounts, monthly_expenses=5000)  # 4 months: min<=4<full
        assert result["status"] == "adequate"

    def test_fully_funded_status(self):
        accounts = [{"account_type": "savings", "owner": "joint", "balance": 40000}]
        result = emergency_fund_check(accounts, monthly_expenses=5000)  # 8 months
        assert result["status"] == "funded"
        assert result["gap_to_min"] == 0
        assert result["gap_to_full"] == 0

    def test_kids_accounts_excluded_from_liquid_assets(self):
        accounts = [
            {"account_type": "savings", "owner": "abby", "balance": 5000},
            {"account_type": "checking", "owner": "joint", "balance": 1000},
        ]
        result = emergency_fund_check(accounts, monthly_expenses=1000)
        assert result["liquid_assets"] == 1000

    def test_investment_accounts_excluded_from_liquid_assets(self):
        accounts = [{"account_type": "taxable", "owner": "joint", "balance": 100000}]
        result = emergency_fund_check(accounts, monthly_expenses=5000)
        assert result["liquid_assets"] == 0
        assert result["status"] == "underfunded"
