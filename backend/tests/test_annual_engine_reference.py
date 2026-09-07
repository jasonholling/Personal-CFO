"""
Phase 2 reference tests for the shared annual withdrawal engine
(annual_engine.py).

Every expected value in this file is hand-calculated independently of the
implementation — worked out on paper/by arithmetic before/without reading
what the code produces — per the consolidation task's explicit
instruction: "Do not assume existing outputs are correct." These are not
characterization tests; a test failing here means the engine is wrong,
not that the test needs updating to match the code (see
CALCULATION_CONTRACT.md and the task's Phase 6 instruction: "Update
expected values only with independent justification").

Each test's docstring/comments show the hand arithmetic so the expected
values are checkable without running anything.
"""

import dataclasses

import pytest

from annual_engine import (
    AccountState,
    AnnualResult,
    DEFAULT_ORDER,
    ROTH_FIRST_ORDER,
    flat_rate_tax_model,
    marginal_bracket_tax_model,
    no_tax_model,
    simulate_conversion,
    simulate_withdrawal_year,
)


# ── Guaranteed income vs. spending need ────────────────────────────────────

def test_surplus_guaranteed_income_is_swept_to_taxable_not_lost():
    """Income $50k, need $40k, zero growth. $10k surplus must land
    somewhere (taxable) — a first draft of this engine let it vanish from
    the books, which reconcile() now catches.
    opening taxable 10,000 + surplus 10,000 = 20,000 closing taxable.
    """
    opening = AccountState(pretax=0, roth=0, taxable=10_000, hsa=0)
    result = simulate_withdrawal_year(
        opening, spending_need=40_000, guaranteed_income=50_000,
        life_event_cash=0, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0,
    )
    assert result.closing.taxable == pytest.approx(20_000)
    assert result.closing.total() == pytest.approx(20_000)
    assert result.draws == {}
    assert result.taxes_paid == {}
    assert result.unmet_need == pytest.approx(0)
    assert result.spending_funded == pytest.approx(40_000)
    assert result.reconcile() is None


def test_income_exactly_matching_need_touches_no_bucket():
    """Income == need: spending is fully funded by income directly, never
    touching any account, so closing == opening exactly."""
    opening = AccountState(pretax=0, roth=0, taxable=5_000, hsa=0)
    result = simulate_withdrawal_year(
        opening, spending_need=30_000, guaranteed_income=30_000,
        life_event_cash=0, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0,
    )
    assert result.closing.taxable == pytest.approx(5_000)
    assert result.closing.total() == pytest.approx(opening.total())
    assert result.reconcile() is None


def test_shortfall_draws_taxable_with_no_tax():
    """Income $20k, need $60k, $40k shortfall drawn from an untaxed
    taxable bucket: 100,000 - 40,000 = 60,000 closing taxable."""
    opening = AccountState(pretax=0, roth=0, taxable=100_000, hsa=0)
    result = simulate_withdrawal_year(
        opening, spending_need=60_000, guaranteed_income=20_000,
        life_event_cash=0, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0,
    )
    assert result.draws == {"taxable": pytest.approx(40_000)}
    assert result.closing.taxable == pytest.approx(60_000)
    assert result.unmet_need == pytest.approx(0)
    assert result.reconcile() is None


# ── Insufficient funds ──────────────────────────────────────────────────────

def test_insufficient_funds_reports_unmet_need_never_goes_negative():
    """Only $10k available against a $50k need with zero income: draw
    everything, report the $40k gap explicitly, and every bucket floors
    at zero rather than going negative."""
    opening = AccountState(pretax=0, roth=0, taxable=10_000, hsa=0)
    result = simulate_withdrawal_year(
        opening, spending_need=50_000, guaranteed_income=0,
        life_event_cash=0, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0,
    )
    assert result.closing.total() == pytest.approx(0)
    assert result.closing.taxable == pytest.approx(0)
    assert result.unmet_need == pytest.approx(40_000)
    assert result.spending_funded == pytest.approx(10_000)
    assert result.spending_funded + result.unmet_need == pytest.approx(50_000)
    assert result.reconcile() is None


def test_zero_assets_zero_income_reports_full_need_as_unmet():
    opening = AccountState()
    result = simulate_withdrawal_year(
        opening, spending_need=25_000, guaranteed_income=0,
        life_event_cash=0, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0,
    )
    assert result.unmet_need == pytest.approx(25_000)
    assert result.closing.total() == pytest.approx(0)
    assert result.reconcile() is None


# ── RMD + marginal tax gross-up ─────────────────────────────────────────────

