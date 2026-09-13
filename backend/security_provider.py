"""
Security lookup provider adapter (codex/portfolio-coach-recommendations).

The provider must be REPLACEABLE — business logic in coach_engine.py/
main.py never hard-wires to one vendor, only to the SecurityProvider
interface below. Never called from the browser directly and never
exposes a provider API key to frontend code: every lookup goes through
a backend endpoint in main.py, which holds any real credential (read
from an env var, never committed) server-side only.

No live market-data vendor is configured for this household today —
MockSecurityProvider (deterministic, offline, used by every test and by
default) is the ACTIVE provider. See CALCULATION_CONTRACT.md's own
"deferred provider integrations" section for what a real adapter
(e.g. a paid market-data API) would need to implement against this same
interface, and why none is wired in on this branch.

Ticker lookup limitations (surfaced to the user, not just documented
here): a ticker identifies a SECURITY, never an account type, owner,
cost basis, or tax treatment — none of those are ever inferred from a
search result. ETF/mutual-fund underlying asset-class composition
("look-through") is out of scope for this adapter; a candidate's own
asset_class is either the provider's own coarse classification (if it
has one) or left unclassified, and the caller must confirm/override it
explicitly (asset_class_source="user_supplied") rather than the system
guessing.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional
import datetime
import json
import os
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen


@dataclass
class SecurityCandidate:
    provider_identifier: Optional[str]
    ticker: Optional[str]
    security_name: str
    exchange: Optional[str]
    currency: Optional[str]
    security_type: Optional[str]  # "stock" | "etf" | "mutual_fund" | "bond" | "cash_equivalent" | "other"
    status: str = "active"        # "active" | "delisted"
    asset_class: Optional[str] = None       # provider's own coarse guess, if any -- NOT authoritative
    asset_class_source: Optional[str] = None  # "provider" when set above; never invented


@dataclass
class SecurityQuote:
    provider_identifier: Optional[str]
    price: Optional[float]
    currency: Optional[str]
    as_of: Optional[str]          # ISO date/timestamp string
    data_source: str = "unknown"


# Item 2 (quote reliability): a quote fetch can fail in several
# meaningfully different ways that the current app previously collapsed
# into one generic "No quote is available" message. QUOTE_STATUSES is
# the vocabulary every provider's get_quote_with_status() reports back
# in, so the UI can show a specific, honest state instead of guessing:
#   "ok"            -- a real quote was returned.
#   "not_found"     -- the identifier isn't recognized by this provider
#                      (delisted, mistyped, or never existed there).
#   "rate_limited"  -- the provider throttled this request; retrying
#                      immediately is expected to fail again.
#   "provider_error" -- a network/parse/unexpected-response failure.
#                      Distinct from rate_limited so the UI can suggest
#                      "try again" rather than "wait."
QUOTE_STATUSES = ("ok", "not_found", "rate_limited", "provider_error")


@dataclass
class QuoteResult:
    """Richer quote outcome than a bare Optional[SecurityQuote] --
    carries WHY a quote could not be produced, never invents a quote to
    paper over a failure, and (via cached/fetched_at, set by the cache
    layer below, not by a provider itself) lets the caller show whether
    this result came from a fresh provider call or a reused one from
    earlier in the same review session."""
    quote: Optional[SecurityQuote]
    status: str            # one of QUOTE_STATUSES
    message: str
    cached: bool = False
    fetched_at: Optional[str] = None  # ISO timestamp of the underlying fetch (fresh or cached)

    def __post_init__(self):
        if self.status not in QUOTE_STATUSES:
            raise ValueError(f"Unknown quote status '{self.status}' -- must be one of {QUOTE_STATUSES}")


class SecurityProvider(ABC):
    """Interface every concrete provider must implement. main.py's
    endpoints resolve exactly ONE active provider via get_active_
    provider() below and never import a concrete provider class
    directly — swapping providers is a one-line change to
    set_active_provider(), not a rewrite of any calling code."""

    @abstractmethod
    def search(self, query: str) -> List[SecurityCandidate]:
        """Symbol/name search. Returns [] for an empty/whitespace query
        rather than raising or returning "everything.\""""

    @abstractmethod
    def get_quote(self, provider_identifier: str) -> Optional[SecurityQuote]:
        """A current/delayed quote for an already-resolved provider
        identifier. Returns None (not a fabricated price) if the
        identifier isn't recognized."""

    def get_quote_with_status(self, provider_identifier: str) -> QuoteResult:
        """Default implementation for any provider (including test
        stubs) that only implements get_quote(): reports "ok" or
        "not_found" but can never distinguish a rate limit from a
        network error, since get_quote() itself throws that information
        away. AlphaVantageSecurityProvider overrides this to report the
        real reason. Never called by tests directly to bypass a
        provider's own status logic -- callers should call THIS method,
        not get_quote(), whenever they need to show the user why a
        quote failed."""
        quote = self.get_quote(provider_identifier)
        if quote is not None:
            return QuoteResult(quote, "ok", "Quote retrieved.")
        return QuoteResult(None, "not_found", "No quote is available for this identifier.")

    @property
    def provider_name(self) -> str:
        return self.__class__.__name__

    @property
    def is_live(self) -> bool:
        return False


