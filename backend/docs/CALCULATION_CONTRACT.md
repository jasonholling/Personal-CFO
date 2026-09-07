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

**(2) and (3) were deliberately left alone.** Both are dense,
already-audit-hardened, off-by-one-sensitive code (the file's own
comments document several previously-shipped bugs in exactly this kind
of timing/boundary logic — a rollover double-counted in two accounts at
once, a timeline off by one year of compounding, a headline number that
disagreed with its own chart by a factor of the growth rate). Critically,
they aren't actually the SAME calculation duplicated twice the way (1)
was: Kids' age-60 account timeline has no Education equivalent at all,
and Education's drawdown tracks an unclamped "worst deficit" figure
across the 4 college years that Kids' drawdown doesn't compute. Forcing
these into one shared shape would mean inventing a common abstraction
that doesn't already exist in proven, agreed-upon behavior on both
sides — the opposite of what made (1) safe. This matches the prior
session's own assessment (see `git log` — "the remaining work there is
code-sharing for its own sake, not a bug fix") and Jason's own prior
decision to leave adjacent Kids/Education work (>2-kids support) out of
scope given how bug-hardened this pair of functions already is. Revisit
only if a real bug (not a duplication-for-its-own-sake concern) is found
in either's drawdown/timeline logic.

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