def test_rmd_grossed_up_draw_matches_hand_calculation():
    """pretax=50,000, need=30,000, RMD=20,000, 20% marginal rate.
    RMD: actual_rmd=20,000, tax=4,000, after-tax=16,000 funds part of the
    30,000 need, leaving 14,000 remaining.
    Then a grossed-up discretionary pretax draw funds the rest:
      gross = 14,000 / (1 - 0.20) = 17,500
      tax = 17,500 * 0.20 = 3,500
      net = 14,000 (exactly covers the remainder)
    Total pretax drawn = 20,000 (RMD) + 17,500 (discretionary) = 37,500.
    Closing pretax = 50,000 - 37,500 = 12,500.
    Total tax = 4,000 + 3,500 = 7,500.
    """
    opening = AccountState(pretax=50_000, roth=0, taxable=0, hsa=0)
    result = simulate_withdrawal_year(
        opening, spending_need=30_000, guaranteed_income=0,
        life_event_cash=0, rmd_amount=20_000,
        tax_model=marginal_bracket_tax_model(pretax_rate=0.20),
        growth_rate=0.0,
    )
    assert result.draws["pretax"] == pytest.approx(37_500)
    assert result.taxes_paid["rmd"] == pytest.approx(4_000)
    assert result.taxes_paid["pretax"] == pytest.approx(3_500)
    assert result.total_tax == pytest.approx(7_500)
    assert result.closing.pretax == pytest.approx(12_500)
    assert result.unmet_need == pytest.approx(0)
    assert result.reconcile() is None


def test_rmd_excess_over_need_is_reinvested_in_taxable():
    """RMD (60,000) far exceeds the 10,000 need at a 10% rate: after-tax
    RMD = 60,000*0.9 = 54,000; 10,000 funds the need, the 44,000 excess
    is swept to taxable."""
    opening = AccountState(pretax=60_000, roth=0, taxable=0, hsa=0)
    result = simulate_withdrawal_year(
        opening, spending_need=10_000, guaranteed_income=0,
        life_event_cash=0, rmd_amount=60_000,
        tax_model=marginal_bracket_tax_model(pretax_rate=0.10),
        growth_rate=0.0,
    )
    assert result.draws["pretax"] == pytest.approx(60_000)
    assert result.taxes_paid["rmd"] == pytest.approx(6_000)
    assert result.closing.pretax == pytest.approx(0)
    assert result.closing.taxable == pytest.approx(44_000)
    assert result.unmet_need == pytest.approx(0)
    assert result.reconcile() is None


# ── Growth timing ────────────────────────────────────────────────────────

def test_growth_applies_after_withdrawal_not_before():
    """100,000 taxable, draw 20,000 for spending, THEN grow the remaining
    80,000 at 5% -> 84,000. If growth were (wrongly) applied before the
    draw, closing would be 105,000 - 20,000 = 85,000 instead."""
    opening = AccountState(pretax=0, roth=0, taxable=100_000, hsa=0)
    result = simulate_withdrawal_year(
        opening, spending_need=20_000, guaranteed_income=0,
        life_event_cash=0, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.05,
    )
    assert result.growth["taxable"] == pytest.approx(4_000)
    assert result.closing.taxable == pytest.approx(84_000)
    assert result.reconcile() is None


def test_zero_growth_zero_inflation_leaves_surplus_untouched():
    """A pure hold year: income covers need exactly, zero growth rate ->
    closing == opening exactly, bit for bit."""
    opening = AccountState(pretax=200_000, roth=100_000, taxable=50_000, hsa=10_000)
    result = simulate_withdrawal_year(
        opening, spending_need=40_000, guaranteed_income=40_000,
        life_event_cash=0, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0,
    )
    assert result.closing == opening
    assert result.reconcile() is None


# ── Withdrawal-order policy ──────────────────────────────────────────────

def test_withdrawal_order_policy_changes_which_bucket_is_drawn():
    """Same scenario, two different orders: DEFAULT_ORDER draws taxable
    first, ROTH_FIRST_ORDER draws roth first. This is the pluggability
    CALCULATION_CONTRACT.md 3.2 requires — run_tax_efficiency_simulation
    must be able to compare these without duplicating the waterfall."""
    opening = AccountState(pretax=0, roth=50_000, taxable=50_000, hsa=0)

    default = simulate_withdrawal_year(
        opening, spending_need=10_000, guaranteed_income=0,
        life_event_cash=0, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0, order=DEFAULT_ORDER,
    )
    assert default.closing.taxable == pytest.approx(40_000)
    assert default.closing.roth == pytest.approx(50_000)
    assert default.reconcile() is None

    roth_first = simulate_withdrawal_year(
        opening, spending_need=10_000, guaranteed_income=0,
        life_event_cash=0, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0, order=ROTH_FIRST_ORDER,
    )
    assert roth_first.closing.roth == pytest.approx(40_000)
    assert roth_first.closing.taxable == pytest.approx(50_000)
    assert roth_first.reconcile() is None


