# Calculation engine consolidation — handoff

**PAUSED FOR REVIEW as of this snapshot.** Per Jason's explicit
instruction: implementation is paused here, the branch is pushed exactly
as-is, and no further changes are being made while this snapshot is
reviewed.

**Exact commit SHA at pause:** `07ac84dbce35d49685e269764c1aebc95caa0c62`
— confirmed identical to `origin/codex/consolidate-calculation-engine`
(nothing local left unpushed).

**Baseline:** `ec61309e6749d74ee31a02070aa46fd238a8ffd5` (verified identical to
`origin/main` and local `main` at the time this branch was created).
**Branch:** `codex/consolidate-calculation-engine`, 29 commits, pushed,
**not merged into `main`**.

*This is the seventh revision of this document. The first (6 commits)
covered Phases 1–4 partial + a documented SWR exception. The second (7
more commits) finished Phase 4, did a scoped Phase 5, and ran Phase 6 for
the first time. The third (4 more commits) covers an independent review
finding 4 genuine bugs. The fourth (3 more commits) covers that same
reviewer's follow-up pass finding 2 more. The fifth (2 more commits)
covers a third follow-up finding one systemic bug. The sixth (3 more
commits) covers item 1 (shared timeline normalizer) and the two adjacent
cases from Jason's 9-item follow-on task list. This revision (4 more
commits) closes items 3, 4, and 5 from that same list, advances item 9
further, and finds items 6/8 substantially already satisfied — see below
for exactly which of the 9
items are and are not complete.

## Status against Jason's 9-item follow-on task list (2026-09-07)

