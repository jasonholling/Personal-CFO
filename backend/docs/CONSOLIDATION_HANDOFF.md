# Calculation engine consolidation — handoff

**Baseline:** `ec61309e6749d74ee31a02070aa46fd238a8ffd5` (verified identical to
`origin/main` and local `main` at the time this branch was created).
**Branch:** `codex/consolidate-calculation-engine`, 6 commits, pushed but
**not merged into `main`** (per instruction).

## Status: partially complete, not finished

Per the explicit instruction this work operated under: *"Do not declare
completion based solely on passing tests or coverage. Completion requires
demonstrated financial reconciliation across the migrated tools."* In that
spirit, this handoff is deliberately not a completion claim. Phases 1–4 are
substantially done and verified; Phase 5 is only partially satisfied by
pre-existing tests, not newly consolidated; Phase 6's invariant suite was
applied per-migration rather than as one comprehensive pass; Phase 7's
mechanics (tests/build green, branch pushed unmerged) are done.

## What's done

### Phase 1 — Calculation contract (`backend/docs/CALCULATION_CONTRACT.md`)

A duplication map across the 9 functions that independently implement
withdrawal-phase or account-projection math, plus explicit conventions
(2.1–2.7: balance/age timing, event ordering, cutoff dates, today's-vs-
future dollars, ownership/tax treatment, future-vs-completed events,
success criteria) and 5 explicitly-flagged material assumptions that must
stay visible rather than get silently resolved one way (tax model choice,
withdrawal-order pluggability, taxable-gain approximation, bridge-job
phasing scope, SWR's undifferentiated spending figure).

### Phase 2 — Independent reference tests (`backend/tests/test_annual_engine_reference.py`)

22 tests against the new shared engine, expected values hand-calculated
independently of the implementation (not derived by running the code
first) — surplus sweep, exact-match income, shortfall draws, insufficient
funds, RMD gross-up + excess reinvestment, growth-after-withdrawal timing,
withdrawal-order policy differences, flat vs. marginal tax models,
positive/negative life events, conversions (full and shortfall-funded tax
paths), and dedicated negative tests proving `reconcile()` actually catches
a broken ledger.

**A real bug was caught before any consumer touched the engine**: the
first draft let surplus guaranteed income (income exceeding need) vanish
from the ledger with nowhere to land. Caught by writing the reconciliation
invariant itself, fixed before Phase 3 was "complete" — see
`annual_engine.py`'s `simulate_withdrawal_year` docstring for the full
account.

### Phase 3 — Shared engine (`backend/annual_engine.py`)

`simulate_withdrawal_year()`: one withdrawal-phase year, given opening
balances, spending need, guaranteed income, life-event cash, RMD amount, a
pluggable tax model, growth rate, and a pluggable withdrawal order. Returns
an `AnnualResult` with `.reconcile()` enforcing: opening + inflows + growth
− spending_funded − tax == closing; no negative balances; unmet need
tracked explicitly, never silently dropped or shown as a negative balance.

`simulate_conversion()`: a Roth conversion layered on top of a computed
year — full converted amount lands in Roth, tax funded from a separate
bucket (taxable by default), with an explicit `conversion_shortfall`
ledger entry if that bucket can't cover it (rather than silently reducing
the converted principal with no record of why).

