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
