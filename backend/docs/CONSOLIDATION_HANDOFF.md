# Calculation engine consolidation — handoff

**Baseline:** `ec61309e6749d74ee31a02070aa46fd238a8ffd5` (verified identical to
`origin/main` and local `main` at the time this branch was created).
**Branch:** `codex/consolidate-calculation-engine`, 13 commits, pushed but
**not merged into `main`** (per instruction — awaiting Jason's review,
particularly of the Roth-conversion dollar-figure changes below).

*This is the second revision of this document. The first (after 6
commits) covered Phases 1–4 partial + a documented SWR exception. This
revision covers a second work session that finished the rest of Phase 4,
did a scoped Phase 5, and ran Phase 6 for the first time.*

## Status: substantially complete, two documented exceptions remain by design

Per the explicit instruction this work has operated under across both
sessions: *"Do not declare completion based solely on passing tests or
coverage."* In that spirit: every consumer identified in Phase 1's
duplication map has now been migrated onto the shared engine, extracted
into a parity-tested shared helper, or explicitly documented as an
independent calculation path with a measured, specific reason (not a
placeholder for "didn't get to it"). Phase 6 (the cross-tool
reconciliation sweep) has now actually run, not just been planned.

## What's done

### Phase 1 — Calculation contract (`backend/docs/CALCULATION_CONTRACT.md`)

