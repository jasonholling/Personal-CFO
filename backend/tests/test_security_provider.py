"""
Security lookup provider adapter tests (codex/portfolio-coach-
recommendations). Entirely offline against MockSecurityProvider --
never requires live credentials or network access. One optional live
smoke test is gated behind an env var (skipped by default) at the
bottom of this file, per the brief's "mock the provider in tests, add
one optional live smoke test only when credentials are configured."
"""
import os

import pytest

from security_provider import (
    SecurityProvider, SecurityCandidate, SecurityQuote,
    MockSecurityProvider, get_active_provider, set_active_provider,
)


@pytest.fixture(autouse=True)
def _reset_active_provider():
    """Never let one test's set_active_provider() leak into the next."""
    set_active_provider(MockSecurityProvider())
    yield
    set_active_provider(MockSecurityProvider())


class TestMockSecurityProviderSearch:
    def test_search_by_ticker(self):
        results = MockSecurityProvider().search("VTI")
        assert any(c.ticker == "VTI" for c in results)

    def test_search_is_case_insensitive(self):
        results = MockSecurityProvider().search("vti")
        assert any(c.ticker == "VTI" for c in results)

    def test_search_by_name_substring(self):
        results = MockSecurityProvider().search("total bond")
        assert any(c.ticker == "BND" for c in results)

    def test_empty_query_returns_no_candidates(self):
        assert MockSecurityProvider().search("") == []

    def test_whitespace_only_query_returns_no_candidates(self):
        assert MockSecurityProvider().search("   ") == []

    def test_no_match_returns_empty_list_not_error(self):
        assert MockSecurityProvider().search("NOT_A_REAL_TICKER_ZZZ") == []

    def test_candidate_includes_exchange_type_currency_status(self):
        results = MockSecurityProvider().search("AAPL")
        c = results[0]
        assert c.exchange == "NASDAQ"
        assert c.security_type == "stock"
        assert c.currency == "USD"
        assert c.status == "active"

    def test_delisted_security_flagged(self):
        results = MockSecurityProvider().search("OLDCO")
        assert results[0].status == "delisted"

    def test_provider_asset_class_guess_marked_with_source(self):
        results = MockSecurityProvider().search("VTI")
        assert results[0].asset_class == "us_large_cap"
        assert results[0].asset_class_source == "provider"

    def test_unclassifiable_fund_has_no_invented_asset_class(self):
        """A fund the provider can't classify shows None, not a guess --
        the caller must show "unclassified" and ask the user to
        confirm, per the brief."""
        results = MockSecurityProvider().search("XYZF")
        assert results[0].asset_class is None
        assert results[0].asset_class_source is None


class TestMockSecurityProviderQuote:
    def test_quote_for_known_identifier(self):
        quote = MockSecurityProvider().get_quote("MOCK:VTI")
        assert quote.price == 275.40
        assert quote.currency == "USD"
        assert quote.data_source == "mock"
        assert quote.as_of  # a real date string, not blank

    def test_quote_for_unknown_identifier_returns_none_not_fabricated_price(self):
        assert MockSecurityProvider().get_quote("MOCK:NOT_REAL") is None


class TestProviderRegistry:
    def test_get_active_provider_defaults_to_mock(self):
        assert isinstance(get_active_provider(), MockSecurityProvider)

    def test_set_active_provider_swaps_without_touching_callers(self):
        class StubProvider(SecurityProvider):
            def search(self, query):
                return [SecurityCandidate("STUB:1", "STUB", "Stub Security", "STUB", "USD", "stock")]

            def get_quote(self, provider_identifier):
                return SecurityQuote("STUB:1", 1.00, "USD", "2026-01-01", "stub")

        set_active_provider(StubProvider())
        results = get_active_provider().search("anything")
        assert results[0].ticker == "STUB"


# ── Optional live smoke test ────────────────────────────────────────────
# Gated behind an env var so the suite never requires (or attempts) a
# live network call/credential by default -- per the brief's explicit
# instruction. No live provider is wired into this branch (see
# CALCULATION_CONTRACT.md's "deferred provider integrations"), so this
# currently always skips; it documents the expected shape for whenever
# one is added, rather than silently having no test at all for that day.
@pytest.mark.skipif(
    not os.environ.get("PERSONAL_CFO_LIVE_SECURITY_PROVIDER_TEST"),
    reason="Live provider smoke test only runs when PERSONAL_CFO_LIVE_SECURITY_PROVIDER_TEST is set "
           "and a real provider is configured -- no live vendor is wired into this branch yet.",
)
def test_live_provider_smoke():
    provider = get_active_provider()
    results = provider.search("AAPL")
    assert results, "live provider returned no candidates for a well-known ticker"