Two tax models (`marginal_bracket_tax_model`, `flat_rate_tax_model`) and
two withdrawal orders (`DEFAULT_ORDER`, `ROTH_FIRST_ORDER`) are provided as
composable, swappable primitives — this is deliberate: the task explicitly
required preserving policy differences (tax-efficiency's 3 draw-order
strategies, Roth-conversion's own policy layer) rather than forcing every
consumer to the same behavior.

### Phase 4 — Consumers migrated

| Consumer | Status | Verification |
|---|---|---|
| `run_retirement_projection` | **Migrated** | golden diff, 12 scenarios — 1 documented reporting-only difference (see below), balances/unmet_need/on_track identical |
| `_run_single` (Monte Carlo + Stress Tests) | **Migrated** | golden diff, 6 scenarios × 2 consumers — bit-for-bit identical |
| `run_swr_analysis` | **Attempted, reverted** | correct but >2x slower (26.83s→57.41s on its test subset); documented as a deliberate, revisitable exception in CALCULATION_CONTRACT.md §4 — this loop runs the withdrawal step up to ~32× more often than any other consumer (binary search × 1000 trials × ~40yrs ≈ 1.28M simulated years/request) |
| `run_roth_conversion_analysis` | **Not migrated** | see below |
| `run_tax_efficiency_simulation` | **Not migrated** | see below |
| `run_survivor_scenario` | **Not migrated** | see below |
| `run_rmd_planning` (retirement_tools_engine.py) | **Not migrated** | see below |

**Why the last four weren't migrated, rather than silently left alone:**
each implements a genuinely different withdrawal *policy*, not just a
different orchestration of the same one — mechanically forcing them
through `simulate_withdrawal_year` would have meant either (a) contorting
the shared engine to special-case their differences until it stopped being
one shared thing, or (b) quietly homogenizing away real, already-audited
policy differences in a financial app, which the task explicitly
prohibited ("Do not force all strategies to behave identically"). Specific
divergences found on inspection:
- `run_roth_conversion_analysis`'s base spending draw is **not** tax
  grossed-up at all (only the conversion itself is taxed) — a different
  tax treatment from every other consumer, and it never draws from
  HSA/Roth for spending, only taxable/pretax. Growth is applied once,
  *after* the conversion, not before it (the shared engine applies growth
  once per call, before any conversion layered on top via
  `simulate_conversion`).
- `run_tax_efficiency_simulation` compares 3 distinct draw-order policies
  against a flat 22%/15% tax approximation by design (its own long-standing
  docstring), and internally runs its own 1000-trial Monte Carlo loop —
  same performance-risk profile as SWR, not measured but likely to show
  the same order-of-magnitude slowdown.
- `run_survivor_scenario` models a single-person post-death transition
  with its own pension-COLA/guaranteed-income rules that don't map onto
  the two-spouse guaranteed-income shape `simulate_withdrawal_year`
  expects without restructuring the caller significantly.
- `run_rmd_planning` reads an already-computed trajectory from
  `run_retirement_projection` (fixed earlier this session to stop
  independently recompounding); it doesn't run its own withdrawal loop at
  all, so there's no waterfall here to migrate — it's already downstream
  of the migrated engine indirectly.

These four remain **independent calculation paths** — flagged explicitly
here rather than left for someone to discover later. Migrating them (or
formally deciding not to, past the performance/policy reasoning already
found) is real remaining work.

**One material difference found and kept** (the only one across every
migration): `run_retirement_projection`'s per-year `withdrawal_taxable`/
`withdrawal` fields now correctly attribute money that flows through the
taxable bucket because of a life event, in both directions — a positive
event (asset sale) fully covering a year's need no longer double-reports
as a taxable draw; a negative event (one-time cost) that draws down
taxable no longer does so invisibly. `taxable_balance`, `portfolio_balance`,
`unmet_need`, and `on_track` were byte-identical before/after in every
scenario tested. Full account: `CALCULATION_CONTRACT.md` §4.

## What's not done

### Phase 5 — Education/Kids consolidation

**Not attempted this session.** `run_education_projection` and
`run_kids_projection` remain two separate implementations. They already
agree on 529-at-college balances and the parent-retirement contribution
cutoff — verified by `TestEducationAndKidsProjectionsAgreeOn529AtCollege`
in `tests/test_projection_engine.py`, from a fix made earlier in this
session (before the consolidation task began) — but that's *consistent
output*, not *shared calculation path*. The duplication itself (both
functions independently walk years-to-college, apply 529 growth, and
compute the 529→Roth rollover) is still real and still flagged in
`CALCULATION_CONTRACT.md`'s duplication map. This is genuine remaining
work, not something this session quietly declared solved.

### Phase 6 — Full invariant suite

Each migration in Phase 4 was verified with its own golden-diff comparison
and targeted regression tests (money conservation, transfers netting to
zero, taxes funded, unmet-need tracked, closing balances never negative —
all enforced directly by `AnnualResult.reconcile()` and exercised by
`test_annual_engine_reference.py`). What was **not** done: a single
comprehensive pass explicitly re-checking every invariant in the task's
list (chart/summary agreement at matching dates, contribution-cutoff/
ownership consistency across every consumer, completed-events-not-replayed)
end-to-end across the *whole* app rather than per-migration. The
per-migration checks are real and specific; a holistic sweep asking "does
every remaining invariant hold everywhere, including the unmigrated
consumers" was not run.

## Verification run this session

- Backend: `649 passed`, `97.39-97.43%` coverage (varies slightly by
  commit — `projection_engine.py` and `simulation_engine.py` both at
  ~99-100% after their migrations).
- Frontend: `13 passed` (3 test files — the full existing suite).
- Frontend build: succeeds (`vite build`, pre-existing >500kB chunk
  warning, unrelated to this work).
- Golden-diff tools (`backend/tools/capture_retirement_golden.py`,
  `capture_simulation_golden.py`, `diff_retirement_golden.py`) are left in
  the tree — reusable for verifying any future migration (Phase 5, or the
  four unmigrated Phase 4 consumers) the same way.

## Material assumptions still flagged, not resolved (see CALCULATION_CONTRACT.md §3)

1. Marginal-bracket vs. flat-rate tax modeling coexist by design (3 tools
   use the flat simplification on purpose).
2. Withdrawal-order is pluggable, not a single hard-coded truth.
3. Taxable-gain approximation varies by tool on purpose.
4. Bridge-job/kids-at-home phasing is `run_retirement_projection`/
   `_run_single`-only, deliberately not attempted elsewhere.
5. SWR's spending figure is a single undifferentiated number by design,
   unlike every other consumer's income+healthcare split.

## Recommended next steps, in priority order

1. Decide whether Phase 5 (Education/Kids) is worth a dedicated pass given
   the two functions already agree on the numbers that matter — the
   remaining work there is code-sharing for its own sake, not a bug fix.
2. If tax-efficiency's inner loop is ever made faster (fewer trials, or a
   smarter search), revisit migrating it and SWR together — same
   performance profile, same fix would likely apply to both.
3. `run_roth_conversion_analysis` and `run_survivor_scenario` are the
   better next migration candidates of the remaining four — single-pass,
   not Monte-Carlo-repeated, so the SWR-style performance risk doesn't
   apply. Their genuinely different tax/bucket/growth-timing semantics
   would need `simulate_withdrawal_year`/`simulate_conversion` extended
   (e.g. an optional bucket subset, an optional "tax spending draws" flag)
   rather than shoehorned into the current interface as-is.
