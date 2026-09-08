# Two-Dimensional Retirement Timing — Design (not built)

Status: **design only, no code written**. This is the largest item on the
second-earner backlog (see `CALCULATION_CONTRACT.md` sections 13–18), and
was deliberately deferred out of every round of that work. Written
2026-09-08 to scope it before anyone touches `timeline_engine.py`, since
getting that module's shape wrong once already cost a systemic
re-fix across all 6 withdrawal-phase consumers during the original
calc-engine consolidation (`CONSOLIDATION_HANDOFF.md`).

## 1. What's being asked for

Today, exactly one retirement age (`ret_age`, always keyed to Jason's age
via `timeline_engine.build_timeline`) drives every consumer. Justin's own
`justin_ret_age` only matters as an *income offset* during the gap between
`ret_age` and Justin's own retirement — nothing ever draws from the
portfolio before `ret_age`, for either spouse (`justin_gap_income_for_year`,
`CALCULATION_CONTRACT.md` section 15).

A true two-dimensional model would let Jason and Justin retire at
independently chosen ages, in either order, with the plan correctly
modeling **three** phases instead of today's two:

| Phase | Today | 2D |
|---|---|---|
| Both working | Accumulation, no withdrawal | Same |
| One retired, one still working | **Doesn't exist** — collapsed into "both working" if before `ret_age`, or modeled as a working-spouse income offset bolted onto full withdrawal if after | A real partial-withdrawal phase: one salary, real portfolio draws, SS/pension may or may not have started for either person |
| Both retired | Full withdrawal (today's `ret_age`-anchored loop) | Same, but now starts at `max(jason_ret_age, justin_ret_age)` instead of a single input |

The middle phase is the actual new work. Everything else is mostly a
relabeling of what already exists.

## 2. Why this is bigger than the gap-income work

The gap-income backlog (6 rounds, this session) only ever added an
**income offset** inside the existing single-phase withdrawal loop — it
never changed when withdrawals *start* or added a new phase. This is
qualitatively different:

- `timeline_engine.Timeline` currently has one `effective_start_age`. A
  second retirement age means either a second timeline object, or
  `Timeline` growing a second axis — and every one of the 6 consumers
  that trusts `Timeline` today would need to be re-validated against the
  new shape, the same way all 6 needed re-touching when
  `build_timeline` was first extracted.
- The middle phase needs its own spending/tax/withdrawal-order policy —
  it's not a smaller version of full withdrawal, and not a bigger version
  of accumulation. Realistically it looks like "full withdrawal loop, but
  with one real paycheck as guaranteed income and no SS/pension for the
  still-working spouse yet" — closest to what Survivor Scenario already
  does for the *deceased* spouse's side, structurally, but for a living
  spouse and only one direction.
- Survivor Scenario already has fragile, hand-rolled age-gap conversion
  (`age_gap`, `justin_age_at`) for a single death event. Layering
  independent retirement ages under an already-independent death age
  is where the edge-case count grows fastest — six combinations of
  {Jason retires first, Justin retires first, same age} × {death before
  either retirement, death in the gap, death after both} — and Survivor
  is the one consumer whose insurance-need math has needed correcting
  three separate times already this session for edge cases exactly like
  this.
- Every one of the 6 consumers' tests (`TestJustinGapIncomeInputsHelper`
  and the per-consumer parity suites) implicitly assume a single
  withdrawal-start axis. Their fixtures and assertions would need
  auditing, not just extending.

## 3. Product/UX questions to settle before any code

These aren't engineering decisions and should be answered first, because
the answer changes what `timeline_engine.py`'s shape needs to be:

1. **How does the user pick two ages?** Today's scenario sweep
   (`RET_AGES = [55..67]`, `Simulation.jsx`) is a single 13-point axis
   rendered as a table/chart. A true 2D sweep is 13×13=169 combinations.
   Options, roughly in order of engineering cost:
   - **(a) Fix one age, sweep the other.** E.g. Justin's `justin_ret_age`
     stays a single Settings input (as it is today) and only Jason's age
     sweeps — this is the cheapest UI-wise and closest to what already
     exists, but doesn't actually deliver "two-dimensional," just
     confirms the existing one-axis sweep uses a real second income
     during the gap (which is already partially true today).
   - **(b) Two linked single-axis sweeps** — "if Justin retires at his
     current planned age, here's Jason's sweep" and vice versa, shown as
     two separate tables. Doubles the compute, no new UI paradigm.
   - **(c) A real 2×2 heatmap/matrix** (e.g. a small grid of "portfolio
     at 90" or "success rate" cells, Jason's age on one axis, Justin's on
     the other). Most honest answer to "two-dimensional," most UI work,
     and 169 scenario evaluations per render for Monte Carlo would need
     real caching/backend-side aggregation — Monte Carlo already runs
     N=1000 trials per single age today.
   - **My recommendation:** (a) or (b) first, as a checkpoint — the
     backend/timeline work needed for (c) is the same as for (b); (c) is
     purely a rendering and performance question layered on top later if
     wanted.

