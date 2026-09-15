"""
qfx_importer.py -- verified against a real Empower 401(k) QFX export
during development (11 positions, <INV401KBAL> total reconciled exactly
to the sum of positions to the penny). These tests use small synthetic
fixtures mirroring that real structure, not the user's actual file.
"""
from qfx_importer import parse_qfx_positions

SAMPLE_401K = """OFXHEADER:100
DATA:OFXSGML
VERSION:102

<OFX>
  <INVSTMTMSGSRSV1>
    <INVSTMTTRNRS>
      <INVSTMTRS>
        <DTASOF>20260911120000.000</DTASOF>
        <INVACCTFROM>
          <ACCTID>10596750.583014-01</ACCTID>
        </INVACCTFROM>
        <INVPOSLIST>
          <POSMF>
            <INVPOS>
              <SECID>
                <UNIQUEID>20602V101</UNIQUEID>
                <UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE>
              </SECID>
              <UNITS>1900.474034</UNITS>
              <UNITPRICE>197.36</UNITPRICE>
              <MKTVAL>375077.55</MKTVAL>
              <DTPRICEASOF>20260911000000.000</DTPRICEASOF>
            </INVPOS>
          </POSMF>
          <POSMF>
            <INVPOS>
              <SECID>
                <UNIQUEID>09258N802</UNIQUEID>
                <UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE>
              </SECID>
              <UNITS>2971.541261</UNITS>
              <UNITPRICE>23.18</UNITPRICE>
              <MKTVAL>68880.33</MKTVAL>
              <DTPRICEASOF>20260911000000.000</DTPRICEASOF>
            </INVPOS>
          </POSMF>
        </INVPOSLIST>
        <INV401KBAL>
          <TOTAL>443957.88</TOTAL>
        </INV401KBAL>
      </INVSTMTRS>
    </INVSTMTTRNRS>
  </INVSTMTMSGSRSV1>
  <SECLISTMSGSRSV1>
    <SECLIST>
      <MFINFO>
        <SECINFO>
          <SECID>
            <UNIQUEID>20602V101</UNIQUEID>
            <UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE>
          </SECID>
          <SECNAME>Vanguard Institutional 500 Index Trust</SECNAME>
          <TICKER>20602V101</TICKER>
        </SECINFO>
      </MFINFO>
      <MFINFO>
        <SECINFO>
          <SECID>
            <UNIQUEID>09258N802</UNIQUEID>
            <UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE>
          </SECID>
          <SECNAME>BlackRock Advantage Small Cap Core K</SECNAME>
          <TICKER>BDSKX</TICKER>
        </SECINFO>
      </MFINFO>
    </SECLIST>
  </SECLISTMSGSRSV1>
</OFX>
"""


class TestParseQfxPositions:
    def test_parses_positions_with_real_ticker_and_cusip_fallback(self):
        result = parse_qfx_positions(SAMPLE_401K)
        assert result["errors"] == []
        assert len(result["positions"]) == 2
        by_ticker = {p["ticker"]: p for p in result["positions"]}
        # BDSKX has a real ticker in SECLIST -- used as-is.
        assert by_ticker["BDSKX"]["security_name"] == "BlackRock Advantage Small Cap Core K"
        assert by_ticker["BDSKX"]["shares"] == 2971.541261
        assert by_ticker["BDSKX"]["market_value"] == 68880.33
        # The Vanguard collective trust has no real public ticker -- SECLIST
        # repeats the CUSIP as <TICKER>, and this position's own ticker
        # falls back to the CUSIP the same way when SECLIST doesn't help.
        assert by_ticker["20602V101"]["security_name"] == "Vanguard Institutional 500 Index Trust"

    def test_account_balance_reads_the_401k_total(self):
        result = parse_qfx_positions(SAMPLE_401K)
        assert result["account_balance"] == 443957.88

    def test_as_of_date_normalized_to_iso(self):
        result = parse_qfx_positions(SAMPLE_401K)
        assert result["as_of_date"] == "2026-09-11"
        assert all(p["as_of_date"] == "2026-09-11" for p in result["positions"])

    def test_account_id_in_file_extracted(self):
        result = parse_qfx_positions(SAMPLE_401K)
        assert result["account_id_in_file"] == "10596750.583014-01"

    def test_ticker_falls_back_to_cusip_when_seclist_has_no_real_ticker(self):
        """A security whose SECLIST entry has no <TICKER> at all (not
        even the CUSIP-repeated-as-ticker convention this custodian
        uses) must still get a non-blank label -- fall back to the
        position's own CUSIP directly."""
        qfx = SAMPLE_401K.replace(
            "<SECNAME>Vanguard Institutional 500 Index Trust</SECNAME>\n          <TICKER>20602V101</TICKER>",
            "<SECNAME>Vanguard Institutional 500 Index Trust</SECNAME>",
        )
        result = parse_qfx_positions(qfx)
        pos = next(p for p in result["positions"] if p["cusip"] == "20602V101")
        assert pos["ticker"] == "20602V101"

    def test_position_with_no_matching_seclist_entry_still_gets_a_label(self):
        """SECLIST is sometimes incomplete -- a position whose CUSIP has
        no SECLIST entry at all must still produce a usable row (label
        falls back to the bare CUSIP), not silently drop the position."""
        qfx = SAMPLE_401K.replace(
            """      <MFINFO>
        <SECINFO>
          <SECID>
            <UNIQUEID>09258N802</UNIQUEID>
            <UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE>
          </SECID>
          <SECNAME>BlackRock Advantage Small Cap Core K</SECNAME>
          <TICKER>BDSKX</TICKER>
        </SECINFO>
      </MFINFO>
""", "")
        result = parse_qfx_positions(qfx)
        pos = next(p for p in result["positions"] if p["cusip"] == "09258N802")
        assert pos["ticker"] == "09258N802"
        assert pos["security_name"] == "09258N802"

    def test_non_ofx_file_returns_a_clear_error_not_a_crash(self):
        result = parse_qfx_positions("just some random text, not a QFX file at all")
        assert result["positions"] == []
        assert result["account_balance"] is None
        assert len(result["errors"]) == 1
        assert "QFX/OFX" in result["errors"][0]

    def test_missing_balance_section_returns_none_not_zero(self):
        """A household must be able to tell "the file had no balance
        section" (fall back to summing positions) apart from "the file
        said the balance is $0" -- these are different facts."""
        qfx = SAMPLE_401K.replace(
            "<INV401KBAL>\n          <TOTAL>443957.88</TOTAL>\n        </INV401KBAL>", "")
        result = parse_qfx_positions(qfx)
        assert result["account_balance"] is None
        assert len(result["positions"]) == 2  # positions themselves are unaffected

    def test_brokerage_style_invbal_availcash_is_read_when_no_401k_total(self):
        qfx = SAMPLE_401K.replace(
            "<INV401KBAL>\n          <TOTAL>443957.88</TOTAL>\n        </INV401KBAL>",
            "<INVBAL>\n          <AVAILCASH>443957.88</AVAILCASH>\n        </INVBAL>",
        )
        result = parse_qfx_positions(qfx)
        assert result["account_balance"] == 443957.88

    def test_sum_of_positions_matches_account_balance_when_custodian_reconciles(self):
        """Not a hard assertion the importer enforces (a custodian CAN
        disagree, e.g. pending trades) -- just documents the invariant
        verified against the real Empower export during development."""
        result = parse_qfx_positions(SAMPLE_401K)
        assert round(sum(p["market_value"] for p in result["positions"]), 2) == result["account_balance"]