class MockSecurityProvider(SecurityProvider):
    """Deterministic, offline provider — no live vendor is configured.
    A small built-in catalog covers search/candidate-confirmation/quote/
    delisted-status flows end to end without any network access, live
    credentials, or nondeterminism (safe for every test)."""

    _CATALOG = [
        SecurityCandidate("MOCK:VTI", "VTI", "Vanguard Total Stock Market ETF", "NYSEARCA", "USD", "etf",
                           asset_class="us_large_cap", asset_class_source="provider"),
        SecurityCandidate("MOCK:VXUS", "VXUS", "Vanguard Total International Stock ETF", "NASDAQ", "USD", "etf",
                           asset_class="international_developed", asset_class_source="provider"),
        SecurityCandidate("MOCK:BND", "BND", "Vanguard Total Bond Market ETF", "NASDAQ", "USD", "etf",
                           asset_class="us_bonds", asset_class_source="provider"),
        SecurityCandidate("MOCK:VNQ", "VNQ", "Vanguard Real Estate ETF", "NYSEARCA", "USD", "etf",
                           asset_class="real_estate", asset_class_source="provider"),
        SecurityCandidate("MOCK:AAPL", "AAPL", "Apple Inc.", "NASDAQ", "USD", "stock",
                           asset_class="us_large_cap", asset_class_source="provider"),
        # A fund the mock catalog deliberately has NO asset-class guess
        # for — exercises the "show unclassified, ask the user to
        # confirm" path without inventing a classification.
        SecurityCandidate("MOCK:XYZF", "XYZF", "Example Alternative Strategies Fund", "NASDAQ", "USD", "mutual_fund"),
        SecurityCandidate("MOCK:OLDCO", "OLDCO", "Old Company Inc.", "OTC", "USD", "stock", status="delisted"),
    ]
    _PRICES = {"MOCK:VTI": 275.40, "MOCK:VXUS": 62.10, "MOCK:BND": 72.85, "MOCK:VNQ": 88.20, "MOCK:AAPL": 230.15}

    def search(self, query: str) -> List[SecurityCandidate]:
        q = (query or "").strip().lower()
        if not q:
            return []
        return [c for c in self._CATALOG if q in (c.ticker or "").lower() or q in c.security_name.lower()]

    def get_quote(self, provider_identifier: str) -> Optional[SecurityQuote]:
        price = self._PRICES.get(provider_identifier)
        if price is None:
            return None
        return SecurityQuote(provider_identifier, price, "USD", datetime.date.today().isoformat(), "mock")

    @property
    def provider_name(self) -> str:
        return "Built-in offline catalog"


