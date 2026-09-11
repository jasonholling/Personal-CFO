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
from typing import List, Optional
import datetime


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


class MockSecurityProvider(SecurityProvider):
    """Deterministic, offline provider — no live vendor is configured.
    A small built-in catalog covers search/candidate-confirmation/quote/
    delisted-status flows end to end without any network access, live
    credentials, or nondeterminism (safe for every test)."""

    _CATALOG = [
        SecurityCandidate("MOCK:VTI", "VTI", "Vanguard Total Stock Market ETF", "NYSEARCA", "USD", "etf",
                           asset_class="us_large_cap", asset_class_source="provider"),
        SecurityCandidate("MOCK:VXUS", "VXUS", "Vanguard Total International Stock ETF", "NASDAQ", "USD", "etf",
                           asset_class="international_stock", asset_class_source="provider"),
        SecurityCandidate("MOCK:BND", "BND", "Vanguard Total Bond Market ETF", "NASDAQ", "USD", "etf",
                           asset_class="bonds", asset_class_source="provider"),
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


_ACTIVE_PROVIDER: Optional[SecurityProvider] = None


def get_active_provider() -> SecurityProvider:
    """Lazily instantiates MockSecurityProvider as the default. A real
    deployment with live credentials configured would call
    set_active_provider() once at startup (e.g. in main.py, gated on an
    env var) — no other code needs to change."""
    global _ACTIVE_PROVIDER
    if _ACTIVE_PROVIDER is None:
        _ACTIVE_PROVIDER = MockSecurityProvider()
    return _ACTIVE_PROVIDER


def set_active_provider(provider: SecurityProvider) -> None:
    """Test hook / real-provider swap-in point."""
    global _ACTIVE_PROVIDER
    _ACTIVE_PROVIDER = provider
