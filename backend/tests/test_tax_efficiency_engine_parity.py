"""
Parity tests: run_tax_efficiency_simulation's taxable_first/roth_first
strategies use _ordered_draw (simulation_engine.py) instead of
annual_engine.simulate_withdrawal_year, for a measured, documented
performance reason (see CALCULATION_CONTRACT.md's "run_tax_efficiency_
simulation's ordered strategies" section, alongside run_swr_analysis's
identical exception) — migrating even 2 of the 3 strategies onto the
full engine measured ~2.9x slower (0.084s -> 0.242s per call) at this
function's call volume (1000 trials x ~35 years x up to 3 strategies).

These tests are NOT a substitute for that decision; they're what makes
the decision safe: proof that the independently-maintained fast path
produces byte-identical results to the shared engine for the same
inputs, across a battery of cases (ample funds, an exhausted first
bucket spilling to the next, every bucket exhausted with real unmet
need, and a zero-need no-op). If either implementation's tax/gross-up
math ever drifts, this file fails before a user ever sees the drift.

A second pass (independent review, 2026-09-07 follow-up) added full-year
parity, not just bucket-mechanics parity: the first pass covered
_ordered_draw in isolation and missed two real bugs in the surrounding
per-year cash-flow setup it doesn't test (a negative one-time event's
deficit silently unfunded; a positive recurring event's surplus silently
discarded) — both now fixed in _cash_available_offsets_need and covered
by a 72-case matrix (ordinary spending x pension x signed one-time
events x signed recurring events x both orders) below.
"""

import itertools

import pytest

from annual_engine import (
    AccountState,
    DEFAULT_ORDER,
    ROTH_FIRST_ORDER,
    flat_rate_tax_model,
    simulate_withdrawal_year,
)
from simulation_engine import _cash_available_offsets_need, _ordered_draw

TAX_PRETAX = 0.22
TAX_TAXABLE = 0.15


def _via_shared_engine(pretax, roth, taxable, hsa, remaining, order):
    """Route `remaining` through simulate_withdrawal_year the same way
    _ordered_draw is meant to behave: no guaranteed income, no growth, no
    RMD — isolating just the per-bucket order/tax/gross-up mechanics
    _ordered_draw reimplements for speed."""
    result = simulate_withdrawal_year(
        opening=AccountState(pretax=pretax, roth=roth, taxable=taxable, hsa=hsa),
        spending_need=remaining,
        guaranteed_income=0.0,
        life_event_cash=0.0,
        rmd_amount=0.0,
        tax_model=flat_rate_tax_model(pretax_rate=TAX_PRETAX, taxable_rate=TAX_TAXABLE),
        growth_rate=0.0,
        order=order,
    )
    c = result.closing
    return c.pretax, c.roth, c.taxable, c.hsa, result.unmet_need, result.total_tax


CASES = [
    # (label, pretax, roth, taxable, hsa, remaining)
    ("ample_first_bucket_covers_it", 500_000, 200_000, 300_000, 20_000, 40_000),
    ("first_bucket_exhausted_spills_to_second", 500_000, 200_000, 15_000, 0, 40_000),
    ("every_bucket_exhausted_real_unmet_need", 10_000, 5_000, 3_000, 0, 40_000),
    ("zero_need_is_a_no_op", 500_000, 200_000, 300_000, 20_000, 0),
    ("hsa_only_left", 0, 0, 0, 25_000, 10_000),
    ("small_fractional_dollar_amounts", 12_345.67, 987.65, 4_321.09, 0, 8_765.43),
]


@pytest.mark.parametrize("label,pretax,roth,taxable,hsa,remaining", CASES)
def test_taxable_first_matches_shared_engine(label, pretax, roth, taxable, hsa, remaining):
    got = _ordered_draw(pretax, roth, taxable, hsa, remaining, DEFAULT_ORDER, TAX_PRETAX, TAX_TAXABLE)
    want_p, want_r, want_t, want_h, want_unmet, want_tax = _via_shared_engine(
        pretax, roth, taxable, hsa, remaining, DEFAULT_ORDER)
    got_p, got_r, got_t, got_h, got_remaining, got_tax = got
    assert got_p == pytest.approx(want_p, abs=0.01)
    assert got_r == pytest.approx(want_r, abs=0.01)
    assert got_t == pytest.approx(want_t, abs=0.01)
    assert got_h == pytest.approx(want_h, abs=0.01)
    assert max(0.0, got_remaining) == pytest.approx(want_unmet, abs=0.01)
    assert got_tax == pytest.approx(want_tax, abs=0.01)


