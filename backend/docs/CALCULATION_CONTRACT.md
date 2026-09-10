# Personal CFO — Calculation Contract

Baseline commit: `ec61309e6749d74ee31a02070aa46fd238a8ffd5` (origin/main).
Branch: `codex/consolidate-calculation-engine`.

This document maps where the app's calculation engines duplicate financial
mechanics, and states the conventions a shared engine must follow so that
identical assumptions produce identical results everywhere.

## 1. Where mechanics are duplicated today

Nine functions in `backend/projection_engine.py` and
`backend/simulation_engine.py` each run their own year-by-year withdrawal
and/or spending simulation:

| # | Function | File | What it duplicates |
|---|---|---|---|
| 1 | `run_retirement_projection` (withdrawal-phase loop, line ~580) | projection_engine.py | The reference implementation: 4-bucket waterfall (taxable→pretax(grossed)→HSA→Roth), RMDs, marginal-rate tax, life events, asset sales, healthcare phasing, age-55 bridge/kids phasing, unmet-need tracking. |
| 2 | `_run_single` | simulation_engine.py | Near-identical bucket waterfall to #1, re-implemented separately. Shared by Monte Carlo and Stress Tests. Already uses the module-level `_pretax_marginal_tax_rate`/`_grossed_up_draw` helpers (added in the 2026-09-07 audit-fix pass) but the loop structure itself is a separate copy of #1's, not shared code. |
| 3 | `run_swr_analysis` → `success_at_withdrawal` (nested) | simulation_engine.py | A third independent copy of the same 4-bucket waterfall, used inside a binary search. Also uses `_pretax_marginal_tax_rate`/`_grossed_up_draw`, but the loop body itself is a third hand-written copy. |
| 4 | `run_tax_efficiency_simulation` → `run_strategy` (nested) | simulation_engine.py | A fourth copy, but deliberately implements **three different withdrawal-order policies** (taxable_first / roth_first / optimal-fill-22%-bracket) rather than the one true policy #1–#3 share. Uses **flat** tax rates (`TAX_PRETAX = 0.22`, `TAX_TAXABLE = 0.15`) instead of the real marginal-bracket lookup #1–#3 use — stated as a deliberate simplification in its own docstring ("Simplified tax: flat 22% on pretax withdrawals... 15% on taxable gains"). |
| 5 | `run_roth_conversion_analysis` | simulation_engine.py | A simpler, single-pass version: one `portfolio_draw` per year, no bucket depletion beyond pretax/roth/taxable, plus its own bracket-fill conversion policy. Duplicates guaranteed-income (pension/SS), inflation, and life-event glue independently. |
| 6 | `run_survivor_scenario` | simulation_engine.py | The simplest copy: a single aggregate `bal` (no pretax/roth/taxable/HSA distinction at all), its own guaranteed-income and inflation glue. |
| 7 | `run_rmd_planning` | retirement_tools_engine.py | **Already consumes #1's output** (as of the 2026-09-07 fix) rather than re-simulating — reads `pretax_at_retirement`/`yearly_detail` from `run_retirement_projection` directly. This is the shape every consumer should end up in. |
| 8 | `run_education_projection` | projection_engine.py | Its own 529/Roth/Custodial per-child projection loop (contribution cutoffs, college drawdown, SECURE 2.0 rollover). |
| 9 | `run_kids_projection` | projection_engine.py | A second, independent per-child projection loop covering the same three account types. As of the 2026-09-07 fix it uses the *same formulas* as #8 (contribution-cutoff rule, rollover netting, timeline-vs-headline consistency) but is still a **separate implementation** — numerically consistent by construction, not by sharing code. |

Cross-cutting glue duplicated at every withdrawal-phase call site (5 of the
9: #1–#5): age-gap-aware spousal SS timing, life-event splitting
(`_split_life_events`), post-retirement event application
(`_post_retirement_year_effects`), and post-retirement asset-sale synthesis
(`_post_retirement_asset_sale_events`). The three helper functions
themselves are already shared; the *code that calls them and feeds their
output into a year's cash flow* is re-written at each call site.

## 2. Conventions (resolved from existing product intent)

### 2.1 Beginning vs. end-of-year balances and age labels
- A `yearly_detail`/`schedule` row's `*_balance` fields are **end-of-year**:
  contributions/withdrawals for that year have been applied, then growth for
  that year has been applied. This is `run_retirement_projection`'s existing
  convention (`pretax = max(0, pretax * (1 + post_ret))` runs *after* the
  withdrawal step, then gets recorded) and every other engine already
  matches it.
- `age`/`jason_age` on a row is the account holder's age **during** that
  year (age at the start of the year the row describes), consistent
  everywhere today. `run_survivor_scenario`'s 2026-09-07 fix depends on this:
  the baseline's end-of-year balance at `age == death_jason_age` already
  reflects that whole year's spending, so the survivor's own distinct
  spending pattern must start at `death_jason_age + 1`, not the same age.

### 2.2 Contribution / income / spending / tax / transfer / growth timing
Within one simulated year, the existing order (established by
`run_retirement_projection`, now matched by `_run_single` and
`run_swr_analysis` after the 2026-09-07 fixes) is:
1. Determine this year's **spending need** (income target + healthcare,
   inflated) and **guaranteed income** (pension, frozen; SS, COLA'd from
   each person's own claim year).
2. Apply any **life-event cash** (one-time, added to taxable) and
   **life-event monthly delta** (adjusts the year's need directly).
3. Compute **net need** = need − guaranteed − life-event monthly offset.
4. Take the **RMD** if applicable (mandatory, before any discretionary
   draw) — its after-tax proceeds count toward net need; any excess is
   reinvested into taxable.
5. Draw discretionary spending from remaining buckets in order: **taxable →
   pretax (gross-up for tax) → HSA → Roth**. Any shortfall after all four is
   **unmet need**, tracked explicitly, never silently discarded and never
   left as a negative account balance.
6. **Growth** applies last, to the post-withdrawal ending balance, for
   every bucket.

Taxed draws (RMD and pretax) are **grossed up**: the withdrawal is sized so
its *after-tax* proceeds cover the need, not the pre-tax amount. This was
the single highest-impact fix from the 2026-09-07 audit pass (Monte
Carlo/Stress/SWR previously treated every withdrawal as tax-free).

**Deliberate policy differences to preserve, not "fix" into one:**
- `run_tax_efficiency_simulation` exists specifically to compare
  *different* draw-order policies (taxable_first / roth_first / optimal) —
  the shared engine must accept a pluggable withdrawal-order policy, not
  hard-code #1–#3's taxable→pretax→HSA→Roth order as the only option.
- `run_tax_efficiency_simulation`'s flat 22%/15% tax rates are a documented
  simplification for comparing strategies quickly, distinct from the real
  marginal-bracket calculation elsewhere. The shared engine's tax step must
  be pluggable (marginal-bracket vs. flat), not force one tax model on
  every consumer.
- `run_roth_conversion_analysis` layers a **conversion** transfer
  (pretax → Roth, at cost) on top of ordinary withdrawal draws — a second
  kind of "policy" (how much to convert this year) orthogonal to the
  withdrawal-order policy.

### 2.3 Retirement and contribution cutoff dates
- **Retirement age** for 401k/Roth contribution purposes: contributions run
  from today through `ret_age`, at the salary-derived rate (optionally
  growing with `salary_growth_pct`), full stop at retirement.
- **Kid 401k/529/Roth/Custodial contributions**: stop at the *earlier* of
  (a) the child turning 18, or (b) the parent's assumed retirement/earning
  cutoff (`PARENT_RETIREMENT_AGE_ASSUMPTION`, currently used by
  `run_education_projection`; `run_kids_projection` was fixed 2026-09-07 to
  match). This is now the single resolved rule both functions use.
- **Custodial contributions** stop at 18, matching every other kid-account
  contribution rule — `run_kids_projection`'s headline figure previously
  continued to 24; fixed 2026-09-07 to match its own timeline.

### 2.4 Today's dollars vs. future dollars, cumulative inflation
- All Settings/planning-input dollar figures (`retirement_income_today_dollars`,
  `healthcare_pre_medicare`, etc.) are **today's dollars** and must be
  inflated to the point of use, not used directly.
- **Pre-inflate once, then compound per simulated year** — the resolved
  pattern is: `figure_at_ret = figure_today * (1+inflation)**years_to_retirement`,
  computed once outside the yearly loop, then `figure_at_ret *
  (1+inflation)**yr` inside the loop (`yr` = years since retirement start).
  Two 2026-09-07 bugs were exactly a violation of this: `run_tax_efficiency_simulation`
  skipped the first step (omitted pre-retirement inflation entirely);
  `run_roth_conversion_analysis` skipped the second (computed the first
  step once and then reused that frozen value for every loop year instead
  of re-compounding).
- **Cumulative inflation must actually accumulate**, not be re-derived from
  the current year's rate. `_run_single` briefly had `(1+eff_inf)**yr`
  where `eff_inf` could change mid-horizon (stress scenarios with a
  multi-year `inflation_mult` override) — fixed 2026-09-07 with a
  precomputed `cum_inflation[]` index built by multiplying in each year's
  own rate in sequence. Any shared engine must use the same true-product
  approach, not a `rate ** years` shortcut, whenever the rate can vary
  across the horizon (stress scenarios, and potentially per-year Monte
  Carlo inflation multipliers in the future).
- Pension is **frozen — no COLA**, everywhere. Social Security **does**
  get COLA, from each person's own claim year forward (not from
  retirement start — a person who claims later starts their own
  inflation clock later). `run_survivor_scenario`'s COLA bug (applying
  COLA to the combined pension+SS total) was a violation of this,
  fixed 2026-09-07.

### 2.5 Account ownership and tax treatment
- Buckets, by account_type: **pretax** (`401k` × `pretax_401k_pct` + `ira`),
  **roth** (`roth_ira` + `401k` × `(1 − pretax_401k_pct)`), **taxable**
  (`taxable`), **hsa** (`hsa`). Kid-owned accounts (`owner` in
  `{"abby","cooper"}`) are excluded from every adult retirement bucket —
  `net_worth_engine.KIDS_OWNERS` is the canonical set.
- **Tax treatment by bucket**: pretax withdrawals are ordinary income
  (marginal-bracket rate, or the flat 22% simplification where explicitly
  chosen); Roth withdrawals are tax-free; HSA withdrawals are treated as
  tax-free (medical-qualified assumption); taxable withdrawals are taxed
  only on the *gain* portion in principle, but every current engine
  approximates this as a flat rate on the *full* draw (`TAX_TAXABLE = 0.15`,
  or 0% within the LTCG threshold in the "optimal" strategy) — a known,
  shared simplification, not something to "fix" per-engine differently.
- **RMD tax** is always at the pretax marginal rate, same bucket.
- `account_type` is now validated against `net_worth_engine.VALID_ACCOUNT_TYPES`
  at write time (2026-09-07 fix) — any shared engine can assume every
  account it receives has a recognized type; it does not need its own
  fallback-to-"other" handling.

### 2.6 Future events vs. completed events already reflected in current balances
- A life event or asset sale dated **before the current calendar year**
  describes something that has *already happened* — its cash is already
  inside today's account balances. It must not be replayed into the
  projection. `_split_life_events`/the projection engine's life-event
  handling should filter `event_year < CURRENT_YEAR` out entirely (this is
  one of the projection-engine agent's 2026-09-07 fixes — confirm it is
  present in the shared engine's event-splitting step, since Monte
  Carlo/Stress/SWR/Roth/tax-efficiency all reuse the same
  `_split_life_events` function and inherit the fix automatically once it
  lives there).
- A life event or asset sale dated **at or after the current year** is a
  real future event and must be applied exactly once, in the calendar
  year it's dated, regardless of which retirement-age scenario or which
  consumer is asking (2026-09-07 fix: asset sales previously only applied
  when `sale_age <= ret_age`, silently vanishing whenever a sale was
  scheduled after the specific retirement age being evaluated).

### 2.7 What constitutes an underfunded year and a successful plan
- **Underfunded year**: after drawing from every applicable bucket in
  order, `remaining > 0` (some real spending need went unfunded). This
  must be tracked as an explicit per-year flag/amount, never silently
  dropped and never allowed to show up as a negative account balance.
- **Successful plan** (Monte Carlo / stress / SWR "survives"): ending
  balance `> 0` **and** no year was underfunded. Checking only the ending
  balance (ignoring mid-horizon rationing) was exactly the class of bug
  fixed across `_run_single`, `run_swr_analysis`, and
  `run_tax_efficiency_simulation`'s "optimal" strategy in the 2026-09-07
  pass. The shared engine must expose an explicit `unmet_need` (or
  boolean `underfunded`) on every annual result so every consumer computes
  "success" the same way, instead of each one deciding independently
  whether to check it.
- `run_retirement_projection`'s `on_track` additionally allows a tiny
  negative-surplus tolerance (`surplus >= -0.5`) for floating-point noise
  — a deliberate, documented rounding allowance, not a different
  correctness threshold.

## 3. Explicit material assumptions (not to be silently resolved)

These are real modeling choices, not bugs, and the shared engine must make
each one an explicit, inspectable parameter rather than an implicit
behavior baked into one policy:

1. **Tax model**: marginal-bracket-plus-state-rate (the "real" model) vs.
   flat-rate simplification (tax-efficiency comparison tool). Both are
   legitimate; which one a given comparison should use is a product
   decision already made per-tool.
2. **Withdrawal-order policy**: taxable→pretax→HSA→Roth (the app's default,
   used everywhere except tax-efficiency) vs. the three alternate policies
   tax-efficiency explicitly compares. The shared engine treats this as a
   pluggable strategy, not a constant.
3. **Taxable-gain approximation**: flat rate on the full draw vs. 0% inside
   the LTCG threshold — both already coexist by design (`run_tax_efficiency_simulation`'s
   three strategies use different capital-gains treatment from each
   other on purpose).
4. **Bridge-job/kids-at-home phasing** (age-55 branch in
   `run_retirement_projection`/`_run_single`): a real, deliberate scope
   limit — this multi-phase income/healthcare model only exists for the
   age-55 scenario. `run_roth_conversion_analysis` and
   `run_tax_efficiency_simulation` do not attempt it (documented as a
   known simplification in their own docstrings pre-dating this task).
   The shared engine should make bridge-phase an optional input, not
   assume every consumer wants it.
5. **SWR's "safe withdrawal amount" is a single undifferentiated dollar
   figure**, not decomposed into income+healthcare like every other
   engine's `year_need`. This is intentional — SWR answers "how much can I
   safely withdraw in total," a different question from "given my actual
   itemized spending, do I survive." The shared engine's per-year
   spending-need calculator must be optional/overridable so SWR can
   supply a single number instead.

## 4. Migration log

### `run_retirement_projection` → `annual_engine.simulate_withdrawal_year` (Phase 4, first consumer)

The withdrawal-phase waterfall (RMD → taxable → grossed-up pretax → HSA →
Roth, growth applied last) was replaced with a call to
`annual_engine.simulate_withdrawal_year` using `DEFAULT_ORDER` and
`marginal_bracket_tax_model(pretax_rate=pretax_tax_rate, taxable_rate=0.0)`
— i.e. the exact tax model and draw order this function already used, now
implemented once instead of duplicated.

**Verification.** `tools/capture_retirement_golden.py` captured this
function's full output across 12 synthetic scenarios (zero returns/
inflation, every retirement age 55-67, an already-past retirement age, a
large spouse age gap, insufficient funds, an asset sale, the bridge-job/
kids-at-home branch, RSU+bonus, salary growth, state tax, life events)
before and after the migration; `tools/diff_retirement_golden.py` diffed
them with a $1 tolerance. All 132 pre-existing `test_projection_engine.py`
tests continued to pass unchanged.

**One material difference found, and it's a correction, not a
regression:** `withdrawal_taxable`/`withdrawal` in the per-year table now
correctly attribute money that flows through the taxable bucket because of
a life event, in both directions:
  - A positive life event (e.g. an asset sale) that fully covers a year's
    need used to still show up as a "$X drawn from taxable" even though
    the money was never really drawn from the account — it was injected
    and then immediately reported as spent-from-taxable by the waterfall's
    bookkeeping. Now it's correctly reported as 0 draw, because the shared
    engine treats life-event cash as offsetting need directly (same as
    guaranteed income), before any bucket is touched.
  - A negative life event (a one-time cost) used to be subtracted directly
    from the taxable balance *before* the waterfall ran, so the resulting
    drop in the bucket was invisible in `withdrawal_taxable` — the number
    went down with no line item saying why. Now that cost correctly shows
    up as an explicit taxable draw.

In every scenario tested, `taxable_balance`, `portfolio_balance`,
`unmet_need`, and `on_track` were byte-for-byte identical before and
after — this is a labeling/attribution fix inside the waterfall's own
reporting, not a change to any dollar figure a user's plan depends on.
Regression tests: `TestWithdrawalWaterfallMigratedToSharedAnnualEngine` in
`tests/test_projection_engine.py`.

### `_run_single` (Monte Carlo + Stress Tests) → `annual_engine.simulate_withdrawal_year`

Same migration, same verification approach
(`tools/capture_simulation_golden.py`) across 6 synthetic scenarios x 2
consumers (Monte Carlo, Stress Tests, both seeded/deterministic). Result:
**zero material differences, bit-for-bit** — this function only returns
aggregate bucket/portfolio balances, so it never had a per-bucket
`withdrawal_taxable`-style field to show the reporting-only difference
found in `run_retirement_projection`.

### `run_swr_analysis`'s inner loop — attempted, reverted, documented as a deliberate exception

`run_swr_analysis`'s `success_at_withdrawal()` closure was migrated onto
`simulate_withdrawal_year` following the same pattern (reconstructing
`spending_need = guaranteed + portfolio_draw` and folding `event_monthly`
into `guaranteed_income`, since SWR's need is expressed as "how much
beyond guaranteed income" rather than the income+healthcare figure every
other consumer uses — see section 3.5 above). It passed every existing
SWR test with identical results. It was **reverted** after measuring
performance: `test_simulation_engine.py`'s SWR-tagged tests went from
26.83s to 57.41s (>2x) with the migration in place.

This loop is a legitimate outlier, not a rationalization to skip
consolidation generally: `success_at_withdrawal` runs inside a binary
search (up to 12 bracket-expansion + 20 bisection calls =~32 calls) over
N=1000 simulated trials over ~40 years each — up to ~1.28M simulated
years for a single SWR request, an order of magnitude more than any other
consumer's hot path. The shared engine's per-year dataclass/dict
allocations and closure construction, cheap everywhere else in this
codebase, are not cheap at that call volume.

**What's still shared, even without going through
`simulate_withdrawal_year`:** the tax-rate pricing
(`_pretax_marginal_tax_rate`) and the gross-up arithmetic
(`_grossed_up_draw`) are the exact same module-level helpers `_run_single`
uses — the *formulas* are shared and identically maintained in one place;
only the per-year orchestration (opening→draws→growth→closing as one
function call) is duplicated here, for a measured, documented reason. If
`success_at_withdrawal`'s call volume is ever reduced (e.g. a smarter
search that needs fewer calls, or N reduced), this exception should be
revisited — it is a performance trade-off, not a permanent architectural
stance.

### `run_tax_efficiency_simulation`'s ordered strategies — same exception, mitigated with a parity-tested shared helper

`run_tax_efficiency_simulation`'s `taxable_first`/`roth_first` strategies
(its `optimal` strategy's LTCG-threshold logic is a genuinely different
policy, not attempted here) were migrated onto `simulate_withdrawal_year`
the same way `_run_single` and `run_retirement_projection` were, and
measured: **~2.9x slower** (0.084s -> 0.242s per call) at 1000 trials x
~35 years x 2 of 3 strategies — worse than SWR's already-documented >2x,
for the same reason (call volume where the shared engine's per-year
dataclass/dict/`Transfer`-list allocations stop being free). Reverted for
the same reason SWR's migration was.

Unlike SWR, this exception ships with a mitigation instead of just a
duplicated implementation: `taxable_first`/`roth_first`'s per-year draw
logic was extracted into `simulation_engine._ordered_draw` — a pure
four-floats-in/four-floats-out function, order-driven so both strategies
share it instead of two independently-copy-pasted blocks, with zero
dataclass/dict overhead (confirmed negligible: 0.084s -> 0.090s per call,
~7%, an acceptable cost for de-duplicating the two strategies into one
function). `tests/test_tax_efficiency_engine_parity.py` proves this fast
path is byte-identical to `simulate_withdrawal_year` for the same inputs
across 6 cases (ample funds, an exhausted first bucket spilling to the
next, every bucket exhausted with real unmet need, a zero-need no-op, an
HSA-only remainder, and fractional-dollar amounts) x both strategies (12
tests total) — so a future drift between the fast path and the engine's
semantics fails a test before it reaches a user, even though the two
aren't literally the same code path. The `optimal` strategy remains
fully independent (no shared helper) since its LTCG-threshold behavior
doesn't fit the order-driven shape at all.

If `_ordered_draw`'s and `simulate_withdrawal_year`'s per-bucket
gross-up/tax formulas are ever changed, both need to change together —
the parity test will catch a missed one, but it won't catch a "changed
both the same wrong way" mistake, since it only proves equivalence
between the two implementations, not correctness against first
principles (that's `test_annual_engine_reference.py`'s job for the
engine itself).

## 5. Phase 5 — Education/Kids consolidation: scoped, not the whole thing

`run_education_projection` and `run_kids_projection` (both
projection_engine.py) duplicate three distinct pieces of math: (1) the
529 saving-phase projection to the moment college/18 starts, (2) the
college-years drawdown (annual cost inflation, contributions that may
continue into college, a SECURE 2.0 529→Roth rollover capped at
$35,000), and (3) a full account-by-account timeline out to age 60/22
(Kids only — Education has no equivalent, it stops after college).

**Only (1) was consolidated**, into `_project_529_saving_phase`, used by
both functions. This was the actual literal duplication — both
functions independently computed the exact same "starting balance grows
for N years, monthly contributions compound for as many of those years
as they're actually active (capped by the college date or the parent's
retirement, whichever binds), then the accumulated sum sits and
compounds untouched for whatever's left" formula, already proven to
agree by `TestEducationAndKidsProjectionsAgreeOn529AtCollege` (a
pre-existing regression test from an external audit that caught
`run_kids_projection` ignoring the parent-retirement cutoff entirely).
That pre-existing agreement is what made the extraction safe: the new
function had to reproduce two independently-audited implementations
exactly, not invent new behavior — verified via a 21-scenario golden
diff (7 input variants × `run_education_projection` ×
`run_education_projection(continue_contributions_during_college=True)` ×
`run_kids_projection`) showing **zero differences**, plus new tests in
`TestSharedSavingPhaseHelper` (test_projection_engine.py) that pin the
helper's behavior independently of either consumer.

**(2) was consolidated 2026-09-07, (3) remains deliberately independent.**
Re-reading both functions closely turned up a real piece of (2) that
Phase 5 missed: the college-years drawdown loop itself — same
`COLLEGE_COST_INFLATION`/`UNL_CURRENT_ANNUAL`-driven cost formula, same
compound-then-subtract-cost recurrence over `COLLEGE_YEARS` — was
copy-pasted in both functions, differing only in two behavioral flags
(Education tracks an unclamped "worst deficit" for its funding-gap
figure; Education can optionally continue contributions into college).
Extracted into `_project_college_drawdown` (both flags as parameters),
verified against both existing (already audit-hardened)
implementations via a 384-scenario golden diff — varying kid ages,
parent age (including past the contribution cutoff), 529 balances,
contribution amounts, and `continue_contributions_during_college` —
**byte-identical, zero differences**, the same discipline that made (1)
safe in the first place. See
`tests/test_projection_engine.py::TestSharedCollegeDrawdownHelper` (7
tests pinning the helper independently of either consumer).

**(3) — Kids' age-60 account timeline — remains deliberately
independent**, unchanged from the original reasoning: dense,
already-audit-hardened, off-by-one-sensitive code (the file's own
comments document several previously-shipped bugs in exactly this kind
of timing/boundary logic — a rollover double-counted in two accounts at
once, a timeline off by one year of compounding, a headline number that
disagreed with its own chart by a factor of the growth rate) with no
Education-side equivalent at all to unify with — Education's chart
stops after college, it has no age-60 concept. Forcing this into a
shared shape would mean inventing an abstraction that doesn't already
exist in proven, agreed-upon behavior on both sides — the opposite of
what made (1) and (2) safe. This matches Jason's own prior decision to
leave adjacent Kids/Education work (>2-kids support) out of scope given
how bug-hardened this pair of functions already is. Revisit only if a
real bug (not a duplication-for-its-own-sake concern) is found in the
timeline logic specifically.

## 6. Independent review findings (2026-09-07) — fixed before merge

An independent review of commit `854855f` (synthetic inputs only, no
application changes) found 4 real issues in the second session's Phase 4
work, all fixed. Recorded here because they're exactly the kind of
mistake this consolidation is supposed to make less likely, not more —
worth being honest that this pass didn't avoid it the first time:

1. **`run_roth_conversion_analysis`'s migration silently stopped funding
   spending from Roth.** The order was set to `("taxable", "pretax")`
   with a comment claiming "this tool has never modeled Roth spending" —
   false. The pre-migration formula explicitly spilled any shortfall into
   Roth once pretax couldn't cover both the year's draw and the
   conversion. Fixed: `order=("taxable", "pretax", "roth")`; `unmet_need`
   is now surfaced per schedule row and as `total_unmet_need`/
   `any_unmet_need` in the summary instead of silently dropped. Also
   applied to the "without conversions" baseline loop (which never
   modeled Roth spending even before this session), for internal
   consistency between the two paths being compared.
2. **Conversion benefit overstated by one year of growth.** `roth_fv_at_73`
   used `RMD_START_AGE - age` as its compounding exponent, but
   `simulate_conversion` layers the conversion on top of an ALREADY-grown
   year — the converted amount already represents its value at the END
   of year `age`. Fixed: `RMD_START_AGE - age - 1`.
3. **`run_tax_efficiency_simulation`'s shared per-year setup (not the
   Phase 4 `_ordered_draw` extraction itself, but the surrounding
   orchestration none of this session's changes touched) unconditionally
   credited/debited signed life-event cash to `taxable` without folding
   it into the spending-need calculation** — a negative one-time expense
   with `taxable` at or near zero drove the bucket negative with nothing
   tracking the resulting deficit, silently erased by the year's final
   `max(0, taxable*(1+ret))` floor. This predates the consolidation
   (flagged by the reviewer as "a remaining defect, not a new
   regression"). Fixed to match the cash-available-offsets-need
   convention every migrated consumer already uses.
4. **`AnnualResult.reconcile()` only verified the aggregate total, not
   individual buckets** — a bug (or a hand-corrupted `AnnualResult`) that
   moved money between two buckets without recording a matching
   `Transfer` passed silently as long as the grand total was still
   right. Fixed: `simulate_conversion` now records its tax-funding debits
   as `Transfer`s too (to a `"tax"` sink), and `reconcile()` verifies
   each bucket's closing balance against its own opening + growth -
   draws - transfers_out + transfers_in (plus taxable's two non-transfer
   credits, the surplus sweep and RMD reinvestment, both already derivable
   from existing fields). Regression tests:
   `test_reconcile_flags_an_unrecorded_transfer_between_buckets` in
   `test_annual_engine_reference.py`.

All four fixes are covered by dedicated regression tests reproducing the
review's exact numbers (`TestRunRothConversionAnalysis` /
`TestRunTaxEfficiencySimulation` in `test_simulation_engine.py`), plus a
Social Security claiming-age/amount cross-tool check added to Phase 6's
sweep (the review noted it checked pension consistency but not SS
timing). Full suite re-verified green after all fixes.

## 7. Independent review, second follow-up (2026-09-07) — 2 more real gaps, fixed

A second look at the section-6 fixes found two more real issues, both
confirmed and fixed:

1. **`run_tax_efficiency_simulation` discarded recurring income above
   spending.** The section-6 fix for the negative-one-time-event bug
   introduced `spending_target = max(0, year_need - life_event_monthly)`
   — flooring at 0, which silently discarded any RECURRING income above
   the ordinary spending need instead of banking the excess as savings,
   the same class of bug as the one-time-event fix but in the opposite
   direction. Reproduced exactly: $1M pretax-only, $0 ordinary spending,
   a $1,000/mo recurring income over a 2-year horizon ($24,000 total)
   ended at ~$1,000,000 (the fixed floor discarded the recurring income
   entirely) instead of the correct $1,024,000. Fixed by extracting the
   per-year cash-flow arithmetic into
   `simulation_engine._cash_available_offsets_need` — `spending_target`
   is no longer floored, matching `annual_engine.simulate_withdrawal_year`'s
   own convention exactly (it never floors `spending_need` either). A
   72-case parity matrix (ordinary spending × pension × signed one-time
   events × signed recurring events × both draw orders) in
   `test_tax_efficiency_engine_parity.py` now covers the FULL per-year
   step against the shared engine, not just `_ordered_draw`'s bucket
   mechanics in isolation — closing the exact gap the reviewer's own
   72-comparison matrix found (48 matches / 24 mismatches, all in the
   recurring-income-above-spending cases).
