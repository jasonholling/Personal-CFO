"""
Parity tests: run_swr_analysis's per-year withdrawal step (_swr_year_step)
against annual_engine.simulate_withdrawal_year, per item 3 of the
2026-09-07 follow-on task list: "Bring SWR under the shared engine — or
make its fast path provably equivalent. If performance prevents full
migration, keep a lightweight implementation but compare it against the
shared engine across randomized annual cases and full multi-year
scenarios."

Full migration was already attempted and reverted (CALCULATION_
CONTRACT.md's run_swr_analysis exception): passed every existing test
with identical results, then measured >2x slower (26.83s -> 57.41s on
the SWR test subset) at this loop's call volume (binary search x 1000
trials x ~40yrs). _swr_year_step is the kept fast path; this file is
what makes keeping it safe.

Mapping to the shared engine (see _swr_year_step's own docstring for
SWR's one real material-assumption divergence — portfolio_draw is drawn
ON TOP OF guaranteed income, never netted against it, so
guaranteed_income=0.0 below is correct, not an oversight):
  - opening = AccountState(pretax, roth, taxable, hsa)
  - spending_need = portfolio_draw - event_monthly (NOT floored — matches
    annual_engine's own convention exactly, letting a large recurring
    event_monthly correctly bank a surplus via the shared engine's
    surplus-sweep branch)
  - guaranteed_income = 0.0
  - life_event_cash = event_cash
  - rmd_amount = rmd
  - tax_model = marginal_bracket_tax_model(pretax_rate=pretax_tax_rate, taxable_rate=0.0)
  - growth_rate = 0.0 (growth is applied separately by the calling loop,
    same convention as _ordered_draw/_optimal_draw's parity tests)
  - order = DEFAULT_ORDER (taxable, pretax, hsa, roth)
"""

import itertools
import random

import pytest

from annual_engine import AccountState, DEFAULT_ORDER, marginal_bracket_tax_model, simulate_withdrawal_year
from simulation_engine import _swr_year_step

PRETAX_TAX_RATE = 0.22


def _via_shared_engine(pretax, roth, taxable, hsa, portfolio_draw, event_cash, event_monthly, rmd,
                        pretax_tax_rate):
    result = simulate_withdrawal_year(
        opening=AccountState(pretax=pretax, roth=roth, taxable=taxable, hsa=hsa),
        spending_need=portfolio_draw - event_monthly,
        guaranteed_income=0.0,
        life_event_cash=event_cash,
        rmd_amount=rmd,
        tax_model=marginal_bracket_tax_model(pretax_rate=pretax_tax_rate, taxable_rate=0.0),
        growth_rate=0.0,
        order=DEFAULT_ORDER,
    )
    c = result.closing
    return c.pretax, c.roth, c.taxable, c.hsa, result.unmet_need


# ── Hand-picked cases covering the documented edge behavior ────────────────

CASES = [
    # (label, pretax, roth, taxable, hsa, portfolio_draw, event_cash, event_monthly, rmd)
    ("ample_funds_no_events", 500_000, 200_000, 300_000, 20_000, 40_000, 0, 0, 0),
    ("negative_one_time_event_exceeds_taxable",
     500_000, 200_000, 100_000, 0, 40_000, -150_000, 0, 0),
    ("positive_recurring_event_exceeds_draw",
     500_000, 200_000, 300_000, 0, 20_000, 0, 30_000, 0),
    ("rmd_covers_the_whole_need", 500_000, 0, 0, 0, 10_000, 0, 0, 30_000),
    ("rmd_partially_covers_need_spills_to_taxable",
     500_000, 0, 100_000, 0, 60_000, 0, 0, 20_000),
    ("every_bucket_exhausted_real_unmet_need", 5_000, 2_000, 1_000, 0, 40_000, 0, 0, 0),
    ("zero_draw_is_a_no_op", 500_000, 200_000, 300_000, 20_000, 0, 0, 0, 0),
    ("negative_event_and_rmd_together",
     200_000, 50_000, 80_000, 0, 40_000, -100_000, 0, 15_000),
]


@pytest.mark.parametrize(
    "label,pretax,roth,taxable,hsa,portfolio_draw,event_cash,event_monthly,rmd", CASES)
def test_swr_year_step_matches_shared_engine(
        label, pretax, roth, taxable, hsa, portfolio_draw, event_cash, event_monthly, rmd):
    got = _swr_year_step(pretax, roth, taxable, hsa, portfolio_draw, event_cash, event_monthly,
                          rmd, PRETAX_TAX_RATE)
    want = _via_shared_engine(pretax, roth, taxable, hsa, portfolio_draw, event_cash, event_monthly,
                               rmd, PRETAX_TAX_RATE)
    got_p, got_r, got_t, got_h, got_remaining = got
    want_p, want_r, want_t, want_h, want_unmet = want
    assert got_p == pytest.approx(want_p, abs=0.01)
    assert got_r == pytest.approx(want_r, abs=0.01)
    assert got_t == pytest.approx(want_t, abs=0.01)
    assert got_h == pytest.approx(want_h, abs=0.01)
    assert max(0.0, got_remaining) == pytest.approx(want_unmet, abs=0.01)


# ── Randomized annual cases (item 3's explicit ask) ─────────────────────────