def test_flat_rate_tax_model_differs_from_marginal_by_construction():
    """The two tax philosophies documented in CALCULATION_CONTRACT.md 3.1
    give different results on an identical pretax draw by design — this
    just pins down that both are wired correctly, not that they agree."""
    opening = AccountState(pretax=100_000, roth=0, taxable=0, hsa=0)
    marginal = simulate_withdrawal_year(
        opening, spending_need=10_000, guaranteed_income=0,
        life_event_cash=0, rmd_amount=0,
        tax_model=marginal_bracket_tax_model(pretax_rate=0.12),
        growth_rate=0.0,
    )
    flat = simulate_withdrawal_year(
        opening, spending_need=10_000, guaranteed_income=0,
        life_event_cash=0, rmd_amount=0,
        tax_model=flat_rate_tax_model(pretax_rate=0.22),
        growth_rate=0.0,
    )
    assert marginal.closing.pretax != pytest.approx(flat.closing.pretax)
    assert marginal.reconcile() is None
    assert flat.reconcile() is None


# ── Life events (asset sales / recurring costs) ────────────────────────────

def test_positive_life_event_cash_reduces_draw_needed():
    """A one-time asset sale of 15,000 counts as cash toward need, same
    as guaranteed income: 20,000(income) + 15,000(sale) = 35,000 available
    against a 30,000 need -> 5,000 surplus swept to taxable, no draw."""
    opening = AccountState(pretax=0, roth=0, taxable=1_000, hsa=0)
    result = simulate_withdrawal_year(
        opening, spending_need=30_000, guaranteed_income=20_000,
        life_event_cash=15_000, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0,
    )
    assert result.draws == {}
    assert result.closing.taxable == pytest.approx(6_000)
    assert result.unmet_need == pytest.approx(0)
    assert result.reconcile() is None


def test_negative_life_event_cash_increases_need_beyond_target():
    """A recurring cost of 15,000 (e.g. a one-time large expense modeled
    as a negative life event) eats into guaranteed income before it can
    fund spending: 40,000(income) - 15,000(cost) = 25,000 available
    against a 30,000 need -> 5,000 shortfall drawn from taxable."""
    opening = AccountState(pretax=0, roth=0, taxable=100_000, hsa=0)
    result = simulate_withdrawal_year(
        opening, spending_need=30_000, guaranteed_income=40_000,
        life_event_cash=-15_000, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0,
    )
    assert result.external_income == pytest.approx(25_000)
    assert result.draws == {"taxable": pytest.approx(5_000)}
    assert result.closing.taxable == pytest.approx(95_000)
    assert result.unmet_need == pytest.approx(0)
    assert result.reconcile() is None


# ── Transfers / Roth conversions ─────────────────────────────────────────

def test_conversion_moves_full_amount_tax_paid_from_taxable():
    """Convert 10,000 pretax -> roth at a 22% rate, tax funded from
    taxable. Roth receives the FULL 10,000 (not 10,000 minus its own
    tax); taxable pays the 2,200 tax; pretax falls by exactly 10,000.
    opening: pretax 50,000 / taxable 20,000.
    closing: pretax 40,000 / roth 10,000 / taxable 17,800.
    """
    opening = AccountState(pretax=50_000, roth=0, taxable=20_000, hsa=0)
    base = simulate_withdrawal_year(
        opening, spending_need=0, guaranteed_income=0,
        life_event_cash=0, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0,
    )
    assert base.closing == opening  # sanity: no-op year changes nothing
    assert base.reconcile() is None

    converted = simulate_conversion(
        base, amount=10_000,
        tax_model=marginal_bracket_tax_model(pretax_rate=0.22),
    )
    assert converted.closing.pretax == pytest.approx(40_000)
    assert converted.closing.roth == pytest.approx(10_000)
    assert converted.closing.taxable == pytest.approx(17_800)
    assert converted.taxes_paid["conversion"] == pytest.approx(2_200)
    assert len(converted.transfers) == 1
    assert converted.transfers[0].from_bucket == "pretax"
    assert converted.transfers[0].to_bucket == "roth"
    assert converted.transfers[0].amount == pytest.approx(10_000)
    assert converted.reconcile() is None