2. **Monte Carlo/Stress Tests still lose Social Security COLA accrued
   before retirement.** `_run_single`'s cumulative-inflation SS formula
   clamps a claim-year index to 0 whenever the claim age precedes
   `ret_age` (e.g. claim at 62, retire at 67) — dropping every year of
   COLA that accrued between claiming and retirement entirely, unlike
   the deterministic `(1+inflation)**max(0,age-jason_ss_age)` formula
   every OTHER consumer (SWR, Roth conversion, tax-efficiency, survivor)
   already gets right for the identical inputs. Reproduced exactly:
   claim at 62 / retire at 67 / 3% inflation / $30,000 SS input —
   `run_retirement_projection` correctly reports $34,778 of SS; Monte
   Carlo with deterministic (0%) simulated returns reported $30,000
   (implying zero pre-retirement COLA). Fixed by computing each person's
   pre-retirement COLA deterministically (`(1+inflation)**max(0,
   ret_age-claim_age)`, same flat rate every other consumer uses — this
   function's per-trial stress/Monte-Carlo inflation variation only
   ever applied to the WITHDRAWAL horizon, never modeled a stochastic
   pre-retirement path) and multiplying it into the existing per-trial
   cumulative-inflation formula for the retirement-period leg. Reduces
   to the original formula exactly whenever a claim age is during/after
   retirement. The Phase 6 SS-timing check added in section 6 retired
   past both claiming ages and never exercised this — the reviewer's own
   observation ("the new SS test compares Retirement with Roth... does
   not call Monte Carlo or Stress") — so a second Phase 6 test
   (`test_social_security_pre_retirement_cola_reaches_monte_carlo_and_stress`)
   was added specifically retiring AFTER an early SS claim, checked with
   deterministic returns against `run_retirement_projection`'s own
   figure for an exact match.

## 8. Independent review, third follow-up (2026-09-07) — the past-ret_age timeline gap, fixed everywhere

`run_retirement_projection` (projection_engine.py) has anchored its own
withdrawal-phase timeline to `withdrawal_start_age = max(ret_age,
jason_age)` since an earlier session — a household selecting an
already-past retirement age (e.g. a "what if I'd retired at 55"
sensitivity column while actually 65 today) simulates forward from its
real current age, not a nominal age years behind it. That fix was never
propagated to any OTHER withdrawal-phase consumer in simulation_engine.py
— `_run_single` (Monte Carlo + Stress Tests), `run_swr_analysis`,
`run_roth_conversion_analysis`, and `run_tax_efficiency_simulation` all
still used raw `ret_age` for their own `retire_yrs`/age-progression math.
Reproduced exactly (both spouses currently 65, selecting ret_age 55, end
age 69, $2M taxable-only, $100K/yr spending, zero returns/inflation/
pension/SS/healthcare/contributions — deterministic returns):

| Consumer | Before | After |
|---|---|---|
| Main Retirement (reference, already correct) | 4 spending years, $1.6M ending | unchanged |
| Monte Carlo | 14 spending years, $600K ending | 4 years, $1.6M — matches Main |
| Stress base case | same 14-year error, chart starts at 55 | matches Main; chart starts at 65 |
| SWR | $142,857/yr vs. $500,000/yr for the current-age selection | both selections agree |
| Tax-efficiency (taxable-first) | $352,941 vs. $1,529,412 for the current-age selection | both selections agree exactly |
| Roth conversion schedule | starts at age 55 | starts at age 65 (the real current age) |

**Fix:** every affected function now computes its own `withdrawal_start_age
= max(ret_age, jason_age)` (mirroring `run_retirement_projection`'s
existing convention exactly) and uses it — not raw `ret_age` — for every
forward-looking timeline computation: `retire_yrs`/`end_age`, the
per-year `age` loop variable, healthcare pre-retirement inflation, SS
pre-retirement COLA (the section-7 fix's own formula also needed this —
it used raw `ret_age` for the pre-retirement-COLA exponent, which is
wrong in exactly this same past-ret_age case), the Roth conversion
window (`conversion_years`), and every reported chart/depletion-age/
lowest-balance-age label. `ret_age` itself is deliberately preserved
unchanged everywhere it represents a genuine POLICY selection rather
than a timeline: `pension_for_age(inputs, ret_age)`, the age-55 bridge-
job/kids-at-home branch condition, and the top-level `"retirement_age"`
field callers report back (what was selected, not the effective
simulation start).

**Explicitly out of scope, flagged rather than silently left alone:**
`_post_retirement_asset_sale_events`'s own inclusion check
(`sale_age <= ret_age`) has this same class of edge case for a sale
scheduled between the past ret_age and the real current age — but
`run_retirement_projection` itself (the reference implementation this
fix mirrors) has the identical gap in its own accumulation-phase asset-
sale logic, so fixing it here alone would create a NEW inconsistency
with the reference rather than close one. Not touched; revisit
alongside `run_retirement_projection`'s own asset-sale timing if this
ever becomes a real reported issue (it wasn't part of the review's
reproduction, which used no asset sales). `run_survivor_scenario`'s
`death_age = ret_age + 10` default has a related shape (also not part
of the review's finding) — also not touched.

Regression tests: `TestPastRetirementAgeTimelineConsistency` in
`test_cross_tool_reconciliation.py` — reproduces the review's exact
scenario across all 6 consumers, asserting the past-age and current-age
selections now produce identical results (not just plausible-looking
ones), with the review's own reported current-age figures pinned so a
regression changes a number, not just an equality.

## 9. Shared timeline normalizer (`timeline_engine.py`) and the duplicate-formula inventory

Per an explicit consolidation task item (2026-09-07): centralize
effective start age, retirement year, end age, spouse age offsets,
claiming years, inflation index, and event years into one shared module
instead of six independently-maintained copies of the same arithmetic.

### What moved

`timeline_engine.Timeline` / `build_timeline()` is now the single source
of `effective_start_age = max(ret_age, jason_age)` and everything
derived from it, used by `run_retirement_projection` (the reference
implementation this was modeled on — migrated too, not left as a 6th
independent copy), `_run_single` (Monte Carlo + Stress Tests),
`run_swr_analysis`, `run_roth_conversion_analysis`,
`run_tax_efficiency_simulation`, and `run_survivor_scenario`.
`build_cumulative_inflation()` is the true cumulative-inflation-index
formula (extracted from `_run_single`, Phase 4) for the one consumer
whose inflation rate varies year to year (stress scenarios).
`Timeline.age()`/`.justin_age_at()`/`.calendar_year()`/
`.claim_year_index()`/`.pre_start_cola()` replace the per-year age/
age-gap/claim-year formulas that previously appeared verbatim in every
withdrawal loop in this file.

`ret_age` itself (as opposed to `effective_start_age`) is deliberately
NOT centralized — `pension_for_age(inputs, ret_age)`, the age-55
bridge-job/kids-at-home branch, and every "what age did the user pick"
reporting field are genuine per-consumer POLICY choices, not a timeline
question, and stay with each caller exactly as before.

### The two adjacent cases — resolved by correcting the reference, not matching it

Both were flagged as "documented adjacent cases" after the past-ret_age
timeline fix (section 8) and explicitly required to be fixed by
correcting whichever implementation was wrong, including the reference,
rather than treating agreement-with-a-known-bug as acceptable:

1. **Asset-sale timing gap.** `_post_retirement_asset_sale_events`'s
   exclusion check and `run_retirement_projection`'s own accumulation-
   phase inclusion check both compared `sale_age` against the same raw
   `ret_age`, which is correct only when `ret_age >= jason_age`. For a
   sale dated between an already-past selected `ret_age` and the
   household's real current age (`effective_start_age`) — e.g. `ret_age`
   55, sale at 60, household actually 65 today — the accumulation-phase
   check (`sale_age <= ret_age`, i.e. `60 <= 55`) evaluated FALSE, so the
   sale was never compounded through the accumulation phase at all; the
   withdrawal-phase function's exclusion check (also `sale_age <=
   ret_age`) likewise evaluated false, so it wasn't skipped there either
   — the sale landed exactly once, but as a same-day cash windfall dated
   "today" (`CURRENT_YEAR + max(0, sale_age - jason_age)`, which
   collapses to `CURRENT_YEAR` once the sale is in the past) with ZERO
   years of growth applied for the years between the actual sale and
   today. Not a double-count or a silent drop — a single count with the
   wrong amount of compounding, invisible whenever the pre-retirement
   return happens to be 0% (exactly why an isolated review of the
   timeline fix alone, using a 0%-return reproduction, wouldn't have
   surfaced this). Both checks now compare against `effective_start_age`,
   and the accumulation phase's growth-years formula is generalized to
   `effective_start_age - sale_age` (previously `years_to_retire -
   yrs_asset1`, silently 0 whenever `ret_age < jason_age`). Reduces to
   the exact original formula whenever `ret_age >= jason_age`. Verified
   with a realistic nonzero pre-retirement return (the test fixture's
   7%): a $500K sale dated between ret_age 55 and real current age 65
   now produces byte-identical `taxable_at_retirement` to selecting
   ret_age 65 directly, including the 5 years of compounding the old
   code silently omitted.
2. **Survivor scenario's `death_age` default.** `death_age = ret_age +
   10` (meant as "10 years into retirement") could default to a death
   age at or before the household's real current age for a past-ret_age
   selection — modeling the household as already dead rather than dying
   10 years from now. Anchored to `effective_start_age + 10` instead.

### Duplicate-formula inventory (item 9)

What's now fully shared, one implementation each: pension interpolation
(`pension_for_age`), RMD amount and start age (`_rmd`,
`rmd_start_age`), the withdrawal-year ledger itself
(`annual_engine.simulate_withdrawal_year`/`simulate_conversion`) for the
3 consumers migrated onto it, the timeline fields this section covers,
and (Phase 4) tax-efficiency's ordered-draw bucket mechanics
(`_ordered_draw`, parity-tested against the shared engine).

What remains duplicated, with reasoning for each:

- **Healthcare pre/post-Medicare phasing** (`healthcare_pre if age < 65
  else healthcare_post`, or the equivalent) still appears independently
  in `run_retirement_projection`, `_run_single`, `run_swr_analysis`,
  `run_roth_conversion_analysis`, and `run_tax_efficiency_simulation` —
  five copies of a two-line conditional. Not consolidated this pass;
  a real remaining item, low-risk/low-complexity to extract into a
  `timeline_engine.healthcare_for_age(age, pre, post)`-style helper
  whenever this module gets touched again.
- **Account buckets as four plain floats** (`pretax, roth, taxable, hsa`
  locals) instead of `annual_engine.AccountState` in `run_swr_analysis`,
  `run_roth_conversion_analysis`, `run_tax_efficiency_simulation`'s
  `_ordered_draw` path, and `run_survivor_scenario`'s single-bucket
  model — the same measured, documented performance exception as
  tax-efficiency's `_ordered_draw` (section 4: migrating even part of
  tax-efficiency onto the dataclass-based engine measured ~2.9x slower
  at its call volume). Not a formula duplicate in the sense of "could
  silently drift" — each consumer's own arithmetic is either parity-
  tested against the shared engine already (tax-efficiency) or a
  genuinely different policy (SWR/Roth/survivor, per CALCULATION_
  CONTRACT.md's existing material-assumptions list) — but flagged here
  as the literal duplicate-representation item 9 asked to inventory.
- **SS COLA formula**: two mathematically-equivalent forms coexist by
  necessity, not oversight — the flat deterministic
  `(1+inflation)**max(0,age-claim_age)` (SWR, Roth, tax-efficiency,
  `run_retirement_projection`) and the cumulative-ratio form via
  `Timeline.pre_start_cola`/`claim_year_index` (`_run_single` only,
  since it's the one consumer whose inflation rate varies year to year
  across the horizon — see section 3.5's material assumption). The flat
  form is provably a special case of the cumulative form when the rate
  is constant; duplicating the simpler form where the general one isn't
  needed is a legibility choice, not a drift risk, since both are now
  expressed through the same `Timeline` object's fields.

## 10. Item 4 (optimal strategy), item 3 (SWR), item 5 (Roth reconciliation) — 2026-09-07

### Item 4 — `run_tax_efficiency_simulation`'s "optimal" strategy

Extracted into `_optimal_draw` (simulation_engine.py) — same pattern as
`_ordered_draw`, same measured-performance reason for not calling
`simulate_withdrawal_year` directly. 7 parity tests in
`test_tax_efficiency_engine_parity.py` decompose its 3-phase policy
(taxable up to the 0% LTCG threshold, then pretax/roth/hsa, then any
taxable remainder above the threshold) into 3 chained
`simulate_withdrawal_year` calls, proving the shared gross-up/shortfall
conventions hold at every phase even though the threshold policy itself
is genuinely distinct from every other consumer's.

### Item 3 — `run_swr_analysis`

Full engine migration remains reverted (measured >2x slower, unchanged
from the original exception). What's new: the per-year step is now
`_swr_year_step`, a standalone function (previously inline in a closure,
untestable in isolation), parity-tested against
`simulate_withdrawal_year` three ways per item 3's own explicit ask:

1. 8 hand-picked cases covering the documented edge behavior (a negative
   one-time event exceeding taxable, a positive recurring event
   exceeding the draw, RMD fully/partially covering need, every bucket
   exhausted).
2. **200 randomized annual cases** (fixed seed for reproducibility) —
   balances, draw amount, signed events, RMD, and tax rate all drawn
   from wide ranges.
3. **A full 20-year chained scenario** — growth, an RMD phase-in, and a
   mid-plan life event, comparing running balances at every year (not
   just single-year snapshots, which could miss a compounding drift bug
   that only shows up after several years).

All 209 pass. SWR's one real material assumption (spending is drawn ON
TOP of guaranteed income, never netted against it — section 3.5) is
preserved exactly: `guaranteed_income=0.0` in every parity comparison,
matching the fast path's own behavior, not an approximation of it.

### Item 5 — Roth conversion reconciliation

`tests/test_roth_conversion_reconciliation.py`, independent of the
existing per-bug regression tests in `test_simulation_engine.py`:

- **Cohort growth** (36-case matrix: retirement ages 60/67/74 × returns
  0%/3%/10% × pretax $400K/$1M × taxable $0/$300K): the sum of every
  year's `roth_fv_at_73` (each cohort's own projected value at RMD age)
  plus the untouched starting Roth balance's own growth must equal the
  actual `roth_at_rmd_age_with_conversion`, to rounding. Holds precisely
  when Roth is never drawn for spending along the way (zero starting
  Roth, zero ordinary spending need) — the same condition used to
  discover it holds at all; a household whose Roth-spending-fallback
  fires in some year will show a real, non-bug gap between the two
  figures, since `roth_fv_at_73` deliberately answers "what will THIS
  conversion be worth if never touched again," not "what will the whole
  account be worth" once later draws are possible.
- **Matched with/without pair**: forcing conversion room to zero
  (guaranteed income alone fills the 22% bracket) makes the "with
  conversions" path's own pretax trajectory identical to the "without
  conversions" baseline's — proving the two loops genuinely differ only
  in conversion policy, not in some other silently-diverged mechanic.
- **RMD impact**: real conversions measurably lower both
  `estimated_rmd_with_conversions` and `pretax_at_rmd_age_with_conversion`
  relative to the no-conversion baseline.
- **Tax funding**: the full converted amount lands in Roth when taxable
  can afford the tax bill (not conversion-minus-its-own-tax).
- **Unmet spending**: zero for a well-funded household, at both the
  per-row and summary level (already covered for the depleted case by
  an existing regression test — see section 6).

## 11. Independent review, fourth follow-up (2026-09-07) — two more real bugs, both on merged `main`

The branch was merged to `main` after the third follow-up. A further
independent review of the merged code found two more real bugs — both
in fixes from that same third pass, both fixed here directly on `main`
(no branch — this repo has no PR process).

### P1 — Asset-sale growth-years fix credited historical investment returns

Section 9's asset-sale timing fix changed the growth-years exponent to
`effective_start_age - sale_age` directly. That's correct ONLY when the
sale is at or after `jason_age` (nothing to clamp) — for a sale that
predates `jason_age`, it silently credited investment RETURN for
calendar years already in the past, on top of the years actually
remaining before the effective retirement start. Reproduced exactly:
age 65 today, retiring at 70, a sale at 60 with 10% pre-retirement
returns — the fix added **$259,374** (10 years of compounding, `70-60`)
instead of the correct **$161,051** (5 real remaining accumulation
years, `70-65`). The prior regression test for this fix only ever
compared two calls using the SAME (buggy) formula against each other
(a past-ret_age selection vs. selecting the real current age directly),
so it could not have caught an overstatement present in both sides of
that comparison equally.

**Fixed formula:** `yrs_to_grow = max(0, effective_start_age - jason_age)
- max(0, sale_age - jason_age)` — years from TODAY to the effective
retirement start, minus years from today to the sale (0 if the sale
already happened). This reduces to the untouched pre-independent-review
formula (`years_to_retire - yrs_assetN`) exactly whenever `ret_age >=
jason_age`, and gives 0 growth years (proceeds added at face value, per
the existing inclusion check — not silently dropped) for a sale that's
already behind "today" even in the past-ret_age case, rather than
inventing a historical-reconstruction policy this app has no data to
support. Verified with 10 hand-calculated cases across both assets,
past/current/future sale dates, and past/current/future retirement
selections (`TestAssetSaleGrowthYearsHandCalculated`, including a
5-combination cross-check against the original formula computed
independently in the test, not imported from the implementation).

### P2 — Survivor `death_age` default computed in the wrong person's age terms

Section 8's survivor `death_age` default fix computed `effective_
start_age + 10` in JASON's age terms unconditionally — but the very next
lines of `run_survivor_scenario` (`death_jason_age = death_age if
deceased == "jason" else death_age + age_gap`) treat the incoming
`death_age` value as already being in the DECEASED person's own age
terms whenever `deceased != "jason"`. Reproduced exactly: Jason 65,
Justin 55, requested retirement 55 (past), `deceased="justin"` — the
broken default (75) was reported as "Justin dies at 75" but actually
indexed to Jason's age 85 (twenty years from today, not the intended
ten), giving a **$790,000** baseline death-year balance in a $1M
taxable-only / $10K-yr-spend / 0%-everything scenario instead of the
correct **$890,000** (Justin's own correctly-computed default death age
of 65).

**Fixed:** the default now computes the deceased person's own effective-
start-age (`timeline.effective_start_age` for Jason,
`timeline.justin_age_at(timeline.effective_start_age)` for Justin) before
adding 10 — matching the age-coordinate system the rest of the function
already expects. Reduces to the exact prior formula when `deceased ==
"jason"`. New regression test:
`test_default_death_age_for_justin_uses_justins_own_age_terms`.

**Frontend gap, found in the same review:** `StressTestWhatIf.jsx`'s
Survivor Scenario form initialized `deathAge` as `retAge + 10` — the
same class of bug the backend default had before ITS OWN third-follow-up
fix — and always sent it explicitly to the API, so the backend's
corrected default was never actually reachable through the normal UI
flow (this frontend gap predates the backend fixes; it was simply never
wired to whatever the backend computed). Fixed: the form now fetches the
household's ages and recomputes the SAME default the backend computes
(mirrored in JS, not re-derived independently) whenever the retirement
age or selected deceased spouse changes — until the user deliberately
edits the field, at which point their typed value is preserved across
later changes. New test file `SurvivorScenario.test.jsx` (4 tests)
verifies the actual request payload for both spouses, an uneven spousal
age gap, and that a manual edit survives switching who dies first.

## 12. Item 2 (shared annual-input builder) — closed, consolidation complete

`annual_inputs.py`'s `build_annual_income_inputs()` (see its own module
docstring for full detail) generalizes the SS/healthcare/life-event
per-year income calculation that item 9's inventory found duplicated
across every withdrawal-phase consumer — the same class of bug
independent review caught three separate times (the 2026-09-06 Justin
age-gap bug, the 2026-09-07 past-ret-age timeline bug, and this
module's own `justin_ss` default-age bug in `run_survivor_scenario`).
Migrated into `run_retirement_projection`, `_run_single` (Monte
Carlo/Stress), and `run_roth_conversion_analysis` (both the
with-conversion and without-conversion comparison loops); documented in
`tests/test_annual_inputs.py` (22 tests: hand-calculated reference
values, a variable-inflation divergence-from-the-old-shortcut proof, a
signed-life-event suite, two multi-year integration tests, and explicit
parity tests naming the two performance exceptions below by their own
test class).

**Two consumers remain deliberately independent, unchanged from section
4/10's existing exceptions** — `run_swr_analysis`'s inner loop and
`run_tax_efficiency_simulation`'s ordered strategies both still inline
the same SS/healthcare formula this module now owns, for the same
measured performance reason (>2x and ~2.9x slower on full-engine
migration, both loops running N=1000 Monte Carlo trials x retire_yrs).
Both inline copies carry a comment pointing here and to their own
parity tests rather than to a nonexistent docstring section (fixed
2026-09-07 -- a fourth review pass found the comments pointed at
`annual_engine.py`'s module docstring, which never mentions SWR or a
fast path at all).

**A related dead-store also fixed this pass:** `_run_single`'s
`hc_this_year` was computed unconditionally every loop iteration via
`healthcare_for_age()` before the shared builder was even called, but
is only ever *read* inside the `ret_age == 55` bridge/kids branch
(which always overwrites it first) -- the non-55 path uses
`income.healthcare` from the shared builder instead. The unconditional
call was pure waste in this file's hottest loop (N=1000 trials x
retire_yrs) with no observable effect on output; removed.

**Roth Conversion planner sanity-checked against real household data
(2026-09-07):** ran `run_roth_conversion_analysis` directly against the
real `cfo.db` (read-only, no API/auth involved) at retirement ages 55,
60, and 65. All three produced internally consistent, sane output --
`total_tax_avoided - total_tax_cost == net_lifetime_benefit` exactly,
`total_unmet_need == 0` in every case, RMDs and pretax/Roth balances
moved in the expected direction under the age-60 conversion schedule,
no negative or NaN figures anywhere in the output. (Real figures from
this check are intentionally not reproduced here -- this doc is
tracked source, not the gitignored `cfo.db`.) No further changes needed
here.

**Status: with item 2 closed, all 9 items from Jason's follow-on list
are now resolved** -- 1, 2, 3, 4, 5, 9 done; 6 and 8 substantially
satisfied by earlier work in the branch; 7 (Education/Kids
drawdown/rollover/custodial/timeline halves) remains the one
deliberately deferred item, per Phase 5's own scope reasoning -- revisit
only if a real bug motivates it, not for consolidation's own sake. This
branch was merged to `main` after the third independent-review
follow-up (section 8); two further review passes since then (section
11's fourth follow-up, and this item-2 closure) were done directly on
`main`, per this repo's no-PR workflow. Full backend suite: 1064
passed. `CONSOLIDATION_HANDOFF.md` is superseded by this section and by
its own closing update -- see that file for the final status.

## 13. Second-earner (Justin) support — independent review, backlog (2026-09-08)

The 2026-09-08 second-earner feature (justin_w2_salary/justin_ret_age/etc.,
section 12's sibling work in projection_engine.py — see
run_retirement_projection's own docstring) was reviewed independently.
Verdict: **the contribution math itself is coherent** — Justin's own
401k/RSU/bonus accumulation, capped at the earlier of his own retirement
or the household's withdrawal start, with correct dormant-compounding
after an early stop, checks out. Seven real gaps were found, all
pre-existing scope limitations rather than bugs in what's actually
implemented — logged here as backlog, not fixed yet:

1. **CLOSED 2026-09-08 — working spouse income during the gap.**
   `justin_w2_salary` used to only create pre-retirement
   contributions/assets. Now: when `justin_ret_age` is set LATER than a
   given scenario's own withdrawal start, Justin's income (net-of-tax at
   the same flat 0.65 approximation this file already uses for RSU/
   bonus — see item 3 below, not a real payroll-tax model) offsets that
   scenario's spending need for exactly the gap years, the same
   mechanism `bridge_income_55` already used for a fixed-duration income
   offset during withdrawal — generalized to any `ret_age` and keyed to
   Justin's own real retirement date. Justin's own contributions still
   cap at the earlier of his own retirement or the household's
   withdrawal start (unchanged, correct — see item 9's reasoning).
   **Reference implementation only (`run_retirement_projection`) — not
   yet propagated** to Monte Carlo/Stress/SWR/Roth conversion/Tax
   efficiency/Survivor, each of which either re-simulates the withdrawal
   phase independently or only reads starting balances
   (`pretax_at_retirement` etc.) rather than the gap-income-adjusted
   later-year need. Verified: `TestJustinGapIncomeOffsetsWithdrawalNeed`
   (4 tests) — zero effect when `justin_ret_age` is unset or ≤ the
   scenario's own retirement, need reduced only during the gap years and
   exactly back to baseline afterward, `projected_surplus` improves on a
   realistic underfunded-without-it fixture.
2. **NOT a symmetric bug — a different, larger ask.** Re-examined during
   item 1's close: the household's withdrawal phase (drawdown/spending
   simulation) only ever starts at Jason's own selected `ret_age` by
   this app's fundamental architecture — there's no "Jason still working,
   drawing down the portfolio because Justin already retired" case to
   fix, because this app doesn't simulate spending *at all* before
   Jason's own retirement, for either spouse (only contribution
   accumulation is modeled pre-retirement — see section 2.3). A true
   symmetric case (either spouse's retirement independently able to
   start the household's withdrawal phase) would mean a two-dimensional
   `jason_ret_age × justin_ret_age` scenario sweep replacing the current
   single-`ret_age` sweep everywhere it appears (every scenario table on
   Retirement Projection, Stress Test, Roth Conversion, etc.) — a real
   product/UX redesign, not a bridge-income trick like item 1's. Left
   open, correctly scoped now instead of conflated with item 1.
3. **Partially addressed — net-of-tax approximation, gap years only.**
   Item 1's gap income uses the same flat 0.65 net-of-tax approximation
   this file already applies to RSU/bonus, so the gap-year figure isn't
   raw gross pay anymore — but this is still not real payroll-tax
   modeling (no brackets, no FICA, no withholding), and it only touches
   the gap years specifically. Pre-retirement, and for the full window a
   scenario's `justin_gap_years` doesn't cover, gross pay/payroll taxes/
   disposable income still never reach `cash_flow_engine.py` or any tax
   calculation. Also clarified: this was never actually Justin-specific
   — Jason's own income doesn't auto-populate cash-flow either; Monthly
   Cash Flow is a fully manual page for both people. Left open for real
   payroll-tax modeling if that becomes a specific ask.
4. **No spouse-specific pension/benefit retirement timing — confirmed
   intentional, now documented as such.** `pension_for_age`'s own
   docstring and the Settings UI (Pension section) now both state
   explicitly: this is one household pension (Jason's employer plan, in
   the default data) with a joint-and-survivor election, not two
   independent pensions — keyed to Jason's `ret_age` by design. If a
   household has a genuinely separate second pension, there's still no
   field for it; that's the remaining real gap, no longer silent.
5. **Aggregated account ownership stays ambiguous.** Justin's
   contributions flow into the same household pretax/Roth totals every
   other consumer already used (consistent with the existing aggregate
   ledger — see section 3's material assumptions), with no owner-specific
   contribution/account rules. Consistent with existing design, but it
   caps survivor/tax/RMD/estate accuracy for a genuine two-earner
   household more than it did for the one-earner case this ledger shape
   was designed around.
6. **CLOSED 2026-09-08 — explicit confirmation required, not just a
   passive warning.** `justin_ret_age = 0` still falls back to Jason's
   scenario `ret_age` (unchanged, correct backward compatibility — see
   section 12), but Settings' `save()` now blocks with a
   `window.confirm()` when `justin_w2_salary > 0` and `justin_ret_age`
   is still 0, spelling out exactly what the fallback means, instead of
   only a passive warning banner elsewhere on the page. Cancel returns
   to editing without saving; confirming proceeds with the fallback as a
   deliberate choice, not a silent default.
7. **CLOSED 2026-09-08 — declared on `PlanningInputs`.** All 6
   second-earner fields now have explicit types/defaults on the Pydantic
   model (matching db.py's own defaults), same mechanism, zero behavior
   change — `extra: allow` + the DB-column whitelist in
   `save_planning_inputs` already accepted them; declaring them just adds
   real request validation and type clarity on top.

**Remaining open: items 2, 3 (payroll-tax modeling specifically), and
5** — items 2 and 5 are real product/architecture redesigns (a
two-dimensional retirement-age sweep; a per-owner ledger), not scheduled
per this doc's standing practice of not building ahead of a specific
need. Item 3's payroll-tax gap is smaller in isolation but only really
worth closing alongside a genuine per-person income/tax model, which is
what items 2/3/5 all ultimately point at.

## 14. Second-earner follow-up review (2026-09-08) — items 1, 2, 3 advanced

A second review of the second-earner feature, after section 13's four
closures, found the item-1 close was real but narrow, and caught a
genuine consistency bug in the gap-income formula itself. Both addressed
directly on `main` (no branch — this repo has no PR process):

1. **Item 1 propagated from 1 of 6 withdrawal-phase consumers to 4 of
   6.** Previously `run_retirement_projection` computed and applied
   `justin_gap_income` entirely inline — Monte Carlo, Stress Tests, and
   Roth Conversion had no way to see it at all, so the same household
   could show a fully-funded Retirement Projection while every other
   planner still modeled the full withdrawal. Fixed by:
   - Extracting the formula into two shared, importable functions in
     `projection_engine.py` — `justin_years_to_retire_for` (the one
     `justin_ret_age`-or-fallback computation every second-earner
     feature depends on) and `justin_gap_income_inputs` (gap years +
     the today's-dollar gap-income figure, net-of-tax, wage-growth-
     escalated — see item 2 below).
   - Adding `justin_gap_years`/`justin_gap_income_at_start`/
     `salary_growth_pct` parameters to `annual_inputs.py`'s
     `build_annual_income_inputs` (the shared per-year income builder
     three consumers already call), returning a new
     `AnnualIncomeInputs.justin_gap_income` field. All default to 0, so
     a caller that never passes them — or a household that never sets
     `justin_ret_age` — sees zero change.
   - Wiring `_run_single` (shared by Monte Carlo + Stress Tests) and
     both of `run_roth_conversion_analysis`'s loops (with-conversion and
     the no-conversion baseline, kept in sync so the comparison stays
     apples-to-apples) to compute these once per call (same precedent as
     `pension_annual`/`income_at_ret`) and subtract
     `income.justin_gap_income` from spending need, same treatment as
     `life_event_monthly` everywhere it already applies.
   - `run_retirement_projection` itself now calls
     `justin_years_to_retire_for`/`justin_gap_income_inputs` too, instead
     of its own separate inline copy — one formula, not two that could
     drift.
   **Still not propagated: `run_swr_analysis`, `run_tax_efficiency_
   simulation` (both documented performance exceptions — full engine
   migration measured too slow, see section 4/10) and `run_survivor_
   scenario` (never migrated onto the shared builder at all — single-
   aggregate-bucket policy, a deliberate divergence per section 3).
   These three remain open, correctly scoped now instead of assumed
   fixed.** Verified: `TestJustinGapIncomeInputsHelper` (4 tests, the
   formula in isolation), plus exact deterministic reconciliation tests
   proving Monte Carlo's and Stress Tests' `median_final_balance`/
   `final_balance` match `run_retirement_projection`'s own figure
   bit-for-bit under zero-variance returns (same idiom as the existing
   SS-COLA reconciliation tests), and a Roth Conversion test showing
   `total_conversions`/`net_lifetime_benefit` both increase with gap
   income present.
2. **CLOSED — gap income was growing with CPI inflation, not wage
   growth.** `justin_gap_income_at_start` used
   `(1 + inflation) ** years_to_retire` (and per-year growth used
   `(1 + inflation) ** yr`), while Justin's own 401k contributions/RSU/
   bonus all grow with `salary_growth_pct` (the assumed-raise
   convention) — two unrelated assumptions conflated into one. Fixed:
   `justin_gap_income_inputs` now takes `salary_growth_pct` and uses it
   exclusively; `inflation` never enters this formula. Defaults to 0
   (flat nominal salary) if no raise assumption is set, matching every
   other `salary_growth_pct` consumer's own default — a household that
   never sets an explicit raise assumption sees gap income held flat in
   nominal dollars, not implicitly inflation-adjusted. Verified:
   `test_uses_salary_growth_pct_not_inflation` proves the figure is
   completely independent of whatever inflation rate the caller passes.
3. **Named constant, still not real payroll-tax modeling.** The three
   independent `0.65` literals for RSU/bonus (pre-retirement
   accumulation) and gap income (withdrawal-phase offset) are now one
   `SECOND_EARNER_NET_OF_TAX_FACTOR` module constant in
   `projection_engine.py`, documented as an explicit, deliberate flat
   approximation — not a real payroll-tax calculation (no brackets, no
   FICA, no filing status, no state tax). Making this a shared payroll/
   tax helper instead of a named flat factor remains open, same
   reasoning as section 13 item 3.
5. **Partially addressed — `justin_gap_income` now its own field in
   `run_retirement_projection`'s `yearly_detail`.** A lower
   `income_need` during the gap years is no longer unexplained — each
   row now also reports `justin_gap_income` directly (same pattern as
   the existing `bridge_income` field). Not yet added to Monte Carlo/
   Stress Tests/Roth Conversion's own output shapes, which don't carry
   this level of per-year detail for other income sources either (e.g.
   `bridge_income` isn't separately exposed there yet either) — a
   broader per-year-output-detail gap, not specific to this feature.

Full backend suite re-verified green after both fixes — see the commit
this section was added in for the exact count.

## 15. Second-earner gap income — full propagation to all 6 consumers (2026-09-08, third pass)

Section 14 propagated gap income to 4 of 6 withdrawal-phase consumers and
left `run_swr_analysis`, `run_tax_efficiency_simulation`, and
`run_survivor_scenario` open. A follow-up request closed all three,
correcting a count discrepancy along the way: an earlier summary said "2
of 6" consumers still missed gap income while actually naming 3
(SWR, Tax Efficiency, Survivor) — the number below is the corrected one.

**A new allocation-free helper, `justin_gap_income_for_year(yr,
justin_gap_years, justin_gap_income_at_start, salary_growth_pct)`**, in
`projection_engine.py` alongside `justin_years_to_retire_for`/
`justin_gap_income_inputs`: pure floats in/out, no object construction,
cheap enough to call inside SWR's binary-search inner loop and Tax
Efficiency's per-trial strategies — the two consumers already documented
as too slow for the shared `annual_engine`/`annual_inputs` machinery
(sections 4/10). `annual_inputs.py`'s `build_annual_income_inputs` now
calls this function too (via a lazy import to avoid a circular import,
same precedent as `post_retirement_year_effects`'s own injection),
instead of keeping a parallel copy of the same one-line formula — every
consumer now reads gap income for a given year through exactly one
function, dataclass-wrapped or not.

**Wired into the remaining 3 consumers, each preserving its own existing
withdrawal/tax policy — no engine migration, no scenario-model
changes:**
- `run_swr_analysis`: gap income is folded into `_swr_year_step`'s
  existing `event_monthly` parameter (no signature change to that
  shared, parity-tested helper) — economically identical to a recurring
  life-event income offset, which this loop already treats that way.
  Verified exact: `test_gap_income_parity_via_swr_year_step` shows
  `_swr_year_step`'s returned `remaining` drops by precisely the
  gap-income amount, all else equal.
- `run_tax_efficiency_simulation`: folded into the existing
  `life_event_monthly` argument of `_cash_available_offsets_need` —
  computed once per year, shared by all 3 strategies
  (taxable_first/roth_first/optimal) before they branch. Verified exact
  the same way (`test_gap_income_parity_via_cash_available_offsets_need`).
- `run_survivor_scenario`: the one genuinely different case — gap income
  only applies when Justin is the SURVIVOR (`deceased != "justin"`), not
  tapered at his own retirement age like every other consumer, but gone
  entirely if Justin is the one who died. `age` in this loop is always
  in Jason-age terms; converted to the same "years since this scenario's
  own withdrawal start" index every other consumer uses via
  `age - timeline.effective_start_age`. Both selection paths tested
  directly: `test_gap_income_applies_when_justin_survives` (deceased=
  "jason", gap income reduces the survivor's draw) and
  `test_gap_income_does_not_apply_when_justin_is_deceased` (deceased=
  "justin", draws identical to a household with no second-earner fields
  configured at all — compared via each year's `draw` specifically, not
  the full schedule, since `starting_balance` legitimately differs
  between those two input sets for an unrelated reason: `justin_w2_salary`/
  `justin_ret_age` also changes the pre-death baseline portfolio via
  `run_retirement_projection`'s own already-closed gap-income offset).

**Item 1 is now fully closed: all 6 of 6 withdrawal-phase consumers see
second-earner gap income.** Tests added (18 total across this pass):
`TestJustinGapIncomeInputsHelper` gained 8 new cases (Justin retiring
before/at/after the scenario's own retirement age; the household's
current age already past the selected retirement age; unequal spouse
ages; the exact yr-equals-justin_gap_years boundary; zero salary; unset
retirement age) plus the pre-existing 4; SWR and Tax Efficiency each got
a directional improvement test and an exact parity test against their
own shared helper function; Survivor got both selection-path tests;
`TestSecondEarnerGapIncomeZeroImpactAcrossAllConsumers` (6 tests) checks
every one of the 6 consumers produces byte-identical output between
plain defaults and explicit `justin_w2_salary=0`/`justin_ret_age=0` —
closing this feature's central promise (opt-in, zero effect otherwise)
for consumers 5 and 6, not just the 4 already covered in section 14.
The exact deterministic Monte Carlo/Stress-Tests-vs-Retirement-Projection
reconciliation tests from section 14 are unchanged and still pass.

**Four larger items remain open, correctly scoped, not silently
dropped** (owner attribution counted explicitly, per the request that
prompted this correction):
1. **The two-dimensional retirement-age redesign** (section 14, item 2)
   — replacing the single `jason_ret_age` scenario sweep with a genuine
   `jason_ret_age × justin_ret_age` sweep everywhere it appears in the
   UI. A real product/UX redesign, not a formula fix.
2. **Real payroll-tax modeling** — `SECOND_EARNER_NET_OF_TAX_FACTOR`
   (0.65) remains a flat, explicit approximation everywhere gap income
   is used, not brackets/FICA/filing-status/state-tax.
3. **Owner-attributed account ledger** (section 13, item 5) — Justin's
   contributions still land in the same pooled pretax/Roth totals every
   consumer already used; no owner-specific RMD/survivor/estate rules.
4. **Per-year output visibility beyond Retirement Projection** (section
   14, item 5) — `justin_gap_income` is its own field in
   `run_retirement_projection`'s `yearly_detail` only. Monte Carlo,
   Stress Tests, SWR, Tax Efficiency, and Survivor Scenario don't expose
   per-year income-source detail for ANY offset (including the
   pre-existing `bridge_income`) — a broader per-year-output-detail gap
   across this file, not specific to gap income.

Full backend/frontend test suite re-verified green (see the commit this
section was added in for the exact count). This work was done on an
isolated branch (`codex/second-earner-gap-income-all-consumers`),
pushed but **not merged to `main`**, per the explicit instruction it was
built under — review before merging.

## 16. Second-earner gap income — output visibility, public-output parity, Survivor timing contract (2026-09-08, fourth pass)

A fourth review of section 15's propagation work found the mechanism was
correct but under-surfaced, and asked for a stronger cross-consumer
regression than the internal-helper parity tests already had. Both
addressed on an isolated branch (per instruction) before merging.

**P1 — gap income now visible in every consumer's public output, not
just applied silently inside the withdrawal math:**
- `run_retirement_projection`: already had `justin_gap_income` per
  `yearly_detail` row (section 15); now also reports
  `second_earner_net_of_tax_factor` per scenario, for transparency (see
  the P2 item below).
- `run_roth_conversion_analysis`: `justin_gap_income` added to each
  `schedule` row (it already computed `income` via the shared builder —
  this was simply never read out into the row), plus
  `second_earner_net_of_tax_factor` at the top level.
- `run_swr_analysis`, `run_monte_carlo`, `run_stress_tests`,
  `run_tax_efficiency_simulation`: none of these have a per-year
  schedule for ANY income source (not even the pre-existing
  `pension_annual`/SS figures get one, except SWR's own steady-state
  summary), so each gets `justin_gap_income_first_year` (the
  today's-dollars-grown-to-withdrawal-start figure) and
  `justin_gap_years` (how many years it lasts) as top-level summary
  fields — plus `second_earner_net_of_tax_factor`, same as the
  per-year-schedule consumers.
- `run_survivor_scenario`: `justin_gap_income` added to each `schedule`
  row (0 whenever `deceased == "justin"`, per section 15's own gating —
  reported as an explicit 0, not omitted), plus
  `second_earner_net_of_tax_factor` at the top level.

**P1 — a genuine public-output-level parity test, not just internal-
helper parity.** `TestSecondEarnerGapIncomePublicOutputParityAcrossAllConsumers`
(2 tests) exercises all 6 consumers against one fixed, deterministic
household:
1. `test_displayed_gap_income_figure_is_identical_across_all_consumers`
   — the exact same $65,000 figure (`100000 * SECOND_EARNER_NET_OF_TAX_FACTOR`,
   flat since `years_to_retire=0` and `salary_growth_pct` defaults to 0
   in this household) must appear, byte-for-byte, in every one of the 6
   consumers' own displayed income field.
2. `test_headline_metric_moves_in_expected_direction_across_all_consumers`
   — each consumer's own headline metric (`projected_surplus`,
   `median_final_balance` ×2, `safe_withdrawal_annual`, each
   strategy's `median_final_balance`, `net_lifetime_benefit`, a
   survivor-schedule `draw`) improves with gap income present vs.
   absent, for the same household. Needed a household sized so neither
   the with- nor without-gap run floors at a fully-depleted $0 balance
   (an earlier draft using `sample_accounts`' own ~$870K under a 30-year
   0%-return horizon did exactly that — both runs hit $0 regardless of
   gap income, which would have silently proven nothing; fixed by using
   a shorter horizon and lower spending in this test's own household,
   not by changing any production code).

**P2 — Survivor's gap-income timing is now an explicit, documented
contract**, not just correct-by-construction: `run_survivor_scenario`'s
own docstring states precisely which years get gap income (based on the
ORIGINAL household withdrawal timeline, `years_to_ret = max(0, ret_age -
jason_age)` — death doesn't shift or reset this window) and which don't
(every year once `deceased == "justin"`, full stop). Four hand-calculated
tests pin this exactly: death before Justin's own retirement (gap
active), death during Justin's working years but landing exactly on his
retirement age (gap ends that very year — the survivor schedule starts
the day after death, so this is the one boundary case worth naming
explicitly), death well after Justin's retirement (gap 0 for the entire
schedule), and a past-selected-`ret_age` scenario (proving the window
anchors to `timeline.effective_start_age`, the household's real current
age, not the stale selection).

**P2 — the net-of-tax approximation's sensitivity is now surfaced, not
just centralized.** `SECOND_EARNER_NET_OF_TAX_FACTOR` (0.65) is used
identically for gap wages, RSU, and bonus despite those having
genuinely different real-world tax/payroll treatment (ordinary income
withholding vs. supplemental-wage withholding vs. capital gains) — this
was already true before this pass and remains a deliberate, simple
approximation, not a new gap. What's new: every consumer's public output
now includes `second_earner_net_of_tax_factor` explicitly, so a caller
(the frontend, or a future audit) can see the exact assumption a
displayed number rests on instead of having to read this source file.
Frontend display of this field is not part of this pass — no UI
currently renders `justin_gap_income`/`justin_gap_income_first_year` at
all yet; that's a separate follow-up once/if these figures are surfaced
in Settings or the relevant planning pages.

**P2 — the two-dimensional retirement-timing limitation is unchanged
and was not started in this pass, per instruction.** Justin's continued
income is fully handled for every scenario where Jason retires first
(sections 15-16); the reverse — modeling Justin retiring first while
Jason keeps working, swept independently in the scenario UI — still
requires the `jason_ret_age × justin_ret_age` redesign described in
section 14, item 2. Restated here because it remains, by a wide margin,
the largest real limitation in this feature area.

Verified: full backend/frontend suite re-run on the isolated branch
before merging (see the commit this section was added in for the exact
count). Branch: `codex/second-earner-output-visibility-and-parity`.

## 17. Survivor insurance-need bug fix; frontend visibility for gap income (2026-09-08, fifth pass)

A fifth review of section 16's closeout found one real bug the earlier
passes' schedule-level checks never exercised, and correctly flagged
that "API visibility" and "app visibility" are different claims.

**P1 — `additional_insurance_needed` never subtracted gap income at
all, independent of everything else this feature already fixed.** The
survivor loop itself (section 15/16) correctly reduces each year's
`draw` by `gap_income_this_year`, and the schedule correctly reports
it — but the separate `additional_insurance_needed`/recommendation
figure used its own, older formula:
`_pv_annuity(net_need, real_rate, years) - starting_balance`, where
`net_need` was a single day-one figure (`income_need_at_death -
guaranteed_day_one`) that never had gap income subtracted from it at
all. A household with real future wages between death and the working
spouse's own retirement reported the IDENTICAL insurance need as a
household with no such income — the schedule and the headline
recommendation were answering two different cash-flow scenarios.
Independent reproduction (both spouses 60, Jason dies at 60, horizon
70, $100K spend/75% survivor factor/0% everything, Justin $100K salary/
retirement 65, $135K starting taxable): old formula reported
**$574,663** regardless of the $65,000/yr × 4 years of real wages;
correct answer is **$315,000**.

**Fixed:** `additional_insurance_needed` now capitalizes the SAME dated
per-year `draw` values the survivor schedule itself computed — pulled
from `schedule` (still the full, unsampled list at this point in the
function; the `[::2]` display sampling only happens at the return
statement) and discounted at the nominal `post_ret` rate, not a
synthetic real/inflation-adjusted rate applied to a single constant
figure. This automatically inherits gap income's exact end date and
`salary_growth_pct` escalation, along with every other year-to-year
variation in need this function already models — there's no longer a
second, independent formula to keep in sync with the loop.
`_pv_annuity` is no longer imported into this function (its only
remaining use here). Verified:
`test_additional_insurance_needed_reflects_gap_income` reproduces the
review's exact numbers ($315,000 fixed vs. a reconstructed $574,663 for
what the old formula would have produced from the same inputs) and
pins the exact $259,663 gap the bug used to silently drop.

**P2 — API-output closeout ≠ app-visible closeout, now actually
closed.** Section 16 added `justin_gap_income`/
`second_earner_net_of_tax_factor` to every consumer's backend response,
but no frontend code read either field — confirmed by the review via a
repo-wide search. Fixed: a small reusable `SecondEarnerNote` component
(`frontend/src/components/SecondEarnerNote.jsx`, renders nothing when
there's no gap to disclose) now appears next to the relevant result on
every page that has one — Monte Carlo/SWR/Roth Conversion (all three
live in `Simulation.jsx`), Stress Tests, Survivor Scenario (gated to
`deceased !== 'justin'`, matching the backend's own gating), and
Retirement Projection (`Retirement.jsx`). **Tax Efficiency has no
frontend page at all** — confirmed via route search, `run_tax_
efficiency_simulation`'s `/api/simulation/tax-efficiency` endpoint is
never called from any file in `frontend/src` — so there's nothing to
wire up there; the backend field stays ready for if/when a consumer of
that route exists. A new frontend test
(`SurvivorScenario.test.jsx`, 2 cases) asserts against the actual
rendered DOM text (`$65,000`, `2 more years`, `65%`) for a mocked
gap-income response, and that the disclosure is absent when Justin is
the deceased spouse — a genuine rendered-output assertion, not just a
check that the mocked data contains the fields.

Verified: full backend/frontend suite re-run on the isolated branch
before merging (see the commit this section was added in for the exact
count). Branch: `codex/second-earner-output-visibility-and-parity`
(same branch as section 16 — these are fixes to that same unmerged
work, not a new branch).

## 18. Survivor insurance minimum-funding fix (timing + signed surplus); explicit gap-duration field; note privacy masking (2026-09-08, sixth pass)

Continued independent review of the section-17 insurance fix found two
further real bugs in the same `additional_insurance_needed` calculation,
plus two frontend P2 findings. All four fixed on the same unmerged
branch (`codex/second-earner-output-visibility-and-parity`).

**P1 — First survivor year discounted by one extra year it shouldn't
be.** Section 17's fix computed
`cap_need = sum(row["draw"] / (1 + post_ret) ** (i + 1) for i, row in
enumerate(schedule))`. The shared annual engine
(`simulate_withdrawal_year`) spends BEFORE applying that year's growth
— so the payout is available at the start of the survivor schedule and
the first year's need must be funded immediately, undiscounted.
Discounting it by one year understated the payout whenever
`post_ret > 0`. Reproduction (both spouses 60, Jason dies at 60,
horizon 62 exclusive — one survivor year — $100K spend/75% factor, zero
assets, 10% post-retirement return): the only year needs $75,000
immediately; the old formula asked for $68,182 (`75000 / 1.10`), which
still left `survives=False` when actually injected as the payout.

**P1 — Flooring `draw` at zero discarded real wage surpluses.** The
same sum used `row["draw"]`, which `max(0, need - guaranteed)` floors
at zero. The simulator itself preserves income above spending as
savings (section 15's shared-engine migration), but the flooring made
that surplus invisible to the capitalization, overstating funding
needed for later years. Reproduction (both spouses 60, ret/death 60,
horizon 64 exclusive — 3 years — $100K spend/75% factor, zero returns/
inflation/assets, Justin $200K salary through his own retirement at
62): death-year baseline already saves $30,000
(`starting_balance_after_payout`); survivor year 61 saves another
$55,000 ($130,000 net wages at the 65% factor − $75,000 spend); years
62-63 each need $75,000 once wages stop. Correct extra funding needed:
$65,000. The old (floored-`draw`) formula reported $120,000, silently
dropping the $55,000 surplus.

**Both fixed together**, per the review's explicit instruction, by
replaying the exact ordered, signed cash flows rather than any flat
sum. A new `_minimum_survivor_funding(net_needs, post_ret)` helper
(module-level, own docstring) performs backward substitution over
`net_needs` — a new list of the UNFLOORED `need - guaranteed` value per
year, collected alongside `schedule` in the same loop:

```python
required = 0.0
for net_need in reversed(net_needs):
    required = max(0.0, net_need + required / (1 + post_ret))
```

The `max(0.0, ...)` at each step is what makes this correct where a
naive full-horizon signed sum would not be: it stops an early surplus
from being spent forward into a later shortfall (money that hasn't
been earned yet can't fund an earlier year), and stops a later
requirement from clawing back an earlier year's already-covered
surplus. Verified by hand against both reproductions above (exactly
$75,000 and $65,000) before writing any test, then by the two new
tests below, then by `test_additional_insurance_needed_reflects_gap_
income` (section 17's own $315,000 case, still passing unchanged since
that scenario has no surplus years).

New tests: `test_additional_insurance_needed_does_not_discount_first_
year` and `test_additional_insurance_needed_credits_wage_surplus_
without_borrowing_from_the_future` (`TestRunSurvivorScenario`). Per the
review's explicit instruction — "add a regression that injects the
recommended payout and verifies spending is actually funded in each
year; handle rounding explicitly" — both tests also re-run the
scenario with `additional_insurance_needed` injected as life insurance
and assert `survives is True`, `depleted_age is None`, and every
schedule row's `ending_balance >= 0`. The rounding note is real: a
minimum-funding recommendation by definition drives the final year's
ending balance to exactly $0, and this function's own depleted-
portfolio check (`bal_after <= 0`) treats an exact zero as "did not
survive" — the same as it would for a truly negative balance. Both
tests inject `additional_insurance_needed + 1` rather than the bare
recommendation, since `round()` can itself underfund by up to $0.50.
This is an inherent property of "minimum funding," not a residual bug
— a household using this number in practice should treat it as a
floor, not round down.

**P2 — Survivor's gap-duration note undercounted years via sampled
schedule rows.** `StressTestWhatIf.jsx` derived `years` for
`SecondEarnerNote` as
`result.schedule.filter(r => r.justin_gap_income > 0).length`, but
Survivor is the only consumer in this codebase whose returned
`schedule` is sampled (`schedule[::2]`, for chart-rendering size, not
changed here). Reproduction: wages continuing at ages 61-64 sample down
to just ages 61 and 63, so the note said "2 more years" instead of the
true 4. Checked every other schedule-based consumer for the same class
of bug (`grep -n '\[::' simulation_engine.py projection_engine.py`) —
Survivor's is the ONLY sampled schedule anywhere in the backend;
Roth Conversion's `schedule`, Retirement Projection's `yearly_detail`,
and every top-level `justin_gap_income_first_year`/`justin_gap_years`
field used by Monte Carlo/SWR/Stress Tests are all full, unsampled
data, so no other consumer needed this fix.

**Fixed:** a new backend field, `justin_gap_income_years_remaining`,
counted from the FULL (pre-sampling) `schedule` list in the same
return statement that samples `schedule` itself —
`sum(1 for row in schedule if row["justin_gap_income"] > 0)`. The
frontend now reads this field directly instead of re-deriving a count
from the (sampled) array it also receives.

**P2 — `SecondEarnerNote` bypassed privacy mode.** The component
formatted the dollar amount directly
(`` `$${Math.round(amount).toLocaleString()}` ``), never consulting the
app's existing `isPrivacyMode()`/`MASK_CURRENCY` convention used
throughout the rest of the app — exposing the working spouse's income
even on pages where nearby monetary values are masked for
screen-sharing. Fixed: the amount now renders through
`isPrivacyMode() ? MASK_CURRENCY : ...`, matching the convention used
elsewhere (e.g. `Accounts.jsx`, `Allocation.jsx`). Per the review's
explicit instruction, the 65%-of-gross policy factor stays visible in
both modes — it's a documented methodology constant, not
household-specific financial data.

New/updated frontend tests in `SurvivorScenario.test.jsx`: the existing
gap-income disclosure test now asserts `justin_gap_income_years_remaining:
4` renders as "4 more years" (previously asserted the old, undercounted
"2 more years," which matched the bug rather than the correct value); a
new test enables privacy mode and asserts the rendered text contains
`$•••,•••` and not `$65,000`, while `65%` remains visible.

**Verified:** full backend suite, 1,116 passed, 97.64% coverage (was
not rerun by the previous reviewer pass; rerun here per their note).
Frontend: 20 passed (was 19 — one new privacy-mode test), production
build succeeds (same pre-existing large-chunk warning, unrelated).
Sensitive-data check passed. Branch:
`codex/second-earner-output-visibility-and-parity` (same branch as
sections 16-17 — still unmerged, pending review).

## 19. Two-dimensional retirement timing — v1 (Phase 2 only), Retirement Projection reference implementation (2026-09-08, on `codex/two-dimensional-retirement`)

Full design lives in `docs/TWO_DIMENSIONAL_RETIREMENT_DESIGN.md`
(sections 1-6 written before any code, section 7 the concrete v1
contract). This section is the calculation-contract-style summary; the
design doc has the full reasoning and is the canonical reference.

**What this is:** a new, additive function,
`run_two_dimensional_retirement_projection` (`projection_engine.py`),
and a new `TwoPersonTimeline`/`build_two_person_timeline`
(`timeline_engine.py`) — explicit, independent retirement ages for both
spouses, one scenario at a time (no sweep, no heatmap), for the
Retirement Projection reference implementation only. **Nothing about
the existing `run_retirement_projection`, `Timeline`/`build_timeline`,
or any of the other 5 withdrawal-phase consumers changed** — this is
new code alongside them, not a modification.

**Key design finding:** the existing second-earner gap-income mechanism
(section 15, `justin_gap_income_for_year`) already implements most of
what a two-axis middle phase needs — it just only ever ran in one
direction (Justin working, Jason's `ret_age` as the sole axis) and only
ever started at Jason's own retirement. `justin_gap_income_for_year`'s
signature was already fully generic, and `simulate_withdrawal_year`
already sweeps income surplus into savings automatically (proven
correct by this session's own Survivor insurance-calc fixes, section
18). Reusing both meant v1 needed no new withdrawal-engine machinery —
only a two-axis timeline and a symmetric version of the existing
gap-income call site (either spouse can be the "still-working" side,
not just Justin).

**Model:** a household passes through "phase 2" (one spouse retired,
one still working — new) then "phase 3" (both retired, identical to
`run_retirement_projection`'s existing single-phase withdrawal loop).
`phase2_start_years`/`phase3_start_years` are the earlier/later of the
two spouses' own years-to-retirement (each independently clamped to 0
if already past, mirroring `build_timeline`'s existing per-person
convention). During phase 2, the still-working spouse's income offsets
spending need at the same flat `SECOND_EARNER_NET_OF_TAX_FACTOR` (0.65)
every other consumer's gap income already uses — no continued 401k
contribution is modeled from that income once the withdrawal loop has
started (a documented simplification: real payroll-tax modeling during
an active withdrawal loop is out of scope for v1). Withdrawal order,
tax treatment, and RMDs are unchanged — reuses `simulate_withdrawal_year`
exactly as the reference implementation does.

**Regression property:** equal ages (or `justin_ret_age` left at the
existing 0/unset sentinel) produce zero phase-2 years and reduce
exactly to `run_retirement_projection`'s own single-axis output for
that age — checked numerically in
`test_two_dimensional_retirement.py`'s simultaneous-retirement case,
which calls both functions on the same inputs and asserts identical
`portfolio_balance` values.

**Test-first:** `test_two_person_timeline.py` (12 cases) and
`test_two_dimensional_retirement.py` (7 cases) were written and
committed BEFORE either piece of implementation existed, with every
expected number hand-calculated against the design doc's contract, not
derived by running a draft implementation first. Categories covered:
either spouse retiring first (2 cases, proving the 65% offset applies
symmetrically), simultaneous retirement (+ the regression cross-check
above), a 10-year spousal age gap, an already-3-years-past retirement
age for one spouse, an income surplus swept into savings, and
insufficient funds reported correctly across the phase2→phase3
boundary. All 19 cases passed on the first implementation attempt — no
expected numbers were adjusted to fit the code.

**API:** `GET /api/projections/two-dimensional-retirement` —
`jason_ret_age`/`justin_ret_age` both required query params, no
defaults (a 422 if either is omitted, so this endpoint is not reachable
by accident from a page that only knows the single-axis model).

**Frontend:** a new "Two-Age Scenario" tab in Retirement Projection
(`TwoAgeScenario.jsx`) — two age inputs, a year-by-year table showing
the phase split and still-working income, and the account-ownership
limitation note (below) rendered directly from the API response. Kept
as its own tab rather than folded into the existing Overview/Side by
Side/Sensitivity tabs, so the two models stay visibly distinct. Every
existing `SecondEarnerNote` usage (Monte Carlo, Stress Tests, Roth
Conversion, Survivor Scenario) now also links back to this tab with a
one-line "single retirement-age model" cross-reference — documentation
only, no behavior change — so a user reading one of those pages cannot
mistake that offset for a genuine second, independently-timed
retirement age.

**Explicitly deferred (unchanged from the design doc):** Survivor
Scenario integration, real payroll-tax modeling, an owner-attributed
account ledger (the aggregated-bucket limitation is now surfaced
directly in the API response and UI, not just documented), and any
heatmap/matrix UI. Monte Carlo, Stress Tests, SWR, Tax Efficiency, and
Roth Conversion are completely unchanged.

**Verified:** all 19 new tests pass; full backend suite re-run (see the
commit history on this branch for the exact count — 1135 passed
immediately after the calculation-engine commit, before the API/UI
commits that followed added their own tests on top); full frontend
suite 24 passed (was 20); production build succeeds; sensitive-data
check passed. Branch: `codex/two-dimensional-retirement`, pushed,
**not merged** — per the explicit instruction this was scoped under,
for independent review.

## 20. Two-dimensional retirement timing — pension gating, age-55 rules preserved, UI fixes (2026-09-08, follow-up review)

Independent review of section 19's commit found two real calculation
bugs and two UI issues. All four fixed on the same unmerged branch.

**P1 — Jason's pension started before he actually retired.** During
phase 2, `year_pen = pension_annual` was added unconditionally from
`phase2_start`, regardless of whether Jason himself had retired yet.
Whenever Justin retired first (Jason is `later_retiree`, still working
during phase 2), his own future pension was paid two years early on top
of his salary-funded gap income. Reproduction: both spouses 60, Justin
retires at 61, Jason at 63, $30,000 pension, $100,000 Jason salary,
$80,000 spend, $200,000 taxable, 0% growth/inflation. Old (buggy) final
balance: $180,000 (two premature $30,000 payments swept in as
surplus). Correct: $120,000. **Fixed:** a new
`jason_pension_start_age = jason_age + timeline.jason_years_to_retire`
gates `year_pen` to `age >= jason_pension_start_age` — pension only
pays once Jason has actually reached his own selected retirement age,
independent of when the withdrawal loop itself started.

**P1 — The age-55 bridge-job/kids-at-home spending phases were
entirely absent.** `run_retirement_projection`'s own `if ret_age ==
55:` branch (bridge income covering healthcare net of a bridge job,
family healthcare while kids are still home, then the normal
pre-/post-Medicare split) had no analog in the new function at all —
every household saw the plain default formula regardless of age.
Reproduction: both spouses at 55 retiring simultaneously (zero phase-2
years), $80,000 spend, $30,000/yr bridge income for 5 years, $200,000
taxable. Existing single-axis tool's year-1 balance: $150,000. The new
function's (buggy) year-1 balance: $120,000 — the bridge offset was
silently dropped. **Fixed:** the same branch, ported verbatim (same
formulas, same four sub-phases), gated on `jason_ret_age == 55` and
anchored to `timeline.jason_years_to_retire` rather than
`phase2_years` — this spending phase is inherently pegged to *Jason's
own* retirement date (`bridge_years_55`/`kids_years_at_home_55` are
"years since Jason retired" concepts), which is not always the same
year the withdrawal loop itself starts (phase2_start can be earlier,
when Justin retires first). A direct parity test now calls both
functions on the same inputs and asserts identical
`portfolio_balance` for the first three years, not just this
function's own arithmetic in isolation.

**P2 — "Portfolio Draw" column showed net spending need, not the
actual withdrawal.** `TwoAgeScenario.jsx` displayed `row.draw` (need
minus guaranteed income, before withdrawal-order/tax mechanics) in a
column implying it was the real amount leaving the portfolio. An
$80,000 need funded from pretax accounts can mean $88,889 actually
withdrawn once grossed up for tax. **Fixed:** the column now displays
`row.withdrawal` (the real total from `simulate_withdrawal_year`,
already computed and returned by the API, just not read by the UI) and
is relabeled "Withdrawal" instead of "Portfolio Draw."

**P2 — Stale results stayed on screen after editing either age, and a
failed rerun kept the old result with no indication it no longer
matched the inputs.** **Fixed:** `result` is cleared immediately when
either age input changes, and at the start of every run (success or
failure) rather than only replaced on success — so a displayed result
can never outlive the inputs that produced it. The summary card now
also states the exact ages the displayed result corresponds to, read
from the API response (`result.jason_ret_age`/`justin_ret_age`) rather
than the current input state, so a result can't be silently mislabeled
if the inputs changed again while a request was still in flight.

New backend tests: `TestPensionGatedToJasonsOwnRetirement` (2 cases)
and `TestAge55BridgeAndKidsRulesPreserved` (2 cases, one a direct
parity check against `run_retirement_projection`) in
`test_two_dimensional_retirement.py`. New frontend tests: withdrawal-
vs-draw display, the age-pair label, stale-result clearing on both an
age edit and a failed rerun — 4 new cases in `TwoAgeScenario.test.jsx`.

**Verified:** full backend suite, 1141 passed, 97.44% coverage (was
1135 immediately after section 19's calculation-engine commit — the 4
new backend tests plus the API/UI commits' own tests account for the
rest of the difference already reflected in that commit's own count).
Frontend: 28 passed (was 24). Production build succeeds. Sensitive-data
check passed. Branch: `codex/two-dimensional-retirement` — still
**not merged**, per the same instruction, for continued review.

## 21. Two-dimensional retirement timing -- effective-start-age fix and RSU flat convention (2026-09-08, third follow-up)

Continued independent review of section 20's fixes found two more real
calculation bugs. Both fixed on the same unmerged branch.

**P1 -- age-55 bridge/kids timing used the raw selected age instead of
Jason's effective retirement start.** `jason_yr = age - jason_ret_age`
is correct only when the household hasn't yet reached the selected age
(jason_ret_age IS the effective start in that case). For a household
already past it today, jason_years_to_retire clamps to 0 and the real
effective start is the household's actual current age -- but the
bridge/kids branch kept indexing from the fictional past date anyway.
Reproduction: both spouses currently 60, retirement selected at 55
(5 years already past), 3% inflation, $80,000 spend, $30,000/yr bridge
income for 5 years. The withdrawal loop's first year IS the effective
retirement start (age 60), but the buggy formula treated it as
jason_yr=5 -- compounding 5 years of inflation immediately ($92,742
instead of $80,000) AND treating bridge income as already expired
(5 is not < bridge_years=5, so the bridge branch wasn't even entered).
**Fixed:** a new `jason_effective_start_age = jason_age +
timeline.jason_years_to_retire` (the same "already past" clamp
`build_timeline`/`TwoPersonTimeline` apply everywhere else, expressed
as an absolute age) replaces `jason_ret_age` as the anchor for both the
bridge/kids branch's `jason_yr` and its own guard, and for the pension
gate from section 20 (which had the same class of correctness, though
not the same bug, since it used `jason_years_to_retire` directly rather
than the raw selected age -- now both share one variable instead of two
that happened to agree only in the not-yet-retired case).

**P2 -- Jason's RSU escalated with salary growth; the reference
implementation keeps it flat.** The new function's shared `_contrib_fv`
helper (401k/bonus/RSU all routed through one growing-annuity-eligible
path) applied `salary_growth_pct` to Jason's RSU too. But
`run_retirement_projection` treats Jason's own RSU as a flat dollar
amount unconditionally (`_fv_annuity`, never the growing variant) --
only 401k contributions and bonus (both salary-derived percentages)
grow with an assumed raise rate; a flat RSU grant has no such
percentage-of-salary basis to grow from. Reproduction: 3 years to
simultaneous retirement, $100,000 annual RSU, 10% salary growth, 0%
investment returns -- flat (correct): $65,000 * 3 = $195,000 at the
65% factor; growing (buggy): $215,150. **Fixed:** a new
`_contrib_fv_flat` helper (same dormant-years compounding pattern, but
always `_fv_annuity`, never `_fv_growing_annuity`) used for Jason's RSU
only. Justin's own RSU deliberately stays on the growing-eligible path
-- `run_retirement_projection`'s own `justin_annual_rsu` handling (via
`_justin_contrib_fv`) already lets Justin's RSU grow with
`salary_growth_pct`, an existing asymmetry between the two spouses'
RSU treatment that predates this branch and is preserved here exactly,
not resolved (a new test asserts Justin's own RSU still matches the
reference's growing value, so a well-intentioned future "fix" of the
asymmetry doesn't silently reappear).

**Expanded parity checks**, per the explicit instruction: the original
simultaneous-retirement parity check (section 19) only covered 0%
inflation/growth with no RSUs -- exactly the dimensions both bugs above
lived in. `TestExpandedSimultaneousRetirementParity` adds two more
direct numeric comparisons against `run_retirement_projection`'s own
output for the same inputs: a past retirement selection with nonzero
inflation, and nonzero salary growth with RSUs present (not yet
retired, contributions still accruing).

New tests: `test_bridge_and_inflation_use_jasons_effective_start_not_
the_raw_selected_age` (`TestAge55BridgeAndKidsRulesPreserved`),
`TestJasonRsuStaysFlatUnlikeSalaryDerivedContributions` (2 cases), and
`TestExpandedSimultaneousRetirementParity` (2 cases) -- 5 new tests
total in `test_two_dimensional_retirement.py`.

**Verified:** full backend suite, 1146 passed, 97.45% coverage.
Frontend unchanged this round (no UI code touched) -- 28 passed,
production build succeeds. Sensitive-data check passed. Branch:
`codex/two-dimensional-retirement` -- still **not merged**, per the
same instruction, for continued review.

## 22. Two-age Monte Carlo and Stress Tests (2026-09-08, on `codex/two-age-monte-carlo-stress`)

Full design context: `docs/TWO_DIMENSIONAL_RETIREMENT_DESIGN.md` and
sections 19-21 (the Retirement Projection reference implementation this
extends). This section documents propagating explicit, independent
two-age retirement timing to Monte Carlo and Stress Tests -- the next
two of the five consumers named as deferred in section 19.

**What this is:** new `jason_ret_age`/`justin_ret_age` parameters on
`run_monte_carlo`/`run_stress_tests` -- both required together (a
`ValueError` if only one is given), completely additive. The existing
`ret_age`/`ss_timing` single-axis path is untouched when these are left
at their `None` default; the full pre-existing `test_simulation_engine.py`
suite passes unchanged.

**Shared calculation policy, not a second copy.** Per the explicit
instruction this branch was scoped under, three module-level helpers
extracted from `run_two_dimensional_retirement_projection` in the
first commit on this branch (`two_age_spending_need_fn`,
`two_age_pension_for_year`, `two_age_still_working_income_inputs`,
plus the already-shared `justin_gap_income_for_year`) are reused
verbatim by a new `_run_single_two_age` (the two-age analog of
`_run_single`) -- no independent second copy of income, pension,
bridge, or contribution formulas. Starting bucket balances come from
calling `run_two_dimensional_retirement_projection` once and reading
its `pretax_at_phase2_start`/`roth_at_phase2_start`/etc. fields (added
in the same first commit) -- the same "call the deterministic reference
once, read its bucket breakdown" pattern the existing single-axis mode
already uses against `run_retirement_projection`, so the
accumulation-phase contribution/RSU/asset-sale/life-event/surplus-
allocation math isn't re-derived either.

**Test-first**, per instruction: `test_two_age_monte_carlo_stress.py`
was written and committed BEFORE `_run_single_two_age` or the new
params existed (19 of 21 cases failed at collection with
`TypeError`/`ImportError`). Two verification strategies:
1. **Deterministic parity** (`random.gauss` monkeypatched to `0.0`,
   matching this codebase's existing single-axis technique): Monte
   Carlo's `median_final_balance` and Stress Tests' `base` scenario
   must reproduce `run_two_dimensional_retirement_projection`'s own
   yearly balances exactly. Covers every category named in the
   instruction: either spouse retiring first (2 directions, proving
   the wiring is symmetric), simultaneous retirement, unequal ages, a
   past retirement selection, pension timing, the age-55 bridge/kids
   phase, an income surplus, and depletion/unmet need.
2. **Hand-calculated adverse-return sequences** (explicit, non-random
   `annual_returns` arrays, verified against `simulate_withdrawal_year`
   arithmetic directly before writing each assertion): a -30% return
   during the middle phase while gap income still covers spending
   (portfolio still grows off a smaller base), and a -80% return
   landing exactly on the phase2->phase3 boundary (guaranteed income no
   longer covers the whole need once both are retired).

Two real bugs were found and fixed while writing these tests against
the first implementation attempt -- both in the TEST construction, not
the implementation: a swapped `(pretax_start, taxable_start)` argument
order in two hand-written `_run_single_two_age` calls, and a household
where the intended "later retiree" and the salary field set didn't
match (produced a $1,325,000 result instead of the intended $420,000
until the pairing was corrected). Documented here since both looked
initially like implementation bugs and are exactly the kind of mistake
a future reviewer re-deriving these numbers should watch for.

**A real latent bug WAS found and fixed in the implementation**: the
`early_sequence` stress scenario's return-sequence construction used
`dict.get(key, expensive_default_expression)` -- Python evaluates the
default argument eagerly every call regardless of whether the key
exists, so a short two-age horizon (`retire_yrs <= len(overrides)`)
crashed with an out-of-range index that was never going to be used.
Fixed with a lazy membership-test branch. The single-axis
`run_stress_tests`'s own `early_sequence` code has the identical latent
bug but was out of scope to touch here -- it never manifests there
since production `retire_yrs` is always long enough.

**Stress Tests scenario coverage:** the `base` scenario (required exact
parity) plus the SCENARIOS dict's pure return-override entries
(`crash_2008`, `lost_decade`, `early_sequence` -- all
`inflation_mult: 1.0`, matching this function's flat-inflation v1 scope
with zero special-casing needed). `stagflation_1970s` (variable
inflation), `bridge_job_loss` (needs a two-age-aware bridge
re-projection), and `ss_reduction` (a scenario-level SS multiplier not
wired into the two-age per-year SS formula) are explicitly skipped --
each needs feature support out of scope for this v1, documented in the
code rather than silently misapplied. Named-scenario result dicts gain
`depletion_age`/`lowest_balance`/`lowest_balance_age` (matching the
single-axis shape exactly) so the existing `StressTestSection.jsx`
scenario cards render two-age results with zero extra branching.

**Success rate correctness**, per explicit instruction: `_run_single_
two_age`'s `survived` computation is `(final balance > 0) and not
any_unmet_need`, inherited by construction from `_run_single`'s own
formula -- any single unfunded year fails the trial regardless of the
final balance. Proven with a household engineered to end $420,000
positive (a huge phase3 pension surplus) despite two real unfunded
years earlier in phase2: `success_rate` is exactly 0.0%, not driven by
the positive ending balance, checked via both Monte Carlo (deterministic,
all 1000 trials identical) and Stress Tests' `base` scenario.

**API:** `jason_ret_age`/`justin_ret_age` added as optional query params
to `GET /api/simulation/monte-carlo` and `GET /api/simulation/stress-tests`
-- a `ValueError` (only one age supplied) becomes a 400, not an
unhandled 500.

**Frontend:** a shared "Use Two Independent Retirement Ages" toggle in
`StressTestWhatIf.jsx` (both Monte Carlo and Historical Stress tabs
read it) replaces the single-age Retirement Age/Social Security
selector with two explicit age inputs when on. `MonteCarloSection`/
`StressTestSection` (`Simulation.jsx`) gain optional `jasonRetAge`/
`justinRetAge` props; when both are set they call the two-age endpoints
directly via the SAME stale-result-guard (`genRef` counter) the
existing single-age flow already used -- an age change or a new run
both invalidate any in-flight response, so a result can never outlive
the inputs that produced it. A shared `secondEarnerNoteProps()` helper
picks the right amount/years/personLabel for either result shape
(single-axis `justin_gap_income_first_year` vs two-age
`still_working_spouse_income_first_year`, where the still-working
spouse can be EITHER person) so `SecondEarnerNote` -- and with it, the
existing privacy-mode masking and the visible 65% factor -- renders
correctly in both modes with no duplicated formatting logic. Both
sections also display the exact age pair a result corresponds to, read
from the API response (`data.jason_ret_age`/`justin_ret_age`), not
local input state, matching the same anti-stale-labeling convention
`TwoAgeScenario.jsx` established in section 19.

**Runtime**, measured against the existing single-axis mode (same
household, N=1000 trials, 5-run average): single-axis Monte Carlo
~0.249s, two-age Monte Carlo ~0.175s -- two-age mode is faster, not
slower, because its `TwoPersonTimeline` is built once outside the
1000-trial loop and passed in, rather than `_run_single`'s existing
per-trial `Timeline` rebuild (a deliberate efficiency choice
documented in `_run_single_two_age`'s own docstring, not required for
correctness but free to include). Stress Tests: both modes ~0.001-0.002s
(a handful of deterministic scenarios, not 1000 trials).

**Explicitly out of scope, unchanged from the design doc:** SWR, Tax
Efficiency, Roth Conversion, Survivor Scenario, real payroll-tax
modeling, an owner-attributed account ledger, and any heatmap/matrix
UI. What-If Builder overrides don't carry into two-age mode in this
v1 (the two-age GET endpoints, not the What-If-aware POST variants).

**Verified:** 21 new backend tests (`test_two_age_monte_carlo_stress.py`)
plus 4 new endpoint tests (`test_main.py`) plus 6 new frontend tests
(`TwoAgeMonteCarloStress.test.jsx`) all pass. Full backend suite: see
the commit history on this branch for the exact final count (350
passed immediately after the `_run_single_two_age`/API commits, before
the depletion-age-fields and doc commits that followed added their own
coverage on top). Full frontend suite: 34 passed (was 28). Production
build succeeds. Sensitive-data check passed. Branch:
`codex/two-age-monte-carlo-stress`, pushed, **not merged** -- per the
explicit instruction this was scoped under, for independent review.

## 23. Two-age Monte Carlo/Stress Tests follow-up review (2026-09-08, on `codex/two-age-monte-carlo-stress`)

Independent review of section 22's commit (`d347e4b`) found five issues.
All five fixed on the same unmerged branch.

**P1 -- two-age mode silently dropped selected assumptions.** Both
`MonteCarloSection`/`StressTestSection`'s two-age branches called the
plain `GET` endpoint with only `jason_ret_age`/`justin_ret_age`,
omitting `ss_timing` entirely and never applying any What-If Builder
overrides -- switching into two-age mode reverted to early SS claiming
and saved Settings regardless of what was already selected. Reproduced
as a real result difference ($400,000 with early SS vs $340,000 with
delayed SS on the same household). **Fixed** on both ends: `post_
monte_carlo`/`post_stress_tests` now read `jason_ret_age`/
`justin_ret_age` from the request body (same two-age dispatch and
400-on-`ValueError` handling the `GET` endpoints already have); the
frontend now `POST`s in two-age mode too, with `ss_timing`/`overrides`
threaded through the same way the single-age branch already does. The
SS-claiming-timing selector, previously hidden entirely while two-age
mode was on (so there was no way to change it), now stays visible and
editable in both modes.

**Incomplete scope -- three stress scenarios were silently skipped.**
`stagflation_1970s`, `bridge_job_loss`, and `ss_reduction` were entirely
absent from two-age Stress Tests' first cut, with the UI simply hiding
them -- none of the three were an agreed deferral (only Survivor
integration, real payroll tax, the ownership ledger, and a heatmap UI
were). **Completed:**
- `stagflation_1970s` (variable inflation): `two_age_spending_need_fn`
  and `_run_single_two_age` gain an optional `inflation_mults`
  parameter, using the same accumulate-don't-retroactively-erase-
  history cumulative-inflation curve `timeline_engine.
  build_cumulative_inflation` already provides for every other
  consumer. Defaults to `None` (flat inflation every year), under
  which the new formulas reduce to the *exact* originals -- verified
  algebraically (both the phase2-anchored default branch and the
  jason-effective-start-anchored bridge/kids branch, which rebases via
  division onto the same underlying curve) and confirmed by every
  existing test passing unchanged. Social Security's own COLA is
  rebased onto the identical curve, relative to each spouse's own claim
  age rather than phase2_start (also verified to reduce to the exact
  original flat formula when `inflation_mults` is `None`), so SS and
  spending need can't silently drift out of sync with each other during
  a variable-inflation scenario.
- `bridge_job_loss`: re-projects starting balances with a shortened
  `bridge_years_55` via `run_two_dimensional_retirement_projection`,
  the same re-projection pattern the single-axis version's own
  `bridge_years_override` handling already uses, gated on
  `jason_ret_age == 55` (bridge/kids timing is always anchored to
  Jason's own retirement -- section 21) rather than the single-axis
  `ret_age`.
- `ss_reduction`: a scenario-level multiplier applied to both spouses'
  SS annual amount before the trial call -- including Justin's own
  benefit from the start, the exact gap the single-axis version needed
  a separate follow-up fix for (external audit 2026-09-06).

All 7 scenarios now run in two-age mode; the frontend's scenario-key
filtering is kept as a defensive no-op rather than removed outright.

**P2 -- a fully funded plan could report 0% success.** `_run_single_
two_age`'s `survived` formula required `balances[-1] > 0` on top of
"every year funded" -- a trial that funds every year in full but ends
at exactly $0 (the money lasted precisely as long as the plan needed
it to) is a real success, not a failure. Reproduced exactly: one year,
$80,000 available, $80,000 spend, 0% return -- the deterministic
Projection reports `on_track=True` (`unmet_need=0`), but Monte Carlo
reported 0% success and Stress reported failure for the identical
scenario. **Fixed:** `survived = not any_unmet_need`, matching `run_
two_dimensional_retirement_projection`'s own `on_track` definition
exactly. `_run_single`'s identical single-axis formula has the same
property -- left unchanged, existing behavior for every other consumer,
out of scope for this branch (documented in the code, not silently
carried over).

**P2 -- rounded starting balances broke deterministic parity.**
`pretax_at_phase2_start`/`roth_at_phase2_start`/`taxable_at_phase2_
start`/`hsa_at_phase2_start` were rounded before ever being consumed as
`_run_single_two_age`'s opening balances, so Monte Carlo/Stress started
from a slightly different number than the deterministic projection's
own full-precision arithmetic for the identical scenario (reproduced: a
$2 drift under fixed returns). **Fixed:** these four fields are now
unrounded floats -- they're consumed as another engine's input, not
displayed anywhere directly; `round()` belongs at display time only.

**P2 -- two-age results described themselves as single-age.**
`SecondEarnerNote`'s trailing model-description line still read "Single
retirement-age model" even on a result whose own `mode` was
`"two_age"`. **Fixed:** a new optional `twoAge` prop, set from
`data.mode === 'two_age'` by the shared `secondEarnerNoteProps()`
helper, switches the note to "Two-age model -- both retirement ages set
independently."

**Verified:** 9 new backend tests (success-rate contract, unrounded
bucket fields, all 7 scenarios present, `ss_reduction`/`bridge_job_
loss`/`stagflation_1970s` each verified with a hand-calculated or
script-verified-before-asserting reproduction) plus 4 new endpoint
tests plus 6 updated/new frontend tests, all passing. Full backend
suite: 1181 passed, 97.56% coverage. Full frontend suite: 34 passed,
production build succeeds. Sensitive-data check passed. Branch:
`codex/two-age-monte-carlo-stress` -- still **not merged**, per the
same instruction, for continued review.

## 24. Two-age Stress Tests second follow-up review (2026-09-08, on `codex/two-age-monte-carlo-stress`)

Independent review of section 23's commit (`d120da7`) found two more
real bugs, both in the newly-completed stress-scenario support. Both
fixed on the same unmerged branch.

**P1 -- stagflation's cumulative inflation reset at the phase2/bridge
boundary.** Section 23's `two_age_spending_need_fn` computed the
bridge/kids branch's own dollar baselines (`income_at_jason_ret`,
`healthcare_*_at_jason_ret`, etc.) anchored to `jason_effective_start_
age` via `timeline.jason_years_to_retire`, then rebased the per-year
cumulative-inflation multiplier as `cum_inflation[yr] /
cum_inflation[jason_offset]`. Under FLAT inflation this was provably
identical to anchoring at `phase2_start` instead -- compounding the
same total number of years via two different splits is the same
arithmetic either way, which is why the original section-21 design's
choice of anchor never mattered. That equivalence breaks under a
VARIABLE per-year rate: whenever Justin retires first,
`jason_effective_start_age` can fall strictly after `phase2_start_age`,
meaning the years between them are already inside the withdrawal loop
and should be subject to `inflation_mults` like any other loop year --
anchoring to `jason_effective_start_age` instead treated that whole
span as flat pre-loop compounding, and the rebasing then reset the
per-year multiplier to exactly 1.0 right at the boundary, discarding
every year of already-accumulated elevated inflation outright.
Reproduced exactly: both spouses currently 53, Justin already retired,
Jason retiring at 55, $100,000 spend, 8% stressed inflation (2% base *
the scenario's own 4x) -- year 3 (age 55, the first bridge year)
reverted to $104,040 (the flat, no-stress figure) instead of the
correct $116,640; the resulting final balance was overstated
($736,603 vs the correct $723,751).

**Fixed** by eliminating the second anchor entirely: every dollar
baseline (`income_at_start`/`healthcare_*_at_start`/`kids_annual_cost_
at_start`/`bridge_income_at_start`) now anchors to `phase2_start`
unconditionally, matching the default branch's own convention exactly,
and both branches share one `cum_inflation[yr]` multiplier with no
rebasing. `jason_yr` (`age - jason_effective_start_age`) is kept, but
narrowed to what it should have only ever been -- a duration counter
compared against `bridge_years`/`kids_years`, not a second inflation
clock. Verified algebraically that this reduces to the exact same
formulas as before whenever `jason_offset == 0` (Jason retires first or
simultaneously, which is every existing bridge/kids test's own
configuration) and confirmed by the full suite passing unchanged;
verified against the review's own reproduction directly
(`two_age_spending_need_fn` called standalone, asserting the exact
per-year need sequence `[100000, 108000, 116640]`) before checking the
resulting `final_balance`.

**P2 -- "bridge job loss" could invent income instead of removing it.**
The scenario unconditionally set `bridge_years_55` to 2 for the
stressed run, even when the household's own configured value was 0 or
1 -- turning a scenario meant to model the bridge job ending EARLY into
one that could instead give a household with no bridge job (or a
shorter one) MORE bridge income than they actually planned for.
Reproduced exactly: $30,000/yr bridge income configured with 0 bridge
years -- the stress scenario ended $60,000 above the neutral base case
($820,000 vs $760,000) instead of identical to it. **Fixed** by capping
the override at the household's own existing value
(`min(bridge_override, inputs.get("bridge_years_55", 0))`) in BOTH the
two-age copy and the single-axis original it was copied from -- the
identical bug was already present in the source this branch's own
`_run_stress_tests_two_age` mirrored, per the explicit instruction to
fix it in both places rather than only the newer copy.

**Verified:** 2 new backend tests (the exact stagflation phase-boundary
reproduction via `two_age_spending_need_fn` called directly, and the
zero-bridge-years `bridge_job_loss` reproduction), full backend suite
1183 passed / 97.51% coverage, full frontend suite 34 passed (no
frontend code changed this round), production build succeeds,
sensitive-data check passed. Branch: `codex/two-age-monte-carlo-stress`
-- still **not merged**, per the same instruction, for continued
review.

## 25. Two-age SWR — design contract, written before implementation (2026-09-08, on `codex/two-age-swr`)

Milestone 1 of 4 (SWR, Roth Conversion, Tax Efficiency, Survivor/
ownership, in that order, each its own reviewed branch). This section
is the required "define exactly what the search solves for" contract,
written and committed before any calculation code on this branch.

### 25.1 What the existing single-axis SWR actually solves for

Read directly from `run_swr_analysis`/`_swr_year_step` before writing
anything new, since the instruction is to preserve this meaning, not
assume it:

- The search variable is `annual_withdrawal_today` — a single TODAY'S-
  dollars figure representing the household's **additional
  portfolio-funded spending, on top of guaranteed income** (pension +
  Jason's SS + Justin's SS + life-event cash/monthly + second-earner
  gap income, the last two folded into `_swr_year_step`'s
  `event_monthly` parameter). It is **not** total household spending —
  `_swr_year_step`'s own docstring states this explicitly (section 3.5
  of this document): SWR answers "how much can I safely withdraw from
  the portfolio alone," not "given my itemized spending need, do I
  survive" (that second question is Retirement Projection/Monte
  Carlo/Stress Tests' job).
- Dollar basis: today's dollars, inflated every loop year via
  `annual_withdrawal_today * (1+inflation)**yr` -- flat compounding,
  no per-year stress `inflation_mults` support (SWR never ran the
  named stress scenarios; that's Stress Tests' own job).
- Begins: at `withdrawal_start_age` (`Timeline.effective_start_age`),
  i.e. the household's single withdrawal-phase start -- there is no
  middle phase in the single-axis version because there's only one
  retirement age.
- **SWR's own withdrawal loop does not separately model bridge/kids
  costs or per-year healthcare at all** -- confirmed by reading the
  loop: `healthcare_pre`/`healthcare_post` are read from `inputs` but
  only ever used once, in the final summary's `income_target`
  comparison (`income_target = (income_today + healthcare_pre) *
  (1+inflation)**years_to_ret`), never inside `success_at_withdrawal`'s
  per-year math. There is no `ret_age == 55` bridge/kids branch in
  this function at all, unlike Retirement Projection/Monte Carlo/
  Stress Tests. This is a real, pre-existing scope limit of SWR, not
  an oversight this milestone is asked to fix.
- Success criterion: across N=1000 pre-generated random return
  sequences (seeded, reproducible), the fraction of trials that fund
  every single year in full (no rationing, no depletion -- `remaining
  <= 0` every year) must be `>= target_success` (default 95%). Found
  via binary search: an expanding-then-bisecting search on
  `annual_withdrawal_today`, 20 bisection iterations after the initial
  bracket is established.
- Shared, allocation-free primitives already used (not reimplemented
  inline a second time): `_swr_year_step` (the withdrawal-order/tax
  step, already parity-tested against `simulate_withdrawal_year`),
  `_pretax_marginal_tax_rate`, `_rmd`/`rmd_start_age`,
  `justin_gap_income_for_year`, `_post_retirement_year_effects`. SS/
  pension COLA is computed inline with the same flat formula every
  other consumer uses, not via the shared per-year builder
  (`build_annual_income_inputs`) -- a documented performance exception
  (this loop runs up to ~32x more often than any other consumer:
  binary search x 1000 trials x ~40yrs), verified equivalent by
  `test_annual_inputs.py::TestPerformanceExceptionParity`.

### 25.2 Two-age SWR contract (what changes, what doesn't)

**Preserved exactly, per instruction ("Preserve the existing SWR
meaning unless an explicit change is approved"):**
- `annual_withdrawal_today` remains a single today's-dollars,
  flat-inflation-compounded, portfolio-funded-spending-on-top-of-
  guaranteed-income search variable, applied uniformly across the
  *entire* horizon (both phase2 and phase3) -- not phase-dependent.
  This is the natural generalization, not a new definition: the
  household still draws the same real amount every year regardless of
  phase; phase2's extra income just makes it easier to hit
  `target_success` those years, exactly the same way pension/SS/life-
  event income already does.
- No bridge/kids/healthcare modeling is added to the two-age search
  loop either -- extending that scope is a separate, explicitly-scoped
  ask, not implied by "two-age SWR." (Flagged prominently here and in
  the milestone's own final report so this reading can be corrected if
  wrong.)
- Success criterion, N=1000 trials, binary search structure, and the
  20-iteration precision are unchanged.
- `_swr_year_step`, `_pretax_marginal_tax_rate`, `_rmd`/
  `rmd_start_age`, `_post_retirement_year_effects` are reused as-is,
  zero changes -- this milestone only needs different *inputs* to
  those same functions, not different functions.

**New, additive (the actual "two-age" extension):**
- Timeline: `build_two_person_timeline` instead of `build_timeline` --
  the loop now runs from `phase2_start_age` (yr=0) through `end_age`,
  covering both phase2 (one retired, one still working) and phase3
  (both retired), instead of a single withdrawal_start_age.
- Guaranteed income during phase2 gains the still-working spouse's net
  income, via `two_age_still_working_income_inputs` +
  `justin_gap_income_for_year` -- folded into `_swr_year_step`'s
  `event_monthly` exactly the same way the existing gap-income offset
  already is (same call shape, same parameter, symmetric to whichever
  spouse is `later_retiree` rather than hardcoded to Justin).
- Pension gates to `two_age_pension_for_year(pension_annual, age,
  jason_effective_start_age)` instead of paying unconditionally from
  day one -- same fix already applied to Monte Carlo/Stress Tests
  (section 20).
- Starting portfolio balances come from
  `run_two_dimensional_retirement_projection`'s own
  `pretax_at_phase2_start`/etc. fields (unrounded, section 23) --
  same "call the deterministic reference once" pattern every other
  two-age consumer already uses, not a re-derivation of the
  accumulation-phase math.
- `jason_ret_age`/`justin_ret_age` on `run_swr_analysis`, both
  required together (`ValueError` if only one given) -- same
  `_require_both_two_age_or_neither` gate the other two-age consumers
  already share (reused directly, not re-implemented).
- Summary fields (`ss_start_age`, `guaranteed_income_steadystate`,
  `cushion_pct`, etc.) recomputed against `phase2_start_age`/
  `phase3_start_age` instead of the single-axis `withdrawal_start_age`.

### 25.3 Performance

Reuses the exact same allocation-free primitives the single-axis
version already relies on for its own documented performance
exception -- no new performance exception is being introduced here;
this milestone's own benchmark (section 26, once implemented) will
confirm the two-age loop's runtime stays in the same range as the
existing single-axis SWR at matched trial count/horizon/seed, not a
new claim requiring its own equivalence proof beyond what
`_swr_year_step` already has.

## 26. Two-age SWR — implementation report (2026-09-08, on `codex/two-age-swr`)

Milestone 1 of 4 (SWR, Roth Conversion, Tax Efficiency, Survivor/
ownership). Full contract in section 25, written and committed before
any code. This section is the required accuracy/performance/limitations
report for the completed implementation.

**What was built:** `jason_ret_age`/`justin_ret_age` on
`run_swr_analysis`, both required together, delegating to
`_run_swr_analysis_two_age`. A new, independently-callable
`_swr_success_rate_two_age` (mirrors `_run_single_two_age`'s own
precedent — a real function, not a closure, so the search itself is
directly testable) reuses the exact same allocation-free primitives the
single-axis version's own documented performance exception already
established: `_swr_year_step`, `_pretax_marginal_tax_rate`,
`_rmd`/`rmd_start_age`, `_post_retirement_year_effects`,
`justin_gap_income_for_year` — plus the shared two-age helpers
(`two_age_pension_for_year`, `two_age_still_working_income_inputs`,
`build_two_person_timeline`). No independent second set of formulas, no
new performance exception. Starting balances come from calling
`run_two_dimensional_retirement_projection` once and reading its bucket
fields, the same pattern every other two-age consumer already uses.

**Accuracy — test-first, per instruction:** 10 hand/script-verified
tests written and committed RED before any implementation code
(confirmed failing with `TypeError` against the not-yet-existing
params). Every boundary withdrawal amount was derived by directly
driving `_swr_year_step` — the existing, unmodified, already-reviewed
primitive — through its own standalone 50-iteration binary search
outside any code this milestone wrote, then hand-verified by replaying
the arithmetic (shown in each test's own docstring). All 10 passed on
the first implementation attempt — no expected numbers were adjusted to
fit the code. Covers: either retirement order (both Jason-first and
Justin-first, proving the still-working-spouse offset is symmetric,
matching the identical $110,000 boundary both directions), simultaneous
retirement, an income surplus swept into the portfolio during phase2, a
zero-balance depleted-account case (boundary exactly $0), and a past
retirement selection (phase2 starting immediately at the real current
age). A direct proof (`_swr_year_step` called with the exact converged
boundary and again $10,000 above it) that a meaningfully higher
withdrawal fails by exactly the expected shortfall, not just that some
withdrawal succeeds. 2 more tests verify the summary fields
(`guaranteed_income_annual` correctly excludes the pension until Jason
actually retires; `guaranteed_income_steadystate` includes it once he
has) and confirm `income_target`/`cushion_pct`/`on_track` are present.

**What the search does NOT model, preserved from the single-axis
version (section 25.1) rather than newly added:** bridge/kids costs and
per-year healthcare are not part of either version's own withdrawal
loop — both only ever appear in the final summary's `income_target`
comparison. Guaranteed income (pension/SS) does not offset the searched
withdrawal amount directly in either version; it only affects the tax
rate applied to pretax withdrawals. These are documented, pre-existing
SWR behaviors this milestone preserves exactly, not scope this branch
was asked to add.

**Performance:** benchmarked at the same trial count (N=1000), a
representative multi-decade horizon, and matched inputs against the
existing single-axis `run_swr_analysis` (5-run average): single-axis
~1.402s, two-age ~1.361s — the two-age version is not slower; if
anything marginally faster, consistent with the same pattern already
observed for two-age Monte Carlo/Stress Tests (no new performance
exception needed — this reuses `_swr_year_step` exactly as-is).

**Frontend:** SWR already had a UI presence — embedded in
`MonteCarloSection`'s existing "Safe Spending Power" card, which had
been left showing "Run simulation to calculate" in two-age mode since
SWR didn't support it yet. `MonteCarloSection`'s two-age branch now also
`POST`s to `/api/simulation/swr` alongside Monte Carlo (same `ss_timing`
+ What-If overrides preservation the Monte Carlo call already has,
same stale-result guard), populating that card with real two-age
numbers instead of a placeholder. The card's own `SecondEarnerNote`
usage now goes through the shared `secondEarnerNoteProps()` helper
(mode-aware amount/years/label, established in section 23) instead of
hardcoding Justin-only fields. The age-55 bridge-detail sub-branch
inside that card is now also gated on `data.mode !== 'two_age'`, so a
stale single-axis `retAge===55` selection can't spuriously render
single-axis-only bridge/kids UI under a two-age result. Both calculated
ages are already shown via the page-level "Ages used" label
(section 23); privacy masking and stale-result invalidation are
inherited for free from the existing shared `secondEarnerNoteProps`/
`genRef` machinery, not re-implemented.

**Verified:** full backend suite 1198 passed, 97.55% coverage. Full
frontend suite 35 passed (was 34), production build succeeds.
Sensitive-data check passed.

**Explicitly out of scope for this milestone**, unchanged from the
overall plan: Roth Conversion, Tax Efficiency, Survivor Scenario, real
payroll-tax modeling, the owner-attributed ledger, heatmaps.

Branch: `codex/two-age-swr`, pushed, **not merged** — per the explicit
instruction ("Do not merge or start the next milestone until the
current one is reviewed and approved"), for independent review.

## 27. Two-age SWR — independent review fixes (2026-09-08, on `codex/two-age-swr`)

First independent review of `codex/two-age-swr`'s prior commit found
three issues in `_run_swr_analysis_two_age`, all now fixed and covered
by new hand/script-verified tests (`test_two_age_swr.py`). None touch
`_swr_success_rate_two_age`'s own withdrawal search loop or
`safe_withdrawal_annual`/`safe_withdrawal_rate` — the portfolio-only
search meaning from section 25 is unchanged.

**P1 — household affordability compared against a flat target that
missed the search's own known blind spots.** `on_track`/`cushion_pct`
used to compare `total_safe_spend` (searched withdrawal + day-one
guaranteed income) against a flat `income_target` figure computed from
just `retirement_income_today_dollars` + `healthcare_pre_medicare` at
one point in time — missing bridge/kids costs, the healthcare
pre/post-Medicare split over time, and per-year timing entirely, since
those were never part of the search loop to begin with (section 25's
own documented limitation). Reproduced: $220,000 portfolio, both retire
now, 2-year horizon, $100,000 income + $30,000 healthcare/kids cost,
0% inflation/growth — reported on_track even though the household's
real 2-year need ($260,000) exceeds the $220,000 portfolio by $40,000;
and $200,000 portfolio, $125,000 income, 10% inflation, a frozen
$30,000 pension — reported on_track despite a real $2,500 unmet need in
year 2. Fixed by reusing `run_two_dimensional_retirement_projection`'s
own `on_track`/`yearly_detail` — already computed above for starting
balances, already running the complete dated cash flow (bridge/kids/
healthcare/guaranteed income/still-working income, all correctly
timed) — instead of a second, cruder comparison. `total_unmet_need`
(summed from `yearly_detail`) is now also returned. `cushion_pct` is
now `(total_need − total_unmet) / total_need − 1`, in percent, over the
complete horizon — negative exactly when the household has a real
shortfall. `income_target` itself is left as the same flat, first-year
figure it always was (still a reasonable "for scale" number for
display); only `on_track`/`cushion_pct` changed basis.

**P2 — a $0 starting portfolio forced a $0 search result regardless of
future income.** The search's upper bound seed (`portfolio * 0.15`) and
its entire expansion loop were gated behind `if portfolio > 0` — a
household with no starting assets but real future income (e.g. a
still-working spouse) got `lo = hi = 0` unconditionally, well before the
search ever got a chance to test a real number. Reproduced: $0 taxable,
Jason retires now, Justin works one more year at a $200,000 salary
($130,000 net via the existing 65% factor) then retires, $65,000/yr
spending — fully fundable from the swept-in year-one wage surplus
(confirmed independently via `run_two_dimensional_retirement_projection`
and by hand via `_swr_year_step`), but the old code returned $0. Fixed
by seeding the search bound from whichever of portfolio, the
still-working spouse's income, or the household's own spending scale is
largest (with a small fixed floor), and always running the expansion
loop — which still verifies the seed actually succeeds before trusting
it, and still converges to exactly $0 when nothing can fund any
withdrawal at all (the existing depleted-account test is unaffected).

**P2 — guaranteed-income summary fields used inconsistent Social
Security inflation.** `guaranteed_income_annual` (day-one) never
compounded either spouse's SS benefit by any elapsed years at all — it
just added the raw annual figure once eligible — while the withdrawal
loop's own per-year `year_jss`/`year_uss` formulas always correctly
compound from each spouse's own claim age. `guaranteed_income_steadystate`
had a related but different bug: it compounded BOTH spouses' benefits
by one shared `years_to_steadystate` value instead of each spouse's own
elapsed years since their own claim date. Reproduced: $30,000 claimed at
62, retiring (and measuring day-one income) at 67, 3% inflation — the
correct COLA'd figure is $34,778 (5 years' compounding), reported as a
flat $30,000. A second case (unequal ages, Jason already claiming his
own SS well before Justin claims his spousal benefit) showed the
steady-state bug independently: correct is $41,878 (each spouse
compounded from their own claim date), the old shared-exponent formula
produced $44,337. Both fields now use the exact same per-spouse
claim-date formula the withdrawal loop itself already uses.

**Verified:** full backend suite 1204 passed (was 1198; 6 new tests),
97.58% coverage. Full frontend suite still 35 passed, production build
succeeds, sensitive-data check passed. `safe_withdrawal_annual` is
unchanged by any of these three fixes — verified directly by the P1
test cases, which assert the underlying search still converges to the
same portfolio-only figure section 25 defines while the now-separate
affordability fields correctly flag the shortfall.

Branch: `codex/two-age-swr`, pushed, **still not merged** — second
review round, awaiting approval before Milestone 2 begins.

## 28. Two-age SWR — second independent review round (2026-09-08, on `codex/two-age-swr`)

Second review found the section 27 fix only partially correct: on_track/
cushion_pct were now derived from run_two_dimensional_retirement_
projection's complete dated cash flow, but only for the household's
ORIGINAL stated spending target -- not for total_safe_spend (the
number this tool actually recommends), which was still the unvalidated
sum `safe_withdrawal_annual + guaranteed_day_one`. Replaying that sum
back through the full pension/inflation scenario could still come up
short. Two more issues (cushion always non-positive for a funded plan;
a regression in the steady-state pension-timing fix) came with it.

**P1 — the recommendation itself wasn't validated.** Added
`_household_spending_success_rate_two_age`, a genuine second search
(same N=1000 randomized-return-trial structure and target_success
convergence as `safe_withdrawal_annual`'s own search) over
`candidate_income_today` -- the same input `two_age_spending_need_fn`
already takes, so this search runs the exact bridge/kids/healthcare
formulas, with guaranteed income (pension/SS) and still-working income
netted directly against the full year's need exactly the way
`run_two_dimensional_retirement_projection`'s `simulate_withdrawal_year`
call does (unlike `_swr_success_rate_two_age`'s deliberately separate
treatment, section 25's contract, untouched). `total_safe_spend` is now
this search's own converged boundary (household fixed costs like
`healthcare_pre_medicare` held constant, only the discretionary income
figure searched, then added back so the basis matches `income_target`
exactly) -- a number that is, by construction, safe to replay.
`on_track`/`cushion_pct` now compare this validated boundary against
`income_target`, not a second, unvalidated formula.
`safe_withdrawal_annual`/`safe_withdrawal_rate` (the portfolio-only
search) are completely unchanged -- verified directly by a new test
asserting the portfolio-only boundary is unaffected by any of this.

**P2 — cushion now reflects genuine spare capacity.** The section 27
formula measured `(total_need − total_unmet) / total_need`, which is
always <= 0% for a funded plan (a household either has 0% unfunded or
some positive shortfall -- there's no way for that formula to express
"how much MORE could this plan afford"). `cushion_pct` is now
`(total_safe_spend / income_target − 1) × 100`, using the validated
search boundary above, so materially different portfolios against the
identical target now report materially different (not just
zero-or-negative) cushions -- new test: a $200,000 and a $1,000,000
portfolio against the same $80,000 target both report on_track but
with clearly different cushion_pct, the $1M portfolio's larger. A new
`shortfall_pct` field (`-cushion_pct` when negative, else 0) surfaces
the magnitude of a real shortfall without overloading cushion_pct's
sign.

**P2 — steady-state pension timing regressed in the section 27 fix.**
That fix computed `ss_start_age` from SS claim ages alone, dropping the
ORIGINAL (section 26) code's implicit `max(years_to_ss,
years_to_pension)` -- so `guaranteed_income_steadystate` could include
a pension before it had actually started whenever pension commencement
falls later than both SS claims. Reproduced: both 65 today, Justin
retires now, Jason at 70 -- SS fully active by 67, pension not until
70 -- reported $63,185 (age-67 SS plus a pension unavailable for three
more years); correct, measured at 70 (the later of the two dates, with
SS COLA'd three additional years): $66,726. Fixed with a separate
internal `steadystate_age = phase2_start_age + max(years_to_ss,
years_to_pension)`, gating pension on `steadystate_age >=
jason_effective_start_age` and computing each spouse's own SS exponent
from that same date -- restores the original code's timing guarantee
while keeping the per-spouse claim-date correctness from section 27.
The exposed `ss_start_age` field itself is unchanged (still SS-only,
as before) -- only the internal steady-state date used for
`guaranteed_income_steadystate` changed.

**Performance:** this necessarily doubles SWR's search cost -- two
full N=1000/20-iteration binary searches instead of one. Benchmarked
(5-run average, matched household/inputs) at ~1.37s for single-axis,
~2.91s for two-age (was ~1.36s before this round). This is a real,
material cost of validating the household-spending recommendation the
same way the portfolio-only search already is -- reported plainly, not
minimized. Still well within interactive request latency; no further
optimization attempted this round.

**Verified:** full backend suite 1204 passed, 97.61% coverage (test
count unchanged from section 27 -- three tests rewritten to the new
semantics, two added). Frontend suite still 35 passed, build succeeds,
sensitive-data check passed (a review-round artifact -- a commit hash
fragment in this file's own prose incidentally matched a denylisted
number; the hash reference was removed, not the denylist).

Branch: `codex/two-age-swr`, pushed, **still not merged** -- third
review round pending before Milestone 2 begins.

## 29. Two-age SWR — third independent review round (2026-09-08, on `codex/two-age-swr`)

Third review found two boundary bugs in the section 28 household
spending search, both narrow to that search's edges (its zero lower
bound, and the final on-track comparison) rather than its core
formulas, which the reviewer confirmed correct.

**P1 — the search's zero lower bound was assumed feasible, not
verified.** The search always started `hh_lo = 0` and, after
converging, added `healthcare_pre` (a fixed cost held constant during
the search) back onto whatever boundary was found -- without ever
checking that `candidate_income_today = 0` itself actually succeeds.
With $0 assets, $0 discretionary spending, and a $20,000 fixed
healthcare cost, nothing funds that $20,000 at all, but the old code
still reported `total_safe_spend = 0 + 20000 = 20000` and `on_track =
True` -- while `run_two_dimensional_retirement_projection` correctly
reports the full $20,000 as unmet need. Fixed by evaluating
`success_at_household_spending(0)` first: if it fails, there is no
valid household-spending recommendation to search for at all --
`total_safe_spend` is reported as `0`, not the unfunded fixed cost,
and the search itself is skipped.

**P2 — an exactly-funded plan could still read on_track=False.** The
20-iteration bisection converges to WITHIN a tiny epsilon of the true
boundary, approaching from below by construction (`hh_lo` only ever
moves up when a trial succeeds) -- so a plan whose true boundary
exactly equals its target reliably lands the searched number a hair
short of it (e.g. `$99,999.9998` instead of exactly `$100,000`).
Comparing that approximate number against `income_target` with `>=`
then reports `on_track=False` for a plan Projection itself confirms is
fully funded (reproduced: $200,000/2yr/$100,000 spending/0% returns,
exactly funded). Fixed by evaluating `on_track` independently of the
search's own numeric result: `success_at_household_spending(income_today)
>= target_success` -- a direct, separate yes/no test of the household's
actual stated target through the same success-rate function the search
itself uses, sidestepping the search's approximation error entirely.
`cushion_pct`/`total_safe_spend` still use the searched boundary (its
tiny approximation error is invisible at the 1-decimal rounding both
already apply).

New tests: 3 added (22 total in test_two_age_swr.py, up from 19) -- an infeasible zero-spending floor (fixed costs alone
unfundable), an exactly-funded target at the true boundary, and targets
$1 above/below a known boundary (confirming on_track flips exactly
where it should, not one search-precision epsilon off).

**Verified:** full backend suite 1208 passed, 97.61% coverage --
1205 tests total after section 28 (1204 passing plus the sensitive-
data-check test, which failed that round on a commit-hash fragment in
this file's own prose and is passing again now) plus the 3 new tests
above, all passed on first attempt. Frontend suite still 35 passed,
build succeeds, sensitive-data check passed.

Branch: `codex/two-age-swr`, pushed, **still not merged** -- fourth
review round pending before Milestone 2 begins.

## 30. Two-age Roth Conversion — working-income tax contract, written before implementation (2026-09-08, on `codex/two-age-roth-conversion`)

**CORRECTED 2026-09-08 (independent review, Roth Conversion follow-up,
P1/P2) — sections 30.1-30.3 below originally asserted that gross
working income never affects bracket capacity "in either direction."
That rule was wrong and is corrected in place here (not append-only
for this specific point, since leaving a known-false rule as the
"authoritative" design contract would actively mislead Milestone 3,
which is required to reuse it). The corrected rule: gross wages ARE
ordinary taxable income and DO reduce 22%-bracket room, the same way
pension/SS/pretax draws already do — only the SEPARATE 65% net-of-tax
figure continues to fund the spending-need offset, unchanged. Section
32 has the full reproduction, fix, and reasoning; sections 30.1-30.3
below now state the corrected rule directly rather than leaving the
wrong one on the record with a footnote.**

**CORRECTED AGAIN 2026-09-08 (independent review, second follow-up
pass) — the FIRST correction above was itself incomplete in three
ways, all now fixed and folded into 30.1-30.3's text directly: (1) the
gross-income figure only covered salary (derived by dividing the net
spending-offset figure back out, which structurally can't recover
bonus/RSU since the underlying helper never reads those fields) — now
computed directly from salary+bonus+RSU; (2) the SAME gross figure now
also feeds `_pretax_marginal_tax_rate`'s estimate for the year's own
ordinary SPENDING withdrawal, not just the conversion's own
bracket-capacity math — a household's real marginal rate for a given
tax year can't correctly exclude income that same year's conversion
math already counts; (3) the progressive-tax affordability cap
(section 32) was dropping unused standard-deduction room instead of
treating it as a free zone, understating what a low-income household
could actually afford. Section 33 has the full detail.**

Milestone 2 of 4 (SWR done and merged, section 25-29). Required by the
milestone's own instruction: document the working-income tax contract
BEFORE any implementation code, so bracket-capacity treatment is a
deliberate decision, not an accident of how the code happened to get
written. Everything below was read directly from the existing
single-axis `run_roth_conversion_analysis` (unmodified by this
milestone) and the shared two-age helpers already built for SWR/Monte
Carlo/Stress/Projection — not assumed.

### 30.1 What counts toward taxable income (bracket capacity) — CORRECTED

`base_taxable` (the figure `room_in_22 = BRACKET_TOP_22 - base_taxable`
is measured against) is, in the existing single-axis tool:

    base_taxable = pension + 0.85 * (jason_SS + justin_SS) + pretax_draw - STD_DEDUCTION

Confirmed by reading the code: single-axis Roth Conversion (and every
other withdrawal-phase engine in this app — SWR, Monte Carlo, Stress
Tests, Projection) never adds a working spouse's gross wages to any
ordinary-income figure at all — but that's a **pre-existing gap in
those tools, not a rule to preserve**. It went uncaught there because
none of those other tools ever has BOTH a still-working spouse's
income AND a bracket-capacity calculation in the same year the way
Roth Conversion's own conversion-window can (a household can retire
one spouse and start converting while the other is still earning a
real W-2 salary). Two-age Roth Conversion's own `base_taxable` DOES
include gross wages — see 30.2/30.3. Reproduced during review: a
$500,000 working salary, no other income, ample assets — this
originally recommended a $243,600 conversion inside the 22% bracket,
when the repo's own deductions/bracket table says that salary alone
(500000-32200=467800 > 211400) already leaves $0 room.

The existing `SECOND_EARNER_NET_OF_TAX_FACTOR = 0.65` net-of-tax
approximation is UNCHANGED and still funds the year's SPENDING-NEED
OFFSET exactly as before (`justin_gap_income_for_year`'s return value,
folded into `event_monthly`/`spending_need` the same way life-event
cash is) — that half of the picture wasn't wrong. What was missing is
a SEPARATE gross-income figure that now ALSO enters `base_taxable`.
**Computed directly from salary + bonus + RSU** (`w2_salary`/
`justin_w2_salary`, `annual_bonus_pct`/`justin_annual_bonus_pct` —
applied to salary, and `annual_rsu_value`/`justin_annual_rsu_value` — a
flat dollar figure), the same input fields `run_two_dimensional_
retirement_projection`'s own accumulation-phase math already reads for
this exact spouse — NOT by dividing the net spending-offset figure
back out, which was tried first and only ever recovered the salary
component, since the net figure's own source
(`two_age_still_working_income_inputs`) never reads bonus/RSU inputs
at all. The gross figure is then run through the same
`justin_gap_income_for_year` per-year lookup every other two-age
gap-income figure already uses, just fed a different "at_start" base.
The two figures (net and gross) serve genuinely different purposes and
are BOTH needed, not a contradiction: the net figure answers "how much
cash does this household actually have to spend," the gross figure
answers "how much ordinary taxable income does this household actually
have" — using only one of the two for both questions (or an incomplete
version of the gross one) is what produced the original bugs. The same
gross figure also now feeds `_pretax_marginal_tax_rate`'s estimate for
the year's own pretax SPENDING withdrawal (an optional `gross_income`
parameter, default 0, so every other existing call site across this
file is unaffected) — a household's real marginal bracket for a given
tax year has to reflect ALL of that year's ordinary income, not just
the portion the conversion's own bracket math happens to count.

### 30.2 How each income source affects bracket capacity — CORRECTED

| Source | Effect on `base_taxable` |
|---|---|
| Pension (`two_age_pension_for_year`, gated to Jason's own retirement) | Added in full — no exclusion. |
| Social Security (each spouse's own claim-date formula) | 85% included, same flat approximation as every other consumer's `_pretax_marginal_tax_rate` estimate. |
| Standard deduction | Flat `STD_DEDUCTION_MFJ_2026` subtracted — unchanged constant, no itemization modeled. |
| Pretax-funded portion of the year's spending draw | Added (`pretax_draw` from `simulate_withdrawal_year`'s own `draws["pretax"]`) — taxable- and Roth-funded spending is NOT added, matching single-axis exactly. |
| Taxable- or Roth-funded spending | No effect (principal draws aren't ordinary income; this tool doesn't model capital-gains tax on taxable-account growth, an existing, unchanged limitation). |
| **Working income (wages/bonus/RSU, either spouse)** | **Added in full, gross** — see 30.1/30.3. The SEPARATE net-of-tax figure still funds spending only; this is not a double-count of the same dollars for the same purpose, since one figure feeds cash flow and the other feeds tax liability. |
| Pretax (401k) contributions during phase2 (the still-working spouse may still be contributing) | **Not modeled at all.** This app only models contributions during the pre-retirement accumulation phase (`run_retirement_projection`'s own contribution formulas, run once to produce the starting Roth-conversion-window balances via `run_two_dimensional_retirement_projection`). No withdrawal-phase consumer in this app — single-axis or two-age — reduces a still-working spouse's OWN taxable income for ongoing contributions once the OTHER spouse has already started the conversion window; this is a pre-existing, explicit approximation, not new scope for this milestone. |
| The conversion amount itself | **Not blended into `base_taxable`/`room_in_22` at all.** `base_taxable` determines how much ROOM exists in the selected bracket (a policy target — "fill up through the 22% bracket"); the conversion that fills that room is taxed separately — see section 32 for the progressive/incremental fix to HOW that tax is computed (no longer a flat rate). The conversion still cannot itself push the household into a higher bracket in this tool's `room_in_22` model — that ceiling is a deliberate, unchanged policy choice, not an oversight. |

### 30.3 How the 65% take-home approximation interacts with conversion taxes — CORRECTED

It's a SEPARATE figure feeding a separate calculation, not a
contradiction of it. The 65% factor represents the wage-earner's net
take-home CASH (after their own W-2 withholding, an approximation this
tool has never itemized) — that net figure funds the year's spending
offset, unchanged. Dividing that net figure back out by the same 0.65
factor recovers the GROSS salary, which is what actually determines
the household's real ordinary-income tax bracket for THIS year's
conversion decision — a real household's W-2 withholding doesn't
somehow shrink their AGI or their bracket; it's a prepayment against
the tax owed on the FULL gross amount. Treating the NET figure as if
it were the household's only taxable income (the original, wrong
approach) understated real income and overstated available bracket
room; conversely, adding the GROSS figure on top of the net spending
offset does NOT double-tax the same dollars, because the two figures
answer different questions (cash available to spend vs. ordinary
taxable income) and neither one is itself a tax charge — the actual
tax charge is computed once, on `base_taxable` (now correctly
including gross wages) plus the conversion, via the progressive
formula in section 32. Full payroll-tax modeling (FICA/Medicare
specifically) remains deferred, per the milestone's own scope — this
fix is about ordinary federal/state income tax bracket capacity, not
building a payroll-tax engine.

### 30.4 Order of operations: income, withdrawals, conversion, growth

Per year, unchanged from single-axis and preserved exactly for
two-age:

1. Compute the year's guaranteed income (pension + each spouse's own
   SS), the still-working spouse's gap income (if in phase2), life
   events, and total spending need (`two_age_spending_need_fn` for
   two-age, bridge/kids/healthcare-aware — see 30.6).
2. `simulate_withdrawal_year` runs the full waterfall against the
   YEAR'S OPENING balances: guaranteed income and life-event cash
   offset need first (surplus swept into taxable if income exceeds
   need); any shortfall draws `("taxable", "pretax", "roth")` in
   order, each draw grossed up for tax; **growth is applied LAST**, to
   every bucket's post-withdrawal (and post-surplus-sweep) balance —
   `annual_engine.py`'s own documented contract.
3. `simulate_conversion` is layered ON TOP of that ALREADY-GROWN
   closing state — no further growth is applied within it. The
   conversion amount is therefore valued at the END of the current
   year / START of the next, which is why compounding it forward to
   RMD age uses `RMD_START_AGE - age - 1` (one fewer year than the
   naive age difference), the existing single-axis fix this milestone
   does not touch.
4. The conversion's own tax is paid from OUTSIDE the IRA (taxable,
   by default) — capped by what remaining taxable can actually afford,
   never leaving an unfunded tax bill, exactly as `simulate_conversion`'s
   own docstring requires.

Income "stopping" (the still-working spouse's own retirement,
`phase2 -> phase3`) is handled entirely by
`justin_gap_income_for_year`'s existing `phase2_duration_years` gate —
the same mechanism SWR/Monte Carlo/Stress two-age already use, not a
new one.

### 30.5 What stays explicitly deferred

- **Full payroll-tax modeling** (FICA/Medicare on wages, employer
  matching, etc.) — out of scope per the milestone instruction. Still
  folded, approximately, into the flat 0.65 net-of-tax factor alongside
  ordinary income tax, exactly as it already is everywhere else in this
  app.
- **The conversion pushing the household into a higher bracket** — this
  tool has never modeled that (the conversion is taxed flat at the
  target bracket's own rate, not incrementally against `base_taxable +
  conversion`); unchanged, pre-existing, not addressed by this
  milestone.
- **Ongoing 401k contributions during phase2** — not modeled in any
  withdrawal-phase consumer, single-axis or two-age; unchanged.
- **Capital-gains tax on taxable-account growth/draws** — not modeled
  anywhere in this app; unchanged.
- **Owner-specific tax treatment** (per-spouse account ownership) —
  out of scope until Milestone 4's ownership foundation; conversions
  here, like every other two-age tool, operate on the household's
  pooled/joint bucket totals (`account_ownership_limitation`).

### 30.6 Two-age-specific additive scope (new for this milestone)

- Both `jason_ret_age`/`justin_ret_age` required together, via the
  existing `_require_both_two_age_or_neither` gate (unchanged).
- The conversion window starts at `phase2_start_age` (the earlier
  retiree's own retirement — the same anchor every other two-age
  withdrawal-phase consumer uses), not Jason's own
  `jason_effective_start_age` — a household where Justin retires first
  can start converting as soon as ANY retirement income exists to
  spend from, matching the household's actual timeline. It still runs
  through `RMD_START_AGE` (Jason's own age — RMDs and the pretax
  account being converted are Jason-anchored throughout this entire
  app, an existing, unchanged convention; there is no separate
  justin_ret_age-anchored pretax pool). Built via
  `build_two_person_timeline(..., retirement_end_age=RMD_START_AGE)` —
  reusing the exact same timeline object every other two-age consumer
  builds, just with a different horizon endpoint, rather than a new
  timeline abstraction.
- Spending need uses the FULL `two_age_spending_need_fn` (bridge/kids/
  healthcare-aware, age-55 branch included) instead of single-axis
  Roth Conversion's own simpler `income + healthcare` formula (which
  never modeled bridge/kids at all, a pre-existing, documented
  single-axis limitation). **This is an intentional, documented
  difference from reuse, not a bug**: two-age's own shared spending
  helper is strictly more complete, and per the milestone's own
  instruction ("reuse the shared ... spending ... helpers"), the
  richer shared formula is used rather than reimplementing single-axis's
  narrower one a second time for two-age.
- Guaranteed income and gap income use the exact same helpers section
  25-29's SWR work already established:
  `two_age_pension_for_year`, per-spouse SS claim-date formulas (the
  same inline pattern four other call sites in this file already use),
  `two_age_still_working_income_inputs` + `justin_gap_income_for_year`.
- Starting pretax/Roth/taxable balances come from
  `run_two_dimensional_retirement_projection`'s own UNROUNDED bucket
  fields (`pretax_at_phase2_start`/`roth_at_phase2_start`/
  `taxable_at_phase2_start`), the same pattern SWR/Monte Carlo/Stress
  two-age already use — not a second, independent accumulation-phase
  calculation.
- The with-conversion vs. no-conversion comparison remains a single
  deterministic pass under identical assumptions/return path for both
  (this tool has never used randomized trials, unlike SWR/Monte Carlo)
  — unchanged methodology, just run against the two-age timeline and
  income/spending helpers above.

Heatmaps, a full payroll-tax engine, and unrelated cleanup remain out
of scope, per the overall instruction.

## 31. Two-age Roth Conversion — implementation report (2026-09-08, on `codex/two-age-roth-conversion`)

Milestone 2 of 4 (SWR done, merged to main). Full working-income tax
contract in section 30, written and committed before any code. This
section is the required accuracy/performance report for the completed
implementation.

**What was built:** `jason_ret_age`/`justin_ret_age` on
`run_roth_conversion_analysis`, both required together, delegating to
`_run_roth_conversion_analysis_two_age`. Reuses the exact shared
helpers section 25-30 already established: `two_age_pension_for_year`,
`two_age_spending_need_fn`, `two_age_still_working_income_inputs`,
`justin_gap_income_for_year`, `build_two_person_timeline`,
`run_two_dimensional_retirement_projection` for starting balances. The
per-year tax math itself (`base_taxable`/`room_in_22`,
`simulate_withdrawal_year` then `simulate_conversion` layered on the
already-grown closing state) is the identical shape single-axis
already uses -- no second, independent formula set, only fed two-age
inputs.

**Accuracy — test-first, per instruction:** 12 tests, all hand-computed
by replaying the shared, already-reviewed primitives' own documented
contracts (not inferred from the code under test), written and
committed RED before any implementation code (confirmed failing with
`TypeError` against the not-yet-existing params). Two bugs were found
against this RED suite during implementation, both fixed before the
first green commit:

1. `build_two_person_timeline`'s own `end_age` is floored at
   `phase2_start_age + 1` (every OTHER two-age consumer needs at least
   one withdrawal-loop year -- "already retired" never means "zero
   years of retirement" for SWR/Monte Carlo/Stress/Projection).
   Reusing it via `retirement_end_age=RMD_START_AGE` for
   `conversion_years` silently forced at least 1 conversion year even
   for a household already at or past RMD age, where 0 is the correct
   answer. Fixed by building the timeline with no `retirement_end_age`
   override at all (`age()`/`justin_age_at()`/`jason_effective_start_age`
   don't depend on it) and computing `conversion_years =
   max(0, RMD_START_AGE - phase2_start_age)` directly.
2. Two of the new tests were internally inconsistent: they varied
   `jason_ret_age` between a "with working spouse" and "without" case
   to introduce gap income, which also moved Jason's own pension
   timing (gated to his actual retirement) -- confounding the
   comparison the tests claimed to make. Fixed by holding
   `jason_ret_age` constant and using Justin as the later-retiring,
   working spouse in both cases instead.

All 12 tests passed after both fixes, no adjustment to any
hand-computed expected number. Covers: a full 2-year schedule with
every per-year field hand-verified (bracket room, conversion amount,
tax cost, Roth future value, tax avoided, net benefit, ending
balances); both retirement orders (proving symmetry -- identical
bracket math and conversion amounts regardless of which spouse's
salary funds the gap income); working income's zero effect on bracket
capacity in either direction (a direct comparison plus a pension-alone-
exhausts-the-bracket case, the milestone's explicit "no room in the
bracket" requirement); a past retirement selection (conversion window
starts at the real current age, zero years once already at RMD age);
unequal ages (each spouse's own SS claim age); nonzero inflation/growth
(the one-fewer-year-of-compounding relationship holds under real
growth, not just 0%); a full shortfall case; and the both-ages-required
gate.

**Performance:** unlike SWR/Monte Carlo, this tool has never used
randomized trials -- it's a single deterministic pass per call, same
methodology single-axis already uses. Benchmarked (20-run average,
matched household/inputs): single-axis ~0.97ms, two-age ~0.45ms per
call. Both effectively instant; no performance exception needed or
expected, and none was.

**Frontend:** Roth Conversion has its own standalone page
(`RothConversion.jsx`, not part of Simulation.jsx's Monte Carlo/Stress
tabs) -- confirmed by reading the nav/page list before touching
anything, not assumed. Added the same "Use Two Independent Retirement
Ages" toggle/two-input pattern `StressTestWhatIf.jsx` already
established for Monte Carlo/Historical Stress, POSTing both ages
instead of GETting the single `ret_age` when on. The summary header
shows both ages in two-age mode; `SecondEarnerNote` (mode-aware
amount/years/personLabel, the same convention `Simulation.jsx`'s own
`secondEarnerNoteProps` uses) discloses the working spouse's gap
income. SS timing stays visible and editable in both modes, matching
the SWR/Monte Carlo precedent (independent review, section 23).

**Verified:** full backend suite 1223 passed, 97.66% coverage. Full
frontend suite 38 passed (was 35), production build succeeds.
Sensitive-data check passed.

**Explicitly out of scope for this milestone**, unchanged from the
overall plan: full payroll-tax modeling, the conversion itself pushing
into a higher bracket, ongoing 401k contributions during phase2,
capital-gains tax, owner-specific treatment (Milestone 4), Tax
Efficiency (Milestone 3), Survivor Scenario (Milestone 4).

Branch: `codex/two-age-roth-conversion`, pushed, **not merged** — per
the explicit instruction ("Do not merge or start the next milestone
until the current one is reviewed and approved"), for independent
review.

## 32. Two-age Roth Conversion — independent review fixes (2026-09-08, on `codex/two-age-roth-conversion`)

Independent review of `codex/two-age-roth-conversion`'s prior commit
found three issues, all now fixed. The first two are corrections to
section 30's own working-income tax contract, not just the code —
section 30.1-30.3 above are revised in place (marked CORRECTED) rather
than left asserting a wrong rule.

**P1 — working income must consume conversion bracket capacity.**
`base_taxable` never included gross wages at all — reproduced: a
$500,000 working salary, no other income, ample assets, recommended a
$243,600 conversion inside the 22% bracket, when that salary alone
(500000-32200=467800 > 211400) already leaves $0 room under the repo's
own deductions/bracket table. Fixed by deriving a GROSS wage figure
(`still_working_income_this_year / SECOND_EARNER_NET_OF_TAX_FACTOR` —
exact, since the factor is a flat multiplier with no other
nonlinearity) and adding it to `base_taxable` in full, alongside
pension/SS/pretax draws. The existing 65% net-of-tax figure is
UNCHANGED and still funds the spending-need offset only — the two
figures serve different purposes (cash available to spend vs. ordinary
taxable income) and using both is not a double-count, since neither
one is itself a tax charge.

**P2 — conversion tax must be progressive/incremental, not flat.**
`simulate_conversion` charged a flat 22% x amount — reproduced: $0
other taxable income, a $243,600 conversion cost $35,932 under the
real progressive table, not the $53,592 flat 22% charged. Added two
new module-level helpers: `_progressive_federal_tax(taxable_income,
brackets)` (total tax owed on an amount under a bracket table, 0 for
non-positive income) and `_incremental_conversion_tax(pre_conversion_
taxable, conversion_amount, state_tax_rate, brackets)` (tax(base +
conversion) − tax(base), both floored at 0 before subtracting — a
negative `base_taxable`, this file's own convention for unused
standard-deduction room, is absorbed tax-free by the conversion before
progressive rates apply, exactly like a real return). The
affordability cap (`_max_conversion_for_tax_budget`) walks the same
bracket table greedily from the pre-conversion taxable-income point,
replacing the old flat `budget / 0.22` cap, so the amount a household
can actually AFFORD to convert and the tax it's actually CHARGED now
agree with each other by construction — they didn't before (the old
affordability cap priced every dollar at the top bracket's rate,
underselling what a real progressive tax bill would actually cost for
the same cash). `room_in_22` itself (the bracket-EDGE policy target)
is unchanged in form — it's a "how far to fill" ceiling, not a tax-
liability calculation, and stays meaningful under progressive taxation
exactly as under flat. The future-RMD-tax-avoided ESTIMATE
(`roth_fv_at_73`/`tax_avoided_at_73`, still a flat 24% marginal-rate
assumption, matching single-axis's own documented convention and the
frontend's own "Estimate assumes a 24% marginal rate" disclosure) is
unchanged — this fix is scoped to the conversion's OWN tax cost, not
that separate, deliberately-simplified future estimate.

Both fixes are scoped to the two-age function only — single-axis
`run_roth_conversion_analysis` is untouched (same boundary every prior
milestone in this series has kept: single-axis behavior is preserved
exactly, only two-age gets new behavior). Single-axis has the
analogous gap (no gross-wage inclusion, flat-rate conversion tax) —
documented here as a known, unresolved discrepancy between the two
paths, not silently ignored, and out of scope for this milestone to
fix (per the same "preserve existing single-axis meaning" precedent
SWR's own review rounds established).

9 of the 12 existing tests were rewritten with newly hand-computed
progressive-tax numbers (all four bracket boundaries recomputed by
hand from the real 2026 MFJ table, not inferred from the code under
test); 3 were unaffected (no wages, and their own assertions didn't
reference tax_cost). All 12 passed after the fix, matching the newly
hand-computed values exactly. `TestWorkingIncomeNeverAffectsBracketCapacity`
(the class whose own premise was the bug) is replaced by
`TestWorkingIncomeConsumesBracketCapacitySymmetrically`, proving the
CORRECT property instead: a $50,000 gross salary reduces room by
exactly $50,000 (211400-(0+50000-32200)=193600, down from the no-wage
baseline's 243600), identically regardless of which spouse earns it,
and a $500,000 salary alone can exhaust the bracket entirely (the
review's own reproduction, now correctly returning $0 room instead of
$243,600).

**P2 (frontend) — a staleness/generation guard was missing.**
`RothConversion.jsx`'s fetch effect had no cancellation or generation
check at all — an older, slower response could land after a newer one
and overwrite it, or clear `loading` incorrectly after a mode switch
had already superseded it. Fixed with the same `genRef` counter
pattern `Simulation.jsx`'s own Monte Carlo/Stress sections already
established: bumped on every effect firing, each `.then`/`.catch`/
`.finally` checks the counter before touching state. New test
simulates two out-of-order responses directly (the second, newer
request's promise resolved before the first, older one) and asserts
the final rendered state reflects the newer result, not the stale one
that happened to resolve last.

**Verified:** full backend suite 1223 passed, 97.62% coverage (12
tests, 9 rewritten). Frontend suite 39 passed (was 38, +1 staleness
test), build succeeds, sensitive-data check passed.

Branch: `codex/two-age-roth-conversion`, pushed, **still not merged** —
second review round, awaiting approval before Milestone 3 begins.

## 33. Two-age Roth Conversion — second independent review round (2026-09-08, on `codex/two-age-roth-conversion`)

Second review confirmed the salary and flat-rate-tax reproductions
fixed, and found three further inconsistencies in the same tax
treatment, all now fixed.

**P1 — the affordability cap dropped unused deduction room.**
`_max_conversion_for_tax_budget` floored `pre_conversion_taxable` at 0
before walking the bracket table, silently discarding the SAME
unused-standard-deduction free zone `_incremental_conversion_tax`'s
own formula already accounts for (both sides of its subtraction
floored at 0, so a conversion "fills" leftover deduction room
tax-free first). This understated what a low-taxable-income household
could actually afford, inconsistent with the very tax formula the cap
is supposed to match — a household with $0 other income, $1,000,000
pretax, and only $40,000 taxable cash had its affordable conversion
capped at $228,350 (the bracket-walk alone), when the true figure
(bracket-walk plus the $32,200 free zone) is $260,550 — which exceeds
`room_in_22` ($243,600), so `room_in_22` should have been the binding
constraint all along, not the artificially-low affordability cap.
Fixed: `free_room = max(0, -pre_conversion_taxable)` is added
unconditionally (it costs nothing, so even a $0 budget can still
afford it) before the budget-constrained bracket walk begins.

**P1 — bonus/RSU compensation was still absent from the gross-income
figure.** The first fix derived the gross-wage figure by dividing the
net spending-offset figure back out by the 65% factor — exact for
salary, but structurally incapable of recovering bonus/RSU, since the
net figure's own source (`two_age_still_working_income_inputs`) never
reads `annual_bonus_pct`/`annual_rsu_value` (or the `justin_` versions)
at all. Fixed by computing the gross figure directly from
salary+bonus+RSU (the same fields `run_two_dimensional_retirement_
projection`'s own accumulation-phase math already reads for this
spouse), run through the same `justin_gap_income_for_year` per-year
lookup every other two-age gap-income figure uses, just with a
different "at_start" base. Verified: $100,000 RSU alone (no salary)
now reduces room by exactly the same amount a $100,000 salary alone
would; a $100,000 salary + 20% bonus + $30,000 RSU ($150,000 gross)
shows real, additional room reduction beyond the salary-only figure.

**P2 — spending withdrawals still used a tax rate that ignored the
working salary.** `_pretax_marginal_tax_rate` (which prices the year's
own ordinary pretax SPENDING draw, not the conversion) never included
gross wages, even after the first fix added them to `base_taxable` for
conversion-bracket-capacity purposes — the same household's same tax
year would show an elevated bracket for its conversion decision but an
artificially low rate for its own spending withdrawal, an internally
inconsistent picture. Fixed by adding an optional `gross_income`
parameter (default 0, so every other existing call site — single-axis
and every other two-age consumer — is completely unaffected) and
passing the same gross-wage figure computed above at both call sites
in the two-age Roth Conversion function (the with-conversions loop and
the no-conversion baseline, keeping their own spending-draw taxation
apples-to-apples). Verified directly against the function itself: $0
gross income prices at the bottom bracket (10%); $500,000 gross income
(after the standard deduction, landing in the 32% bracket) prices at
32% — isolated from the full per-year loop's own cash-flow effects,
which would otherwise confound a same-scenario comparison (gross wages
affect both the tax rate AND the cash available to spend
simultaneously, by design).

CALCULATION_CONTRACT.md section 30 corrected again in place (marked
"CORRECTED AGAIN," not silently rewritten) — the first correction's
own description of the gross-income derivation is now itself
corrected to describe the direct salary+bonus+RSU computation, and
30.2's table/30.1's text now also cover the spending-withdrawal-rate
consistency fix.

4 new tests (RSU-alone parity with salary, bonus+RSU stacking, a
direct unit-level rate comparison for the withdrawal-tax fix, and the
affordability-cap fix reaching the full bracket room instead of an
artificially low cap) — all passed on first attempt, no adjustment to
any hand-computed number. The 16 existing tests were unaffected (none
of them exercised the affordability cap as the binding constraint, so
that fix changes no prior test's outcome).

**Verified:** full backend suite 1227 passed (1223 + 4 new), 97.62%
coverage. Sensitive-data check passed. Frontend unaffected (no
frontend change this round).

Branch: `codex/two-age-roth-conversion`, pushed, **still not merged** —
third review round, awaiting approval before Milestone 3 begins.

## 34. Two-age Tax Efficiency — design notes, written before implementation (2026-09-08, on `codex/two-age-tax-efficiency`)

Milestone 3 of 4. Branched from `codex/two-age-roth-conversion` (not
`main`) since this milestone's own instruction requires reusing that
branch's reviewed working-income tax treatment, which isn't on `main`
yet (Milestone 2 is pushed but not merged — Jason's explicit
instruction: keep going without waiting for that review to land,
circle back to merge both once the auditor is available again).

**Scope confirmed before writing any code:** `run_tax_efficiency_
simulation` is exposed at `GET /api/simulation/tax-efficiency`
(`main.py`) but **nothing in the frontend calls that endpoint at
all** — grepped the entire `frontend/src/` tree for any reference to
`tax-efficiency`/`TaxEfficiency`/`tax_efficiency`; there are none. This
tool has no existing UI surface. Per the milestone's own instruction
("Confirm where this tool is actually exposed. Do not create an
unrelated new page without approval"), this branch adds two-age
support to the calculation engine and its API endpoint only — no new
frontend page. Building one would be new, unapproved UI surface, not
"extending existing two-age support."

**How "reuse the reviewed working-income tax treatment from the Roth
milestone" applies here:** read directly from the existing single-axis
`run_tax_efficiency_simulation` — this tool has never had bracket-
aware or marginal-rate-aware taxation at all. Every pretax withdrawal
(RMD or ordered draw) is taxed at a single **flat** `TAX_PRETAX = 0.22`
constant, taxable draws at a flat `TAX_TAXABLE = 0.15` (LTCG
approximation), Roth at 0% — regardless of the household's actual
income level, guaranteed income, or (for two-age) a working spouse's
salary. There is no `base_taxable`/`room_in_22` concept here, and
consequently no place where Roth Conversion's actual bug (gross wages
omitted from a bracket-capacity/marginal-rate calculation) could
recur — the flat rate doesn't depend on total income in either the
single-axis or two-age version. The relevant part of the Roth
milestone's reviewed treatment that DOES apply: the still-working
spouse's income is modeled via the SAME net-of-tax
(`SECOND_EARNER_NET_OF_TAX_FACTOR`) figure, folded ONLY into the
spending-need offset (`justin_gap_income_for_year`, added to the
existing `life_event_monthly` channel via `_cash_available_offsets_
need`, exactly matching single-axis's own established pattern) — never
into any tax-rate calculation, because none exists here to feed. This
is not a gap being silently carried forward; it's confirmed, by
reading the code, that there is nothing analogous to fix.

**Shared engine reuse (no second, independent formula set):**
`build_two_person_timeline`, `two_age_spending_need_fn` (bridge/kids/
healthcare-aware — richer than single-axis's own healthcare-only
formula here too, same intentional, documented difference Roth
Conversion's section 30.6 already established), `two_age_pension_for_
year`, per-spouse SS claim-date formulas (the same inline pattern six
other call sites in this file already use), `two_age_still_working_
income_inputs` + `justin_gap_income_for_year`,
`run_two_dimensional_retirement_projection` for starting balances
(unrounded buckets). The three draw-order strategies themselves reuse
`_ordered_draw`/`_optimal_draw` completely unmodified — both already
take `tax_pretax_rate`/`tax_taxable_rate` as plain parameters, so
two-age needs no changes to either. `_cash_available_offsets_need` is
reused directly (already a module-level, shared, tested function — not
duplicated a second time for two-age, unlike the single-axis version's
own older inline copy this function was originally extracted from).

**Preserving the milestone's explicit requirements:** all three
strategies run against ONE shared `all_returns` array (built once,
same reproducible-seed pattern every other consumer uses) and
identical timeline/income/spending/life-events/starting-balances per
trial — "only the intended strategy should differ" holds by
construction, matching single-axis's own existing structure exactly.
Signed cash flows are preserved via `_cash_available_offsets_need`'s
existing surplus/shortfall handling (unchanged). Unfunded spending is
reported via `any_unmet_need`/`success_rate`'s existing "funded AND
positive ending balance" definition (unchanged from single-axis; not
in scope to revisit here, matching the same boundary SWR's own
`survived` field kept in section 23).

**Performance:** this loop runs N=1000 trials × `retire_yrs` years ×
3 strategies per call — the same order of magnitude as Monte Carlo's
own loop. Will be benchmarked against single-axis after implementation
(same methodology every prior milestone's report used) and reported
plainly.

Both retirement ages required together via the existing
`_require_both_two_age_or_neither` gate, unchanged pattern.

## 35. Two-age Tax Efficiency — implementation report (2026-09-08, on `codex/two-age-tax-efficiency`)

Milestone 3 of 4. Design notes in section 34, written before any code.
This section is the required accuracy/performance report.

**What was built:** `jason_ret_age`/`justin_ret_age` on
`run_tax_efficiency_simulation`, both required together, delegating to
`_run_tax_efficiency_simulation_two_age`. Reuses the exact shared
helpers section 25-34 already established, and the completely
UNMODIFIED `_ordered_draw`/`_optimal_draw`/`_cash_available_offsets_
need` — no second, independent formula set, only fed two-age inputs.
All three strategies (`taxable_first`/`roth_first`/`optimal`) run
against one shared `all_returns` array and identical per-trial
timeline/income/starting balances, matching single-axis's own
structure and the milestone's explicit "only the intended strategy
should differ" requirement.

**Scope confirmed before writing any code:** this tool has no frontend
UI at all (section 34) — no page was added, per the milestone's own
"confirm where this tool is exposed" instruction.

**Accuracy — test-first, per instruction:** 12 tests, all hand-computed
by replaying `_ordered_draw`/`_optimal_draw`'s own documented gross-up
contracts, written and committed RED before any implementation code.
One real error was found and fixed against this RED suite during
implementation (not a review finding): the initial hand-computed
expected numbers assumed taxable draws were tax-free, matching Roth
Conversion's own `simulate_withdrawal_year` convention
(`taxable_rate=0.0` there) — but Tax Efficiency's `_ordered_draw` is
called with `tax_taxable_rate=TAX_TAXABLE=0.15` (the LTCG
approximation this file's own docstring documents), a genuine
difference between the two tools. All affected numbers were recomputed
by hand against the correct rate; all 12 tests passed unchanged after
the fix, no adjustment to any implementation code needed. Covers: a
full 2-year `taxable_first` schedule and a `roth_first` counterpart on
the same household (both hand-verified against `_ordered_draw`'s own
arithmetic); either retirement order (byte-identical results in both
directions — this tool's flat tax rate has no bracket-capacity concept
for working income to ever affect, unlike Roth Conversion, so
"symmetric" here means literally identical, not just proportionally
so); a past retirement selection and unequal ages; nonzero inflation/
growth; RMD forced regardless of spending need (added for coverage —
none of the other cases reached RMD age); a full shortfall case (0%
success, not a false 100%); a zero-spending sanity check proving all
three strategies share identical starting conditions; and the
both-ages-required gate.

**Performance:** benchmarked (5-run average, matched household/
inputs) at ~0.196s single-axis vs ~0.208s two-age — no material
difference (~6%), well within call-to-call variance; no performance
exception needed.

**Verified:** full backend suite 1241 passed (1238 + 3 API tests),
97.68% coverage. Sensitive-data check passed. No frontend changes
this milestone (see scope note above).

**Explicitly out of scope for this milestone**, unchanged from the
overall plan: Survivor Scenario + account ownership (Milestone 4,
design-gated), real payroll-tax modeling, heatmaps, unrelated cleanup.

Branch: `codex/two-age-tax-efficiency`, pushed, **not merged** — per
Jason's explicit instruction (auditor unavailable; proceed through
remaining milestones, circle back to review/merge SWR-forward once
it's back), for independent review whenever that resumes.

## 36. Milestone 4 — Survivor Scenario + account ownership: DESIGN PROPOSAL, gated on approval before implementation (2026-09-08, on `codex/two-age-survivor-design`)

Milestone 4 of 4. Per the milestone's own explicit instruction, this
section is design-only — no application code changes on this branch.
Implementation does not start until this design is approved.

### 36.1 Key discovery: ownership is already tracked, not missing

`accounts.owner` is a required (`NOT NULL`), already-populated column —
confirmed by reading `db.py`'s schema and `Accounts.jsx`'s own fixed
domain: `jason` / `justin` / `joint` / `abby` / `cooper` / `trust`.
Every real account in this app already has an owner. The retirement-
phase engines (SWR, Monte Carlo, Roth Conversion, Tax Efficiency,
Projection) already read this field today — but only to EXCLUDE kids'
accounts (`owner not in ("abby","cooper")`); every remaining owner
(`jason`/`justin`/`joint`/`trust`) is then summed into one pooled
total per bucket type (pretax/roth/taxable/hsa), discarding the
attribution that was already there. **There is no "unknown ownership"
case to invent a policy for** — every account is already attributed,
by the household, at entry time. The milestone's own phrase
"including treatment of unknown ownership" is addressed by this
finding: v1 needs no fallback/default-ownership policy, because the
data already has none missing. (`trust`-owned accounts get an explicit
policy below since a trust isn't a person.)

Similarly, contribution inputs are already per-spouse:
`w2_salary`/`employee_401k_pct`/`annual_bonus_pct`/`annual_rsu_value`
(Jason's own) vs. `justin_w2_salary`/`justin_employee_401k_pct`/
`justin_annual_bonus_pct`/`justin_annual_rsu_value` (Justin's own) —
and `run_two_dimensional_retirement_projection`'s own accumulation
math ALREADY computes each spouse's contribution growth as a separate
intermediate value before summing them
(`_contrib_fv(annual_401k_pretax, jason_contrib_years, ...) +
_contrib_fv(justin_annual_401k_pretax, justin_contrib_years, ...)`).
Splitting by owner is not inventing new information; it's preserving
information the code already computes and then discards at the final
`+`.

### 36.2 Proposed v1 ownership model

- `jason`-owned accounts → Jason's own bucket.
- `justin`-owned accounts → Justin's own bucket.
- `joint`-owned accounts → a separate JOINT bucket, not merged into
  either spouse's own bucket and not split 50/50 (explicit instruction:
  "do not silently split pooled accounts 50/50"). For survivor
  purposes, a jointly-titled account is fully accessible to the
  survivor by construction (the real-world legal reality of joint
  titling) — no transfer logic needed, it simply continues being
  available.
- `trust`-owned accounts → treated as part of the JOINT bucket for v1,
  flagged explicitly as a simplification (this app has never modeled
  trust succession terms, and still doesn't after this milestone).
- `abby`/`cooper` (kids' accounts) → excluded, unchanged from today.

Every currently-pooled consumer (SWR, Monte Carlo, Roth Conversion,
Tax Efficiency, single-axis and two-age Projection) is **completely
unaffected** — this is new, additive scope: `run_two_dimensional_
retirement_projection` would gain optional owner-split output fields
(`pretax_at_phase2_start_jason`/`_justin`/`_joint`, etc.) ALONGSIDE its
existing pooled totals, not replacing them. The invariant `jason +
justin + joint == the existing pooled total, exactly` is the core
correctness property, directly parity-testable against the already-
reviewed pooled figure — "without changing household totals" is
enforced by construction, not by inspection.

### 36.3 Death timing: before, during, and after the middle phase

Two-age's own `phase2_start`/`phase3_start` structure already
distinguishes "before both retired" (phase2, one spouse still working)
from "after both retired" (phase3) — death can fall in either, and
the existing single-axis Survivor Scenario's own death-year-indexing
logic (snap to the nearest modeled year, no double-counted death-year
spending) carries over unchanged, just re-anchored to `phase2_start`
instead of the single-axis `effective_start_age`.

- **Income:** the deceased spouse's own W2/gap income stops entirely,
  the survivor's own continues (net-of-tax, unchanged) if the survivor
  is the one still working. This generalizes the existing single-axis
  rule (`gap_income_this_year = 0 if deceased == "justin" else
  ...`, since only Justin could ever be the still-working spouse in
  the single-axis model) to whichever spouse is `later_retiree` — the
  gate becomes `0 if deceased == later_retiree else (normal gap income)`.
- **Contributions:** NOT modeled during any withdrawal-phase consumer
  in this app, single-axis or two-age, before or after this milestone
  — an existing, unchanged limitation (confirmed by reading every
  other consumer's own withdrawal loop). Survivor's v1 does not
  introduce ongoing contributions during a mid-phase death either,
  for the same reason every other consumer doesn't: explicitly
  deferred, not silently assumed.
- **Pension:** already a single, Jason-attributed benefit
  (`pension_for_age`'s own documented "one employer pension (Jason's,
  in the default data)" assumption, section 13) with 100% Joint &
  Survivor already modeled. Unchanged — if Jason dies, Justin (the
  survivor) continues receiving it; if Justin dies, Jason (its actual
  owner) simply keeps receiving what he was always entitled to. No new
  ownership logic needed here; the existing single-pension-source
  design decision already resolves this correctly by construction.
- **Social Security:** already a genuinely per-individual benefit
  (`jason_social_security`/`justin_social_security`, each spouse's own
  claim age), already correctly modeled as switching to the higher
  (survivor) benefit. Two-age's own per-spouse claim-date formulas
  (established in sections 25-35) apply directly — no ownership-model
  change needed.
- **Insurance:** already per-spouse (`jason_life_*` vs `justin_life_*`
  input fields) — the deceased's own policies pay out. Unchanged.
- **Account treatment at death:** the deceased's individually-owned
  accounts merge into the survivor's own buckets (v1 assumes the
  common spousal-rollover/inheritance treatment — the simplest real
  case, not modeling non-spouse-beneficiary or 10-year-rule inherited-
  IRA timing, which genuinely differ and are explicitly deferred).
  Joint accounts require no transfer at all (already fully accessible).
  The household TOTAL portfolio (ignoring the insurance payout itself)
  is unchanged by death — pure relabeling from "jason + justin + joint"
  to "survivor + joint," not a value change — verified via reconciliation
  against the pre-death owner-split totals from section 36.2.

### 36.4 Owner-specific withdrawal/RMD rules: v1 scope

**Required for v1:**
- Post-death RMDs computed on the SURVIVOR's own age (not always
  Jason's, which is what every current consumer does even in two-age
  mode) against the merged pretax total (survivor's own + inherited).
  This is a real, new capability — RMD is already a simple age+balance
  lookup (`_rmd(balance, age, rmd_start_age(age))`), so this just means
  calling it with the survivor's own age instead of always Jason's.
- Household-total reconciliation (36.2's invariant) verified by
  independent test cases, both spouses' death paths, unequal ages,
  and retirement-boundary timing (death exactly at a phase boundary).
- Insurance recommendation figures re-verified by injecting the
  recommended funding back into the simulation and confirming
  `survives` flips to `True` — the same "does the number actually
  work when replayed" standard section 33's Roth Conversion review
  established for a different tool, applied here.

**Explicit approximations, deferred (not silently assumed):**
- Per-spouse RMDs during NORMAL (both-alive, non-Survivor) two-age
  operation — every current two-age consumer (SWR/Monte Carlo/Roth
  Conversion/Tax Efficiency/Projection) still computes ONE household
  pretax RMD keyed to Jason's age only. Fixing this for the living-
  couple case would mean touching all 5 of those consumers' own
  accumulation/withdrawal math — a substantially larger undertaking
  than "Survivor + an ownership foundation," explicitly out of scope
  for this milestone.
- Inherited-IRA-specific RMD timing (10-year rule, non-spouse
  beneficiary rules) — v1 assumes simple spousal rollover.
- Trust succession terms — trusts folded into the joint bucket, no
  trust-specific logic.
- The MFJ→Single tax-bracket jump after death — already an explicitly
  documented, unaddressed limitation in the EXISTING single-axis
  Survivor Scenario (its own docstring says so); unchanged, still
  deferred, not newly introduced by this milestone.

### 36.5 What this milestone does NOT do without further approval

Per the explicit instruction, this section is a proposal, not a plan
already underway. **No account-ownership ledger, ledger migration, or
Survivor calculation change has been implemented on this branch.**
Awaiting approval of sections 36.1-36.4 before writing any code.

Branch: `codex/two-age-survivor-design` (branched from
`codex/two-age-tax-efficiency`, not `main`, matching the same
sequencing note section 35/`CONSOLIDATION_HANDOFF.md` already record).

## 37. Milestone 4 — Survivor Scenario + account ownership: REVISED DESIGN PROPOSAL (2026-09-08, on `codex/two-age-survivor-design`)

Revises section 36 per explicit review feedback (five points, all
addressed below). Still design-only — no application code changed on
this branch. Section 36 is left in place for history rather than
edited in place; this section supersedes it on every point where they
disagree.

### 37.1 Trust assets stay a distinct bucket, with an explicit availability assumption

Section 36 folded `trust`-owned accounts into the joint bucket. **Wrong
— reverted.** A trust is a separate legal entity with its own
succession terms; whether its assets are actually available to a
surviving spouse depends on the trust's own provisions (revocable vs.
irrevocable, the spouse's status as trustee/beneficiary, distribution
terms this app has no way to know), not on how the household happened
to label the account.

**Revised model:** `trust` becomes its own bucket (`jason`/`justin`/
`joint`/`trust`), tracked and reported separately, never auto-included
in the survivor's spendable resources. A new explicit input,
`trust_available_to_survivor` (boolean, default `False`), controls
whether Survivor Scenario's own calculation includes it — the default
being "not available" is the conservative choice (never overstates
survivor resources), and choosing `True` is a deliberate, visible
household assumption, not an inferred one. The trust balance and
whichever assumption was used are both surfaced in the result payload
so the number is never presented without its own caveat attached.

### 37.2 "Joint" ownership does not imply automatic survivorship

Section 36 treated `joint`-owned accounts as automatically passing to
the survivor in full, inferred from the `joint` label. **Wrong** — as
the review notes, joint ownership isn't a single legal arrangement.
Joint tenants with right of survivorship (JTWROS, the common case for
a married couple's shared brokerage/bank accounts) transfers to the
survivor automatically; tenants in common does not — the deceased's
share passes through their estate instead, which this app has no model
of at all (no will/probate/beneficiary-designation data).

**Revised model:** a new explicit input, `joint_accounts_survivorship`
(boolean, default `True` — JTWROS is materially more common for a
married couple's shared accounts in practice, so this is a reasonable
default, but it is a STATED default, not an inference from the word
"joint"). When `True`, the joint bucket passes to the survivor in
full, as section 36 proposed. When `False`, only half of the joint
bucket is treated as the survivor's own (the deceased's share is
modeled as lost to the survivor's household resources — NOT split
50/50 as a calculation choice invented here, but as the literal legal
default for tenants-in-common ownership when no other split was
specified — this is the one place a 50% figure appears in this design,
and it's a named legal default being modeled, not an ownership-
attribution guess). Both the assumption used and its effect are
surfaced in the result payload, same as 37.1.

### 37.3 Survivor RMDs: a named, narrow spousal-rollover election — not applied automatically to every deceased-owned pretax account

Section 36 proposed merging every deceased-owned pretax account into
the survivor's own bucket and computing RMDs on the survivor's own age
against the merged total, unconditionally. **Too broad** — a surviving
spouse has multiple real options for an inherited IRA (treat as their
own via rollover; keep as a separately-titled inherited IRA with its
own RMD schedule; for an inherited 401(k) specifically, options differ
again), and automatically assuming rollover for every account
overstates what this app can actually claim to model.

**Revised model, v1 scope:** model exactly ONE named election —
**"spouse treats the inherited pretax account(s) as their own"** (the
IRS-permitted spousal rollover / treat-as-own treatment) — and label
it as exactly that in the UI/API output, not as a generic "inheritance"
behavior. This is opt-in per scenario via a new explicit input,
`spousal_rollover_election` (boolean, default `True` for v1, since
it's the single most common real choice and the only one this
narrow v1 supports) — when `False`, v1 does NOT attempt to model the
alternative (a separately-scheduled inherited IRA with its own RMD
timeline); it flags the result as "not modeled, assumes rollover was
declined" rather than silently defaulting to rollover behavior anyway.
This keeps the claim narrow and honest: v1 supports one clearly-named
path, not "inheritance in general."

### 37.4 Ownership is tracked through the whole projection, year by year — not just at initialization

Section 36 proposed only splitting the STARTING balance by owner (at
`phase2_start`) and verifying the household TOTAL matched the existing
pooled figure. **Insufficient**, per the review: a household total
match at time zero says nothing about whether contributions, growth,
withdrawals, conversions, and transfers are attributed to the correct
owner in EVERY subsequent year — two owner-split ledgers could
disagree in every single interior year and still coincidentally sum to
the same total at the end.

**Revised model:** three parallel bucket sets (`jason`, `justin`,
`joint` — each with its own pretax/roth/taxable/hsa sub-buckets) are
carried through the ENTIRE per-year loop, not just derived once at the
start:

- **Contributions** (pre-retirement accumulation phase only, matching
  every existing consumer's boundary — no withdrawal-phase consumer in
  this app models ongoing contributions, unchanged): each spouse's own
  401k/RSU/bonus contributions land in THEIR OWN bucket, using the
  same per-spouse input fields (`w2_salary` vs `justin_w2_salary`,
  etc.) `run_two_dimensional_retirement_projection`'s accumulation math
  already keys off — this part is unchanged from section 36, just
  explicitly stated as applying every accumulation year, not once.
- **Growth**: each owner's own sub-bucket compounds independently at
  the same assumed rate every other consumer already uses — no new
  rate concept, just applied three times (jason/justin/joint) instead
  of once.
- **Withdrawals**: the existing account-TYPE draw order (taxable →
  pretax → hsa → roth, or whichever strategy a given tool uses)
  determines WHICH BUCKET TYPE funds a shortfall; a NEW, explicit
  owner-order policy determines WHICH OWNER's bucket of that type is
  drawn first. Proposed v1 owner-order: **joint first, then the
  currently-withdrawing "reference" spouse's own bucket (Jason's, by
  this app's existing single-pension/RMD-anchoring convention), then
  the other spouse's own bucket** — i.e., joint funds shared spending
  before either spouse's individually-titled accounts are touched, a
  reasonable default a real household's own draw preference could
  override, but stated explicitly as a policy choice here, not
  incidental. Each draw event only reduces the SPECIFIC owner-bucket it
  was drawn from.
- **Conversions** (Roth Conversion specifically): a conversion moves
  money from a specific owner's PRETAX bucket to that SAME owner's ROTH
  bucket — never across owners (a spouse cannot convert their own
  pretax IRA into the OTHER spouse's Roth IRA; that's not how
  conversions work). Two-age Roth Conversion's own `jason_ret_age`/
  `justin_ret_age`/`later_retiree` already identify whose pretax
  account the conversion window's own math is modeling — v1 continues
  converting the household's POOLED pretax figure (matching the
  existing, reviewed Milestone 2 behavior, section 30-33) for that
  tool; the owner-split ledger this milestone adds is a NEW, parallel
  view for Survivor Scenario specifically, not a retrofit of Roth
  Conversion's own already-reviewed math. (Reconciling Roth
  Conversion's pooled figure against a hypothetical owner-split version
  is explicitly out of scope for this milestone — flagged, not
  silently skipped.)
- **Transfers/surplus sweeps**: land in the SAME owner-bucket the
  income source they came from belongs to (e.g., Jason's own pension
  surplus sweeps into Jason's own taxable bucket, not joint) — pension
  is Jason-attributed (section 13, unchanged), so its surplus is too.
  Guaranteed income with no single natural owner (a joint life-event
  windfall, for instance) sweeps into the joint bucket.

**Reconciliation, per the review's own instruction:** verified per
account TYPE (pretax/roth/taxable/hsa) and per YEAR, not just a single
starting-total check — `jason[type][year] + justin[type][year] +
joint[type][year] == the existing pooled consumer's own [type][year]
balance`, for every year of the projection, both pre- and post-death.
This is the actual test surface or Milestone 4's implementation phase,
replacing section 36's weaker "totals match at time zero" property.

### 37.5 Death-year cash-flow ordering, stated explicitly

Section 36 didn't state the death year's own internal ordering.
**Revised, explicit sequence, in the order these events are modeled as
occurring within the death year:**

1. **The death year itself runs as a normal, both-alive year** in the
   underlying deterministic Projection baseline this whole family of
   tools already uses for its starting figures — i.e., the deceased's
   own wages (if still working pre-retirement) are earned in FULL for
   that year, guaranteed income (pension/SS) accrues normally, and any
   RMD obligation the deceased already had that year (age at or past
   their own `rmd_start_age`) is taken and taxed exactly as it would be
   in a normal year — **the death-year RMD is therefore already
   satisfied by construction**, inherited from the baseline
   projection's own per-year math, not a separately-modeled "final
   RMD" event. This is the existing single-axis convention
   (`portfolio_at_death` already reflects a full year of normal
   activity) — stated explicitly here rather than left implicit, per
   the review.
2. **Wages stop** for the deceased spouse effective the END of the
   death year (i.e., the FOLLOWING year is the first year with no
   contribution/income from them) — no sub-year proration, matching
   this app's existing annual-only granularity everywhere else.
3. **The insurance payout arrives** and **ownership transfers**
   (deceased's own buckets merging into the survivor's, per 37.1-37.3's
   now-explicit assumptions) both take effect **at the START of the
   first post-death year** — i.e., `starting_balance` for the survivor
   schedule (already `portfolio_at_death + payout` in the existing
   code) is the point where the payout and the ownership transfer both
   land, together, not gradually or mid-year.
4. **Survivor income begins** (reduced-need spending pattern, survivor
   SS benefit switch, gap income gating) in that SAME first post-death
   year — matching the existing single-axis behavior (the survivor's
   own distinct spending pattern starts the year AFTER death, never the
   death year itself, to avoid double-counting that year's spending —
   unchanged from the existing, reviewed fix in section 18).
5. **Pension commencement when Jason dies BEFORE his own retirement**
   (a genuinely NEW case two-age introduces — single-axis could never
   reach it, since its own `ret_age` anchor always assumed Jason had
   already retired by the time Survivor Scenario runs; two-age's
   `phase2_start` can fall before Jason's own retirement when Justin
   retires first). This app's existing pension model already assumes
   100% Joint & Survivor continuation for a death occurring AFTER
   retirement — whether that SAME continuation applies to a
   pre-retirement death depends on the real plan's own pre-retirement
   death-benefit terms, which are typically DIFFERENT from (often less
   generous than) the post-retirement J&S election, and this app has
   no data to distinguish them. **This needs your decision, not an
   assumption made on your behalf:**
   - **Option A (simpler, more optimistic):** treat a pre-retirement
     death exactly like a post-retirement one — the survivor still
     receives the same 100% J&S pension figure, as if Jason had
     retired at the moment of death. Extends the existing
     simplification rather than introducing a new one.
     - **Option B (conservative):** the pension pays $0 if Jason dies
     before his own actual retirement, on the reasoning that
     pre-retirement death benefits are a materially different (and
     often absent or reduced) provision this app doesn't model, and
     assuming full continuation could overstate the survivor's real
     resources.
   - **Decided (Jason, 2026-09-08): Option A.** The survivor pension
     pays the full 100% Joint & Survivor benefit even when Jason dies
     before his own actual retirement — a pre-retirement death is
     treated exactly like a post-retirement one for this purpose,
     extending the existing J&S simplification rather than introducing
     a new, harsher rule for this one timing case. Still surfaced
     explicitly in the result payload (which case applied — pre- or
     post-retirement death — and that the same 100% J&S figure was
     used either way), matching 37.1/37.2's pattern of never presenting
     a number without its own caveat, even though the number itself is
     now settled rather than pending.

### 37.6 Status

Sections 37.1-37.5 replace the corresponding parts of section 36.
Section 36.4's v1-required/deferred RMD scope and 36.2's "no invented
ownership, read from the existing `owner` field" foundation both still
stand, now with 37.1-37.3's explicit-assumption inputs layered on top
rather than inferred defaults. 37.5's pension-commencement question is
now decided (Option A). **Still no application code changed on this
branch — implementation begins once Jason confirms the design overall
(not just the one pension question) is ready to build.**

## 38. Milestone 4 — Survivor Scenario + account ownership: implementation report (2026-09-08, on `codex/two-age-survivor-design`)

Milestone 4 of 4 (final milestone). Design in sections 36-37, approved
by Jason 2026-09-08 (including the 37.5 pension-commencement decision,
Option A) before any of this existed. This section is the required
accuracy/performance report for the completed implementation.

**What was built, in dependency order:**

1. `owner_split_starting_balances_two_age` (projection_engine.py) —
   the pooled accumulation-phase formulas `run_two_dimensional_
   retirement_projection` already uses, partitioned into `jason`/
   `justin`/`joint`/`trust` buckets by reading each account's own
   `owner` field (no invented ownership). Verified to sum EXACTLY to
   the pooled function's own `*_at_phase2_start` figures across
   multiple households, ages, and retirement orders.
2. `run_owner_split_two_dimensional_projection` (projection_engine.py)
   — carries those buckets through the ENTIRE per-year walk (section
   37.4's "not just at initialization" requirement), reusing the exact
   same `simulate_withdrawal_year` call the pooled function's own loop
   makes each year, then allocating the single pooled result's per-type
   balance change across owner buckets via a stated withdrawal-order
   policy (`joint`, `jason`, `justin`, `trust`) rather than recomputing
   the tax/RMD/draw math independently. Reconciliation is by
   construction, verified exactly (to the dollar) across long horizons,
   RMD years, both retirement orders, past selections, large age gaps,
   depletion, and a trust-only household.
3. `_run_survivor_scenario_two_age` (simulation_engine.py) — the actual
   two-age Survivor calculation, built on (2)'s reconciled pre-death
   walk. Implements all of section 37's explicit-assumption inputs
   (`trust_available_to_survivor`, `joint_accounts_survivorship`,
   `spousal_rollover_election`), real post-death RMDs on the survivor's
   own age against a merged pretax sub-balance tracked separately
   through a two-bucket withdrawal walk, Option A pension continuation,
   and gap income generalized from single-axis's Justin-only gate to
   `deceased != later_retiree`.

**A real bug was found and fixed during development** (not a review
finding, caught by the reconciliation tests written before the fix):
`WITHDRAWAL_OWNER_ORDER` originally excluded `trust`, conflating
section 37.1's "trust never auto-included" rule (about the SURVIVOR's
post-death access) with ordinary PRE-death spending, where the existing
pooled engine already spends trust-owned balances as part of its one
pooled total. Excluding trust from the pre-death draw order let trust
money grow unchecked while the pooled reference correctly drew it
down — diverging by over $1.2M by year 24 of a test projection before
the fix. Caught immediately by the reconciliation test (which failed
loudly, exactly as intended), fixed by including trust last in the
pre-death order, re-verified exact to the dollar afterward.

**Accuracy — test-first, per instruction:** 31 new tests total across
three files (15 for the owner-split projection layer, 16 for Survivor
itself), all written before or alongside implementation and verified
either by exact reconciliation against the already-reviewed pooled
engine (sections 19-24) or by hand-computed arithmetic (the ownership-
transfer math is simple sums/halves of already-reconciled figures).
One hand-calculation error was caught and fixed during test-writing
(not a code bug): an initial docstring miscounted how many pre-death
years elapse by a given death age, producing a wrong expected number —
caught by comparing against the actual function's output (which had
already passed the independent reconciliation tests), corrected by
redoing the year-by-year trace rather than adjusting the implementation
to match the wrong number.

Covers: ownership transfer at death (all three explicit-assumption
flags, both directions), pension commencement before Jason's own
retirement (both the Option A case and the unaffected-survivor case),
real post-death RMDs on the survivor's own age (both before and after
the survivor's own `rmd_start_age`), gap income generalized to
whichever spouse is `later_retiree` (both directions — the still-
working spouse dying vs. surviving), the both-ages-required gate, and
single-axis-mode-unaffected.

**Performance:** this milestone adds two NEW per-household-year
computations (the owner-split walk, then the post-death survivor walk)
on top of what single-axis Survivor already does — a single
deterministic pass each, not a randomized-trial loop like SWR/Monte
Carlo, so the added cost is small and linear in years, not
multiplicative. Not separately benchmarked against single-axis Survivor
given the structural difference (a genuinely new capability — owner
attribution — not a faster/slower path to the same single-axis
answer); the existing single-axis `run_survivor_scenario` is completely
unmodified and unaffected.

**Frontend:** no page changes this milestone. Survivor Scenario already
has a real UI presence inside `StressTestWhatIf.jsx`'s "Survivor
Scenario" tab (single-axis only, using the page's own `retAge`) —
wiring two-age support into that tab (the same toggle pattern
established for Monte Carlo/Historical Stress) is deferred to a
follow-up pass, since it wasn't part of the approved design's own
scope (sections 36-37 covered the calculation-engine and ownership
model only) and this session's remaining budget was directed at
getting the calculation layer correct and reviewed first.

**Explicitly deferred, matching section 37's own scope (not silently
dropped):** per-spouse RMDs during NORMAL (non-Survivor, both-alive)
two-age operation — every other two-age consumer still uses one
household RMD keyed to Jason's age; inherited-IRA-specific RMD timing
rules beyond the single named spousal-rollover election; trust
succession terms beyond the binary available/not-available assumption;
the MFJ→Single tax-bracket jump (already an explicitly documented,
unaddressed limitation in single-axis Survivor, unchanged).

**Verified:** full backend suite 1270 passed, 97.52% coverage.
Frontend suite unaffected, still 39 passed (no frontend changes this
milestone). Sensitive-data check passed.

Branch: `codex/two-age-survivor-design`, pushed, **not merged** — per
Jason's explicit instruction (auditor unavailable; get all four
milestones built, review everything together once it's back).

## 39. Two-age Roth Conversion + Survivor Scenario — fourth independent review round, fixes (2026-09-08, on `codex/two-age-roth-conversion` merged forward through `codex/two-age-tax-efficiency` into `codex/two-age-survivor-design`)

Independent review of Roth Conversion (`d4ad975`), Tax Efficiency
(`587c9a7`), and Survivor Scenario (`5ce8981`) found 8 numbered issues
(6 P1, 2 P2) plus two scope notes explicitly flagged as retained
limitations, not regressions (Tax Efficiency's flat-rate tax model and
0%-success-on-exact-funding quirk; two-age Survivor remaining API-only)
— no changes made for those two, matching the review's own framing.

**Finding 1 (P1, Roth Conversion)** — `_max_conversion_for_tax_budget`
treated the unused-federal-deduction "free zone" as costing $0 in
TOTAL, but `_incremental_conversion_tax`'s own formula charges state
tax on the ENTIRE conversion regardless of that federal offset. Fixed:
the free zone's own state-tax cost is now greedily filled against the
budget first, same pattern as the bracket walk. Reproduced: $0 cash,
5% state tax, $1M pretax → now correctly returns $0 affordable (was
$32,200, which previously drained Roth via `simulate_conversion`'s own
`conversion_shortfall` fallback to pay the real $1,610 state-tax bill
the affordability cap never budgeted for).

**Findings 2–8 (all Survivor Scenario, `_run_survivor_scenario_two_age`
in simulation_engine.py unless noted):**

- **2 (P1)** — pension was frozen at the death row's own snapshot.
  Fixed: computed PER YEAR in the post-death loop against the
  survivor's own advancing age (`two_age_pension_for_year`) when Justin
  is deceased; unconditional every year (Option A, unchanged) when
  Jason is deceased.
- **3 (P1)** — survivor Social Security was a flat
  `max(raw jason_social_security, raw justin_social_security)` input,
  ignoring `ss_timing` and COLA entirely. Fixed: computed PER YEAR the
  same way the pre-death walk computes it (each spouse's own
  `ss_timing`-selected, COLA-compounding benefit from their own claim
  age), taking the higher of the two.
- **4 (P1, projection_engine.py)** — `_allocate_type_delta_across_owners`
  credited every positive delta (surplus/RMD-reinvestment) to `joint`
  unconditionally, violating section 37.4's own "transfers land in the
  same owner-bucket the income source belongs to" rule. Fixed: the
  caller (`run_owner_split_two_dimensional_projection`) now computes
  `surplus_source_shares` from that year's own income breakdown
  (pension + Jason's SS → jason, Justin's SS + gap income → justin,
  unattributed life-event cash → joint) and the allocator prorates by
  it, falling back to all-joint only when there's no positive income
  basis to attribute against (a pure RMD-reinvestment year).
- **5 (P1)** — `life_event_cash=0.0` was hardcoded in the post-death
  loop, dropping every post-death expense/windfall from both the
  schedule and `_minimum_survivor_funding`'s insurance-shortfall calc.
  Fixed: `post_events` built the same way the pre-death walk builds its
  own `post_life_events`, applied via `_post_retirement_year_effects`
  each year, netted into both `need`/`simulate_withdrawal_year`'s
  `life_event_cash` AND `net_needs`.
- **6 (P2)** — a death requested before either spouse retired silently
  snapped forward to the first available (post-retirement) row while
  still reporting the original requested `death_age`. Fixed: explicitly
  rejected (`has_data: False`, `error: "death_before_first_retirement_unsupported"`)
  when `death_jason_age < phase2_start_age`.
- **7 (P2)** — joint/trust contributions were folded entirely into
  `starting_other`, losing pretax/RMD status on transfer. Fixed: split
  into `joint_pretax_contribution`/`joint_other_contribution` and
  `trust_pretax_contribution`/`trust_other_contribution`, added
  respectively into `starting_pretax`/`starting_other`.
- **8 (P2)** — the pre-death walk's RMD is always Jason-anchored, so an
  older Justin's own independent RMD obligation in his final year could
  go unenforced. Fixed: when Justin is deceased, his own final-year RMD
  (against his own age and his own individually-owned pretax balance)
  is forced from pretax into taxable before the ownership transfer.
  Reproduced exactly per the review: Jason 61, Justin 75, Justin's own
  $1,000,000 IRA — `_rmd(1000000, 75, 75)` = $40,650.41 (was $0).

New diagnostic return fields (additive, no existing field removed):
`starting_pretax_after_payout`/`starting_other_after_payout` (so
findings 7/8 are independently verifiable — the total
`starting_balance_after_payout` is unchanged by money moving between
buckets within the same owner). `survivor_ss_annual`/`pension_annual`
now report the first post-death year's own computed value (previously
a single frozen scalar for the whole schedule).

New tests: `test_state_tax_still_applies_to_the_free_zone`
(Roth Conversion); 7 new classes in `test_two_age_survivor_scenario.py`
covering findings 2, 3, 5, 6, 7, 8, each reproducing the review's own
numbers where given; 2 new tests in `test_owner_split_projection.py`
covering finding 4 (pension surplus credits Jason, SS surplus credits
Justin, neither credits joint).

**Verified:** full backend suite passed, coverage back at/above the
95% floor. Sensitive-data check passed.

Branches: fix committed on `codex/two-age-roth-conversion`
(finding 1), merged forward through `codex/two-age-tax-efficiency`
into `codex/two-age-survivor-design` (findings 2–8), all pushed —
**none merged to `main`** beyond Milestone 1, per Jason's standing
instruction (auditor unavailable; get everything built and reviewed as
a set).

## 40. Survivor Scenario — fifth independent review round, fixes (2026-09-08, on `codex/two-age-survivor-design`)

Independent review of section 39's own fixes found 3 more issues (1
P1 double-withdrawal, 1 P1 ownership-attribution gap, 1 P2 ownership-
attribution gap) — all in code section 39 had just introduced or
touched.

**Finding 1 (P1)** — the finding-8 catch-up (deceased's own final-year
RMD) computed Justin's full obligation against his END-of-death-year
pretax balance (already net of whatever the normal death-year draw/RMD
had already taken), then forced that WHOLE amount out again. Reproduced
exactly: both spouses 75, Justin's $1,000,000 sole pretax IRA — the
normal year already withdraws $40,650 (pooled RMD, Jason also 75); the
bug then pulled ANOTHER $38,998 from the remaining ~$959,350. With
rollover declined and a subsequent $100,000 expense, this overstated
insurance needed at $63,415 instead of the correct $24,417. Fixed:
Justin's own full obligation is now computed against his STARTING-of-
year pretax balance (from the prior walk row, or `starting_buckets` if
the death year is the walk's first row), and only the shortfall (if
any) beyond what his own pretax was already reduced by that year
(growth-adjusted, so a nonzero `post_ret` doesn't mask the real draw
amount) is forced.

**Finding 2 (P1, `run_owner_split_two_dimensional_projection`)** — two
related ownership-attribution gaps in the finding-4 surplus-source-
shares fix:
- Reinvested RMD proceeds (a positive delta with no cash-INCOME basis
  to prorate against — $0 need, $0 guaranteed income, so the forced
  RMD has nothing to fund and gets swept back to savings) still fell
  back to the all-joint default, even though the money came directly
  out of a specific owner's own pretax account. Reproduced: Jason 75
  (his own $1,000,000 IRA), Justin 61, joint survivorship disabled —
  survivor resources understated at $977,642 instead of $995,935.
  Fixed: pretax is now allocated FIRST each year (its own reduction/
  increase, via the existing `_allocate_type_delta_across_owners` call
  moved ahead of the type loop), and any pretax reduction attributed to
  an owner is folded into that owner's share of the basis alongside
  cash income — a delta with no income basis but a real pretax
  reduction now correctly attributes by whoever's pretax was drawn
  from, instead of defaulting to joint.
- Recurring monthly life-event income (`life_event_monthly_this_year`)
  was missing from the basis entirely — only the one-time component
  was counted — so it silently vanished into whichever OTHER source
  happened to be in the basis, over-crediting that source. Reproduced:
  $30,000 pension + $12,000/yr recurring household income, $0 spending,
  disabled joint survivorship — $42,000 (all credited to Jason, since
  the $12,000 wasn't in the basis at all) instead of the correct
  $36,000 ($30,000 Jason's own pension, unaffected, + $6,000 = half of
  joint's correctly-attributed $12,000). Fixed: `joint_income_this_year`
  now includes `max(0.0, life_event_monthly_this_year)` alongside the
  one-time component.

**Finding 3 (P2, same function)** — the still-working spouse's own
gap-income surplus was hardcoded to credit `justin_income_this_year`,
even though the gap-income mechanism itself (`justin_gap_income_for_year`)
is already generalized to whichever spouse is `timeline.later_retiree`.
Reproduced: Jason retires at 65, Justin at 61, Jason earns $100,000, $0
spending — the $65,000 net surplus went into Justin's bucket even
though JASON is the one still working. Fixed: `still_working_income_this_year`
now credits `jason_income_this_year` or `justin_income_this_year`
based on `timeline.later_retiree`, not a hardcoded assumption.

New tests: 1 in `test_two_age_survivor_scenario.py` (finding 1) plus 1
new class (finding 2, recurring-income scope note above); 2 new
classes in `test_owner_split_projection.py` (findings 2 part 1 and 3).

**Verified:** full backend suite passed, coverage at/above the 95%
floor. Sensitive-data check passed.

Branch: `codex/two-age-survivor-design`, pushed — **not merged to
`main`** beyond Milestone 1, same standing instruction as section 39.

## 41. Survivor Scenario — sixth independent review round, fixes (2026-09-08, on `codex/two-age-survivor-design`)

Independent review of commit `08198b2` found the three previous
reproductions now pass, but 2 more calculation issues still blocked
approval — both in code section 39/40 had just introduced or touched.

**Finding 1 (P1, `run_owner_split_two_dimensional_projection`)** —
the finding-4/2 surplus-source-shares fix attributed a positive delta
by each income source's GROSS amount, which let money already fully
consumed by spending still dilute an unrelated same-year RMD-
reinvestment surplus. Reproduced: $30,000 Jason pension exactly funds
$30,000 spending (Jason's net contribution to any surplus is $0); a
trust-owned $1,000,000 IRA's forced RMD is reinvested the same year —
the old gross-proportion split still credited $15,190 of that trust
RMD to Jason, making it available to the survivor even with trust
availability disabled. Fixed: the basis now uses each source's NET
leftover after funding need, not its gross amount — the true
underlying need (`year_need_baseline`, adding back what life-event/
gap income already reduced `year_need` by) is subtracted from total
gross cash available to get `income_surplus_this_year`, and ONLY that
actual leftover is split proportionally by gross share. When gross
income exactly equals need (as here), the leftover is $0, so nothing
dilutes the trust-owned RMD reinvestment, which is then attributed
entirely via the existing pretax-reduction shares.

**Finding 2 (P1, `_run_survivor_scenario_two_age`)** — the finding-8/1
deceased final-RMD catch-up bypassed both the death year's own tax
treatment and its growth:
- It moved the shortfall into taxable UNTAXED, when a real RMD is
  taxed like any other pretax distribution. Reproduced: Jason 61,
  Justin 75, Justin's $1,000,000 IRA, $0 spending/growth — the
  catch-up reported $1,000,000 total (untaxed), not the correctly-
  taxed $995,935 ($40,650.41 shortfall taxed at that year's own 10%
  rate, $0 other income here).
- It applied the adjustment AFTER that year's growth had already run,
  instead of "inside" the death year alongside everything else.
  Reproduced with 10% growth: reported $1.1M instead of the correctly
  taxed-then-grown $1,095,528.
- It also only ever checked Justin, never Jason — but the pooled RMD
  draws from JOINT's pretax FIRST (`WITHDRAWAL_OWNER_ORDER`), so when
  Jason is deceased and the year's RMD came entirely out of a joint
  account, his own individually-owned account's RMD obligation went
  completely unenforced.

Fixed: generalized to whichever spouse is `deceased` (reads
`death_row[f"{deceased}_age"]`/`inputs[f"{deceased}_age"]` instead of
hardcoding `"justin"`), taxed at the SAME `pretax_tax_rate` the death
row's own normal draw used (now exposed in `yearly_detail` alongside
every other per-year field, so this doesn't recompute the marginal-
rate formula a second, independent way), and applied PRE-growth
(reversing/reapplying `(1 + post_ret)` around the pretax reduction AND
the taxable addition) so the shortfall grows symmetrically with the
rest of that year, matching the main per-year loop's own convention.

New tests: 1 class in `test_owner_split_projection.py` (finding 1); 2
new tests plus 1 existing test's assertions updated to the now-correct
(taxed) numbers in `test_two_age_survivor_scenario.py` (finding 2,
including a growth case and a Jason-deceased case).

**Verified:** full backend suite passed, coverage at/above the 95%
floor. Sensitive-data check passed.

Branch: `codex/two-age-survivor-design`, pushed — **not merged to
`main`** beyond Milestone 1, same standing instruction as sections
39-40.

## 42. Survivor Scenario — seventh independent review round: owner cash-flow waterfall replaces proportional reweighting (2026-09-08, on `codex/two-age-survivor-design`)

Independent review of commit `ca898fb` confirmed the death-year tax/
growth fix (section 41, finding 2) works, but found one more ownership-
allocation issue with three reproducible cases, all in the same
`run_owner_split_two_dimensional_projection` surplus-attribution code
sections 39/40/41 had each iterated on without changing the underlying
approach: **reweighting a pooled ending-balance change by proportional
income shares fundamentally cannot preserve exact ownership**, because
it mixes untaxed income with gross (pre-tax) pretax distributions, and
because clamping negative life-event costs to $0 drops real signed
cash flows from the calculation entirely.

Reproduced exactly per the review (spouses 75, $0 growth/inflation,
trust availability disabled):
- $60,000 Jason pension / $30,000 spending / $1,000,000 trust IRA:
  pension alone funds need with a real $30,000 leftover entirely
  Jason's own; the trust's forced RMD is separate, entirely the
  trust's own. Old: $27,929 survivor resources (diluted Jason's real
  leftover against the trust's gross RMD). Correct: $30,000.
- Same, plus a $40,000 one-time expense: clamped to $0 and dropped,
  inventing a phantom $30,000 pension surplus. Old: $10,944. Correct:
  $0 (the expense consumes the pension entirely; only the trust's own
  excluded RMD proceeds remain).
- No pension, $9,000 spending, $10,000 joint IRA + $990,000 trust IRA:
  the joint IRA's own $9,000 after-tax RMD proceeds already exactly
  fund spending. Old: $6,786 (still credited some of trust's own
  proceeds despite joint's distribution being fully consumed).
  Correct: $0.

**Fixed with a structurally different approach**, per the review's own
explicit guidance ("allocate actual cash transactions: preserve signed
costs, track each owner's distribution and tax, apply the funding
order, then credit only that owner's remaining proceeds — reweighting
the pooled ending-balance change keeps losing this information"):
1. Each owner's own AFTER-TAX pretax distribution this year is
   computed from `pretax_allocation` (their own share of the pretax
   reduction, already correctly per-owner from the finding-2/part-1
   fix) taxed at that year's own `pretax_tax_rate`.
2. Each owner's own cash this year is built SIGNED, never clamped:
   jason/justin get their own guaranteed income (+ gap income for
   whichever is `later_retiree`) plus their own after-tax pretax
   proceeds; joint gets SIGNED life-event cash (one-time and
   recurring, a real expense now stays negative) plus its own after-
   tax pretax proceeds; trust gets only its own after-tax pretax
   proceeds (no natural income).
3. The TRUE underlying need (`year_need_baseline`) is funded from
   those owner-cash amounts via a waterfall in `WITHDRAWAL_OWNER_ORDER`
   — the SAME funding-order convention every account draw already
   uses — and whatever's left over per owner becomes the attribution
   weight for the actual pooled surplus delta. This never re-derives
   the pooled total a second, independent way (preserving exact
   reconciliation), it only changes how that total's ownership is
   attributed.

New test class in `test_two_age_survivor_scenario.py` reproducing all
three cases exactly (`starting_balance_after_payout` == $30,000 / $0 /
$0).

**Verified:** full backend suite passed, coverage at/above the 95%
floor. Sensitive-data check passed. All prior review-round tests
(sections 39-41) still pass unchanged against the new approach.

Branch: `codex/two-age-survivor-design`, pushed — **not merged to
`main`** beyond Milestone 1, same standing instruction as sections
39-41.

## 43. Survivor Scenario — eighth independent review round: death year becomes one integrated calculation (2026-09-08, on `codex/two-age-survivor-design`)

Independent review of commit `889fb5b` confirmed the waterfall fix
passes its three reproductions plus 72 additional mixed-cash-flow
cases, but found one P1 remaining in the deceased's final-RMD catch-up
-- the SAME root cause every round from section 39 onward kept
resurfacing in a new form: the catch-up modified `death_row`'s balances
*after* that year's spending, tax, and growth calculations had already
finished, rather than being part of the year's own single calculation.

Reproduced exactly:
- Jason 61, Justin 75, Justin's $1,000,000 IRA, $100,000 joint taxable,
  $50,000 spending, $0 growth, joint survivorship disabled: reported
  $1,020,935 instead of $1,002,642. The Jason-anchored aggregate RMD at
  Jason's age 61 is $0, so the normal year funded the full $50,000
  spending entirely from joint taxable; the RMD catch-up was then
  bolted on AFTER, never getting the chance to fund spending itself --
  leaving too much Justin-owned cash and draining joint further than
  necessary.
- $100,000 pension exactly funding $100,000 spending, same IRA: the
  additional RMD should push the marginal-rate estimate from 12% to
  22%, but the catch-up reused the rate already computed BEFORE its own
  addition -- $995,122 instead of the correctly-taxed $991,057.

**Fixed by restructuring the death year as one calculation**, per the
review's own explicit guidance ("incorporate the deceased's required
distribution before determining taxes, funding spending, allocating
ownership, and applying growth — avoid adjusting completed balances
afterward"):
- `run_owner_split_two_dimensional_projection` gained two optional
  parameters, `death_jason_age`/`deceased` (every other two-age
  consumer leaves them at their `None` default, completely unaffected).
  When set, the FIRST year reaching `death_jason_age` computes the
  deceased's own individual RMD obligation (their own age, their own
  STARTING-of-year pretax balance) and folds `rmd = max(rmd, deceased_
  individual_rmd)` in **before** `taxable_income_est`/`pretax_tax_rate`
  are derived — so a bigger forced distribution correctly moves the
  bracket in the SAME calculation that uses it, not a stale one.
  `simulate_withdrawal_year` then funds that year's spending against
  this already-correct combined RMD, exactly like any other year.
- The pretax allocation step forces the deceased's own account to
  contribute AT LEAST its own individual RMD first (capped at what's
  actually there), then allocates whatever's left of the total pretax
  change normally (`WITHDRAWAL_OWNER_ORDER`) across the remaining
  balances — so the aggregate RMD is satisfied WITHOUT double-counting
  a separate obligation on top of it.
- `_run_survivor_scenario_two_age` now computes `phase2_start_age`/
  `later_retiree` directly from its own already-built `timeline`
  (identical formulas the walk uses internally) so `death_jason_age`
  can be determined and passed into the walk from the start, calling
  it exactly once. The entire ~60-line post-hoc catch-up block
  (findings 8, fifth-follow-up-1, sixth-follow-up-2) is deleted —
  `death_row["owner_balances"]` is read as-is.

New test class in `test_two_age_survivor_scenario.py` reproducing both
cases exactly ($1,002,642 and $991,057). One existing test (Jason-
deceased catch-up, section 41) had its scenario adjusted: since the
deceased's own minimum now draws as part of the SAME aggregate RMD
rather than layered on top, a mixed jason+joint-pretax scenario needed
`spousal_rollover_election=False` to isolate whether Jason's own
account was actually touched (the aggregate total alone can't
distinguish the two allocations when both owners' shares roll into the
survivor by default).

**Verified:** full backend suite passed, coverage at/above the 95%
floor. Sensitive-data check passed. All prior review-round tests
(sections 39-42) still pass against the restructured approach.

Branch: `codex/two-age-survivor-design`, pushed — **not merged to
`main`** beyond Milestone 1, same standing instruction as sections
39-42.

## 44. Social Security claiming age 62-70 — design proposal (2026-09-08, on `codex/ss-claim-age`)

Jason's request: model Social Security continuously across every
claiming age 62-70 (not just the existing binary early/delayed
toggle), and asked specifically whether modeling to 70 (not just 67)
has real value. It does — delayed retirement credits keep accruing
past full retirement age (assumed 67 in this app, matching
`JUSTIN_SPOUSAL_AGE`'s existing convention) all the way to 70, and the
resulting difference (a ~24% higher benefit at 70 than at 67, ~77%
higher than at 62) is frequently the single highest-value lever in a
real retirement plan — an inflation-adjusted, guaranteed-for-life
increase, and for many households a better risk-adjusted return than
continuing to hold market assets. A tool that only compares 62 vs. 67
can recommend a materially suboptimal claiming strategy.

**Current state, confirmed by reading the code (not assumed):**
Social Security is NOT a single household-level setting today —
`ss_timing: "early" | "delayed"` is a query/body parameter re-derived
independently in roughly 20 separate call sites across
`simulation_engine.py` and `projection_engine.py`, each with its own
local `jason_ss_age = 62 if ss_timing == "early" else 67`. Jason
already has two real dollar inputs (`jason_social_security` at 62,
`jason_ss_delayed` at 67); Justin has only one
(`justin_social_security`, implicitly at his own FRA=67 via the
`JUSTIN_SPOUSAL_AGE` default) — no early option at all, an existing
asymmetry this proposal also closes. A `jason_ss_age`/`justin_ss_age`
field already exists on the `PlanningInputs` Pydantic model and in
`db.py`'s schema, but is dead: nothing in the engine reads it (every
consumer derives its own local `jason_ss_age` from `ss_timing`
instead) and the frontend never surfaces it.

**Decided (Jason, 2026-09-08):**
1. A real THIRD dollar input at age 70 (not formula-derived) for both
   spouses — matches exactly what a real SSA.gov statement shows
   (amounts at 62, FRA, and 70), avoiding the error a pure-formula
   projection could introduce against a household's real benefit
   history/COLA record.
2. Symmetric treatment for both spouses — Justin gains the same
   62/67/70 structure Jason has, closing the existing asymmetry.

**New inputs (additive, no existing field renamed or removed):**
- `jason_social_security` (62) — existing, unchanged.
- `jason_ss_delayed` (67/FRA) — existing, unchanged.
- `jason_ss_70` — NEW.
- `justin_ss_early` — NEW (justin's own 62 figure; `justin_social_security`
  keeps its existing meaning as Justin's 67/FRA figure, unchanged, to
  avoid a breaking rename of a field that's persisted for every
  existing household).
- `justin_ss_70` — NEW.
- `jason_ss_claim_age` (62-70, default 62) — NEW, replaces the
  *concept* `ss_timing` maps Jason onto; independent of Justin's.
- `justin_ss_claim_age` (62-70, default 67) — NEW, independent of
  Jason's — real households often have spouses claim at different
  ages, and this app already models fully independent per-spouse
  retirement ages (`jason_ret_age`/`justin_ret_age`) elsewhere, so
  independent per-spouse SS claim ages is the same philosophy, not a
  new one.

**Benefit formula** (whole-year ages only, matching this app's
existing whole-year-age precision everywhere else — no monthly
granularity needed since claim age is itself a whole-year input):
anchored EXACTLY to the household's own three real dollar inputs at
62/67/70, with ages IN BETWEEN interpolated using the real SSA
reduction/credit formula's own shape (5/9%/month for the first 36
months before FRA, 5/12%/month beyond that, 2/3%/month — 8%/year —
after FRA), scaled so the endpoints match the real anchors exactly
rather than a single derived PIA (more accurate than simple linear
interpolation, since the reduction rate changes at the 3-year-early
mark, and more accurate than a pure single-PIA formula projection,
since it never disagrees with the household's own real numbers at 62,
67, or 70):

```
reduction_fraction(age) for age in 62..66 (months before FRA = (67-age)*12):
  62: 30.0000%   63: 25.0000%   64: 20.0000%   65: 13.3333%   66: 6.6667%   67: 0%
credit_fraction(age) for age in 68..70 (months after FRA = (age-67)*12):
  68: 8.0000%    69: 16.0000%   70: 24.0000%

def ss_benefit_for_claim_age(benefit_62, benefit_67, benefit_70, claim_age):
    if claim_age <= 62: return benefit_62
    if claim_age >= 70: return benefit_70
    if claim_age == 67: return benefit_67
    if claim_age < 67:
        progress = (30.0 - reduction_fraction[claim_age]) / 30.0   # 0 at 62, 1 at 67
        return benefit_62 + progress * (benefit_67 - benefit_62)
    else:
        progress = credit_fraction[claim_age] / 24.0                # 0 at 67, 1 at 70
        return benefit_67 + progress * (benefit_70 - benefit_67)
```

Shared helper, module-level in `projection_engine.py` next to the
other SS constants — every consumer calls this instead of hand-rolling
`jason_ss_age = 62 if ss_timing == "early" else 67`.

**Migration is ADDITIVE, not a breaking replacement** — `ss_timing`
stays fully functional (its own ~20 call sites and their existing test
suites are untouched) for any caller that doesn't supply the new
per-spouse claim-age inputs. Each consumer, migrated one at a time
across the milestones below, prefers `jason_ss_claim_age`/
`justin_ss_claim_age` when present and falls back to its existing
`ss_timing`-derived age otherwise — mirrors the same additive-
parameter pattern `death_jason_age`/`deceased` used on
`run_owner_split_two_dimensional_projection` (section 43).

**Milestone sequence** (each its own reviewed step, matching the
two-age project's own successful pattern):
1. **This milestone**: design doc (above) + `ss_benefit_for_claim_age`
   shared helper + hand-verified unit tests + wire into the reference
   single-axis `run_retirement_projection` (the function every other
   consumer has historically been reconciled against).
2. Monte Carlo + Stress Tests (already share helpers with each other).
3. SWR, Tax Efficiency, Roth Conversion (single-axis).
4. Survivor Scenario (single-axis) — needs care around the survivor's
   own "higher of the two benefits" rule (section 18-era logic),
   revisited against real per-spouse claim ages instead of a flat
   `max(raw early inputs)`.
5. Two-age consumers (`run_two_dimensional_retirement_projection`,
   the owner-split walk, and every two-age sibling) — these already
   track jason/justin independently, so wiring in per-spouse claim
   ages is the more natural fit than the single-axis versions were.
6. Frontend: new Settings fields (age-70 for both spouses, an early
   input for Justin), replacing every early/delayed toggle with two
   independent age selectors (62-70) per relevant page.

**Explicitly out of scope for this proposal:** the heatmap/sweep UI
item from the backlog (a separate ask, composes well with this once
claim age is continuous, but is its own piece of work); modeling
survivor benefits' own reduction rules beyond the existing "higher of
the two" simplification; any FRA value other than 67 (this app has
never modeled birth-year-dependent FRA, an existing simplification
left unchanged).

## 45. Social Security claiming age 62-70 — milestone 2: Monte Carlo + Stress Tests (2026-09-08, on `codex/ss-claim-age`)

Propagated the section 44 design to the next two consumers, per the
milestone sequence: Monte Carlo and Stress Tests (single-axis).

**New shared resolver**, `resolve_ss_benefits(inputs, ss_timing,
jason_ss_claim_age, justin_ss_claim_age)` in `projection_engine.py` —
returns `(jason_ss_annual, jason_ss_age, justin_ss_annual,
justin_ss_age)`, replacing the independent
`jason_ss_age = 62 if ss_timing == "early" else 67` ternary this file
still hand-rolls in ~18 remaining call sites (migrated one consumer at
a time across the remaining milestones, not all at once). A spouse
left at `None` gets the exact existing `ss_timing`-derived behavior; a
spouse given a claim age gets `ss_benefit_for_claim_age`'s real-anchor
formula instead.

**`run_monte_carlo`/`run_stress_tests`** both gained optional
`jason_ss_claim_age`/`justin_ss_claim_age` params (mirroring
`jason_ret_age`/`justin_ret_age`'s own established additive-parameter
pattern). Each now calls `resolve_ss_benefits` instead of its own
ternary, and forwards the claim ages into its internal
`run_retirement_projection` call (for the pre-retirement starting
balances) via a shallow-copied `inputs` dict — `run_retirement_
projection` reads `jason_ss_claim_age`/`justin_ss_claim_age` off
`inputs` itself (section 44 milestone 1), not as explicit kwargs, so
this is how every downstream caller threads them through. The internal
scenario-label lookup (`f"age_{ret_age}_{ss_timing}"`) is adjusted to
`f"age_{ret_age}_custom"` when Jason's claim age is set, matching
`run_retirement_projection`'s own label change (`"custom"` replaces
`"early"`/`"delayed"` for that scenario — see section 44 milestone 1).

**Verified:** a claim age at 62 vs. 70 produces a genuinely different
`median_final_balance` (confirms the wiring reaches the actual
1000-trial simulation, not just accepted-and-silently-ignored); every
existing caller that leaves both claim ages at `None` gets byte-for-
byte the same `success_rate`/`ss_timing` output as before. New tests:
4 for `resolve_ss_benefits` itself (matches both existing ternary
branches exactly, both spouses' overrides independent of each other),
4 for Monte Carlo/Stress Tests (backward-compatible without claim age,
outcome genuinely changes with one, no crash). Full backend suite:
1317 passed, 97.58% coverage. Sensitive-data check passed.

Branch: `codex/ss-claim-age`, pushed — **not merged to `main`**.
Remaining milestones (SWR/Tax Efficiency/Roth Conversion, Survivor,
two-age consumers, frontend) unchanged from section 44's own sequence.

## 46. Social Security claiming age 62-70 — milestone 3: SWR, Roth Conversion, Tax Efficiency (2026-09-08, on `codex/ss-claim-age`)

Propagated to the next three single-axis consumers per the milestone
sequence: `run_swr_analysis`, `run_roth_conversion_analysis`,
`run_tax_efficiency_simulation`. Same pattern as milestone 2: each
gained optional `jason_ss_claim_age`/`justin_ss_claim_age`, calls
`resolve_ss_benefits` instead of its own ternary, forwards the claim
ages into its internal `run_retirement_projection` call, and adjusts
its scenario-label lookup to `"custom"` when Jason's claim age is set.

**Incidental fix, not new scope**: `run_roth_conversion_analysis` had
its own inconsistent SS-defaulting convention — `jason_ss`/`justin_ss`
used `inputs.get(key, 0)` directly, defaulting to a bare `$0` when
`jason_ss_delayed` was unset and `ss_timing="delayed"`, instead of the
`JASON_SS_DELAYED_RATIO`-based fallback every sibling consumer already
applies (`run_swr_analysis`, `run_monte_carlo`, `run_stress_tests`,
`run_retirement_projection`). Migrating it onto the shared
`resolve_ss_benefits` resolver automatically aligns it with everyone
else instead of carrying the inconsistency forward into a 6th call
site. A household with `jason_social_security` set but
`jason_ss_delayed` unset now gets the same fallback figure (equal to
the early value, since `JASON_SS_DELAYED_RATIO = 1.0` — "no assumption
without real inputs") instead of a silent `$0`.

New tests: 6, covering backward compatibility (no claim age → unchanged
output) and a genuine outcome change with one, for all three functions;
`run_roth_conversion_analysis`'s fallback fix gets its own explicit
test. Full backend suite: 1323 passed, 97.56% coverage. Sensitive-data
check passed.

Branch: `codex/ss-claim-age`, pushed — **not merged to `main`**.
Remaining: Survivor Scenario (single-axis), two-age consumers,
frontend.

## 47. Social Security claiming age 62-70 — milestone 4: Survivor Scenario, single-axis (2026-09-08, on `codex/ss-claim-age`)

Propagated to `run_survivor_scenario` (single-axis). Same additive
`jason_ss_claim_age`/`justin_ss_claim_age` params; the internal
baseline `run_retirement_projection` call and its scenario-label
lookup (previously hardcoded to `"early"` regardless of `ss_timing`,
since portfolio/pension don't vary by SS choice) now adjusts to
`"custom"` when Jason's claim age is set, matching every other
migrated consumer.

**Incidental fix, not new scope**: `survivor_ss_annual` read the raw
`jason_social_security`/`justin_social_security` inputs directly
(`max(inputs.get(...), inputs.get(...))`), ignoring `ss_timing`
entirely — a household that selected "delayed" still saw the survivor
schedule computed off the early-claim figure. Migrating onto
`resolve_ss_benefits` fixes this the same way milestone 3 fixed Roth
Conversion's analogous gap. Still a single flat figure COLA'd forward
from the death year (the pre-existing simplification, unchanged) — a
genuinely per-year, claim-age-gated survivor SS schedule (matching the
two-age Survivor's own much more involved finding-3 fix from the
eighth review round, sections 39-43) is a larger scope than adding
claim-age input support and was not attempted here.

New tests: 3, covering backward compatibility, the ss_timing fix
(delayed now genuinely differs from early), and a genuine outcome
change with a claim age. Full backend suite: 1326 passed, coverage
at/above the 95% floor. Sensitive-data check passed.

Branch: `codex/ss-claim-age`, pushed — **not merged to `main`**.
Remaining: two-age consumers (all of them share jason/justin tracking
already, so this is expected to be the most natural fit of any
milestone), frontend.

## 48. Social Security claiming age 62-70 — milestone 5: every two-age consumer (2026-09-08, on `codex/ss-claim-age`)

Propagated to all remaining two-age functions: the two core walks
(`run_two_dimensional_retirement_projection`,
`run_owner_split_two_dimensional_projection`, both in
`projection_engine.py`) and all six `_run_X_two_age` siblings in
`simulation_engine.py` (Monte Carlo, Stress Tests, SWR, Roth
Conversion, Tax Efficiency, Survivor).

**Different wiring shape than milestones 2-4, by design**: the two
core walk functions now read `jason_ss_claim_age`/`justin_ss_claim_age`
directly off `inputs` via `resolve_ss_benefits`, rather than as
explicit function parameters — every two-age caller already passes
`inputs` straight through unchanged, so this needed NO signature
change on either walk function. The six `_run_X_two_age` siblings each
had their own independent, redundant SS-resolution block (mirroring
their single-axis counterparts) that also now calls
`resolve_ss_benefits` the same way, reading claim ages off `inputs`
too. The six PUBLIC dispatchers (`run_monte_carlo`, `run_stress_tests`,
`run_swr_analysis`, `run_roth_conversion_analysis`,
`run_tax_efficiency_simulation`, `run_survivor_scenario`) already had
explicit `jason_ss_claim_age`/`justin_ss_claim_age` kwargs from
milestones 2-4 — each now injects them into a shallow-copied `inputs`
dict immediately before delegating to its two-age sibling, so the SAME
kwargs work identically in both single-axis and two-age mode from the
caller's perspective.

The `_run_survivor_scenario_two_age` block (findings 2-8 of the
eight-round Survivor review, sections 39-43) needed no structural
change — its own per-year COLA-compounding gate already consumed
`jason_ss_annual`/`jason_ss_age`/`justin_ss_annual`/`justin_ss_age` as
base figures; only their SOURCE (now `resolve_ss_benefits` instead of
the local ternary) changed.

New tests: 6, covering the two core walks and 2 representative
consumers (SWR, Survivor) in two-age mode — backward compatibility and
a genuine outcome change with a claim age, mirroring the single-axis
test pattern from milestones 2-4. Full backend suite: 1332 passed,
coverage at/above the 95% floor. Sensitive-data check passed.

Branch: `codex/ss-claim-age`, pushed — **not merged to `main`**.
Remaining: frontend (Settings fields, replacing early/delayed toggles
with age selectors) — the last milestone in section 44's sequence.

## 49. Social Security claiming age 62-70 — ninth follow-up review round, fixes (2026-09-08/09, on `codex/ss-claim-age`)

Independent review of commit cb80bf5 (milestone 5) found four issues,
plus one guard-note gap, before the engine work could be considered
closed out. All five are fixed on this branch.

**Finding 1 (P1) — saved claiming ages weren't handled consistently.**
Milestone 5's two-age dispatch blocks (each public `run_X` function's
own `if jason_ss_claim_age is not None or justin_ss_claim_age is not
None: inputs = {**inputs, ...}` injection) unconditionally overwrote
BOTH spouses' `jason_ss_claim_age`/`justin_ss_claim_age` fields in the
shallow-copied `inputs` dict with THIS call's own params — including
`None` for whichever spouse the caller didn't override. A caller
passing only `jason_ss_claim_age=65` would silently clear Justin's own
already-saved claim age back to the legacy early/delayed default,
because the injected `None` clobbered whatever `inputs` already held.
Separately, `run_retirement_projection`,
`run_two_dimensional_retirement_projection`, and
`run_owner_split_two_dimensional_projection` read claim ages directly
off `inputs` (milestones 1 and 5's own design) — any unrelated caller
that forwards a raw `inputs` row (e.g. `save_scenario`,
`get_retirement_sensitivity`, `get_income_sources` in `main.py`, none
of which have a claim-age control of their own) would silently start
producing a single "custom" scenario instead of the early/delayed pair
its own label lookups expect, the moment a household saved ANY
continuous claim age in Settings.

Fix, two parts:
1. New `resolve_ss_claim_ages(inputs, jason_ss_claim_age=None,
   justin_ss_claim_age=None)` in `projection_engine.py` — the 3-tier
   resolution (explicit override → saved Settings value on `inputs` →
   `None`/legacy fallback), applied independently per spouse so
   overriding one never touches the other's own resolution. Every
   two-age dispatch block now calls this helper instead of the naive
   `{**inputs, "jason_ss_claim_age": jason_ss_claim_age, ...}` merge.
2. `run_retirement_projection`, `run_two_dimensional_retirement_projection`,
   and `run_owner_split_two_dimensional_projection` now take
   `jason_ss_claim_age`/`justin_ss_claim_age` as EXPLICIT keyword
   parameters (default `None`) instead of reading them off `inputs` —
   reverting milestone 5's "no signature change needed" design, which
   this review (and independently, testing for milestone 6) showed was
   the actual root cause: an implicit `inputs`-dict read is exactly the
   kind of action-at-a-distance that breaks an unrelated caller the
   moment Settings persists a value it never asked for. A caller must
   now opt in by passing the kwarg explicitly; every existing caller
   that doesn't is structurally guaranteed unchanged behavior. This
   also let three defensive "strip claim-age keys before calling
   run_retirement_projection" patches added ad hoc in `main.py` during
   milestone 6 be simplified back to plain calls — they were guarding
   against a class of bug that can no longer occur once the function
   only listens to its own explicit parameters.

**Finding 2 (P1) — the SS-reduction stress scenario could increase
income.** In `run_stress_tests`' single-axis SS-reduction scenario,
`scenario_justin_ss = inputs.get("justin_social_security",
JUSTIN_SPOUSAL_ANNUAL) * ss_mult` reduced Justin's RAW FRA input
instead of his resolved, claim-age-selected benefit. Two-age mode
already reduced the resolved figure correctly; only the single-axis
path had the bug. With an early claim age already below FRA (e.g.
$10,500 selected at 62 vs. $15,000 at FRA), the "25%-reduction"
scenario computed `15000 * 0.75 = 11250` — MORE than the real
unstressed benefit of $10,500, i.e. a "stress" test that paid a
windfall. Fixed to `scenario_justin_ss = justin_ss_annual * ss_mult`,
matching two-age mode's already-correct treatment.

**Finding 3 (P1) — single-age Survivor ignored when benefits begin.**
Two parts, both in `run_survivor_scenario`'s single-axis path:
(a) The pre-death baseline scenario lookup used `_ss_label = "custom"
if jason_ss_claim_age is not None else "early"` — hardcoded "early"
instead of falling back to `ss_timing` when no claim age was set. A
household that selected `ss_timing="delayed"` (with no continuous
claim age) got its pre-death portfolio/withdrawal trajectory computed
under the WRONG (early) SS assumption, silently — SS materially
affects each year's guaranteed income and therefore each year's
portfolio draw. Fixed to `else ss_timing`.
(b) `survivor_ss_annual` was computed ONCE as a flat
`max(jason_ss_annual, justin_ss_annual)` and applied unconditionally
from the very first post-death year, regardless of whether either
spouse had actually reached their own claim age yet. With Jason
claiming at 70 and Justin dying at 62, the post-death schedule paid
Jason's full age-70 benefit starting at his then-current age 63 —
eight years early. Two-age Survivor already gated benefits per-year
against each spouse's own resolved claim age (sections 39-43); the
single-axis path never had that gating. Rebuilt to match: each
post-death year now computes `year_jss`/`year_uss` gated by
`age >= _jason_ss_age` / `justin_age_this_year >= _justin_ss_age`
with COLA compounding from the claim year, taking `max(...)` per year
rather than once. `survivor_ss_annual` in the return dict is now the
FIRST post-death year's actual (possibly zero) figure, not the flat
lifetime max. Verified against the review's own reproduction: Jason
claims at 70, Justin dies at 62 → `survivor_ss_annual` at the first
post-death year (age 63) is now `$0`.

**Finding 4 (P2) — the shared formula assumed a worker benefit, but
Justin's field has spousal-benefit semantics.** SSA's spousal-benefit
early-reduction schedule differs from the worker schedule: 25/36% per
month for the first 36 months before FRA and 5/12%/month beyond (a
maximum 35% reduction at 62), vs. the worker schedule's 5/9%/month and
5/12%/month (a maximum 30% reduction at 62) — `_SS_REDUCTION_FRACTION_AT_AGE`
already encoded the worker table only. Reproduced with the review's
own numbers: $9,750 at 62, $15,000 at 67, evaluated at 64 — the OLD
(worker-rate) interpolation gave `9750 + (1/3)*(15000-9750) = 11500`;
the correct spousal amount is `9750 + (2/7)*(15000-9750) = 11250`.
Fix: new `_SS_SPOUSAL_REDUCTION_FRACTION_AT_AGE = {62: 0.35, 63: 0.30,
64: 0.25, 65: 1/6, 66: 1/12}` table; `ss_benefit_for_claim_age(...,
benefit_type: str = "worker")` now takes an explicit `benefit_type`
and selects the matching reduction table, using
`reduction_table[62]` as the interpolation's own divisor so the
delayed-credit side (67-70) stays identical for both benefit types
(SSA's delayed-credit rate doesn't distinguish worker vs. spousal).
`resolve_ss_benefits` passes `benefit_type="worker"` for Jason's call
and `benefit_type="spousal"` for Justin's, matching the existing field
semantics (`justin_social_security` has always been a spousal figure
in this app — see section on Justin's benefit fields). This is a
targeted formula fix, not a survivor-model redesign, per the review's
own note.

**Guard note — claiming age wasn't range-clamped consistently.** The
benefit AMOUNT already clamped via `ss_benefit_for_claim_age`'s
below-62/above-70 handling, but the reported claim AGE (the benefit's
START date) did not — an input of 60 could receive the age-62 dollar
amount while the engine still treated benefits as starting at age 60,
four years early. New `_clamp_claim_age(claim_age)` in
`projection_engine.py`: `max(SS_CLAIM_AGE_MIN, min(SS_CLAIM_AGE_MAX,
claim_age))` (identity on `None`). Applied everywhere a claim age
feeds either the benefit lookup or the reported start age:
`resolve_ss_claim_ages`, `resolve_ss_benefits`, and both the
Jason-side and Justin-side blocks of `run_retirement_projection`.
Verified: `jason_ss_claim_age=60` now resolves to `jason_ss_age=62` (not
60) at the age-62 dollar amount; `jason_ss_claim_age=75` resolves to
`jason_ss_age=70` at the age-70 dollar amount.

New tests: 7, in a dedicated `TestNinthFollowUpReviewFindings` class in
`tests/test_ss_claim_age.py`, one per finding (Finding 3 gets two,
covering both the survivor-gating and the pre-death-label sub-issues)
plus two for the guard note (below-range and above-range), each
reproducing the review's own exact numbers. Three existing tests
updated to pass claim age as an explicit keyword argument to
`run_retirement_projection`/`run_two_dimensional_retirement_projection`
instead of embedding it in the `inputs` dict, matching the new
explicit-parameter architecture; one loosened from exact equality to
inequality because Finding 3(b)'s fix now applies real COLA compounding
to the first post-death year where the old code used a flat,
uncompounded figure. A fourth, pre-existing test in
`tests/test_simulation_engine.py`
(`test_survivor_ss_is_higher_of_the_two_not_both`) failed for the same
reason on the first full-suite run after this fix and was updated the
same way: it asserted a flat, uncompounded `30000` for a household
where Jason (claiming early at 62) dies at 70 — 8 years of 2% COLA
already earned by the first post-death year (his age 71) — so the
correct figure is `30000 * 1.02**9 = 35853`, not the raw un-compounded
input. Full suite: 47/47 in `tests/test_ss_claim_age.py`; full backend
suite 1339/1339 passed, coverage 97%+ (95% floor); sensitive-data check
passed.

An unrelated, pre-existing `IndexError` in `run_stress_tests`' `early_sequence`
scenario at very short retirement horizons was found while reproducing
Finding 1's "can crash" claim, confirmed reproducible with or without
any SS claim-age involvement (a control run with `ss_timing="early"`
and no claim age crashes identically), and left untouched as out of
scope for this review round.

Branch: `codex/ss-claim-age`, pushed — **not merged to `main`**.

## 50. Social Security claiming age 62-70 — milestone 6: frontend (2026-09-09, on `codex/ss-claim-age`)

Settings.jsx gets the three missing real-dollar anchor fields (Jason
SS at 70, Justin Spousal SS at 62, Justin Spousal SS at 70 — the app
already had Jason's 62/67 and Justin's 67), completing the 62/67/70
anchor triple per spouse the design (section 44) calls for.

A new `ClaimAgeSlider` control sits under each spouse's SS fields: an
off-by-default checkbox (claim age stays `null`/unset, meaning "use
the existing early/delayed toggle everywhere else" — purely additive,
zero behavior change for a household that never touches it) that, once
checked, reveals a 62-70 slider with a live "Benefit at N: $X/yr
(computed)" readout. The readout is computed client-side by
`frontend/src/utils/ssBenefit.js`, a hand-mirrored copy of
`ss_benefit_for_claim_age` (worker vs. spousal reduction tables,
delayed-credit table, same clamping) — kept in sync by hand since
there's no shared Python/JS source of truth; the actual saved
projection always runs through the backend's own copy.

Saved claim ages are picked up automatically by the 7 endpoints
already wired in milestones 2-5 (Monte Carlo, Stress Tests, SWR, Roth
Conversion, Tax Efficiency, Survivor Scenario, Sequence Risk) via each
endpoint's existing `_ss_claim_ages(inputs_row)` call in main.py — no
endpoint changes were needed for those.

**Deliberately NOT wired**: `/api/projections/retirement`
(`get_retirement_projections`), the data source for both Retirement.jsx
and WhatIf.jsx. This endpoint's early/delayed PAIR is a hard dependency
of Retirement.jsx's own toggle buttons and WhatIf.jsx's hardcoded
`age_X_early` label lookups (`WhatIf.jsx` lines ~27-29, ~258, ~337-338)
— passing a saved claim age would collapse the pair into a single
"custom" scenario (per milestone 1's `run_retirement_projection`
contract) and silently break WhatIf.jsx's comparison logic. Rather than
either breaking WhatIf.jsx or rewriting its label-matching logic as
part of this milestone, Retirement.jsx now shows an explanatory banner
when a claim age is saved, listing exactly which pages it does affect,
so the split is visible instead of the toggle looking silently ignored.
This is a scoping decision, not a bug — revisit if/when WhatIf.jsx
itself is generalized to handle a "custom" scenario label.

Verification: `./venv/bin/python -c "import main"` succeeds; `npm run
build` succeeds (695 modules, no new errors — the one warning is a
pre-existing chunk-size notice unrelated to this change).

Branch: `codex/ss-claim-age`, pushed — **not merged to `main`**.

## 51. Social Security claiming age 62-70 — pre-merge review of commit 0c1a569, fixes (2026-09-09, on `codex/ss-claim-age`)

Independent review of commit 0c1a569 (frontend milestone 6) found six
issues before merging. All six are fixed here.

**Finding 1 (P1) — two-age Survivor loses the selected claim age
before death.** `run_owner_split_two_dimensional_projection` requires
`jason_ss_claim_age`/`justin_ss_claim_age` as explicit parameters only
(section 49, finding 1's architectural fix) -- `_run_survivor_scenario_two_age`'s
own pre-death call (simulation_engine.py) left them unset, so the
pre-death owner walk silently fell back to the legacy `ss_timing`
default regardless of what claim age the dispatcher had already
resolved into `inputs`, a few lines above the (already-correct)
`resolve_ss_benefits` call used for the post-death schedule. Reproduced
exactly: both spouses 67, Jason claims at 70, Justin dies at 69, $1M
portfolio, zero spending/returns -- credited 3 years (67-69) of Jason's
EARLY benefit he never actually claims, reporting $1,063,000 at death
instead of $1,000,000. Fixed by reading the same `jason_ss_claim_age`/
`justin_ss_claim_age` off `inputs` and passing them through to the
pre-death walk call.

**Finding 2 (P1) — unfilled new benefit fields silently erase
benefits.** `planning_inputs.jason_ss_70`/`justin_ss_early`/
`justin_ss_70` default to `REAL DEFAULT 0` (db.py), so the column is
ALWAYS present in an existing household's row -- `inputs.get(key,
fallback)` never actually fell back, because the key was never
missing, only zero-valued. Enabling Jason's slider with a real $30,000
FRA benefit but an untouched age-70 field interpolated straight down
toward that $0 "anchor": $30,000 at 67, $20,000 at 68, $10,000 at 69,
$0 at 70. Fixed in `resolve_ss_benefits` (projection_engine.py) with
`inputs.get(key) or fallback` (falsy-aware, not just missing-aware) for
all three new fields, falling back to the same FRA figure the existing
`jason_ss_delayed` fallback already uses -- a real SS benefit is never
actually $0, so this never discards genuine household data. Settings.jsx's
`ClaimAgeSlider` mirrors the same fallback client-side and now shows an
explicit "⚠ ... is $0 — the slider below is an ESTIMATE" warning
whenever an anchor is missing, rather than silently computing a smaller
number with no explanation.

**Finding 3 (P1) — What-If's SS multiplier misses the new anchors.**
`_apply_whatif_overrides`'s `ss_mult` (main.py) only ever scaled
`jason_social_security`/`jason_ss_delayed`/`justin_social_security` --
a household with a saved claim age of 70 gets its benefit ENTIRELY
from the (unscaled) `jason_ss_70` anchor once claimed
(`ss_benefit_for_claim_age` returns `benefit_70` exactly at age ≥ 70),
so the SS multiplier slider had no effect on that household's What-If/
Monte-Carlo/Stress-Test results at all -- reproduced: with Jason
claiming at 70, moving the multiplier from 100% to 0% left deterministic
Monte Carlo's final balance unchanged. Fixed by scaling
`jason_ss_70`/`justin_ss_early`/`justin_ss_70` the same way as the
original two fields (scaling order matters: `jason_ss_delayed` is
scaled first, so if `jason_ss_70` is itself unset and falls back to it
per finding 2's fix, the fallback value is already correctly scaled).

**Finding 4 (P2) — Monte Carlo's income chart contradicts its
simulation.** `/api/retirement/income-sources`
(`get_income_sources`, main.py) is Simulation.jsx's own companion chart
to Monte Carlo (not a generic/shared endpoint like
`get_retirement_projections`/`get_retirement_sensitivity`, which were
correctly left on the legacy toggle in section 50 since they have no
claim-age-aware simulation counterpart) -- Monte Carlo's own simulation
already honored a saved claim age via `_ss_claim_ages`, so leaving this
chart on `ss_timing` made them silently disagree: reproduced with
Jason's saved claim age of 70, Monte Carlo correctly paid $0 SS at 67
while the chart showed the early/delayed toggle's $21,000 age-67
figure. Fixed: this endpoint now resolves the same claim age Monte
Carlo uses and, when either spouse has one set, looks up the resulting
`"custom"` label instead of the `ss_timing`-derived one.

**Finding 5 (P2) — active Early/Delayed controls can silently do
nothing.** With a saved claim age, `resolve_ss_claim_ages`'s 3-tier
resolution makes it win over the Early/Delayed buttons on Monte Carlo/
Historical Stress (StressTestWhatIf.jsx), yet the buttons stay
active-looking and the response still labels itself early/delayed --
verified identical balances under both selections. Fixed on the
frontend: the Social Security label now reads "(overridden by
Settings)" and an explanatory banner appears whenever a claim age is
saved, sourced from WhatIf.jsx's own single `/api/planning-inputs`
fetch via a new `onSettingsLoaded` callback prop (WhatIf.jsx stays
mounted on every tab already) rather than a second fetch -- a second,
independent fetch broke `ScenarioFlow.test.jsx`'s existing "exactly one
`/api/planning-inputs` call across a full tab-switch flow" invariant on
the first attempt, caught immediately by `npm test`.

**Finding 6 (P2) — Settings permits an independent benefit but always
applies spousal math.** Justin's existing FRA field's hint text says
"or Justin's own independent benefit, if entered directly," but the
claim-age slider always applies SSA's SPOUSAL-benefit reduction/credit
schedule (different rates than a worker's own record -- section 49,
finding 4) regardless of what kind of figure is entered. Reproduced
with worker anchors $21,000/$30,000/$37,200: age 64 gives ~$23,571 via
spousal math instead of the correct $24,000 for a worker record. Fixed
by restricting wording rather than adding a benefit-type toggle (larger
scope, deferred): the two new anchor fields' hints now say "only
meaningful for a spousal benefit," and the slider shows an explicit
scope note explaining it only supports a spousal benefit and that a
household with Justin's own independent work-record benefit should
leave the slider off and use the flat FRA figure via the legacy
Early/Delayed toggle instead (which applies no formula to that field).
The pre-existing FRA-only, flat-figure use case for a worker record is
unaffected -- only the new slider's scope is restricted.

New tests: 5 in a new `TestExternalAuditReviewOfCommit0c1a569` class in
`tests/test_ss_claim_age.py` (findings 1-4; two for finding 2, one per
new field), each reproducing the review's own exact numbers. Findings
5-6 are frontend-only (messaging/wording), verified by inspection and
`npm run build`/`npm test` rather than a new backend test. Full backend
suite: 1345 passed, coverage 97.45% (95% floor). Sensitive-data check
passed. Frontend: `npm test` 39/39, `npm run build` clean.

Branch: `codex/ss-claim-age`, pushed — **not merged to `main`**.

## 52. Social Security claiming age 62-70 — pre-merge review of commit aaa3cf5, fixes (2026-09-09, on `codex/ss-claim-age`)

Independent review of commit aaa3cf5 (six-finding fix round) found
three remaining issues before merge. All three are fixed here.

**Finding 1 (P1) — the missing-anchor fallback wasn't propagated to
the reference projection.** `run_retirement_projection` duplicated
`resolve_ss_benefits`' own anchor-resolution logic inline for both
Jason's custom-scenario benefit and Justin's benefit, instead of
calling the shared resolver -- so section 51's finding-2 fix (the
falsy-aware `inputs.get(key) or fallback`) never reached this function.
An untouched (0-valued) `jason_ss_70` anchor still interpolated toward
$0 here even though Monte Carlo (which goes through `resolve_ss_benefits`
directly) already estimated the FRA figure instead. Reproduced:
Monte Carlo ended at $1,300,000 while its own income-sources chart
(built on `run_retirement_projection`) showed $0 SS/yr for the same
household. This also affected every other consumer of this function's
balances, including single-age Survivor. Fixed by replacing both
duplicated blocks with calls to `resolve_ss_benefits` -- Jason's
custom-scenario branch and Justin's benefit resolution now go through
the exact same code path as every other consumer, so a future
anchor-fallback fix only ever needs to change one place.

**Finding 2 (P2) — saving only Justin's claim age breaks the income
chart.** `get_income_sources` (main.py) requested the `"custom"`
scenario label whenever EITHER spouse had a saved claim age, but
`run_retirement_projection`'s scenario label is driven by
`jason_ss_claim_age` alone -- Justin's claim age changes his own
benefit amount within whichever of Jason's two scenarios is being
computed, but never creates its own scenario branch (there is no
per-Justin scenario sweep in this single-axis function). With only
Justin's claim age saved (Jason's left unset), the label stayed
`"early"`/`"delayed"` as normal, but the endpoint looked up `"custom"`
anyway and returned `{"error": "Scenario not found"}`. Fixed to match
the label `jason_ss_claim_age` alone actually produces.

**Finding 3 (P2) — the new override banner can be false.** Each
spouse's claim age resolves independently (`resolve_ss_claim_ages`,
section 49 finding 1) -- with only one spouse's age saved, the
Early/Delayed buttons keep controlling the OTHER spouse's SS normally.
The single "these buttons have no effect" banner (section 51 finding
5) was simply false in that case: reproduced with only Justin's claim
age saved, toggling Early/Delayed still moved the final balance from
$1,423,000 to $1,540,000 via Jason's own SS. The banner was also
skipped entirely in two-age mode, where saved claim ages apply the
same way (section 48). Fixed on the frontend
(StressTestWhatIf.jsx): tracks each spouse's saved claim age
separately (`savedClaimAges: {jason, justin}`), and the banner text
now names which spouse is actually overridden and at what age --
"only {spouse}'s claim age ... overrides these buttons for {spouse} --
{other spouse}'s SS still responds normally" when just one is set, or
names both when both are. The same note now also renders in two-age
mode.

New tests: 3 in a new `TestExternalAuditReviewOfCommitAaa3cf5` class in
`tests/test_ss_claim_age.py` (findings 1-2; finding 1 gets two, one per
spouse), reproducing the review's own exact numbers. Finding 3 is
frontend-only (banner wording), verified by inspection and
`npm run build`/`npm test`. Full backend suite and frontend build/test
re-run; sensitive-data check passed.

Branch: `codex/ss-claim-age`, pushed — **not merged to `main`**.

## 53. Social Security claiming age 62-70 — closeout review of commit ecc862b, fix (2026-09-09, on `codex/ss-claim-age`)

Independent review of commit ecc862b (three-finding fix round) found
one small wording correction before merge, frontend-only.

**Finding — the override banner's jasonOverridden-only message implied
these buttons ever controlled Justin's SS.** `ssTiming` (the Early/
Delayed buttons) only ever drives Jason's benefit -- `resolve_ss_benefits`'
non-claim-age branch never reads `ss_timing` for Justin at all, with or
without a claim age saved; Justin's benefit has always been a flat
figure independent of this toggle. The jasonOverridden-only banner text
said "...overrides these buttons for Jason — Justin's SS still responds
normally," which implied the buttons ever affected Justin to begin
with. They never did, so once Jason is overridden the buttons have no
effect on ANYONE, not "no effect on Jason but Justin still responds."
Fixed: the jasonOverridden case now states plainly that the buttons
have no effect right now and that they only ever affected Jason's SS,
not Justin's. Applied the same correction to the justin-only case and
the button-group label suffix, which had the same category of error
(implying the toggle was "overridden for Justin" when it was never
connected to Justin's SS at all) -- that case now reads as a plain
informational note (Justin's claim age is fixed independently; the
buttons still work normally for Jason as always) rather than an
"overridden" warning, and the label suffix only appears when Jason's
claim age is actually what makes the buttons inert.

Verification: `npm test` 39/39, `npm run build` clean. No backend code
changed — full backend suite not re-run for this frontend-only wording
fix.

Branch: `codex/ss-claim-age`, pushed — **not merged to `main`**.

## 54. Social Security claiming age 62-70 — per-page interactive sliders (2026-09-09, on `main`)

Follow-up to the merged feature (sections 44-53): the claim-age slider
previously lived ONLY in Settings, applying automatically wherever the
7 wired endpoints already read the saved value. Per the user's own
request ("I want on all the pages where I can pick what age I am
taking it... so I should be able to vary that and run tests against
it"), every page that already has an Early/Delayed SS-timing toggle
now also has its own interactive claim-age slider(s), letting a
household try a claim age ad hoc without saving it to Settings first.

**Backend — explicit per-request override, layered on top of the
existing 3-tier resolution.** `main.py`'s `_ss_claim_ages(inputs_row)`
helper gained `jason_override`/`justin_override` parameters:
`jason_override if jason_override is not None else inputs_row.get(...)`
— an explicit per-request value wins, falling back to the saved
Settings value exactly as before when omitted. All 10 call sites (the
7 already-wired endpoints' GET/POST variants: Monte Carlo, Stress
Tests, SWR, Roth Conversion, Tax Efficiency, Survivor Scenario,
Sequence Risk, plus the income-sources chart) now accept
`jason_ss_claim_age`/`justin_ss_claim_age` as query params (GET) or
body fields (POST/GET-with-body), threading them through to
`_ss_claim_ages`. Omitting them (as every existing caller still does)
is byte-for-byte the same as before this change.

**`get_retirement_projections` is the one deliberate exception**: it
does NOT fall back to the saved Settings value on its own (see its own
docstring, section 50) — WhatIf.jsx's hardcoded `age_X_early` label
lookups have no claim-age awareness and would silently break the
moment this endpoint auto-applied a saved claim age. It DOES now
accept an explicit `jason_ss_claim_age`/`justin_ss_claim_age` query
param, which Retirement.jsx's own new slider passes when turned on;
WhatIf.jsx never sends these params, so it's completely unaffected.

**Frontend — shared scenario state + a shared, reusable slider
component.**
- `utils/scenario.js`/`hooks/useScenario.js`: gained
  `jasonSsClaimAge`/`justinSsClaimAge` (nullable) alongside the
  existing `retAge`/`ssTiming`, persisted to localStorage the same
  way — picking "Jason at 68" on Monte Carlo carries over to
  Historical Stress, Roth Conversion, Survivor Scenario, and
  Retirement.jsx without re-entering it. `null` means "no page-local
  override," which still correctly falls through to the saved
  Settings value via the backend's own resolution — this state is
  purely an ADDITIONAL, temporary override on top, never a replacement
  for what's saved in Settings.
- `components/ClaimAgeSlider.jsx`: the checkbox+slider+live-preview
  control, extracted from Settings.jsx's own local copy (which now
  imports this instead) so it can render inline on any page. Same
  finding-2/finding-6 protections as the Settings original (missing-
  anchor estimate warning, spousal-benefit scope note).
- `hooks/useSsAnchors.js`: shared fetch of the anchor fields
  (`jason_social_security`/`jason_ss_delayed`/`jason_ss_70`/etc.) for
  the slider's live "benefit at this age" preview. Exports
  `ssAnchorsFromPlanningInputs(d)` separately so a page that already
  fetches `/api/planning-inputs` for another reason can derive the
  same shape WITHOUT a second fetch — StressTestWhatIf.jsx reuses
  WhatIf.jsx's own single fetch this way. A second, independent fetch
  broke `ScenarioFlow.test.jsx`'s "exactly one `/api/planning-inputs`
  call across a tab-switch flow" invariant on the first attempt here
  (the same failure mode section 51's finding 5 fix had already hit
  once before it was caught) — caught immediately by `npm test` and
  fixed by reusing the existing fetch instead.

**Pages wired**: Retirement.jsx (two sliders, replacing the old
"doesn't apply here" banner with a real control; the Early/Delayed
toggle disables itself while Jason's slider is on, since it has no
effect once the response is the single "custom" scenario);
StressTestWhatIf.jsx's Monte Carlo, Historical Stress, and Survivor
Scenario tabs (one shared slider pair for Monte Carlo/Stress, since
they share the same `ssTiming`/retAge selector already; Survivor gets
its own since it has independent controls); RothConversion.jsx (one
slider pair). The override-banner logic on StressTestWhatIf.jsx
(section 53) was extended to track an "effective" claim age (this
page's own slider if set, else Settings) and name which source is
driving it, rather than only knowing about Settings.

**Deliberately NOT wired**: SideBySide.jsx (hardcodes `ss_timing=early`
with no existing toggle to extend — out of scope per the user's own
framing, "pages where I can pick what age I am taking it") and
SavedScenarios.jsx (bound to `save_scenario`'s binary early/delayed
contract for a NAMED, persisted scenario, not a live simulation to
"run tests against" — extending that contract to a continuous claim
age is a separate, larger redesign, not just adding a slider).

New tests: 3 in `tests/test_ss_claim_age.py`
(`TestPerRequestClaimAgeOverride`) covering `_ss_claim_ages`'s
override-priority unit behavior, an explicit query-param override on
Monte Carlo, and `get_retirement_projections`'s explicit-override-only
contract. Full backend suite: 1351 passed, coverage 97.45% (95%
floor). Sensitive-data check passed. Frontend: `npm test` 39/39 (one
existing RothConversion.test.jsx test updated to filter its
call-order assertions by URL instead of raw call order, since this
page now also fetches `/api/planning-inputs` once on mount for its own
slider's anchor preview), `npm run build` clean.

## 55. Social Security claiming age 62-70 — Monte Carlo/Stress UX review, fixes (2026-09-09, on `main`)

Independent UX review of the Monte Carlo/Stress Test tabs (built on the
new claim-age sliders, sections 44-54) found five issues.

**Finding 1 — "Safe Spending Power" repeated the success probability.**
Its headline read `data.success_rate` -- the exact same number the
adjacent "Probability of Success" card already showed -- while the
actually-useful figure (the safe annual draw) was buried in a detail
row below. Fixed: headline is now `fmtK(swr.safe_withdrawal_annual)`
(e.g. "$45K/yr"), with the draw rate and cushion moved up directly
underneath it; the now-redundant "Safe portfolio draw" detail row
(duplicating the new headline) and its accompanying "Cushion" row
(duplicating the new subline) were removed. Same fix applied to the
phased-plan (age 55) branch, which had the identical duplicate.

**Finding 2 — the claim-age slider preview ignored What-If's SS
multiplier.** The slider reads its anchors from saved Settings, but
the simulation it feeds applies `_apply_whatif_overrides`' `ss_mult`
(main.py) to the actual result -- at 50%, the simulation runs on half
the benefit while the slider kept showing the full, unscaled figure.
Fixed: `ClaimAgeSlider` gained an `ssMultiplier` prop (default 1) that
scales its own preview computation; StressTestWhatIf.jsx passes
`whatIfAssumptions?.ss_mult` through on both sliders, and the "Benefit
at N" line now appends "· N% of projected (What-If)" whenever the
multiplier is active, so the preview and the result next to it share
the same effective assumption.

**Finding 3 — a result didn't show the assumptions that produced it.**
Only two-age retirement ages were shown (when in two-age mode); not
the effective SS claim ages, return/inflation assumptions, income
target, bridge income, or SS multiplier. New `AssumptionsUsed`
component (native `<details>`, no extra state) renders a compact
one-line summary (ages · SS · N What-If overrides) that expands to the
full list. Sources the two-age retirement ages from the RESPONSE's own
`jason_ret_age`/`justin_ret_age` (not just the request props) so a
snapped/adjusted age is still shown accurately.

**Finding 4 — two-age retirement inputs accepted invalid values.** The
six `<input type="number">` fields across StressTestWhatIf.jsx,
RothConversion.jsx, and TwoAgeScenario.jsx had no `min`/`max`/`step`
and no clamping, relying on a generic simulation error for 0,
negative, decimal, or out-of-range ages. New `TWO_AGE_MIN`/
`TWO_AGE_MAX`/`clampTwoAge` in `utils/scenario.js` (50-75, the same
range `save_scenario`'s own backend validation already uses) applied
to all six fields, with a "Ages 50-75" hint under each.

**Finding 5 — changing a control silently cleared the result.** The
existing stale-response-guard effect (external audit 2026-09-07,
finding #13) reset `data`/`swr`/etc. to null on any dependency change
with no visible explanation -- a user moving a slider had no
confirmation the app even noticed. New `describeAssumptionChange(prev,
next, ...)` compares the previous render's snapshot to the current one
and returns a short, specific description of whichever single input
changed (checked in a fixed, most-to-least-specific order); the
"Ready to simulate"/"Ready to stress test" panels now show "Cleared
the previous result — {reason}. Run again to see the updated numbers."

**Also, reordering (control-hierarchy finding):** the "Custom Social
Security Claim Age" card was moved to render AFTER the retirement-age
controls (both single-axis and two-age variants) instead of before
them, matching Mode → Retirement Ages → SS Claim Ages → What-If →
Run — the order the reviewer laid out — rather than claim age
appearing above the ages it's claimed alongside.

Two existing tests in `TwoAgeMonteCarloStress.test.jsx` updated to
match the corrected rendering: the old literal "Alex 61"/"Sam 63" (from
the removed "Ages used" line) now reads "Alex retires 61"/"Sam retires
63" (AssumptionsUsed's own wording); "$45,000" (the old duplicate-
headline detail row) now reads "$45K" (the new dollar-amount headline,
`fmtK`-formatted).

Frontend only. `npm test` 39/39, `npm run build` clean. No backend
changes.

Branch: `main`, pushed.

## 56. Social Security claiming age 62-70 — self-review + live UX follow-up, fixes (2026-09-09, on `main`)

Two rounds: a self-driven review (clicking through the app after section
55) caught one bug before the user found it; the user's own live
testing then found seven more UX issues, none of them calculation bugs.

**Self-caught: two-age retirement inputs couldn't be typed.** Section
55's finding-4 fix (`clampTwoAge` on every keystroke) clamped the
FIRST digit of a multi-digit age immediately -- typing "65" character
by character clamped "6" alone to the 50 floor, making it impossible
to type 65 at all. Fixed with a new `parseTwoAgeInput` (lenient, no
clamp, same `|| 0` fallback every other number input in this app
already uses) for `onChange`; `clampTwoAge` moved to `onBlur`, where
snapping an out-of-range or empty value is actually wanted. Applied to
all six retirement-age inputs across StressTestWhatIf.jsx,
RothConversion.jsx, and TwoAgeScenario.jsx.

**User's live-testing findings (StressTestWhatIf.jsx / Simulation.jsx):**

1. The SS slider's "off" wording didn't say what "off" falls back TO.
   "off = use whatever's saved in Settings, if anything" didn't
   explain that turning the slider off returns to the SAVED Settings
   age (silently), not to the Early/Delayed toggle, when a Settings
   age exists. Reworded to state the full fallback chain: "off = falls
   back to your saved Settings claim age if you have one, otherwise
   the Early/Delayed toggle."

2. "Total safe spend" and "Guaranteed Income Floor" use different
   timing bases (day-one vs. steady-state-once-SS-starts) but shared
   an ambiguous "annual" framing, reading as contradictory when one
   exceeded the other. Labeled explicitly: "Total safe spend
   (day-one)" and "Guaranteed Income Floor (steady-state)," with a
   note on the steady-state card that the two aren't directly
   comparable.

3. The assumptions summary was entirely collapsed behind one
   `<details>` -- "the user has to discover and expand the
   disclosure." Split into PRIMARY rows (retirement ages, effective
   SS, returns, inflation, income target) shown immediately, with only
   SECONDARY rows (bridge income, pension/SS multipliers) still behind
   a collapsible disclosure.

4. Historical Stress's detail chart (the direct answer to "what does
   the scenario I clicked do to my portfolio") rendered LAST on the
   page, after the unrelated Roth Conversion Optimizer and
   Contribution Rate Sensitivity sections. Moved the detail chart to
   render immediately after the scenario cards; Roth/Contribution
   collapsed behind a single "▶ More tools" disclosure, off by
   default.

5. Scenario cards had no visible affordance explaining they were
   clickable or why clicking one changed the chart below. Added an
   explicit instruction line above the cards and a "▸ Viewing this
   scenario's chart below" label plus a thicker border on whichever
   card is currently selected.

6. The single-axis/two-age/SS/What-If controls all appeared in one
   flow with no mode-specific layout. Noted as a larger, deferred
   redesign (a true "choose mode first, then show only relevant
   controls" wizard) -- the section 55 hierarchy reorder (Mode → Ages
   → SS → What-If → Run) already addresses the ordering complaint;
   a full mode-specific layout is out of scope for this round.

7. `describeAssumptionChange` said "changed to X" with no reference to
   the previous value. Now says "changed from X to Y" (e.g. "claim age
   changed from 67 to 70"), matching the reviewer's own suggested
   wording, for every comparison (retirement ages, SS timing, SS claim
   ages).

No calculation errors found in this round -- purely presentation/UX.
`npm test` 39/39, `npm run build` clean. No backend changes.

Branch: `main`, pushed.

## 57. Social Security claiming age 62-70 — logical field order + third live UX follow-up, fixes (2026-09-09, on `main`)

Two independent reports: the user noticed Justin's three Settings SS
anchor fields weren't in age order, then a further live-testing pass
(no new calculation errors found) turned up six more presentation
issues on Simulation.jsx/StressTestWhatIf.jsx/ClaimAgeSlider.jsx.

**SS field ordering (Settings.jsx).** Justin's three anchor `<Row>`s
were ordered 67/62/70 -- the 67 field predates the other two and was
never moved when they were added alongside it later. Reordered to
62/67/70, matching Jason's already-correct order.

**User's live-testing findings:**

1. "Assumptions used" didn't resolve saved SS ages -- with the custom
   sliders off, a completed run said "Jason SS: take at 62 (or
   Settings, if saved)" and omitted Justin's effective age entirely.
   `AssumptionsUsed` now takes `savedJasonClaimAge`/`savedJustinClaimAge`
   (threaded from the planning-inputs fetch already done for the
   What-If Builder) and states the actual resolved age and its source
   for both people symmetrically: "Jason: 70, from Settings; Justin:
   67, from Settings" (or "from this page" when a slider here is on).

2. Switching tabs discarded a completed run -- Monte Carlo and
   Historical Stress were each conditionally rendered
   (`{tab === X && <Section/>}`), which unmounts a section entirely on
   tab-away; switching back remounted from scratch and lost the
   result. Both are now mounted-but-hidden (`<div hidden={tab !==
   X}>`), the same pattern already used for the What-If Builder --
   neither fetches anything on mount, only on its own Run button, so
   staying mounted costs nothing. Survivor Scenario was deliberately
   left conditionally-rendered: it fetches `/api/planning-inputs` on
   mount (for the household's current ages, to default the "years
   into retirement" field), so mounting it eagerly would fire that
   request before its tab is ever opened and race with the What-If
   Builder's own settings fetch for the SAME endpoint -- confirmed by
   `ScenarioFlow.test.jsx` regressing (extra `/api/planning-inputs`
   call, and the two mocked promise resolvers colliding) when Survivor
   was made eager during this fix; reverted to conditional rendering
   for that one section only. Losing Survivor's run on tab-switch
   wasn't part of what was reported.

3. Stress descriptions contradicted the selected scenario:
   Assumptions showed a 7%/yr post-retirement return while the chart
   legend hardcoded "Base Case (6%/yr)" regardless of the actual
   assumption -- the backend already computes the correct label
   (`results["base"]["label"] = f"Base Case ({post_ret*100:g}%
   every year)"`) but the frontend ignored it. Fixed by using
   `base?.label` for the chart legend `name` instead of a fixed
   string. Separately, "Bridge Job Loss" always said "a bridge job
   ending at age 57 instead of 60" and reported the plan surviving,
   even for a household with zero bridge years configured (where the
   scenario's own `bridge_years_55` override was already correctly
   capped to 0 impact by `min(bridge_override,
   inputs.get("bridge_years_55", 0))` -- the CALCULATION was right,
   only the description text was a fixed string that never reflected
   it). `simulation_engine.py`'s `run_stress_tests` (both the two-age
   and single-axis implementations) now override the description to
   "Not applicable to this scenario -- no bridge job is modeled for
   this household (Settings has 0 bridge years configured)." whenever
   `bridge_years_55 <= 0`.

