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

## 7. Phase 2 contract (v1 scope, on `codex/two-dimensional-retirement`)

Written before any code changes on this branch, answering section 3's
open questions for a first pass:

**Scope decision (answers section 3, question 1):** explicit ages for
both spouses, one scenario at a time — not a 13-age sweep, not a
heatmap. The UI is two number inputs (Jason's retirement age, Justin's
retirement age) and a "Run" action, producing one result. This is
option (a)/(b) from section 3, the cheapest UI-wise, deferred to a
later ask if a real matrix/heatmap (option c) is wanted.

**Scope decision (question 2):** the existing flat
`SECOND_EARNER_NET_OF_TAX_FACTOR` (0.65) stays exactly as-is, applied
symmetrically to *whichever* spouse is still working during the middle
phase — not just Justin. Real payroll-tax modeling stays deferred.

**Scope decision (question 3):** Survivor Scenario is explicitly OUT of
scope for v1. It keeps its existing single-`ret_age`-anchored gap-income
contract (`CALCULATION_CONTRACT.md` section 16) unchanged.

**Scope decision (question 4):** the existing aggregated, unattributed
account-bucket model (pretax/roth/taxable/hsa, no per-owner ledger) is
unchanged. A phase-2 wage surplus swept into savings can't be traced
back to whose paycheck it came from, same limitation every other
consumer already has — documented, not fixed, here.

### 7.1 A key simplification found while designing this

The existing second-earner gap-income mechanism (`justin_gap_years`,
`justin_gap_income_at_start`, `justin_gap_income_for_year` in
`projection_engine.py`) already implements *most* of what a phase-2
middle period needs — it just only ever runs in one direction (Justin
still working, Jason's `ret_age` fixed as the household's sole
withdrawal-start axis) and only ever starts at Jason's own retirement.

Two observations simplify v1 considerably:

1. `simulate_withdrawal_year` (`annual_engine.py`) already sweeps any
   income surplus (guaranteed income exceeding spending need) into the
   taxable bucket automatically — this is the exact mechanism verified
   repeatedly during this session's Survivor insurance-calculation fixes
   (`CALCULATION_CONTRACT.md` section 18). It requires no new code:
   modeling the still-working spouse's phase-2 income as
   `guaranteed_income` (net-of-tax at the 65% factor, same as today's
   gap income) gets surplus-sweeping for free.
2. `justin_gap_income_for_year(yr, gap_years, income_at_start,
   salary_growth_pct)` is already fully generic — nothing about its
   signature is Justin-specific. It can be reused verbatim for either
   direction (Jason still working while Justin has retired, or vice
   versa) just by feeding it whichever spouse's salary applies.

Given this, v1 does **not** need new withdrawal-engine machinery. It
needs: (a) a timeline that knows about *two* independent retirement
ages instead of one, and (b) a generalization of the existing
Jason-anchored gap-income call site so either spouse can be the
"still-working" side. This is why Phase 2 is scoped smaller here than
section 5's original estimate suggested — the hard part (the shared
withdrawal engine's surplus/shortfall handling) was already built and
proven correct by the gap-income backlog work.

### 7.2 Timeline shape

A new dataclass, `TwoPersonTimeline` (`timeline_engine.py`), additive —
the existing `Timeline`/`build_timeline` used by all 6 current
consumers is untouched, so nothing about their behavior changes:

- `jason_years_to_retire = max(0, jason_ret_age - jason_age)`,
  `justin_years_to_retire = max(0, justin_ret_age - justin_age)` — each
  spouse's own clamp for an already-past retirement age, same
  `max(ret_age, age)`-style convention `build_timeline` already uses,
  applied per-person instead of only to Jason.
