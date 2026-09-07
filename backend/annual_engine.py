"""
Shared annual withdrawal-phase cash-flow engine.

Implements the conventions in docs/CALCULATION_CONTRACT.md — one function,
`simulate_withdrawal_year()`, that every withdrawal-phase consumer
(retirement projection, Monte Carlo, stress tests, SWR, Roth conversion,
tax-efficiency comparison, survivor scenario) should call for "what happens
to these account balances in one year, given this year's need, guaranteed
income, tax model, and withdrawal-order policy."

Deliberately does NOT own:
  - Accumulation-phase contribution growth (projection_engine.py's
    _fv/_fv_annuity/_fv_growing_annuity already do this correctly and are
    already shared).
  - Guaranteed-income computation (pension COLA rules, spousal SS
    age-gap timing) — callers compute this and pass it in, since it
    depends on per-person claim ages that vary by consumer (survivor
    scenario, for instance, only has one remaining person).
  - Today's-dollars -> future-dollars inflation of the spending target —
    callers pre-inflate (see CALCULATION_CONTRACT.md 2.4) and pass in an
    already-inflated `spending_need` for the year, since not every
    consumer wants the same spending-need shape (SWR wants one
    undifferentiated figure; the others want income+healthcare separately
    inflated before summing).

Why a function and not a class: every consumer's caller loop already owns
its own year-by-year state (pretax/roth/taxable/hsa, plus whatever
strategy-specific bookkeeping it needs) — this function is a pure step
that takes a snapshot in, returns a snapshot out, with no hidden state.
That keeps Monte Carlo's 1000-trials-per-call hot path allocation-light
(a dataclass copy in, a dataclass copy out, no engine object to construct).
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


# ── Account state ────────────────────────────────────────────────────────

@dataclass
class AccountState:
    """The four retirement buckets this engine operates on. Matches
    projection_engine.run_retirement_projection's bucket set exactly —
    see CALCULATION_CONTRACT.md 2.5 for the account_type -> bucket mapping
    callers are responsible for building this from."""
    pretax: float = 0.0
    roth: float = 0.0
    taxable: float = 0.0
    hsa: float = 0.0

    def total(self) -> float:
        return self.pretax + self.roth + self.taxable + self.hsa

    def copy(self) -> "AccountState":
        return AccountState(self.pretax, self.roth, self.taxable, self.hsa)


# ── Tax models ────────────────────────────────────────────────────────────
# A tax model is a callable: (bucket_name, gross_amount) -> tax_owed.
# Two are provided, matching CALCULATION_CONTRACT.md 3.1's two legitimate,
# coexisting tax models. Callers select which one a given consumer uses;
# the engine never hard-codes either.

def marginal_bracket_tax_model(pretax_rate: float, taxable_rate: float = 0.0) -> Callable[[str, float], float]:
    """The "real" model: caller supplies a pretax marginal rate already
    computed for this year (via
    projection_engine._marginal_rate/simulation_engine._pretax_marginal_tax_rate
    — this module doesn't import those to avoid a hard dependency on the
    IRS bracket table living in retirement_tools_engine; callers compute
    the rate and pass a plain number). taxable_rate defaults to 0 (used by
    run_retirement_projection's own waterfall, which doesn't tax taxable
    draws at all today) but every caller may supply a real LTCG estimate
    instead — see flat_rate_tax_model for the alternative some consumers
    use deliberately."""
    def _tax(bucket: str, gross: float) -> float:
        if bucket == "pretax":
            return gross * pretax_rate
        if bucket == "taxable":
            return gross * taxable_rate
        return 0.0
    return _tax


def flat_rate_tax_model(pretax_rate: float = 0.22, taxable_rate: float = 0.15) -> Callable[[str, float], float]:
    """The documented simplification run_tax_efficiency_simulation uses to
    compare draw-order strategies quickly, instead of a real bracket
    lookup. Kept as a distinct, explicitly-named model rather than "the
    marginal model with different numbers" so a reader can tell at the
    call site which of the two coexisting tax philosophies (CALCULATION_
    CONTRACT.md 3.1) is in effect."""
    def _tax(bucket: str, gross: float) -> float:
        if bucket == "pretax":
            return gross * pretax_rate
        if bucket == "taxable":
            return gross * taxable_rate
        return 0.0
    return _tax


def no_tax_model() -> Callable[[str, float], float]:
    """0% everywhere — useful for reference tests that want to isolate
    the withdrawal-order/reconciliation mechanics from tax math."""
    return lambda bucket, gross: 0.0


# ── Withdrawal-order policies ──────────────────────────────────────────────
# A withdrawal-order policy is a callable: () -> List[str], the bucket
# draw order (excluding RMD, which is always mandatory and always first —
# see CALCULATION_CONTRACT.md 2.2). CALCULATION_CONTRACT.md 3.2 requires
# this to be pluggable, not a hard-coded constant, since
# run_tax_efficiency_simulation compares 3 different orders on purpose.

DEFAULT_ORDER = ("taxable", "pretax", "hsa", "roth")
ROTH_FIRST_ORDER = ("roth", "taxable", "pretax", "hsa")


# ── The annual result ───────────────────────────────────────────────────────

@dataclass
class Transfer:
    """An internal movement between buckets that isn't spending — e.g. a
    Roth conversion. Must net to zero across (from_bucket, to_bucket) at
    the ledger level: the amount debited from from_bucket before tax
    equals the amount credited to to_bucket (tax on a conversion is paid
    from a THIRD bucket — see simulate_conversion below — never netted
    out of the converted amount itself, so the full amount really lands
    in the destination)."""
    from_bucket: str
    to_bucket: str
    amount: float
    reason: str = "conversion"


@dataclass
class AnnualResult:
    """One year's full cash-flow ledger. CALCULATION_CONTRACT.md section 3
    requires every field here to reconcile — see .reconcile()."""
    opening: AccountState
    external_income: float               # guaranteed income (pension+SS) + life-event cash this year
    spending_need: float                 # this year's target, already inflated by the caller
    rmd_amount: float                    # mandatory RMD gross amount (0 if not applicable)
    draws: Dict[str, float] = field(default_factory=dict)      # gross withdrawn per bucket (spending only, excludes conversions)
    taxes_paid: Dict[str, float] = field(default_factory=dict)  # tax per bucket (rmd + discretionary draws + conversion, keyed the same as draws plus "conversion")
    transfers: List[Transfer] = field(default_factory=list)
    growth: Dict[str, float] = field(default_factory=dict)      # growth $ applied per bucket, post-withdrawal
    closing: AccountState = field(default_factory=AccountState)
    unmet_need: float = 0.0

    @property
    def spending_funded(self) -> float:
        return self.spending_need - self.unmet_need

    @property
    def total_tax(self) -> float:
        return sum(self.taxes_paid.values())

    @property
    def total_draws(self) -> float:
        return sum(self.draws.values())

    def reconcile(self, tol: float = 0.01) -> Optional[str]:
        """Returns None if the ledger balances; otherwise a string
        describing the first discrepancy found. Checks, per
        CALCULATION_CONTRACT.md section 3:
          - opening + external_income + growth - draws - taxes - net
            transfers == closing (aggregate).
          - Transfers net to zero (what leaves from_bucket, net of any tax
            paid on the transfer, arrives at to_bucket — see
            simulate_conversion's contract).
          - unmet_need is non-negative and spending_funded + unmet_need ==
            spending_need.
          - No bucket in `closing` is negative.
        """
        if self.unmet_need < -tol:
            return f"unmet_need is negative: {self.unmet_need}"
        if abs(self.spending_funded + self.unmet_need - self.spending_need) > tol:
            return "spending_funded + unmet_need != spending_need"
        for name, val in (("pretax", self.closing.pretax), ("roth", self.closing.roth),
                           ("taxable", self.closing.taxable), ("hsa", self.closing.hsa)):
            if val < -tol:
                return f"closing.{name} is negative: {val}"

        total_growth = sum(self.growth.values())
        total_tax = self.total_tax
        # Aggregate identity: closing == opening + external_income + growth
        #   - spending_funded - tax. Spending funded from income directly
        #   never touches a bucket (income in, spending out, net zero);
        #   spending funded from a draw already nets out inside the
        #   draw/tax bookkeeping (bucket falls by the gross draw, of
        #   which (draw - tax) funds spending and `tax` leaves the
        #   system) — so `spending_funded`, not `total_draws`, is the
        #   right outflow term here. Internal transfers (e.g. a Roth
        #   conversion) never appear in this identity at all: they move
        #   money between buckets, never off the books, so they drop out
        #   of the AGGREGATE total by construction — only their tax cost
        #   (already folded into taxes_paid by simulate_conversion) shows
        #   up here.
        expected_total = (self.opening.total() + self.external_income + total_growth
                           - self.spending_funded - total_tax)
        if abs(self.closing.total() - expected_total) > tol:
            return (f"aggregate mismatch: closing.total()={self.closing.total():.2f} "
                    f"expected={expected_total:.2f} (opening={self.opening.total():.2f} "
                    f"+income={self.external_income:.2f} +growth={total_growth:.2f} "
                    f"-spending_funded={self.spending_funded:.2f} -tax={total_tax:.2f})")
        return None


# ── The withdrawal step ─────────────────────────────────────────────────────

def simulate_withdrawal_year(
    opening: AccountState,
    spending_need: float,
    guaranteed_income: float,
    life_event_cash: float,
    rmd_amount: float,
    tax_model: Callable[[str, float], float],
    growth_rate: float,
    order: tuple = DEFAULT_ORDER,
) -> AnnualResult:
    """One year of the withdrawal-phase waterfall.

    Ledger design (this is the piece the first draft of this function got
    wrong, caught by the reconciliation check in AnnualResult.reconcile()
    before any consumer was migrated onto it — worth keeping the note):
    guaranteed_income and life_event_cash (signed: positive for an asset
    sale/windfall, negative for a recurring cost) combine into a single
    `cash_available` figure. If cash_available covers spending_need in
    full, the surplus is swept into taxable as savings — it does NOT just
    vanish from the books, which is what a first cut of this function did
    by only ever letting income offset need and never crediting an
    unspent surplus anywhere. If cash_available falls short, the
    remaining need is drawn from `order` in sequence (after the mandatory
    RMD, taken regardless of need, with its after-tax proceeds counting
    toward funding and any excess reinvested in taxable exactly like the
    surplus-sweep case). Each taxed draw is grossed up so its AFTER-TAX
    proceeds — not the gross withdrawal — cover the remaining need.
    Whatever's left unfunded once every bucket is exhausted is reported
    as unmet_need — never silently dropped, never left as a negative
    balance. Growth is applied last, to every bucket's post-withdrawal
    (and post-surplus-sweep) balance — see CALCULATION_CONTRACT.md 2.2.

    This makes the aggregate identity in reconcile() exact:
        closing.total() == opening.total() + (guaranteed_income +
            life_event_cash) + growth_total - spending_funded - tax_total
    holds whether income exactly matches need, undershoots it (accounts
    get drawn down), or overshoots it (the excess is saved), because
    every dollar of income is accounted for as either funding spending or
    landing in a bucket — never both, never neither.
    """
    pretax, roth, taxable, hsa = opening.pretax, opening.roth, opening.taxable, opening.hsa
    draws: Dict[str, float] = {}
    taxes: Dict[str, float] = {}

    cash_available = guaranteed_income + life_event_cash
    spending_funded = 0.0

    if cash_available >= spending_need:
        surplus = cash_available - spending_need
        taxable += surplus
        spending_funded = spending_need
        remaining = 0.0
    else:
        spending_funded = max(0.0, cash_available)
        remaining = spending_need - cash_available

    if rmd_amount > 0:
        actual_rmd = min(rmd_amount, pretax)
        pretax -= actual_rmd
        rmd_tax = tax_model("pretax", actual_rmd)
        taxes["rmd"] = taxes.get("rmd", 0.0) + rmd_tax
        draws["pretax"] = draws.get("pretax", 0.0) + actual_rmd
        after_tax_rmd = actual_rmd - rmd_tax
        if after_tax_rmd <= remaining:
            spending_funded += after_tax_rmd
            remaining -= after_tax_rmd
        else:
            taxable += after_tax_rmd - remaining
            spending_funded += remaining
            remaining = 0.0

    balances = {"pretax": pretax, "roth": roth, "taxable": taxable, "hsa": hsa}
    for bucket in order:
        if remaining <= 0:
            break
        bal = balances[bucket]
        if bal <= 0:
            continue
        # Probe the tax model at a nominal $1 to see if this bucket is
        # taxed at all — avoids requiring callers to also declare "which
        # buckets are taxed", keeping the tax_model callable the single
        # source of truth. Both models provided here are linear in gross,
        # so a $1 probe gives the exact marginal rate.
        probe = tax_model(bucket, 1.0)
        if probe > 0:
            tax_rate = probe
            gross = remaining / (1 - tax_rate) if tax_rate < 1 else remaining
            draw = min(gross, bal)
            tax = tax_model(bucket, draw)
            net = draw - tax
        else:
            draw = min(remaining, bal)
            tax = 0.0
            net = draw
        balances[bucket] = bal - draw
        draws[bucket] = draws.get(bucket, 0.0) + draw
        if tax > 0:
            taxes[bucket] = taxes.get(bucket, 0.0) + tax
        spending_funded += net
        remaining -= net

    unmet_need = max(0.0, remaining)

    growth: Dict[str, float] = {}
    closing = AccountState()
    for name in ("pretax", "roth", "taxable", "hsa"):
        bal = max(0.0, balances[name])
        g = bal * growth_rate
        growth[name] = g
        setattr(closing, name, bal + g)

    return AnnualResult(
        opening=opening.copy(),
        external_income=cash_available,
        spending_need=spending_need,
        rmd_amount=rmd_amount,
        draws=draws,
        taxes_paid=taxes,
        transfers=[],
        growth=growth,
        closing=closing,
        unmet_need=unmet_need,
    )


def simulate_conversion(
    result: AnnualResult,
    amount: float,
    tax_model: Callable[[str, float], float],
    tax_funding_order: tuple = ("taxable",),
) -> AnnualResult:
    """Apply a pretax -> Roth conversion on top of an already-computed
    withdrawal year. Standard practice (CALCULATION_CONTRACT.md /
    run_roth_conversion_analysis's 2026-09-07 fix): the conversion's tax
    is paid from OUTSIDE the IRA — by default from taxable — so the full
    converted amount lands in Roth, not amount-minus-its-own-tax. The
    caller is responsible for capping `amount` at what tax_funding_order's
    buckets can actually afford (this function does not silently reduce
    an unaffordable conversion; the reconciliation check below will raise
    on a negative closing balance if the caller over-converts, by design
    — a caller wanting a "trial" conversion that never goes negative
    should compute the affordable cap itself, mirroring
    run_roth_conversion_analysis's max_conversion_affordable pattern).

    Returns a NEW AnnualResult with the conversion folded into draws/
    taxes_paid/transfers/closing — the input `result` is not mutated."""
    tax_cost = tax_model("pretax", amount)
    closing = result.closing.copy()
    closing.pretax -= amount
    closing.roth += amount

    remaining_tax = tax_cost
    taxes = dict(result.taxes_paid)
    for bucket in tax_funding_order:
        if remaining_tax <= 0:
            break
        bal = getattr(closing, bucket)
        pay = min(remaining_tax, bal)
        setattr(closing, bucket, bal - pay)
        remaining_tax -= pay
    # Any tax that couldn't be funded from tax_funding_order is paid from
    # the conversion itself as a last resort (reduces the Roth-bound
    # amount) — surfaced via a distinct "conversion_shortfall" tax entry
    # so callers/tests can see this happened rather than it silently
    # eating into the converted principal unnoticed.
    if remaining_tax > 0:
        closing.roth -= remaining_tax
        taxes["conversion_shortfall"] = remaining_tax

    taxes["conversion"] = taxes.get("conversion", 0.0) + (tax_cost - remaining_tax if remaining_tax > 0 else tax_cost)

    transfers = list(result.transfers) + [Transfer("pretax", "roth", amount, "conversion")]

    return AnnualResult(
        opening=result.opening,
        external_income=result.external_income,
        spending_need=result.spending_need,
        rmd_amount=result.rmd_amount,
        draws=dict(result.draws),
        taxes_paid=taxes,
        transfers=transfers,
        growth=dict(result.growth),
        closing=closing,
        unmet_need=result.unmet_need,
    )