4. Change notices showed raw/keystroke/null values instead of a
   meaningful comparison -- "null to 65", "65 to null", "6 to 65".
   The underlying bug: `prevSnapshotRef` was overwritten on every
   render (including mid-keystroke and mid-mode-toggle transitions),
   so `describeAssumptionChange` was diffing against the PREVIOUS
   RENDER, not the last completed run. Renamed to
   `lastRunSnapshotRef` and moved the assignment out of the
   `useEffect` into `run()`'s own success handlers (both the two-age
   and single-axis branches, in both `MonteCarloSection` and
   `StressTestSection`) -- it's now set only when a run actually
   completes. `describeAssumptionChange` also gained an explicit
   two-age-mode-toggle check *before* its raw field diffs, so
   switching between single-age and two-age mode reports "changed to
   two-age mode" / "changed to single retirement age" instead of
   diffing the now-null/now-populated per-person age fields directly.

5. The spending cards required too much interpretation, and didn't
   explain why the simplified SWR check and the full Monte Carlo plan
   can disagree. "Safe Spending Power" renamed to "Safe Portfolio
   Withdrawal"; "Total safe spend (day-one)" renamed to "Total
   spending capacity (day-one)"; the SWR card's target line now reads
   "...vs $X/yr target (including healthcare, in retirement-year
   dollars)" instead of leaving "target" ambiguous; and a one-line
   note ("A simplified single-rate check, separate from the Monte
   Carlo simulation above -- the two can disagree.") was added under
   the headline so a lower SWR number next to a healthy Monte Carlo
   result doesn't read as contradictory.

6. Claim-age slider labels wrapped awkwardly -- "Jason's claim age"
   plus a long offHint phrase, all packed into one flex row with the
   checkbox (`gap:8`), wrapped onto three short lines with a large
   gap between the checkbox and the label text whenever the row
   wasn't wide enough. `ClaimAgeSlider.jsx`'s header is now two lines:
   name (+ age, when known) on its own line, a short status line
   beneath it ("Custom age" when on; "Using Settings (age N)" when
   off and a saved age is known via the new optional `savedAge` prop;
   otherwise the existing offHint text). `savedAge` is threaded from
   StressTestWhatIf.jsx's `savedClaimAges` state for the Monte
   Carlo/Historical Stress inline sliders; Settings.jsx/Retirement.jsx/
   RothConversion.jsx/the What-If Builder's own sliders don't have
   that value in scope and fall back to the offHint-only display,
   unchanged from before.

No calculation errors found in this round except the pre-existing
`bridge_job_loss` description bug (the underlying stress-test number
was already correct; only its text was wrong). `npm test` 39/39,
`npm run build` clean, backend suite 1351/1351 passed at 97.46%
coverage, `check_sensitive_data.py` clean.

Branch: `main`, pushed.

## 58. Social Security claiming age 62-70 — section 57's own bridge_job_loss fix was incomplete (2026-09-09, on `main`)

A verification pass against section 57's own six items, run before
reporting them done, found the "not applicable" fix from item 3 only
covered half of its own reproduction case.

Section 57 gated the description override on `bridge_years_55 <= 0`
alone -- but the actual re-projection (the only place `bridge_job_loss`
has any numeric effect) is separately gated to `jason_ret_age == 55`
(two-age) / `ret_age == 55` (single-axis), several lines below. A
household WITH bridge years configured, viewed at any OTHER
retirement age, hit the exact bug section 57's own comment described
reproducing ("retirement at 65 ... description still said age 57
instead of 60") -- the first fix's condition just didn't cover it.
Confirmed by re-reading the code, not by a fresh live-testing report.

Added a second condition (`jason_ret_age != 55` / `ret_age != 55`) to
both copies (`run_two_age_stress_tests`/`run_stress_tests` in
`simulation_engine.py`), with its own message ("no bridge period at
the selected retirement age -- bridge income only applies at age
55") distinct from the zero-bridge-years message, since they're
different reasons for the same "not applicable" outcome.

No test asserted on the scenario description text either way, so this
shipped without a red test catching it -- worth a regression test if
this scenario gets touched again.

Backend suite 1351/1351 passed at 97.42% coverage,
`check_sensitive_data.py` clean. No frontend changes.

Branch: `main`, pushed.

## 59. Variable kid count (0-5) — replaces the fixed 2-kid abby/cooper model (2026-09-09, on `kids-variable-count`)

The app assumed exactly 2 kids everywhere: two fixed `planning_inputs`
columns (`kid1_name`/`kid2_name`/`kid1_age`/`kid2_age`/
`abby_529_monthly`/`cooper_529_monthly`), and `'abby'`/`'cooper'` as
literal, hardcoded account owner strings baked into ~15 exclusion
checks across `net_worth_engine.py`, `retirement_tools_engine.py`,
`allocation_engine.py`, and `projection_engine.py`. User request: "we
could have zero or 5 kids... but I want to be able to vary it."

**New data model.** A `kids` table (`id`, `name`, `age`, `monthly_529`,
`display_order`), 0-5 rows. A kid's account-ownership key is now
`f"kid_{id}"` (that kid's own database id), not a name-derived string —
renaming a kid never orphans their accounts. Every owner-exclusion
check across the four backend engines now tests the `"kid_"` prefix
(`is_kid_owner`) instead of membership in a fixed `{"abby","cooper"}`
set, so it works identically at 0, 1, or 5 kids without threading the
kid list into functions that only ever needed to know "is this a
kid's account."

**Migration** (`db.py`'s `migrate_legacy_kids`, called from
`init_kids_table()`, same unconditional-at-import pattern as
`init_tasks_table()`/`init_cash_flow_table()`): converts an existing
household's real `kid1`/`kid2` data (and their `'abby'`/`'cooper'`-owned
accounts) into the new table, once. Guarded by a `kids_migrated` flag
on `planning_inputs` — NOT by "is the kids table empty" — since a
household that migrates and later deletes back down to 0 kids must
stay at 0 on the next backend restart (this function runs at every
process start, not once ever).

**Backend:** new `/api/kids` CRUD (GET/POST/PUT/DELETE), capped at 5.
`run_education_projection`/`run_kids_projection`/`run_insurance_analysis`
now take an explicit `kids` list instead of reading `kid1_age`/
`kid1_name` etc. off `inputs` and looping exactly twice.
`_get_kids_surplus_529_monthly` generalized from two fixed goal keys
("Education funding - Abby"/"Cooper") to one per current kid
(`surplus_goal_key_for_kid`, `f"Education funding - kid_{id}"`).

**Frontend:** new `useKids()` hook, shared by every page needing the
current kid list. `usePersonNames()` dropped `kid1Name`/`kid2Name`.
`Kids.jsx`/`Education.jsx` were already written generically (map over
however many entries come back) and needed no structural changes —
only their empty-state messaging, which used to assume 0 always meant
a data-import gap rather than a genuinely kid-less household.
`Settings.jsx` replaced the fixed "Child 1/2 Name" + "Ages & 529" rows
with a real add/edit/remove list (0-5).

Backend suite 1366/1366, 97.18% coverage. `npm test` 39/39, `npm run
build` clean. `check_sensitive_data.py` clean.

**Real `cfo.db` note:** the FIRST import of this code against the real
database (an accidental `python -c "import main"` sanity check, not a
deliberate run) already executed the migration for real — confirmed
correct (2 real kids converted, accounts re-owned to `kid_1`/`kid_2`),
a safety backup was taken, and the user chose to leave it migrated
rather than revert.

Committed to the `kids-variable-count` branch, not merged — user
explicitly wants to test in the browser before merging to `main`.

### Follow-up: user's own synthetic-data testing found two migration gaps (same day)

Testing with synthetic data before release, the user found:

1. **Backup export/restore omitted the new `kids` table entirely** —
   `main.py`'s `_BACKUP_TABLES` tuple (used by both `/api/backup/export`
   and `/api/backup/restore`) predated this feature and was never
   updated. A restore reported success but left a kid's changed name/
   age/contribution untouched, since the table wasn't in the payload
   to begin with. Fixed by adding `"kids"` to `_BACKUP_TABLES` — a
   backup taken before this fix genuinely doesn't have kids data (there
   was no kids table yet), so restoring one now correctly 400s via the
   existing "every table must be a present key" check (section 57's
   own `main.py` fix from finding #14 the prior week) rather than
   silently leaving kids stale.

2. **Existing `surplus_allocations` rows under the OLD fixed goal keys
   ("Education funding - Abby"/"Cooper") were never remapped** by
   `migrate_legacy_kids` — only account owners were. A household with,
   say, $500/mo already earmarked to a kid's 529 via Assign Surplus
   would have that money become invisible (a migrated $500/mo showed
   as $0 in the education projection) — nothing after migration ever
   looks up the literal old key again, only `f"Education funding -
   kid_{id}"`. Fixed by remapping the goal string inside
   `migrate_legacy_kids` itself, using the exact new id just assigned
   to that kid (same transaction as the account-owner remap).

   Investigating this surfaced a THIRD, related gap the user was
   specifically testing for: **deleting a funded kid left that same
   goal orphaned but still counted.** Unlike an account (a real
   balance, deliberately kept non-destructive on kid delete —
   `DELETE /api/kids/{id}`'s own existing comment), a
   `surplus_allocations` row holds no balance, only a monthly
   earmarking INSTRUCTION. `SurplusPlan.jsx` only ever renders a row
   per CURRENT kid, so a deleted kid's goal became permanently
   invisible in the UI while `GET /api/surplus-allocations`'s
   `assigned` total (a plain unconditional sum over every row) kept
   counting it forever, silently shrinking `unassigned` with no way to
   ever reclaim or even see that money again. Fixed by deleting the
   kid's `surplus_allocations` row in the same `DELETE /api/kids/{id}`
   call that deletes the kid — returns the money to `unassigned`,
   where it's visible and re-allocatable, instead of leaving it stuck
   under a goal key nothing can reach.

New regression coverage: `test_main.py`'s
`test_backup_export_includes_kids_and_restore_round_trips_them`;
`test_db.py`'s `test_legacy_education_surplus_goals_remap_to_new_kid_keys`;
`test_kids_api.py`'s
`test_deleting_a_kid_clears_their_orphaned_surplus_allocation_goal`.

Still on `kids-variable-count`, not merged.

## 60. Insurance page — four fields displayed with nowhere to edit them (2026-09-09, on `main`)

User report: "it says that i need to update rental building insurance
but i do not see a place to modify." Investigation found the Rental
Property card's "Coverage unknown / Verify current policy" warning was
permanent and unfixable — `run_insurance_analysis` hardcoded
`rental_insured` to `0` with no `planning_inputs` field behind it at
all, unlike `home_insured` (a real editable Settings field feeding the
identical primary-home gap check right next to it). Asked to check the
rest of the page for the same pattern turned up two more: the
Disability card's "✓ Employer-paid — no action needed" and the LTC
card's "✓ Coverage in place · monitor premiums annually" were both
unconditional green checkmarks, regardless of whether that was ever
true for this household — `disability.to_age`/`disability.funded_by`
were hardcoded to `65`/`"Employer group policy"`, and `ltc.premium_annual`
hardcoded to `369`, all with no Settings field.

Added four real fields (`rental_insured`, `disability_funded_by`,
`disability_to_age`, `ltc_premium_annual`), mirroring the existing
`home_insured`/`umbrella` pattern exactly — new `planning_inputs`
columns, new Settings.jsx rows in "Disability & Other Insurance", read
by `run_insurance_analysis` instead of hardcoded. `report_generator.py`
needed no change — it already reads the `insurance` dict dynamically
via `.get()`, so it picks up real values automatically.

Insurance.jsx's status lines are now conditional on the real data
instead of a fixed claim: Rental Property shows an underinsured
warning only if `rental_gap > 0` (same as Primary Home already did);
Disability shows the actual `funded_by` value in green only if
`monthly_benefit > 0`, amber "no disability coverage on file" otherwise;
LTC shows its green line only if `max_benefit > 0`. The "Premium" row
that displayed `disability.funded_by` (a policy-source string, not a
dollar amount) under a "Premium" label was also mislabeled — relabeled
to "Funded By".

New regression tests in `TestRunInsuranceAnalysis`: rental
insured/gap reflects the real field (and reaches $0 gap when fully
insured); disability to_age/funded_by come from Settings; an unset
`disability_funded_by` (empty string, matching a fresh column default)
still falls back to "Employer group policy" rather than showing blank;
LTC premium comes from Settings.

Backend suite 1373/1373 passed, 97.18% coverage. `npm test` 39/39,
`npm run build` clean. `check_sensitive_data.py` clean.

Branch: `main`.

## 61. Codebase-wide sweep for the same hardcoded-field bug class — one more found (2026-09-09, on `main`)

Following section 60's insurance-page fix, asked for a systematic
sweep of the rest of the app for the same bug class: a value or status
line presented as real data with no path for the household to ever
change it. Checked every backend engine file's API response fields
against `planning_inputs`/`Settings.jsx`/`db.py`, plus every
status-heavy frontend page for unconditional claims.

**One real instance found**, in `retirement_tools_engine.py`'s
`run_rmd_planning` → `RetirementTools.jsx`'s "Lifetime RMD Total"
label: `73 + (rmd.schedule?.length ? rmd.schedule.length*2 - 2 : 0)`.
Two compounding problems, not one — hardcoded `73` as the base
(ignoring `rmd.first_rmd_age`, the real SECURE-2.0-aware value already
computed correctly and displayed two rows up on the same card), AND
the arithmetic itself was structurally unreliable regardless: it
inferred the end age from the length of the *sampled* (every-other-
year) `schedule` array while `lifetime_rmd_total` sums the *full*,
unsampled one — an assumed fixed stride through data that was already
downsampled before reaching the frontend. Fixed by having the backend
return the real value directly (`last_rmd_age`, computed off the
unsampled schedule before slicing) rather than asking the frontend to
infer it at all.

Everything else checked came back clean: the rest of
`projection_engine.py`, `retirement_tools_engine.py`'s other functions,
`debt_engine.py`, `allocation_engine.py`, `net_worth_engine.py`,
`estate_engine.py`, `rental_engine.py`, `cfo_briefing_engine.py`,
`confidence_engine.py`, `life_event_engine.py`, `cash_flow_engine.py`,
`annual_engine.py`, `quicken_importer.py`, `task_engine.py`, and
`report_generator.py` — every dollar figure, status flag, or
recommendation string traces back to real `accounts`/`inputs`/`kids`/
`cash_flow` data at call time. The hardcoded constants present
throughout (tax brackets, RMD table, standard deduction, Roth
phase-out, QCD limits, estate exemption, emergency-fund thresholds,
concentration thresholds, the local-market LTC cost benchmark, the age-based
glide-path formula) are documented, dated tax-law/market-reference
assumptions, correctly left alone. `Estate.jsx`, `Risk.jsx`,
`Insurance.jsx` (post-section-60), `TaxPlanning.jsx`, and Dashboard's
CFO Briefing/Plan Setup cards all condition their status text on real
values already.

New regression tests in `TestRunRmdPlanning`: `last_rmd_age >=
first_rmd_age` and consistent with the real (unsampled) schedule; a
household young enough to hit the 75-start SECURE 2.0 rule sees
`last_rmd_age` reflect 75, not the old hardcoded 73 base.

Backend suite 1375/1375 passed, 97.18% coverage. `npm test` 39/39,
`npm run build` clean. `check_sensitive_data.py` clean.

Branch: `main`.

## 62. Independent audit: 3 P1 + 2 P2 findings on Insurance/Estate/migration (2026-09-09, on `main`)

An independent audit of recent work (sections 59-61) reported five
findings. All five verified against the actual code and fixed.

**P1 — an insurance shortfall displayed as a green surplus.**
`justin`'s `on_track` carried a hidden $100,000 tolerance
(`justin_surplus >= -100000`, "within $100k is acceptable since Jason
keeps earning"), while `Insurance.jsx` takes `Math.abs(surplus_gap)`
and labels it "Surplus" whenever `on_track` is true — a genuine
$50,000 shortfall with zero coverage rendered as a **green "Surplus
$50,000."** Fixed on both ends: the tolerance is gone
(`justin_surplus >= 0`, matching Jason's side, which never had one),
and the frontend no longer trusts a separate boolean at all — both
rows now derive their label/color directly from `surplus_gap`'s own
sign, so display can never disagree with the number it's showing
regardless of what a backend field does in the future. Bonus: this
also silently fixes the same wrong "ON TRACK" claim in the PDF annual
report (`report_generator.py` reads the identical `on_track` field).

**P1 — child-dependent coverage inflated Justin's own coverage.**
`justin_life_kids` (Settings: "Employer dependent (\<kids\> combined)")
— money payable if a **child** dies — was summed into
`justin_current_coverage`, the figure answering "if Justin dies, is
there enough coverage to replace him." Removed from that sum; those
are separate insured lives, same principle `jason_current_coverage`
already followed (it only ever summed Jason's own policies).

**P1 — Estate records were outside the app's backup.** Both halves of
Estate.jsx were localStorage-only, never backed by a database table:
the beneficiary-designation table had no backend table at all (new
`estate_beneficiaries`, mirroring `estate_documents`'s own shape), and
`estate_documents` — which already had a full table + working API —
had simply never been called from Estate.jsx, so it sat unused the
whole time. Wiring it up surfaced a second latent bug: the existing
endpoint validated `status` against `not_started`/`in_progress`/
`complete`, a vocabulary that never matched Estate.jsx's own UI
(`executed`/`verify`/`outdated`/`pending`) — built before ever being
connected to the frontend it was meant to serve. Both tables added to
`_BACKUP_TABLES`. Estate.jsx rewritten to fetch-merge-render (matching
every other data page's convention) instead of synchronous
localStorage lazy-init; seed templates still regenerate display shape
live (labels, which kid-Roth rows exist) while real edited values
persist server-side by stable key, so a kid rename still updates the
label without touching the saved beneficiary designation.

**P2 — Insurance and Education disagreed on college funding.**
`run_insurance_analysis`'s own `total_529_gap` used a simplified
lump-sum FV-vs-total-cost comparison instead of the actual year-by-year
drawdown simulation (`_project_college_drawdown`) Education/Kids
already share. Reproduced exactly: an 18-year-old with $40,000 in a
529, $10,000/yr cost, no contributions — Education correctly shows $0
gap (growth outpaces the drawdown); the old formula compared a static
$40,000 against ~$42,466 of total cost and invented a $2,465 gap that
was never real. Now shares the identical helpers
(`_project_529_saving_phase` + `_project_college_drawdown`,
`track_unclamped=True`), so this figure can't disagree with what
Education/Kids show for the same kid.

**P2 — the surplus migration repair skipped already-migrated
databases.** Section 59's `migrate_legacy_kids` goal-key remap (added
in section 60's `dcf10cb`) is itself only reachable while
`migrate_legacy_kids`'s own `kids_migrated` guard hasn't fired yet — a
database that migrated kids BEFORE that remap code existed keeps a
dangling `"Education funding - Abby"/"Cooper"` row forever.
`repair_dangling_legacy_education_goals` is a separately versioned fix
(own `legacy_surplus_goal_repair_done` flag, own guard), called
unconditionally alongside `migrate_legacy_kids` so it reaches a
database in exactly that stuck state regardless of what the other
function already did. Identity mapping matches by NAME against
`planning_inputs`' still-present `kid1_name`/`kid2_name` columns —
reliable in the specific sense asked for: it either correctly
identifies today's `kids` row for that legacy slot, or (if the kid was
renamed since migrating) explicitly leaves the row alone rather than
guessing by row order/id, which could confidently remap to the WRONG
kid. Confirmed via direct read-only query that the real `cfo.db` has
no dangling row (nothing to repair there), but the gap was real for
any database in that intermediate state.

**P2 — main itself failed the sensitive-data check.** Section 61's own
prose named the same local-market city this app's LTC benchmark is
keyed to, tripping the denylist entry for that name — a verification/
wording issue in a doc I wrote, not a credential leak; the
`omaha_daily_cost_low/high` field *names* in code are lowercase and
never matched. Reworded to "the local-market LTC cost benchmark" (and,
per this section's own history, deliberately not spelling the city
name out again here either).

New regression tests: `TestRunInsuranceAnalysis` (kids-coverage
exclusion, shortfall-stays-a-shortfall, insurance/education parity),
`TestRepairDanglingLegacyEducationGoals` (repair + reliable-mapping +
no-clobber + one-time-only, 4 tests), `test_main.py` (estate-documents
status-vocabulary rejection, estate-beneficiaries CRUD + upsert +
mismatched-key rejection, backup round-trip for both estate tables).

Backend suite 1386/1386 passed, 97.19% coverage. `npm test` 39/39,
`npm run build` clean. `check_sensitive_data.py` clean.

Branch: `main`.

## 63. Milestone 3, first slice: navigation map + duplication fix (2026-09-09, on `codex/milestone-3-navigation`)

Backlog brief's Milestone 3 goal: reorganize the app around Household
→ Plan → Compare → Protect → Review instead of the existing topic-based
groups (WEALTH/RETIREMENT/EDUCATION & KIDS/PROTECTION/ESTATE &
PLANNING), with an explicit requirement to propose the map and get it
approved before restructuring any UI. Proposed the map (below), user
approved ("ok i'm game") before any code changed.

**Approved map:**
- Dashboard and Annual Report stay pinned outside the 5 groups (an
  entry point and a report aren't themselves one of the 5 activities).
- **Household**: Accounts, Net Worth, Debt Payoff, Monthly Cash Flow,
  Planning Inputs, Backup & Restore.
- **Plan**: Retirement Projection, Roth Conversion, Tax Planning,
  Retirement Tools, Education, Kids, Goals & Funding, Assign Surplus,
  Life-Event Planning.
- **Compare**: Compare Scenarios (new — Side by Side + Sensitivity,
  moved out of Retirement Projection's own tabs), Stress Test &
  What-If, Saved Scenarios.
- **Protect**: Insurance, Risk Management, Protection Scorecard,
  Estate Planning.
- **Review**: Action Tracker, Annual Review Checklist, Review &
  Decision Rules (renamed from "CFO Operating System").

**Two real fixes fell out of drafting the map, not just relabeling.**
Enumerating every page's actual content (required to place it
correctly) surfaced a genuine duplication: PlanOperatingSystem.jsx
("CFO Operating System") had its OWN estate-document tracker calling
the same `/api/estate-documents` endpoint Estate.jsx uses, but with a
disjoint `document_type` key set ("Will"/"Revocable trust"/etc. vs.
Estate.jsx's "trust"/"wills"/...) and a disjoint status vocabulary
(`not_started`/`in_progress`/`complete` vs.
`executed`/`verify`/`outdated`/`pending`) -- two independent,
never-reconciled checklists both claiming to answer "is our estate
plan current," for a household using both pages. This had ALREADY
caused a real regression earlier the same day: section 62's status-
vocabulary "fix" (correcting the endpoint to Estate.jsx's vocabulary
only) would have 400'd every save from PlanOperatingSystem, since it
was never checked against that second caller. Fixed immediately
(accept the union — see section 62's own follow-up commit `ade65d0`)
before this milestone's work began.

This milestone resolves it properly: Estate Planning becomes the
single canonical place for document + beneficiary tracking (it
already had the richer UI -- tax exposure, beneficiaries, action
items). PlanOperatingSystem.jsx's estate-document section is removed
entirely, replaced with a link into Estate Planning. Its other,
genuinely distinct content (assumption presets/review history,
financial runway, planning calendar) stays, renamed "Review &
Decision Rules" to fit the Review group.

**Implementation, no calculation changes:**
- New `Compare.jsx`: hosts Side by Side + Sensitivity as tabs,
  unchanged components, just a different parent. Extracted from
  `RetirementProjection.jsx`, which now only tabs Overview + Two-Age
  Scenario.
- `App.jsx`'s `NAV` array restructured into the 5 groups above;
  `pages` map updated with the new `compare` entry.
- `PlanOperatingSystem.jsx` rewritten (was a single minified line) to
  remove the `DOCS`/estate-document block and its `updateDoc`/`docs`
  state, add an `onNavigate('estate')` link, reformatted to this
  codebase's normal multi-line style since it was already being
  substantially edited.

**Verified:** `npm run build` clean, `npm test` 39/39 (no existing
test covered `App.jsx`'s nav directly, so this doesn't newly prove the
restructuring itself — see limitations below). No backend files
touched; backend suite unaffected, not re-run for this commit.

**Explicitly NOT done in this slice** (Milestone 3's other acceptance
criteria, staged as follow-up work rather than attempted all at once):
one clearly identified "active scenario" indicator across Plan/
Compare tools; progressive disclosure for advanced controls; a clear
visual distinction between saved household defaults, temporary
scenario overrides, and historical snapshots; preserving completed
results/drafts when navigating between related views; stale-result
detection + an explicit recalculate action when an assumption changes;
the exhaustive acceptance journeys the brief specifies (new vs.
existing household, two earners, unequal ages, 0/5 kids, failed
requests, unsaved-changes navigation, keyboard operation, actual
browser behavior — not yet run, since the Chrome connection needed for
a live pass wasn't available when this slice was verified). A live
browser walkthrough of the new nav itself is also still owed before
this is considered fully checked, despite the build/unit-test evidence
above.

Branch: `codex/milestone-3-navigation`, pushed, not merged — per the
backlog brief's own instruction not to merge or start the next
milestone without approval.

## 64. Milestone 3, second slice: visible active-scenario indicator (2026-09-09, on `codex/milestone-3-navigation`)

Milestone 3 acceptance criterion: "establish one clearly identified
active scenario across relevant tools." The underlying state already
existed and already carried across pages — `useScenario()`
(`utils/scenario.js`) shares `retAge`/`ssTiming`/
`jasonSsClaimAge`/`justinSsClaimAge` via a module-level store +
localStorage, so picking "Retire at 58" on one page already carried
over to the next. What was missing was ever telling the user so — each
page showed its own controls with no indication they were the SAME
choice, not a fresh per-page default.

New `components/ActiveScenarioBanner.jsx`: a compact, consistent strip
("Active scenario: Retire at X · SS \<timing\>") mounted at the top of
every page that reads `useScenario()` for something the user would
recognize as "the plan I'm looking at": `Retirement.jsx` (Overview),
`RetirementSensitivity.jsx`, `RothConversion.jsx`,
`StressTestWhatIf.jsx`. `SideBySide.jsx` and `SavedScenarios.jsx`
intentionally excluded — the former sweeps every age at once (no
single "active" age to highlight), the latter is about viewing saved
snapshots, not the live scenario.

Distinguishes a temporary override from a saved Settings default only
where that distinction actually exists in the data model: SS claim
age. `jasonSsClaimAge`/`justinSsClaimAge` are page-local overrides on
top of whatever's saved in Settings (section 54's 3-tier resolution);
when either differs from the saved value, the banner calls it out
explicitly ("— this session only, not saved"). `retAge`/`ssTiming`
have no separate "Settings default" to contrast against — the shared
scenario value IS the persistent choice already, so it's labeled
"Active scenario" rather than an invented override of something that
doesn't exist.

Self-caught before it shipped: the banner's own `savedJasonClaimAge`/
`savedJustinClaimAge` fetch (needed for the override-vs-default check)
duplicated a fetch `StressTestWhatIf.jsx` already makes for
`AssumptionsUsed` — caught immediately by
`ScenarioFlow.test.jsx`'s own call-count assertion (`toHaveLength(1)`
on `/api/planning-inputs` calls), the same test class that caught an
identical mistake earlier this session (section 57's Survivor-Scenario
eager-mount revert). Fixed by accepting optional
`savedJasonClaimAge`/`savedJustinClaimAge` props — a page that's
already fetched the value passes it through instead of the banner
redundantly fetching its own copy; pages that haven't (Retirement/
RothConversion/RetirementSensitivity) leave the props unset and the
banner fetches for itself.

**Verified:** `npm run build` clean, `npm test` 39/39 (including the
regression this caught and fixed). No backend changes. **Still not
verified live in a browser** — Chrome's extension connection wasn't
available across two attempts this session; the banner's actual
rendering/wording across the 4 pages is unconfirmed beyond build +
unit tests.

**Still explicitly open from Milestone 3's acceptance criteria**:
progressive disclosure for advanced controls; preserving completed
results/drafts when navigating between top-level pages (App.jsx fully
unmounts a page on every nav switch — pre-existing behavior, not
introduced by this milestone, but making it match "preserve drafts
between related views" properly would mean lifting page state up or
extending the mounted-but-hidden pattern App-wide, a larger
architecture decision not taken here); a stale-result/recalculate
audit of Roth Conversion and any other explicit-run tool beyond the
Monte Carlo/Stress Test pattern already built; and the brief's full
acceptance-journey testing (two earners, 0/5 kids, keyboard operation,
actual browser behavior).

Branch: `codex/milestone-3-navigation`, pushed, not merged.

## 65. Milestone 3, third slice: progressive disclosure + Survivor Scenario stale-result audit (2026-09-09, on `codex/milestone-3-navigation`)

Two more Milestone 3 acceptance criteria closed this slice.

**Progressive disclosure.** The "Custom Social Security Claim Age
(62-70)" card appears on 4 pages (`Retirement.jsx`,
`RothConversion.jsx`, `StressTestWhatIf.jsx` ×2 — the Survivor
Scenario tab and the Monte Carlo/Stress tab) as an always-expanded
card, even though it's an advanced, optional override most visits
never touch (the Early/Delayed toggle already covers the common case).
All 4 wrapped in a native `<details>`, matching this codebase's
existing pattern (`Simulation.jsx`'s "More tools" section) instead of
introducing new state — collapsed by default, but `open` automatically
whenever either slider (or, for the Monte Carlo/Stress copy, the
What-If Builder's SS multiplier) is actually active, so an in-effect
override is never hidden behind a click.

**Survivor Scenario stale-result audit.** Checked every explicit-run
tool for the `genRef`/`lastRunSnapshotRef` stale-response-guard and
clear-previous-result-notice pattern Monte Carlo/Stress Tests already
have (sections 55-56). Roth Conversion/Retirement/Sensitivity don't
need it — they auto-recompute on every input change via `useEffect`,
so there's no explicit "Run" step and therefore nothing to go stale.
Survivor Scenario (`StressTestWhatIf.jsx`'s `SurvivorScenarioSection`)
DOES have an explicit "Run Scenario" button and had NEITHER pattern:
a slow response could silently overwrite a newer selection's result,
and changing deceased/death age/survivor-need-% or a claim age after
a completed run gave no indication the result on screen no longer
matched. Both added, adapted to this section's own fields (deceased/
deathAge/needFactor instead of retAge/ssTiming/overrides) rather than
literally sharing `describeAssumptionChange` (different field
semantics; same pattern, separate implementation).

New tests in `SurvivorScenario.test.jsx`: a slow response resolving
after the deceased-spouse selection changed must not render under the
new selection; changing an input after a completed run must show
"Cleared the previous result — changed who died...".

**Verified:** `npm run build` clean, `npm test` 41/41 (39 existing +
2 new). No backend changes. Live browser verification of the
`<details>` collapse/expand behavior and the new stale notices is
still owed — same Chrome-connection gap as sections 63-64.

**Still explicitly open from Milestone 3**: cross-page result/draft
preservation (deliberately skipped per user decision — documented as
a known limitation, not attempted, since it's a larger architecture
change than fits this branch); the brief's full acceptance-journey
testing (two earners, unequal ages, 0/5 kids, failed requests,
unsaved-changes navigation, keyboard operation, actual browser
behavior).

Branch: `codex/milestone-3-navigation`, pushed, not merged.

## 66. Milestone 3 closeout + Milestone 1, first slice: reproducible saved scenarios (2026-09-09, on `codex/milestone-1-saved-scenarios`)

**Milestone 3 closeout.** The Chrome-connection gap noted at the end of
section 65 closed once the browser extension reconnected: keyboard
operability was verified live — Tab reaches the claim-age `<summary>`
(visible focus ring), `Enter` expands it, `Tab`+`Space` operates the
checkbox inside and reveals the slider, all native with no custom JS.
Two-earner/unequal-age/0-5-children journeys were re-scoped rather than
live-walked against the real household: the brief requires preserving
real data and using synthetic fixtures for those variations, and the
real household here is single-earner with 2 kids — not a stand-in for
either case. Verified instead by code inspection that none of the three
new/changed components (`ActiveScenarioBanner`, `Compare`, the `<details>`
wrappers) branch on earner count or kids count at all, so household-shape
variation is exercised by the underlying pages' own pre-existing coverage,
unchanged by this milestone. Failed-request handling verified by code
(`ActiveScenarioBanner`'s fetch has a silent `.catch(() => {})`, no crash)
rather than by killing the live backend against real data. Backend
1386 passed at 97.19% coverage, frontend 41 passed, sensitive-data check
clean — all against the actual merge commit. **Merged to `main` at
`a75258d`** after explicit user approval ("merge").

**Milestone 1** ("Complete, reproducible saved scenarios") starts here.
Prior state: `POST /api/saved-scenarios` only ever captured
`retirement_age` + `ss_timing` into `assumptions_json`, and re-saving
under an existing name silently overwrote the row (`INSERT ... ON
CONFLICT(name) DO UPDATE`) — exactly the "changing something can
silently change a saved snapshot" failure mode Milestone 1's acceptance
criteria rule out, just self-inflicted at save time instead of by a
later Settings edit.

Changes:
- `CALCULATION_ENGINE_VERSION` constant added to `projection_engine.py`
  — bump it whenever a change to `projection_engine.py`/
  `simulation_engine.py`/`annual_engine.py` changes what a given set of
  inputs produces, independent of whether the household's own inputs
  also changed. Stored on every new saved scenario as
  `calculation_version`.
- `saved_scenarios` schema gains `schema_version`, `calculation_version`,
  `seed`, `trial_count`, `revision_of`, `revision_number`, `is_legacy`
  (ALTER TABLE ADD COLUMN, same idiom as every prior migration in
  `db.py`). Existing rows get `schema_version=1`/`is_legacy=1` via
  column DEFAULT — they aren't retroactively given assumptions that were
  never captured; the migration only labels what's already true of them.
- `_capture_resolved_assumptions()`: a saved scenario's `assumptions_json`
  now holds the actual `planning_inputs` row, `accounts`, `kids`,
  `life_events`, and `surplus_allocations` used by the projection — not a
  hand-picked subset that drifts out of sync as new inputs get added —
  plus `ss_timing`, both claim-age overrides, and optional `seed`/
  `trial_count` for a future stochastic (Monte Carlo) save.
- `POST /api/saved-scenarios` is now insert-only: saving over an existing
  name 409s ("choose a different name, or use Recalculate") instead of
  overwriting. Added `jason_ss_claim_age`/`justin_ss_claim_age` to
  `ScenarioSave` so a scenario saved with a claim-age override in effect
  is actually reproducible — required fixing the scenario-lookup itself
  too, since a `jason_ss_claim_age` override collapses
  `run_retirement_projection`'s scenario set to a single `"custom"`-labeled
  entry instead of the early/delayed pair (see `jason_ss_options` in
  `projection_engine.py`); looking up `body.ss_timing` directly against
  that set returns nothing and 500s — caught by a new test before it
  shipped, fixed by checking for the override first.
- `POST /api/saved-scenarios/{id}/recalculate` (new): re-runs the
  original's exact retirement age / SS choice against *current*
  household data, inserts a new row linked via `revision_of` (pointing at
  the lineage root, so recalculating a revision doesn't create a new
  chain), and never modifies the original row. Verified by test that the
  original's own `summary_json` is byte-identical after a recalculation
  that used genuinely different account balances.
- `GET /api/saved-scenarios/{id}` (new): single-scenario fetch, 404 on
  missing id — used by the reopen/recalculate flow.
- Frontend `SavedScenarios.jsx` rewritten (was a single minified line):
  inline 409 error surfaced instead of silently failing, a `LEGACY` badge
  on pre-migration rows, a "Recalculate with current data" button per
  scenario, and a lightweight two-scenario compare panel (checkbox-select
  two → outcome diff + differing `planning_inputs` fields) satisfying
  "support comparison of saved scenarios" without a full diff framework.
- `Retirement.jsx` gains a "Save this scenario" card right under
  `ActiveScenarioBanner` — the one page whose result this milestone wires
  up first, since it's already the same `run_retirement_projection` call
  the save endpoint itself makes. Posts exactly the claim-age overrides
  currently active on screen, not a separate hand-entered copy.

**Verified**: 13 new/rewritten backend tests (insert-only 409, legacy
flagging via a hand-inserted pre-migration row, revision creation +
lineage linkage, recalculation reflecting changed account data while the
original stays untouched, single-scenario 404, claim-age override
capture) — all passing alongside the full existing suite. Frontend: 5 new
`SavedScenarios.test.jsx` tests (legacy badge exclusivity, save payload,
409 surfaced inline, Recalculate posts to the right endpoint and reloads,
compare panel renders both outcome and assumption diffs) + 3 new
`Retirement.test.jsx` tests (save payload matches on-screen assumptions,
409 surfaced, no request sent with an empty name) — full frontend suite
green (9 files, 49 tests). Full backend suite run separately against this
branch (see completion report for the count).

**No calculation changes** — `run_retirement_projection` itself is
untouched; only what gets captured/stored around it changed.

**Explicitly out of scope for this slice** (left for a follow-up slice or
noted as a known limitation, not silently dropped):
- "Save this scenario" was added to the Retirement Projection *overview*
  result only — Monte Carlo, Roth Conversion, and the other "major
  results" Milestone 2 will also cover don't have it yet.
- The `seed`/`trial_count` fields exist on the schema and in
  `ScenarioSave`/`_capture_resolved_assumptions`, but nothing currently
  saved is a stochastic (Monte Carlo) result, so they're written as
  `null` on every save so far — wiring a Monte Carlo "save this scenario"
  call site through them is unbuilt.
- Comparison is two-scenario-at-a-time via checkboxes, not an N-way
  comparison view.
- Backup/restore was not given a dedicated new test for the added
  columns — `_BACKUP_TABLES` dumps/restores `saved_scenarios` generically
  by `SELECT *`/dict, so the existing backup/restore tests already
  exercise the new columns incidentally, but no test asserts on them
  specifically.

Branch: `codex/milestone-1-saved-scenarios`, branched from `main` at
`a75258d` (post-Milestone-3-merge). Not yet pushed — pending the full
backend suite run against this commit.