Unchanged from the first revision: a duplication map across the 9
functions that independently implement withdrawal-phase or
account-projection math, explicit conventions, and 5 material assumptions
flagged as deliberate divergences, not bugs (tax model choice per tool,
withdrawal-order pluggability, taxable-gain approximation, bridge-job
phasing scope, SWR's undifferentiated spending figure). A 6th
consideration (Phase 5's scope boundary) was added this session — see
below.

### Phase 2 — Independent reference tests (`backend/tests/test_annual_engine_reference.py`)

Unchanged: 22 hand-calculated tests against the shared engine, none
derived from running the code first. Untouched this session, as
instructed — these stay the independent standard the rest of the
consolidation is checked against, not something that gets "updated" to
match a migration.

### Phase 3 — Shared engine (`backend/annual_engine.py`)

Unchanged: `simulate_withdrawal_year()` and `simulate_conversion()`, two
tax models, two withdrawal orders. `simulate_conversion()` got its first
real consumer this session (`run_roth_conversion_analysis`, below) —
previously only exercised by Phase 2's reference tests.

### Phase 4 — Consumers: now fully resolved, one at a time

| Consumer | Status | Verification |
|---|---|---|
| `run_retirement_projection` | Migrated (prior session) | golden diff, 12 scenarios |
| `_run_single` (Monte Carlo + Stress Tests) | Migrated (prior session) | golden diff, 6 × 2 |
| `run_swr_analysis` | **Independent — documented exception** | attempted, reverted: >2x slower (26.83s→57.41s) |
| `run_survivor_scenario` | **Migrated this session** | golden diff, 8 scenarios, byte-identical |
| `run_roth_conversion_analysis` | **Migrated this session, WITH a real fix** | golden diff run (not silent — numbers moved, see below), 37 existing tests + full suite pass |
| `run_tax_efficiency_simulation` | **Independent — documented exception, mitigated** | ordered-draw logic extracted to a parity-tested shared helper (`_ordered_draw`); full engine migration measured ~2.9x slower |
| `run_rmd_planning` (retirement_tools_engine.py) | Already downstream | reads `run_retirement_projection`'s output directly, no waterfall of its own to migrate |

#### `run_survivor_scenario` (commit `ceddeb6`)

Single untaxed "taxable" bucket, no RMD, `no_tax_model()`. Byte-identical
across 8 golden scenarios. One real behavior change inherited from the
shared engine, not previously possible: a year where guaranteed income
exceeds a survivor's reduced need now sweeps the surplus into savings
instead of silently discarding it (matches every other migrated
consumer). No golden scenario happens to exercise this, so no numbers
moved in practice — flagged for the record.

#### `run_roth_conversion_analysis` (commits `563d9e1`) — **the one Jason should look at closely**

**Fixed a real tax-modeling gap, per Jason's explicit decision (asked via
AskUserQuestion mid-session, chose "Fix it now"):** the tool correctly
counted the pretax-funded portion of spending as taxable income when
computing 22%-bracket room, but never deducted that tax from any
balance — only the conversion's own tax was ever paid. Reproduced: a
scenario with a modest taxable balance produced a year with $63,284 of
self-reported taxable income and $0 of tax paid on it anywhere.

**Numbers move as a result, and moved in a direction that surprised the
prediction going in.** Both the with-conversions loop and the
"without conversions" comparison baseline were migrated (so they stay an
apples-to-apples comparison), and the golden diff was run and read, not
assumed silent. Direction: `total_conversions` and `net_lifetime_benefit`
go **up** in every one of 6 tested scenarios (default: $579,352 →
$616,576 total conversions; $190,130 → $200,995 net lifetime benefit) —
not down, as predicted before running the numbers. Root cause: the
tax-gap fix itself is the smaller contributor; the larger one is that
`simulate_conversion` (by its own pre-existing design, first real
consumer here) layers a conversion on top of an ALREADY-GROWN year, so
both the taxable pool funding the conversion's tax and the pretax pool
available to convert get a year of growth headroom the hand-rolled
version never gave them. This was isolated with a side-by-side
pre-growth-pool variant before deciding to keep the engine's standard
convention rather than special-case this one consumer.

A smaller latent bug was also fixed as a side effect: the original
capped the year's conversion by the OPENING pretax balance rather than
the balance left after that year's own spending draw, silently flooring
an over-conversion instead of truly capping it.

**This is the one place in the whole consolidation where a user-facing
number changed for reasons beyond "same math, cleaner code."** 37
Roth-related tests + the full 649→661-test suite passed unchanged
throughout (no test hardcoded the old buggy totals) — but a passing test
suite doesn't mean the new numbers are what Jason wants displayed; it
means the code does what it's now supposed to do. Recommend Jason
actually opens the Roth Conversion planner against his real household
data and sanity-checks the new total before this branch merges.

#### `run_tax_efficiency_simulation` (commit `01f1a82`)

Migrating `taxable_first`/`roth_first` onto the full shared engine
measured ~2.9x slower (0.084s → 0.242s/call) — worse than SWR's
documented >2x, for the same underlying reason (per-year dataclass/dict/
`Transfer`-list allocation stops being free at this call volume: 1000
trials × ~35 years × up to 3 strategies). Reverted.

Unlike SWR, this one shipped with a mitigation instead of a second
silently-duplicated implementation: the two strategies' per-year draw
logic was extracted into `simulation_engine._ordered_draw` — pure
floats in/out, no allocation overhead (measured: 0.084s → 0.090s/call,
~7%, an acceptable one-time cost to de-duplicate two copy-pasted
blocks into one). `tests/test_tax_efficiency_engine_parity.py` (12
tests) proves this fast path is byte-identical to
`simulate_withdrawal_year` for the same inputs across 6 cases. The
`optimal` strategy (LTCG-threshold logic) remains fully independent —
it doesn't fit the order-driven shape at all, same assessment as the
prior session.

### Phase 5 — Education/Kids: scoped consolidation (commit `dd94930`)

Extracted `_project_529_saving_phase` (projection_engine.py) — the one
piece of `run_education_projection`/`run_kids_projection` that was
genuinely the SAME calculation independently implemented twice: 529
balance at the moment college/18 starts. Already proven to agree by a
pre-existing regression test (`TestEducationAndKidsProjectionsAgreeOn529AtCollege`,
from an external audit that caught `run_kids_projection` ignoring the
parent-retirement cutoff) — that pre-existing agreement is what made the
extraction safe: the shared function had to reproduce two
independently-audited implementations exactly, verified via a
21-scenario golden diff showing zero differences.

**Deliberately not touched:** the college-years drawdown (rollover, cost
inflation) and Kids' age-60 account timeline. Both are dense,
already-audit-hardened, off-by-one-sensitive code (this file's own
comments document several previously-shipped bugs in exactly this kind
of timing logic) with no MATCHING literal duplication to remove — Kids'
timeline has no Education equivalent at all, and Education's drawdown
tracks a "worst deficit" figure Kids' drawdown doesn't compute. Forcing
these into one shape would mean inventing a shared abstraction that
doesn't already exist in proven, agreed-upon behavior on both sides —
the opposite of what made the saving-phase extraction safe. This matches
Jason's own prior decision to leave adjacent Kids/Education scope
(>2-kids support) alone given how bug-hardened this pair of functions
already is.

### Phase 6 — Cross-tool reconciliation sweep, now actually run (commit `0db69cf`)

The first revision of this document listed this as not done. It's now
14 tests in `tests/test_cross_tool_reconciliation.py`, checking — for
the SAME household inputs, across every withdrawal-phase consumer at
once, not per-migration — 4 genuinely cross-cutting invariants:

1. `retirement_end_age` actually wired to each consumer's simulated
   horizon (not just echoed back), across all 6 consumers that have one.
2. `pension_for_age`-derived guaranteed income consistent between
   `run_retirement_projection` and `run_roth_conversion_analysis`.
3. No consumer reports a negative balance under a stress scenario —
   specifically covering the three that don't get this for free from
   `AnnualResult.reconcile()`: tax-efficiency's `optimal` strategy,
   survivor scenario, Roth conversion.
4. A life event dated before retirement is never replayed during the
   withdrawal phase, across retirement projection, Roth conversion,
   survivor scenario, and tax-efficiency simulation.

**Result: all 14 pass. No systemic invariant violations found.** This is
scoped, not exhaustive — CALCULATION_CONTRACT.md section 3's material
assumptions are deliberate divergences and correctly excluded from every
check.

### Phase 7 — This handoff, verification

- Backend: **680 passed**, **97.45% coverage** (floor 95%).
- Frontend: **13 passed** (unchanged — no frontend code touched this
  session), build succeeds (`vite build`, same pre-existing >500kB chunk
  warning as before, unrelated).
- Golden-diff tools added this session:
  `backend/tools/capture_survivor_golden.py`,
  `backend/tools/capture_roth_golden.py` (join the prior session's
  `capture_retirement_golden.py`/`capture_simulation_golden.py`/
  `diff_retirement_golden.py`) — all left in the tree, reusable for any
  future migration the same way.

## What's still independent, on purpose (not oversights)

1. **`run_swr_analysis`** — measured >2x slower on migration, reverted.
   Shares the tax-rate/gross-up *formulas* via module-level helpers, not
   the per-year orchestration.
2. **`run_tax_efficiency_simulation`'s `optimal` strategy** — LTCG-
   threshold logic doesn't fit the order-driven shape `taxable_first`/
   `roth_first` now share via `_ordered_draw`.
3. **Education/Kids' drawdown and timeline halves** — genuinely
   different calculations (not duplicated), left alone per Phase 5's
   scope decision above.
4. **The 5 material assumptions in CALCULATION_CONTRACT.md §3** — tax
   model choice, withdrawal-order pluggability, taxable-gain
   approximation, bridge-job phasing scope, SWR's undifferentiated
   spending figure. Product decisions, not bugs.

## Recommended next steps, in priority order

1. **Jason: sanity-check the Roth Conversion planner's new numbers**
   against real household data before this branch merges — this is the
   one place a user-facing dollar figure changed for a reason beyond
   pure refactoring.
2. Merge to `main` once (1) is done, or ask for specific changes if the
   Roth-conversion growth-timing convention (post-growth, matching the
   shared engine's existing design) isn't the one Jason wants for that
   tool specifically.
3. If tax-efficiency's or SWR's inner loop is ever made faster
   (fewer Monte Carlo trials, a smarter SWR search), revisit full
   engine migration for both — same performance profile, same
   fix would likely apply to both.
4. Education/Kids' drawdown/timeline consolidation remains available if
   a real bug is ever found there (not for consolidation's own sake —
   see Phase 5 above).
