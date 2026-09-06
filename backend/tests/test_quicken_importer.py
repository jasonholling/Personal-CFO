"""Tests for quicken_importer.py — Quicken Net Worth Summary CSV parsing."""
import json
import quicken_importer
from quicken_importer import (
    parse_quicken_networth_csv,
    get_net_worth_summary,
    _clean_amount,
    _guess_institution,
)

SAMPLE_CSV = """Net Worth Summary
Some Report Header
---
Category,Account,Balance
Checking,First National Checking,"1,234.56"
Savings,Metro FCU Savings,"500.00"
Retirement,Empower 401k,"250,000.00"
Investments,Schwab Brokerage Creative Planning,"75,000.00"
Investments,Old ETrade Account,"12.00"
Property,2024 Highlander,"0.00"
Total,Total Assets,"326,746.56"
"""


class TestCleanAmount:
    def test_strips_commas(self):
        assert _clean_amount("1,234.56") == 1234.56

    def test_empty_string_is_zero(self):
        assert _clean_amount("") == 0.0

    def test_non_numeric_is_zero(self):
        assert _clean_amount("n/a") == 0.0

    def test_strips_quotes(self):
        assert _clean_amount('"1,000"') == 1000.0


class TestGuessInstitution:
    def test_schwab(self):
        assert _guess_institution("Schwab Brokerage") == "Schwab"

    def test_empower(self):
        assert _guess_institution("Empower 401k") == "Empower"

    def test_unknown_returns_empty(self):
        assert _guess_institution("Totally Unknown Account") == ""


class TestParseQuickenNetworthCsv:
    def test_public_importer_skips_unmapped_accounts(self, monkeypatch, tmp_path):
        """The public repo must never carry a household's account aliases."""
        monkeypatch.setattr(quicken_importer, "_LOCAL_MAP_PATH", str(tmp_path / "missing-map.json"))
        accounts = parse_quicken_networth_csv(SAMPLE_CSV)
        assert accounts == []

    def test_local_mapping_imports_accounts(self, monkeypatch, tmp_path):
        local_map = tmp_path / "account-map.json"
        local_map.write_text(json.dumps({"retirement account": ["401k", "person1"]}))
        monkeypatch.setattr(quicken_importer, "_LOCAL_MAP_PATH", str(local_map))
        accounts = parse_quicken_networth_csv("Header\n---\nRetirement,Retirement Account,250000\n")
        assert accounts[0]["account_type"] == "401k"
        assert accounts[0]["owner"] == "person1"

    def test_skips_zero_balance_accounts(self):
        accounts = parse_quicken_networth_csv(SAMPLE_CSV)
        assert not any(a["name"] == "2024 Highlander" for a in accounts)

    def test_skips_unmapped_accounts(self):
        accounts = parse_quicken_networth_csv(SAMPLE_CSV)
        assert not any("ETrade" in a["name"] for a in accounts)

    def test_skips_total_rows(self):
        accounts = parse_quicken_networth_csv(SAMPLE_CSV)
        assert not any("Total" in a["name"] for a in accounts)


    def test_empty_csv_returns_empty_list(self):
        assert parse_quicken_networth_csv("") == []

    def test_no_dashes_means_no_data_rows_found(self):
        csv_without_dashes = "Account,Balance\nChecking,100.00\n"
        assert parse_quicken_networth_csv(csv_without_dashes) == []

    def test_short_rows_are_skipped(self, monkeypatch, tmp_path):
        local_map = tmp_path / "account-map.json"; local_map.write_text(json.dumps({"checking account": ["checking", "joint"]}))
        monkeypatch.setattr(quicken_importer, "_LOCAL_MAP_PATH", str(local_map))
        csv_content = "Header\n---\nonly,two\nChecking,Checking Account,\"1,000.00\"\n"
        accounts = parse_quicken_networth_csv(csv_content)
        assert len(accounts) == 1

    def test_section_header_row_is_skipped(self, monkeypatch, tmp_path):
        local_map = tmp_path / "account-map.json"; local_map.write_text(json.dumps({"checking account": ["checking", "joint"]}))
        monkeypatch.setattr(quicken_importer, "_LOCAL_MAP_PATH", str(local_map))
        csv_content = 'Header\n---\nSection,Investments,\nChecking,Checking Account,"1,000.00"\n'
        accounts = parse_quicken_networth_csv(csv_content)
        assert len(accounts) == 1

    def test_partial_match_fallback_maps_unlisted_variant(self, monkeypatch, tmp_path):
        local_map = tmp_path / "account-map.json"; local_map.write_text(json.dumps({"checking account": ["checking", "joint"]}))
        monkeypatch.setattr(quicken_importer, "_LOCAL_MAP_PATH", str(local_map))
        csv_content = 'Header\n---\nChecking,Checking Account Extra,"500.00"\n'
        accounts = parse_quicken_networth_csv(csv_content)
        assert len(accounts) == 1
        assert accounts[0]["account_type"] == "checking"


class TestGetNetWorthSummary:
    def test_computes_assets_liabilities_and_net_worth(self):
        accounts = [
            {"account_type": "checking", "balance": 1000},
            {"account_type": "taxable", "balance": 5000},
            {"account_type": "mortgage", "balance": 2000},
        ]
        summary = get_net_worth_summary(accounts)
        assert summary["total_assets"] == 6000
        assert summary["liabilities"] == 2000
        assert summary["net_worth"] == 4000
        assert summary["account_count"] == 3

    def test_investments_subset_is_correct(self):
        accounts = [
            {"account_type": "roth_ira", "balance": 1000},
            {"account_type": "checking", "balance": 500},
        ]
        summary = get_net_worth_summary(accounts)
        assert summary["investments"] == 1000

    def test_empty_accounts_list(self):
        summary = get_net_worth_summary([])
        assert summary["net_worth"] == 0
        assert summary["account_count"] == 0