class AlphaVantageSecurityProvider(SecurityProvider):
    """Small, dependency-free Alpha Vantage adapter.

    The provider is deliberately metadata/quote-only: it never assigns an
    asset class, expense ratio, cost basis, or account availability. Those
    require user confirmation because a ticker alone cannot establish them.
    Alpha Vantage documents SYMBOL_SEARCH and GLOBAL_QUOTE for stock, ETF,
    and mutual-fund symbols.  Transient network/rate-limit/provider errors
    become an empty result rather than breaking the holdings form.
    """
    _BASE_URL = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str, timeout_seconds: float = 8.0):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _request(self, **params) -> Dict:
        """Kept for backward compatibility with any caller that only
        wants the payload -- discards the failure reason. Prefer
        _request_with_status below when the caller needs to tell a rate
        limit apart from a network/parse error."""
        payload, _status = self._request_with_status(**params)
        return payload

    def _request_with_status(self, **params) -> "tuple[Dict, str]":
        """Returns (payload, status) where status is "ok",
        "rate_limited", or "provider_error" -- never fabricates data for
        any of them. Alpha Vantage signals a rate limit via a "Note" (or
        sometimes "Information") field in an otherwise-200 response
        rather than an HTTP error code, so that has to be detected in
        the payload itself, not via an exception."""
        query = urlencode({**params, "apikey": self.api_key})
        try:
            with urlopen(f"{self._BASE_URL}?{query}", timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (URLError, OSError, ValueError, json.JSONDecodeError):
            return {}, "provider_error"
        if not isinstance(payload, dict):
            return {}, "provider_error"
        if "Note" in payload or "Information" in payload:
            return {}, "rate_limited"
        if "Error Message" in payload:
            return {}, "provider_error"
        return payload, "ok"

    @staticmethod
    def _security_type(instrument_type: Optional[str]) -> str:
        normalized = (instrument_type or "").strip().lower()
        if "mutual" in normalized:
            return "mutual_fund"
        if "etf" in normalized:
            return "etf"
        if "bond" in normalized:
            return "bond"
        return "stock" if normalized in {"equity", "stock"} else "other"

    def search(self, query: str) -> List[SecurityCandidate]:
        query = (query or "").strip()
        if not query:
            return []
        matches = self._request(function="SYMBOL_SEARCH", keywords=query).get("bestMatches", [])
        candidates = []
        for match in matches[:10]:
            symbol = match.get("1. symbol")
            name = match.get("2. name")
            if not symbol or not name:
                continue
            candidates.append(SecurityCandidate(
                provider_identifier=f"ALPHAVANTAGE:{symbol}", ticker=symbol, security_name=name,
                exchange=match.get("4. region"), currency=match.get("8. currency"),
                security_type=self._security_type(match.get("3. type")), status="active",
            ))
        return candidates

    def get_quote(self, provider_identifier: str) -> Optional[SecurityQuote]:
        prefix = "ALPHAVANTAGE:"
        if not provider_identifier or not provider_identifier.startswith(prefix):
            return None
        quote = self._request(function="GLOBAL_QUOTE", symbol=provider_identifier[len(prefix):]).get("Global Quote", {})
        try:
            price = float(quote["05. price"])
        except (KeyError, TypeError, ValueError):
            return None
        return SecurityQuote(provider_identifier, price, None, quote.get("07. latest trading day"), "alpha_vantage")

    def get_quote_with_status(self, provider_identifier: str) -> QuoteResult:
        prefix = "ALPHAVANTAGE:"
        if not provider_identifier or not provider_identifier.startswith(prefix):
            return QuoteResult(None, "not_found", "This holding has no Alpha Vantage identifier on file.")
        payload, status = self._request_with_status(function="GLOBAL_QUOTE", symbol=provider_identifier[len(prefix):])
        if status == "rate_limited":
            return QuoteResult(None, "rate_limited", "Alpha Vantage rate-limited this request. Try again in a minute.")
        if status == "provider_error":
            return QuoteResult(None, "provider_error", "Could not reach Alpha Vantage right now. Try again shortly.")
        quote_payload = payload.get("Global Quote", {})
        try:
            price = float(quote_payload["05. price"])
        except (KeyError, TypeError, ValueError):
            return QuoteResult(None, "not_found", "Alpha Vantage does not recognize this symbol (it may be delisted or mistyped).")
        quote = SecurityQuote(provider_identifier, price, None, quote_payload.get("07. latest trading day"), "alpha_vantage")
        return QuoteResult(quote, "ok", "Quote retrieved.")

    @property
    def provider_name(self) -> str:
        return "Alpha Vantage"

    @property
    def is_live(self) -> bool:
        return True


_ACTIVE_PROVIDER: Optional[SecurityProvider] = None


def get_active_provider() -> SecurityProvider:
    """Lazily instantiates MockSecurityProvider as the default. A real
    deployment with live credentials configured would call
    set_active_provider() once at startup (e.g. in main.py, gated on an
    env var) — no other code needs to change."""
    global _ACTIVE_PROVIDER
    if _ACTIVE_PROVIDER is None:
        api_key = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
        _ACTIVE_PROVIDER = AlphaVantageSecurityProvider(api_key) if api_key else MockSecurityProvider()
    return _ACTIVE_PROVIDER


def set_active_provider(provider: SecurityProvider) -> None:
    """Test hook / real-provider swap-in point. Also clears the quote
    cache below -- a cached quote from the previous provider must never
    be served as if it came from the new one."""
    global _ACTIVE_PROVIDER
    _ACTIVE_PROVIDER = provider
    clear_quote_cache()


# ── Session quote cache (item 2: avoid repeatedly calling the provider
# during one review session) ─────────────────────────────────────────
# Deliberately a plain in-process dict, not a DB table: this cache is a
# same-review-session convenience only. It is never treated as a
# recorded fact (a holding's own market_value/as_of_date are the
# recorded facts; "Use quote value" is still a separate, explicit
# confirmation step -- see main.py). Restarting the backend clears it,
# which is fine since it exists only to avoid re-hitting a rate-limited
# or slow provider for the same identifier within one sitting.
DEFAULT_QUOTE_CACHE_TTL_SECONDS = 15 * 60  # 15 minutes

_QUOTE_CACHE: Dict[str, "tuple[QuoteResult, datetime.datetime]"] = {}


def clear_quote_cache() -> None:
    _QUOTE_CACHE.clear()


def get_cached_or_fetch_quote(provider_identifier: str, ttl_seconds: float = DEFAULT_QUOTE_CACHE_TTL_SECONDS) -> QuoteResult:
    """The single choke point main.py should call for a quote --
    reuses a still-fresh cached result instead of calling the provider
    again, and marks the returned QuoteResult as `cached=True` with the
    original `fetched_at` so the UI can show "from 3 minutes ago"
    instead of implying every check is a brand-new live call. Only "ok"
    and "not_found" results are cached -- a rate-limited or
    provider_error result is deliberately NOT cached, so the very next
    manual retry actually hits the provider again rather than replaying
    the same failure for the full TTL."""
    now = datetime.datetime.now()
    cached = _QUOTE_CACHE.get(provider_identifier)
    if cached is not None:
        result, fetched_at = cached
        if (now - fetched_at).total_seconds() <= ttl_seconds:
            return QuoteResult(result.quote, result.status, result.message, cached=True, fetched_at=fetched_at.isoformat())
    result = get_active_provider().get_quote_with_status(provider_identifier)
    if result.status in ("ok", "not_found"):
        _QUOTE_CACHE[provider_identifier] = (result, now)
    return QuoteResult(result.quote, result.status, result.message, cached=False, fetched_at=now.isoformat())


def peek_cached_quote(provider_identifier: str, ttl_seconds: float = DEFAULT_QUOTE_CACHE_TTL_SECONDS) -> Optional[QuoteResult]:
    """Reads a still-fresh cached quote WITHOUT ever calling the
    provider -- used where a fresh fetch would be inappropriate (e.g.
    CSV import preview, which must stay a pure "compare what's already
    known" step, never an automatic quote check). Returns None if
    nothing cached, the cached entry expired, or the cached result
    wasn't a real quote (a not_found result never becomes a comparison
    price)."""
    cached = _QUOTE_CACHE.get(provider_identifier)
    if cached is None:
        return None
    result, fetched_at = cached
    if (datetime.datetime.now() - fetched_at).total_seconds() > ttl_seconds:
        return None
    if result.quote is None:
        return None
    return QuoteResult(result.quote, result.status, result.message, cached=True, fetched_at=fetched_at.isoformat())