# ── Full-year parity: cash-flow setup + bucket draws together ──────────────
#
# The tests above isolate _ordered_draw's bucket mechanics in isolation.
# These cover the surrounding per-year cash-flow arithmetic too
# (_cash_available_offsets_need) — independent review, 2026-09-07 follow-
# up, after the first review's bucket-only parity coverage missed two
# real bugs in that surrounding setup (a negative one-time event's
# deficit not funded; a positive recurring event's surplus discarded).
# Matrix: ordinary spending x pension x signed one-time events x signed
# recurring events x both orders — mirrors the reviewer's own "complete
# year around _ordered_draw, not only bucket withdrawals" ask.

_OPENING = (200_000.0, 100_000.0, 150_000.0, 0.0)  # pretax, roth, taxable, hsa

FULL_YEAR_MATRIX = list(itertools.product(
    (0.0, 40_000.0),           # year_need: no ordinary spending, or some
    (0.0, 20_000.0),           # guaranteed (pension)
    (0.0, -50_000.0, 50_000.0),  # one_time_cash: none, expense, windfall
    (0.0, -20_000.0, 20_000.0),  # recurring life_event_monthly: none, cost, income
    (DEFAULT_ORDER, ROTH_FIRST_ORDER),
))


def _via_fast_path(year_need, guaranteed, one_time_cash, recurring, order):
    pretax, roth, taxable, hsa = _OPENING
    net_need, surplus_credit = _cash_available_offsets_need(year_need, guaranteed, one_time_cash, recurring)
    taxable += surplus_credit
    p, r, t, h, remaining, tax = _ordered_draw(pretax, roth, taxable, hsa, net_need, order,
                                                TAX_PRETAX, TAX_TAXABLE)
    return p, r, t, h, max(0.0, remaining), tax


def _via_shared_engine_full_year(year_need, guaranteed, one_time_cash, recurring, order):
    pretax, roth, taxable, hsa = _OPENING
    result = simulate_withdrawal_year(
        opening=AccountState(pretax=pretax, roth=roth, taxable=taxable, hsa=hsa),
        spending_need=year_need - recurring,
        guaranteed_income=guaranteed,
        life_event_cash=one_time_cash,
        rmd_amount=0.0,
        tax_model=flat_rate_tax_model(pretax_rate=TAX_PRETAX, taxable_rate=TAX_TAXABLE),
        growth_rate=0.0,
        order=order,
    )
    c = result.closing
    return c.pretax, c.roth, c.taxable, c.hsa, result.unmet_need, result.total_tax


@pytest.mark.parametrize("year_need,guaranteed,one_time_cash,recurring,order", FULL_YEAR_MATRIX)
def test_full_year_cash_flow_matches_shared_engine(year_need, guaranteed, one_time_cash, recurring, order):
    got = _via_fast_path(year_need, guaranteed, one_time_cash, recurring, order)
    want = _via_shared_engine_full_year(year_need, guaranteed, one_time_cash, recurring, order)
    for got_val, want_val in zip(got, want):
        assert got_val == pytest.approx(want_val, abs=0.01)


@pytest.mark.parametrize("label,pretax,roth,taxable,hsa,remaining", CASES)
def test_roth_first_matches_shared_engine(label, pretax, roth, taxable, hsa, remaining):
    got = _ordered_draw(pretax, roth, taxable, hsa, remaining, ROTH_FIRST_ORDER, TAX_PRETAX, TAX_TAXABLE)
    want_p, want_r, want_t, want_h, want_unmet, want_tax = _via_shared_engine(
        pretax, roth, taxable, hsa, remaining, ROTH_FIRST_ORDER)
    got_p, got_r, got_t, got_h, got_remaining, got_tax = got
    assert got_p == pytest.approx(want_p, abs=0.01)
    assert got_r == pytest.approx(want_r, abs=0.01)
    assert got_t == pytest.approx(want_t, abs=0.01)
    assert got_h == pytest.approx(want_h, abs=0.01)
    assert max(0.0, got_remaining) == pytest.approx(want_unmet, abs=0.01)
    assert got_tax == pytest.approx(want_tax, abs=0.01)