def _random_cases(n, seed):
    rng = random.Random(seed)
    for _ in range(n):
        pretax = rng.uniform(0, 1_000_000)
        roth = rng.uniform(0, 500_000)
        taxable = rng.uniform(0, 500_000)
        hsa = rng.uniform(0, 100_000)
        portfolio_draw = rng.uniform(0, 150_000)
        # Occasionally a big negative event, occasionally a big positive
        # recurring stream, occasionally neither.
        event_cash = rng.choice([0, rng.uniform(-300_000, 300_000)])
        event_monthly = rng.choice([0, rng.uniform(0, 100_000)])
        rmd = rng.choice([0, rng.uniform(0, pretax)])
        tax_rate = rng.choice([0.0, 0.12, 0.22, 0.24, 0.32])
        yield (pretax, roth, taxable, hsa, portfolio_draw, event_cash, event_monthly, rmd, tax_rate)


@pytest.mark.parametrize("case", list(_random_cases(200, seed=42)))
def test_swr_year_step_matches_shared_engine_randomized(case):
    pretax, roth, taxable, hsa, portfolio_draw, event_cash, event_monthly, rmd, tax_rate = case
    got = _swr_year_step(pretax, roth, taxable, hsa, portfolio_draw, event_cash, event_monthly,
                          rmd, tax_rate)
    want = _via_shared_engine(pretax, roth, taxable, hsa, portfolio_draw, event_cash, event_monthly,
                               rmd, tax_rate)
    got_p, got_r, got_t, got_h, got_remaining = got
    want_p, want_r, want_t, want_h, want_unmet = want
    assert got_p == pytest.approx(want_p, abs=0.01)
    assert got_r == pytest.approx(want_r, abs=0.01)
    assert got_t == pytest.approx(want_t, abs=0.01)
    assert got_h == pytest.approx(want_h, abs=0.01)
    assert max(0.0, got_remaining) == pytest.approx(want_unmet, abs=0.01)


# ── Full multi-year scenario (item 3's second explicit ask) ────────────────

def test_swr_year_step_matches_shared_engine_over_a_full_multi_year_run():
    """Chain _swr_year_step across a 20-year horizon with growth, RMDs
    kicking in partway through, and a life event mid-plan — comparing the
    running balances against the same chain built from
    simulate_withdrawal_year at every step. Not just single-year parity:
    a bug that only shows up after several years of compounding drift
    (e.g. a rounding asymmetry) would surface here even if every
    individual year matched in isolation."""
    from projection_engine import _rmd, rmd_start_age

    pretax_fast, roth_fast, taxable_fast, hsa_fast = 800_000.0, 150_000.0, 200_000.0, 30_000.0
    pretax_eng, roth_eng, taxable_eng, hsa_eng = 800_000.0, 150_000.0, 200_000.0, 30_000.0
    jason_age = 60
    rmd_start = rmd_start_age(jason_age)
    growth = 0.05

    for yr in range(20):
        age = jason_age + yr
        portfolio_draw = 60_000 * (1.02 ** yr)
        event_cash = -50_000 if yr == 5 else 0
        event_monthly = 8_000 if yr == 10 else 0
        rmd_fast = _rmd(pretax_fast, age, rmd_start)
        rmd_eng = _rmd(pretax_eng, age, rmd_start)
        assert rmd_fast == pytest.approx(rmd_eng, abs=0.01)  # sanity: both chains track identically so far
        tax_rate = 0.22 if age < 73 else 0.24

        pretax_fast, roth_fast, taxable_fast, hsa_fast, remaining_fast = _swr_year_step(
            pretax_fast, roth_fast, taxable_fast, hsa_fast, portfolio_draw,
            event_cash, event_monthly, rmd_fast, tax_rate)
        pretax_eng, roth_eng, taxable_eng, hsa_eng, unmet_eng = _via_shared_engine(
            pretax_eng, roth_eng, taxable_eng, hsa_eng, portfolio_draw,
            event_cash, event_monthly, rmd_eng, tax_rate)

        assert pretax_fast == pytest.approx(pretax_eng, abs=0.01), f"pretax diverged at yr={yr}"
        assert roth_fast == pytest.approx(roth_eng, abs=0.01), f"roth diverged at yr={yr}"
        assert taxable_fast == pytest.approx(taxable_eng, abs=0.01), f"taxable diverged at yr={yr}"
        assert hsa_fast == pytest.approx(hsa_eng, abs=0.01), f"hsa diverged at yr={yr}"
        assert max(0.0, remaining_fast) == pytest.approx(unmet_eng, abs=0.01), f"unmet diverged at yr={yr}"

        # Growth applied identically to both chains (same convention as
        # the real run_swr_analysis loop — growth happens after the
        # withdrawal step, outside this function).
        pretax_fast, roth_fast, taxable_fast, hsa_fast = (
            max(0, pretax_fast * (1 + growth)), max(0, roth_fast * (1 + growth)),
            max(0, taxable_fast * (1 + growth)), max(0, hsa_fast * (1 + growth)))
        pretax_eng, roth_eng, taxable_eng, hsa_eng = (
            max(0, pretax_eng * (1 + growth)), max(0, roth_eng * (1 + growth)),
            max(0, taxable_eng * (1 + growth)), max(0, hsa_eng * (1 + growth)))