After the third review's systemic finding, Jason asked for 9 specific
items in priority order, plus a closing directive (build the shared
timeline normalizer, fix the two adjacent cases by correcting the
reference where wrong, inventory duplicates, expand the systematic
matrices, keep the branch unmerged). This section was written after the
first pass (item 1 + adjacent cases + item 9's inventory) and has been
updated after a second pass that closed items 3, 4, and 5 and found
items 6 and 8 substantially already satisfied. Honest status, item by
item, current as of this revision:

1. **Shared timeline normalizer — DONE.** `timeline_engine.py`, migrated
   into all 6 consumers including the reference implementation. See
   `CALCULATION_CONTRACT.md` section 9.
2. **Finish shared annual cash-flow coverage — PARTIALLY DONE.**
   `healthcare_for_age` (item 9) is one piece of this; spending/SS/
   pension/life-event offset computation otherwise remains independent
   per consumer. Real remaining work.
3. **Bring SWR under the shared engine, or prove its fast path
   equivalent — DONE (equivalence proven, not full migration).**
   `_swr_year_step` extracted and parity-tested against
   `simulate_withdrawal_year`: 8 hand-picked cases, 200 randomized annual
   cases, and a full 20-year chained scenario (209 tests total). Full
   engine migration remains reverted — still measured >2x slower,
   unchanged from the original exception. See `CALCULATION_CONTRACT.md`
   section 10.
4. **Tax-efficiency's "optimal" strategy onto the shared ledger —
   DONE.** Extracted into `_optimal_draw`, 7 parity tests decomposing its
   3-phase policy into 3 chained `simulate_withdrawal_year` calls. See
   section 10.
5. **Complete Roth conversion reconciliation — DONE.**
   `test_roth_conversion_reconciliation.py`: a 36-case cohort-growth
   matrix, a matched with/without-conversion pair, RMD impact, tax
   funding, and unmet-spending checks (40 tests). See section 10.
6. **Consolidate survivor calculations around the shared account state —
   SUBSTANTIALLY ALREADY DONE**, from a prior session's migration, not
   new this pass: `run_survivor_scenario` already calls
   `simulate_withdrawal_year` with `AccountState`, so growth/tax/unmet-
   need already go through the shared ledger. What's still genuinely
   independent is the single-aggregate-bucket policy itself (survivor
   never models pretax/roth/taxable/hsa separately) — a deliberate
   policy choice per the existing material-assumptions list, not an
   unconsolidated ledger.
7. **Education/Kids: compare/share drawdown, rollover, custodial, and
   timeline calcs — NOT DONE.** Phase 5's scoped saving-phase
   consolidation stands; the drawdown/rollover/custodial/timeline halves
   remain independent, as previously scoped and documented. Still real
   remaining work, still deliberately not attempted without a specific
   bug motivating it (see Phase 5's own reasoning).
8. **API-output-level invariant tests across all consumers —
   SUBSTANTIALLY ALREADY DONE**, from Phase 6 (an earlier pass in this
   same branch), not new this pass: `test_cross_tool_reconciliation.py`
   already covers effective-age/horizon consistency
   (`TestRetirementEndAgeRespectedEverywhere`,
   `TestPastRetirementAgeTimelineConsistency`), SS/pension consistency
   (`TestGuaranteedIncomeConsistentAcrossConsumers`), no-negative-balance
   invariants (`TestNoConsumerReportsANegativeBalance`), and life-event
   replay invariants (`TestCompletedLifeEventsNotReplayedInWithdrawalPhase`)
   across all 6 consumers already. Not exhaustive (e.g. no single test
   asserts EVERY field item 8 names for EVERY consumer in one pass), but
   the substance of the ask is covered, not absent.
9. **Duplicate-formula inventory — DONE, consolidation substantially
   advanced.** `healthcare_for_age` now eliminates the healthcare-
   phasing duplicate the inventory flagged (5 call sites → 1 shared
   function). Account-bucket representation and the two SS-COLA formula
   forms remain justified exceptions, not drift risks, as originally
   documented.

**Closing directive:** the two adjacent cases (asset-sale timing,
survivor `death_age` default) were resolved by correcting the reference
implementation itself, not just matching consumers to a known-wrong
baseline (first pass). The systematic matrices have been substantially
expanded this pass (SWR's 209-case matrix, Roth's 40-test reconciliation
suite, tax-efficiency's 7 new parity cases) — this now covers most of
what the directive's "expand the systematic matrices" asked for, though
not exhaustively for every consumer.

**In short, after both passes: items 1, 3, 4, 5, and 9 are done; items 6
and 8 are substantially already satisfied by earlier work in this
branch (not net-new, but genuinely covering the ask); items 2 and 7
remain open — both deliberately deferred, per Jason's own framing, not
dropped:**

- **Item 2 (shared annual-input builder) remains part of the intended
  consolidation and should follow this review.** `healthcare_for_age` is
  one piece; the real remaining scope is the duplicated income/timing
  calculations (spending need, SS, pension, life-event offsets) that
  caused several of the earlier independent-review findings in the first
  place — consolidating this is meant to close off that whole class of
  bug, not just tidy formatting.
- **Item 7 (Education/Kids) is deferred pending review of the
  overlapping education calculations specifically** — not declined.
  Different outputs (Education's college-drawdown chart vs. Kids'
  age-60 timeline) can share underlying mechanics without being forced
  into one shape; before extending the shared-timeline abstraction
  there, the next step is identifying the PRECISE overlap and writing
  acceptance tests for it, the same discipline that made the Phase 5
  saving-phase extraction safe (that extraction worked specifically
  because the two implementations were already proven to agree before
  the shared function existed — the same standard should apply to
  whatever of drawdown/rollover/custodial/timeline is tackled next).

## A third follow-up review found a systemic timeline bug — also fixed

The same reviewer's third pass found that `run_retirement_projection`'s
own past-ret_age fix (`withdrawal_start_age = max(ret_age, jason_age)`,
from a much earlier session) was never propagated to ANY other
withdrawal-phase consumer — Monte Carlo, Stress Tests, SWR, Roth
conversion, and tax-efficiency all still used the raw, possibly-past
`ret_age` for their own simulated horizon. A household selecting an
already-past retirement age (e.g. a "what if I'd retired at 55"
sensitivity column while actually 65 today) got a wildly different
number of spending years, SS/healthcare timing, and conversion window in
each tool. Full before/after numbers: `CALCULATION_CONTRACT.md` section
8. Fixed in all 5 functions with the same pattern the reference
implementation already used; full suite re-verified green (767 tests,
97.47% coverage).

**This is the third review pass in a row to find real bugs — and unlike
the first two (each 1-2 fairly localized issues), this one was
systemic, present in 5 functions at once, from a fix that was made once
in one place years ago and simply never generalized.** That's a
different kind of signal than "a fix introduced an adjacent bug" — it
suggests the underlying pattern (a correctness property established in
one reference implementation, silently assumed elsewhere without being
mechanically enforced or tested) may recur again in places not yet
reviewed. Worth being direct about: three rounds of real findings, the
last one systemic, is a stronger case for a deliberate comprehensive
sweep of the remaining surface than for a fourth reactive round. Still
Jason's call, not decided here — but the case for it has gotten
stronger each time, not weaker.

## Independent review found 4 real bugs — fixed, not disputed

An external reviewer examined commit `854855f` (synthetic inputs only, no
application changes, no merge) and requested changes before merge. All 4
findings checked out as real: two in the freshly-migrated
`run_roth_conversion_analysis` (a dropped Roth spending fallback with an
inaccurate comment claiming it was never modeled; a growth-timing
exponent that overstated conversion benefit by one year of compounding),
one in `run_tax_efficiency_simulation` that predates this session (a
negative life-event's deficit was silently floored away instead of
funded — the reviewer's own assessment: "a remaining defect, not a new
regression"), and one gap in `AnnualResult.reconcile()` itself (aggregate-
only checking couldn't catch an unrecorded movement between two buckets).
Full detail, reproduction numbers, and fixes: `CALCULATION_CONTRACT.md`
section 6. All fixed and covered by regression tests reproducing the
review's exact numbers; full suite re-verified green (685 tests, 97.46%
coverage) after every fix. **This means the branch has now had one real
adversarial pass and survived it — not that it no longer needs one.**
Recommend a second look at the fixes themselves before merging, given the
first review's hit rate on a branch that had already passed 649+ tests
and multiple golden diffs.

## A second follow-up review found 2 more real bugs — also fixed

The same reviewer looked again and found two more real issues, both in
code the first pass's fixes had just touched or adjacent to it: (1) the
recurring-income floor the first pass's negative-life-event fix
introduced in `run_tax_efficiency_simulation` silently discarded any
RECURRING income above spending (mirror-image of the bug just fixed,
in the opposite direction), and (2) Monte Carlo/Stress Tests still drop
Social Security COLA accrued before retirement whenever SS is claimed
before the scenario's own retirement age — a gap the first pass's new
SS-timing check didn't cover because it only exercised
`run_retirement_projection`/`run_roth_conversion_analysis`, never Monte
Carlo or Stress. Full detail and reproduction numbers:
`CALCULATION_CONTRACT.md` section 7. Both fixed, both covered by
regression tests reproducing the review's exact numbers (including a
72-case full-year parity matrix for the first fix, matching the size of
the reviewer's own matrix), full suite re-verified green (761 tests,
97.47% coverage). **This is now the second review pass in a row to find
real bugs in this branch.** Two rounds of real findings on a branch that
kept passing its own growing test suite between them is worth sitting
with, not just fixing and moving on — a broader systematic sweep of the
remaining consumers, rather than reactive fixes chasing each new review,
may be worth considering before another round. Left as Jason's call, not
decided here.

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

- Backend: **1048 passed**, **97.57% coverage** (floor 95%) — after
  closing items 3/4/5; was 680/97.45% right after Phase 6, 685/97.46%
  after the first review's fixes, 761/97.47% after the second,
  767/97.47% after the third, 787/97.50% after the timeline-normalizer
  pass.
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

1. **Jason: decide whether to keep reviewing reactively or run one
   broader systematic sweep next.** Three review passes in a row have
   now each found real bugs (4, then 2 more, then a systemic one present
   in 5 functions at once). The third finding in particular — a fix made
   once in `run_retirement_projection` years ago, never generalized to
   its 5 siblings — is the kind of pattern most likely to recur
   elsewhere unreviewed. The case for a deliberate, comprehensive sweep
   over the remaining surface (rather than a fourth reactive round) is
   stronger now than after either prior round. Not decided here —
   Jason's call.
2. A review pass on all 7 independent-review fixes together (commits
   `7ccb43e`, `520e2a6`, `f18c6b0`, `00e85f9`, `cfbc64f`, `e6c0056`,
   `19ee3e9`) — each prior review caught real bugs in code that had
   already passed its own golden diffs and test suite, which is exactly
   the situation to not assume is resolved just because it's fixed and
   tested again now.
3. **Jason: sanity-check the Roth Conversion planner's new numbers**
   against real household data before this branch merges — the tax-gap
   fix AND the Roth-fallback/growth-timing fixes from the independent
   review move this tool's dollar output; this is the one function in
   the whole consolidation where that's expected and intentional, not a
   sign something's still wrong.
4. Merge to `main` once (1)–(3) are resolved to Jason's satisfaction.
5. If tax-efficiency's or SWR's inner loop is ever made faster
   (fewer Monte Carlo trials, a smarter SWR search), revisit full
   engine migration for both — same performance profile, same
   fix would likely apply to both.
6. Education/Kids' drawdown/timeline consolidation remains available if
   a real bug is ever found there (not for consolidation's own sake —
   see Phase 5 above).