2. **Does the middle phase model real payroll tax, or keep the existing
   flat 65%-of-gross approximation** (`SECOND_EARNER_NET_OF_TAX_FACTOR`)?
   Real payroll-tax modeling is its own deferred backlog item; doing it
   only for the middle phase would put two different income-modeling
   fidelities in the same plan.

3. **What happens to Survivor Scenario?** Does 2D retirement timing
   extend into Survivor at all in v1, or does Survivor keep its current
   single-`ret_age`-anchored gap-income contract (section 16) and 2D only
   applies to the four non-Survivor consumers plus Tax Efficiency? Given
   Survivor's track record this session, I'd scope it OUT of a first
   pass and revisit once the base 2D model is proven correct elsewhere.

4. **Ownership during the middle phase.** The still-working spouse's
   salary is going into *someone's* accounts (401k, taxable). The
   existing owner-attribution gap (backlog item 5, still open) becomes
   more visible here than in the current model, where it's easy to
   ignore because both spouses are symmetric (either both working or
   both retired). Not blocking, but worth naming so it isn't rediscovered
   mid-implementation.

## 4. Proposed phased implementation (once the above is answered)

Assuming answers land on the cheap end (1a or 1b, flat tax factor kept,
Survivor out of scope for v1):

**Phase 1 — `timeline_engine.py` shape.** Decide whether `Timeline` grows
a second age field (`justin_effective_start_age`) plus a
`joint_effective_start_age = max(jason, justin)` derived field, or whether
a parallel `build_timeline` call is made for Justin and the two are
reconciled by callers. Leaning toward the former — a single `Timeline`
object carrying both, since `justin_age_at`/`age_gap` already exist and a
second full `Timeline` instance would duplicate `end_age`/`retire_yrs`
logic that should stay shared. This is the piece that needs the most
design care and the most test coverage before anything downstream touches
it — same lesson as the original consolidation's systemic timeline bug.

**Phase 2 — the middle-phase loop, isolated.** Build and test the
one-retired/one-working spending+withdrawal logic as its own function
first, against `run_retirement_projection` only (the reference
implementation), the same order the original gap-income work followed
(single consumer first, propagate after). Needs its own test matrix:
Jason retires first / Justin retires first / same age (degenerates to
today's behavior — this is the regression check that 2D didn't break the
existing single-axis case), each crossed with a few salary/spending
combinations.

**Phase 3 — propagate to the remaining consumers**, in the same order
gap income was propagated (Monte Carlo/Stress via the shared
`_run_single`, then SWR, Tax Efficiency, Roth Conversion), each verified
against Phase 2's reference implementation via exact or parity tests, one
isolated branch per round, same review-and-fix cadence as this session's
6 rounds.

**Phase 4 (optional, separate ask) — Survivor Scenario integration** and
**Phase 5 (optional, separate ask) — UI option (c)**, the real heatmap,
if (a)/(b) prove out and a heatmap is still wanted.

## 5. Rough sizing

Using this session's gap-income work as the closest comparable (6 rounds,
each touching a subset of the same 6 consumers, to go from "one consumer,
income offset only" to "all 6 consumers, fully correct including two
non-obvious insurance-math bugs"): 2D retirement timing is strictly harder
per consumer (a new spending phase vs. an income offset) and touches the
same 6 consumers plus the shared timeline module they all depend on. I'd
expect Phases 1–3 alone to run longer than the full gap-income effort did,
likely several isolated-branch rounds with independent review between
each, before Phase 4/5 are even considered. Not a "next session" task —
worth treating as its own multi-week effort with the product questions in
section 3 settled first.

## 6. Non-goals for a first pass

Explicitly not included unless separately asked for, to keep any future
implementation scoped the way this session's rounds were:
- Real payroll-tax modeling (own backlog item)
- Owner-attributed account ledger (own backlog item)
- Survivor Scenario 2D integration (Phase 4 above)
- The heatmap UI (Phase 5 above)
- Any change to accumulation-phase (pre-retirement) logic — 2D timing
  only affects what happens once the first spouse retires
