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
        query = urlencode({**params, "apikey": self.api_key})
        try:
            with urlopen(f"{self._BASE_URL}?{query}", timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (URLError, OSError, ValueError, json.JSONDecodeError):
            return {}
        # API "Note", "Information", and "Error Message" responses do
        # not contain usable data. Never turn one into a fabricated result.
        return payload if isinstance(payload, dict) and not any(k in payload for k in ("Note", "Information", "Error Message")) else {}

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
    """Test hook / real-provider swap-in point."""
    global _ACTIVE_PROVIDER
    _ACTIVE_PROVIDER = provider
