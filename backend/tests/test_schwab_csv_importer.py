"""
schwab_csv_importer.py -- verified against a real Schwab "Positions"
export during development (8 rows including cash, "Positions Total"
reconciled exactly to the sum of positions). These tests use a small
synthetic fixture mirroring that real structure, not the user's actual
account data.
"""
from schwab_csv_importer import parse_schwab_positions_csv

SAMPLE_CSV = (
    '"Positions for account Sample Account ...999 as of 08:23 PM ET, 2026/09/14"\n'
    '\n'
    '"Symbol","Description","Qty (Quantity)","Price","Price Chng $ (Price Change $)",'
    '"Price Chng % (Price Change %)","Mkt Val (Market Value)","Day Chng $ (Day Change $)",'
    '"Day Chng % (Day Change %)","Cost Basis","Gain $ (Gain/Loss $)","Gain % (Gain/Loss %)",'
    '"Ratings","Reinvest?","Reinvest Capital Gains?","% of Acct (% of Account)","Asset Type",\n'
    '"ABCD","SAMPLE WIDGET CO","100","50.00","0.10","0.2%","$5,000.00","$10.00","0.2%",'
    '"$4,000.00","$1,000.00","25%","-","No","N/A","80%","Equity",\n'
    '"EFGH","SAMPLE BOND ETF","10","100.00","0.00","0%","$1,000.00","$0.00","0%",'
    '"$950.00","$50.00","5.26%","--","No","N/A","16%","ETFs & Closed End Funds",\n'
    '"Cash & Cash Investments","--","--","--","--","--","$250.00","$0.00","0%","--","--","--",'
    '"--","--","--","4%","Cash and Money Market",\n'
    '"Positions Total","","--","--","--","--","$6,250.00","$10.00","0.16%","$4,950.00",'
    '"$1,300.00","26.26%","--","--","--","--","--",\n'
)


class TestParseSchwabPositionsCsv:
    def test_parses_securities_and_cash_row(self):
        result = parse_schwab_positions_csv(SAMPLE_CSV)
        assert result["errors"] == []
        assert len(result["positions"]) == 3
        by_ticker = {p["ticker"]: p for p in result["positions"]}
        assert by_ticker["ABCD"]["security_name"] == "SAMPLE WIDGET CO"
        assert by_ticker["ABCD"]["shares"] == 100.0
        assert by_ticker["ABCD"]["market_value"] == 5000.0
        assert by_ticker["ABCD"]["cost_basis"] == 4000.0
        assert by_ticker["CASH"]["security_name"] == "Cash & Cash Investments"
        assert by_ticker["CASH"]["shares"] is None
        assert by_ticker["CASH"]["market_value"] == 250.0

    def test_positions_total_row_becomes_account_balance_not_a_position(self):
        result = parse_schwab_positions_csv(SAMPLE_CSV)
        assert result["account_balance"] == 6250.0
        assert all(p["ticker"] != "Positions Total" for p in result["positions"])

    def test_as_of_date_and_account_label_from_preamble(self):
        result = parse_schwab_positions_csv(SAMPLE_CSV)
        assert result["as_of_date"] == "2026-09-14"
        assert result["account_label"] == "Sample Account ...999"

    def test_sum_of_positions_matches_account_balance(self):
        result = parse_schwab_positions_csv(SAMPLE_CSV)
        assert round(sum(p["market_value"] for p in result["positions"]), 2) == result["account_balance"]

    def test_thousands_separator_in_quantity_is_handled(self):
        csv_text = SAMPLE_CSV.replace('"ABCD","SAMPLE WIDGET CO","100"', '"ABCD","SAMPLE WIDGET CO","1,000"')
        result = parse_schwab_positions_csv(csv_text)
        pos = next(p for p in result["positions"] if p["ticker"] == "ABCD")
        assert pos["shares"] == 1000.0

    def test_negative_price_change_dollar_sign_does_not_break_market_value(self):
        # Confirms the "-$1,273.32"-style negative money cells elsewhere in a
        # real row never get confused with a genuinely negative Mkt Val.
        csv_text = SAMPLE_CSV.replace('"$10.00","0.2%"', '"-$10.00","0.2%"')
        result = parse_schwab_positions_csv(csv_text)
        pos = next(p for p in result["positions"] if p["ticker"] == "ABCD")
        assert pos["market_value"] == 5000.0

    def test_missing_symbol_header_returns_a_clear_error_not_a_crash(self):
        result = parse_schwab_positions_csv("just,some,random,csv\n1,2,3,4\n")
        assert result["positions"] == []
        assert result["account_balance"] is None
        assert len(result["errors"]) == 1
        assert "Positions export" in result["errors"][0]

    def test_empty_file_returns_a_clear_error(self):
        result = parse_schwab_positions_csv("")
        assert result["positions"] == []
        assert result["errors"] == ["Empty file."]

    def test_row_with_no_market_value_is_skipped_with_an_error_not_silently_dropped(self):
        csv_text = SAMPLE_CSV.replace(
            '"EFGH","SAMPLE BOND ETF","10","100.00","0.00","0%","$1,000.00"',
            '"EFGH","SAMPLE BOND ETF","10","100.00","0.00","0%","--"',
        )
        result = parse_schwab_positions_csv(csv_text)
        assert not any(p["ticker"] == "EFGH" for p in result["positions"])
        assert any("EFGH" in e for e in result["errors"])