def test_conversion_tax_shortfall_falls_back_to_the_converted_roth_amount():
    """Convert 10,000 at 22% (tax=2,200) but taxable only has 1,000 to pay
    it with. 1,000 comes from taxable; the remaining 1,200 shortfall is
    paid out of the just-converted Roth principal itself (surfaced as a
    distinct 'conversion_shortfall' tax entry, not silently absorbed).
    closing: pretax 40,000 / roth 5,000+10,000-1,200=13,800 / taxable 0.
    """
    opening = AccountState(pretax=50_000, roth=5_000, taxable=1_000, hsa=0)
    base = simulate_withdrawal_year(
        opening, spending_need=0, guaranteed_income=0,
        life_event_cash=0, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0,
    )
    converted = simulate_conversion(
        base, amount=10_000,
        tax_model=marginal_bracket_tax_model(pretax_rate=0.22),
    )
    assert converted.closing.pretax == pytest.approx(40_000)
    assert converted.closing.taxable == pytest.approx(0)
    assert converted.closing.roth == pytest.approx(13_800)
    assert converted.taxes_paid["conversion"] == pytest.approx(1_000)
    assert converted.taxes_paid["conversion_shortfall"] == pytest.approx(1_200)
    assert converted.reconcile() is None


# ── Reconciliation invariants across a mixed multi-feature year ────────────

def test_reconciliation_holds_for_a_scenario_using_every_feature_at_once():
    """RMD + discretionary pretax/taxable draws + a negative life event +
    growth, all in one year — the invariant must hold end to end even
    when every mechanism fires simultaneously, not just in isolated unit
    tests."""
    opening = AccountState(pretax=80_000, roth=20_000, taxable=15_000, hsa=5_000)
    result = simulate_withdrawal_year(
        opening, spending_need=45_000, guaranteed_income=18_000,
        life_event_cash=-3_000, rmd_amount=6_000,
        tax_model=marginal_bracket_tax_model(pretax_rate=0.18, taxable_rate=0.05),
        growth_rate=0.04, order=DEFAULT_ORDER,
    )
    assert result.reconcile() is None
    assert result.closing.pretax >= 0
    assert result.closing.roth >= 0
    assert result.closing.taxable >= 0
    assert result.closing.hsa >= 0
    assert result.spending_funded + result.unmet_need == pytest.approx(result.spending_need)


def test_flat_and_marginal_tax_models_return_zero_on_hsa_and_roth():
    """Neither provided tax model taxes hsa/roth draws — pin this down
    explicitly so a future edit can't silently start taxing them."""
    flat = flat_rate_tax_model(pretax_rate=0.22, taxable_rate=0.15)
    marginal = marginal_bracket_tax_model(pretax_rate=0.20, taxable_rate=0.0)
    for model in (flat, marginal):
        assert model("hsa", 10_000) == 0.0
        assert model("roth", 10_000) == 0.0


def test_total_draws_property_sums_across_buckets():
    opening = AccountState(pretax=0, roth=30_000, taxable=30_000, hsa=0)
    result = simulate_withdrawal_year(
        opening, spending_need=40_000, guaranteed_income=0,
        life_event_cash=0, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0, order=DEFAULT_ORDER,
    )
    # Draws taxable (30,000, exhausted) then pretax/hsa (none available)
    # then roth (10,000) to cover the remaining 10,000.
    assert result.total_draws == pytest.approx(40_000)


# ── reconcile() must actually catch broken ledgers, not just pass good ones ─

def _base_result():
    opening = AccountState(pretax=0, roth=0, taxable=50_000, hsa=0)
    return simulate_withdrawal_year(
        opening, spending_need=20_000, guaranteed_income=0,
        life_event_cash=0, rmd_amount=0, tax_model=no_tax_model(),
        growth_rate=0.0,
    )


def test_reconcile_flags_negative_unmet_need():
    broken = dataclasses.replace(_base_result(), unmet_need=-500)
    err = broken.reconcile()
    assert err is not None and "unmet_need" in err


def test_reconcile_flags_spending_funded_unmet_mismatch():
    broken = dataclasses.replace(_base_result(), spending_need=999_999)
    err = broken.reconcile()
    assert err is not None and "spending_funded" in err


def test_reconcile_flags_negative_closing_balance():
    good = _base_result()
    broken_closing = dataclasses.replace(good.closing, taxable=-1_000)
    broken = dataclasses.replace(good, closing=broken_closing)
    err = broken.reconcile()
    assert err is not None and "negative" in err


def test_reconcile_flags_aggregate_mismatch_when_money_appears_from_nowhere():
    """Directly corrupt closing balances (money appears with no matching
    inflow) — the aggregate identity must catch it even though every
    individual field looks locally valid."""
    good = _base_result()
    broken_closing = dataclasses.replace(good.closing, taxable=good.closing.taxable + 5_000)
    broken = dataclasses.replace(good, closing=broken_closing)
    err = broken.reconcile()
    assert err is not None and "aggregate mismatch" in err