- `phase2_start_years = min(...)` of the two — years from today until
  the FIRST spouse retires. This is where the withdrawal loop begins
  (a real change from today: the loop can now start at Justin's
  retirement, not just Jason's).
- `phase3_start_years = max(...)` of the two — years from today until
  BOTH have retired. This is where gap/still-working income stops and
  full withdrawal (identical to today's existing single-axis model)
  begins.
- `later_retiree`: `"jason"` | `"justin"` | `None` (equal ages —
  simultaneous retirement, zero-length phase 2). This determines whose
  salary funds the phase-2 income offset.
- `end_age`/`retire_yrs`: same mortality-cap formula as `build_timeline`
  (`max(phase2_start_age + 1, min(110, retirement_end_age or 99))`),
  anchored to `phase2_start_age` instead of the single
  `effective_start_age`.

**Regression property, by construction:** when `jason_ret_age ==
justin_ret_age` (or `justin_ret_age` is left at its existing 0/unset
sentinel and falls back to Jason's date), `phase2_start_years ==
phase3_start_years` — zero phase-2 years — and the model reduces
exactly to today's single-axis withdrawal loop starting at that age.
This is the primary regression check (see the hand-calculated
"simultaneous retirement" test case, cross-checked numerically against
`run_retirement_projection`'s own existing output for the same inputs).

### 7.3 Spending / wages / contributions / withdrawals during phase 2

- **Accumulation phase (today → phase2_start):** unchanged in kind —
  each spouse's own 401k/RSU/bonus contributions run for their own
  years-to-retirement, using the exact same `_fv_annuity`/
  `_fv_growing_annuity` + "contribute until you stop, then compound
  dormant" pattern `_justin_contrib_fv` already implements — just
  capped at `phase2_start_years` (the earlier retirement) instead of
  always at Jason's date, so whichever spouse retires LATER only gets
  contribution credit for the accumulation-phase portion; the rest of
  their working years (phase 2 itself) are handled as income, next
  bullet.
- **Phase 2 (one retired, one working):** the still-working spouse's
  income is modeled as `guaranteed_income` in the shared withdrawal
  step — `salary * SECOND_EARNER_NET_OF_TAX_FACTOR`, grown at
  `salary_growth_pct` from the phase-2-start baseline, via
  `justin_gap_income_for_year` reused symmetrically. **No further 401k
  contribution is modeled from this income during phase 2** — once the
  loop starts, the still-working spouse's paycheck is treated as plain
  after-tax cash (spendable or, if it exceeds need, automatically swept
  into taxable savings). This is a deliberate simplification, consistent
  with real payroll-tax modeling being out of scope: modeling continued
  payroll-deducted 401k contributions *during* a withdrawal-phase loop
  would require extending the shared engine itself, which section 6
  explicitly defers.
- **Phase 2 spending need:** identical formula to today's household
  spending target (inflation-adjusted `retirement_income_today_dollars`
  plus healthcare/life-events/etc.), unchanged — only the income side
  gains the still-working spouse's offset.
- **Phase 3 (both retired):** identical to today's existing model —
  no working income, full withdrawal, same bucket order/tax treatment.
- **Withdrawal order, tax treatment, RMDs, healthcare phasing:**
  unchanged from the reference implementation — this function reuses
  `simulate_withdrawal_year`, `_rmd`, `rmd_start_age`, the marginal-rate
  helper, and `healthcare_for_age` exactly as `run_retirement_projection`
  does today, not a new withdrawal policy.
- **Insufficient funds:** no new logic — `simulate_withdrawal_year`'s
  existing `unmet_need` reporting is used as-is in both phases.
- **Income surplus:** no new logic — the existing surplus-sweep-to-
  taxable behavior (section 7.1) is used as-is in both phases.

### 7.4 Where this lives in code (decision, not yet built)

A **new, additive function** —
`run_two_dimensional_retirement_projection(inputs, accounts,
jason_ret_age, justin_ret_age, ...)` in `projection_engine.py` — rather
than adding a branch inside the existing (already dense)
`run_retirement_projection`. Reasons: (1) zero risk to the existing
reference implementation's behavior or its own test suite while this is
built and reviewed; (2) matches how `run_survivor_scenario` already
exists as its own function reusing shared helpers rather than a mode
flag on another consumer; (3) keeps this branch's diff reviewable in
small, isolated commits per the instruction this was scoped under. It
reuses (does not reimplement) `simulate_withdrawal_year`,
`_fv`/`_fv_annuity`/`_fv_growing_annuity`, `_rmd`/`rmd_start_age`,
`_split_life_events`/`_post_retirement_asset_sale_events`/
`_pre_retirement_taxable_add`/`_surplus_allocations_at_retirement`/
`_post_retirement_year_effects`, `pension_for_age`,
`healthcare_for_age`, and `justin_gap_income_for_year`.

### 7.5 Other consumers — explicit limitation, not silently equivalent

Monte Carlo, Stress Tests, SWR, Tax Efficiency, Roth Conversion, and
Survivor Scenario are **unchanged** by this work and keep their existing
single-axis-plus-income-offset behavior (`justin_gap_income`, section
15). None of them become "two-dimensional" by this branch. Their
existing `SecondEarnerNote` disclosure and API docstrings get an
explicit cross-reference added (not a behavior change) so a user reading
one of those pages cannot mistake the existing gap-income offset for
genuine two-axis retirement timing — that distinction only exists in
the new Retirement Projection two-age tool built here.
