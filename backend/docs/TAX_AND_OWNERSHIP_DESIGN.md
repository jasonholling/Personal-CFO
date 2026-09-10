# Consistent Household Tax and Account Ownership — Design Proposal (not built)

Status: **design only, no code written**. Milestone 4 of the current
four-milestone backlog brief. Per the brief's own instruction: "Begin
with a design-only proposal. Do not implement the full redesign before
approval." Nothing in this document changes behavior — it's an
inventory and a proposed shared contract, written after inspecting the
actual current implementation (`projection_engine.py`,
`simulation_engine.py`, `annual_engine.py`), not from assumptions about
what it does.

**Revised 2026-09-10** per independent review: the original version
(section headings below reflect the revision) mainly proposed exposing
ownership data and standardizing tax output fields without achieving
consistent tax calculations across consumers, and didn't distinguish
between work that's risk-free by construction and work that would need
its own verification or its own approval before touching a household's
actual numbers. Section 4 explains the three-category split this
revision organizes everything else around.

## 1. Goal, restated

"Equivalent household assumptions produce consistent treatment across
planning tools." Two consumers given the same household should not
silently disagree about (a) how much tax a dollar of withdrawal costs,
or (b) whose account a dollar sits in — unless the disagreement is
itself a documented, intentional product decision, in which case it
needs to say so where the user can see it, not just in a code comment.

## 2. Inventory: tax treatment across every consumer

The shared `annual_engine.py` module already documents (CALCULATION_
CONTRACT.md section 3.1) that **two tax philosophies coexist by
design**:

| Model | What it does | Who uses it |
|---|---|---|
| **Marginal-bracket + state** (`marginal_bracket_tax_model`, via `_marginal_rate`/`_pretax_marginal_tax_rate`) | Estimates a federal marginal rate from that year's taxable income (pension + SS-taxable-portion + RMD + gross income), adds the flat `state_income_tax_rate` input on top, caps at 90% | `run_retirement_projection` (the main Retirement Projection page), Monte Carlo, Stress Tests (both single-age and two-age), `run_roth_conversion_analysis` |
| **Flat-rate simplification** (`flat_rate_tax_model`, hardcoded 22%/15%) | A fixed 22% on pretax withdrawals, 0% on Roth, 15% on taxable gains — no bracket estimate, no state rate | `run_tax_efficiency_simulation` only (its own docstring says so explicitly: *"Simplified tax: flat 22% on pretax withdrawals, 0% on Roth, 15% on taxable gains"*) |

This split is **already intentional and already documented** — Tax
Efficiency's whole purpose is comparing draw-ORDER strategies quickly,
and a fixed rate isolates that comparison from bracket-estimate noise.
This proposal does not change that. What it needs to fix is that the
distinction lives in a code comment and a function docstring, not
anywhere a user looking at Tax Efficiency's numbers can see "this tool
uses a simplified flat tax rate, not your actual bracket" — the
milestone's own Milestone 2 counterpart ("explain intentional
differences between tools") is the natural home for surfacing this, and
should be wired up once Tax Efficiency gets its own explainer (see
Milestone 2's own "explicitly out of scope" list).

**Where state tax is and isn't applied**, confirmed by direct inspection
rather than assumed:

| Consumer | Applies `state_income_tax_rate`? |
|---|---|
| Retirement Projection | Yes |
| Monte Carlo / Stress Tests (single-age and two-age) | Yes |
| Roth Conversion | Yes |
| Tax Efficiency | **No** — uses the flat-rate model above, which has no state-rate input at all |
| Survivor Scenario | Yes (goes through the same shared `annual_engine` path as Retirement Projection) |

Again: not a bug, a consequence of the already-documented flat-rate
choice. Listed here so it's inventoried once instead of re-discovered
per-consumer.

**What is genuinely unaddressed, not just a documented tradeoff**:
- **Withholding vs. annual liability.** Nothing in this codebase models
  paycheck withholding, quarterly estimated payments, or an April
  true-up. Every consumer computes an estimated marginal rate for the
  year and treats that as the tax "owed" on that year's withdrawal —
  there's no over/under-withholding carryover, no distinction between
  "tax withheld at the time of a distribution" (real 401k/IRA
  distributions typically have mandatory withholding) and "tax actually
  due for the year." This is a real simplification with no double-
  counting risk today only because there's nothing to double-count
  against — but it means a household expecting "how much do I need to
  set aside for taxes this year" gets an estimate, not a payment
  schedule. Flagging as unsupported rather than trying to fix silently.
- **Filing status.** No filing-status input exists anywhere
  (`_marginal_rate`'s bracket table is not surfaced as parameterized by
  filing status in what's visible from these consumers' call sites) —
  every household is implicitly modeled the same way regardless of
  actual filing status. Whether that's "married filing jointly" or
  something else isn't an explicit, inspectable assumption today; it
  should become one.
- **Capital-gains treatment inside the LTCG threshold.** Already
  flagged in CALCULATION_CONTRACT.md section 3.1 item 3 as an
  intentional per-strategy difference within `run_tax_efficiency_
  simulation` alone (its three draw-order strategies deliberately use
  different capital-gains treatment from EACH OTHER, not just from the
  other consumers) — inventoried here for completeness, not new.

**Single-age vs. two-age tax-model divergence — checked directly, none
found.** Revision note (2026-09-10, independent review): the original
version of this proposal didn't check this explicitly. Read the actual
two-age implementations of the three consumers that have both a
single-age and a two-age form:
- `_run_tax_efficiency_simulation_two_age`: its own docstring states it
  "has never had bracket-aware or marginal-rate-aware taxation" and
  reuses the identical flat-rate model — confirmed consistent with
  single-age Tax Efficiency by design, not coincidence.
- `_run_roth_conversion_analysis_two_age`: reads `state_income_tax_rate`
  and calls the same `_pretax_marginal_tax_rate`/marginal-bracket
  pattern single-age Roth Conversion uses, at the same call sites
  (`base_taxable`/`room_in_22` bracket-capacity math included) — a
  comment at this call site explicitly notes still-working income is
  folded into the spending-need offset "in either direction -- exactly
  the existing single-axis" behavior, i.e. deliberately mirrored, not
  independently reimplemented and hoped to match.
- `run_owner_split_two_dimensional_projection` (two-age Retirement
  Projection/Survivor Scenario's underlying walk): uses the identical
  `min(0.90, _marginal_rate(...) + max(0, state_income_tax_rate))`
  pattern found in single-age `run_retirement_projection` — same
  formula, independently confirmed at each of the three call sites in
  `projection_engine.py`.

So: no currently-existing single-age/two-age tax-model gap was found.
What IS real, and is exactly what makes "no gap today" fragile rather
than reassuring: this identical formula is typed out independently at
6+ call sites across `projection_engine.py` and `simulation_engine.py`,
not called from one shared function. A past real bug (Roth Conversion's
`base_taxable` once omitting gross wages from bracket capacity, fixed
per CALCULATION_CONTRACT.md section 32) lived in exactly one of these
copies while the others were unaffected — proof duplication is how a
single-age/two-age (or any-consumer/any-other-consumer) divergence
actually gets introduced in this codebase, even though none currently
exists. See category 2 below.

## 3. Inventory: account ownership across every consumer

**The default, everywhere except one deliberately-scoped exception**:
portfolio buckets (pretax/roth/taxable/hsa) are a single aggregated
household total. `ACCOUNT_OWNERSHIP_LIMITATION_NOTE`
(`projection_engine.py`) already says this explicitly: *"Portfolio
buckets... are a single aggregated household total, not attributed to
either spouse."* Every account's `owner` field (`jason`/`justin`/
`joint`) exists in the schema and is read for exactly one purpose today
— summing into the pooled total — never for keeping balances separate
during withdrawal.

**The one exception is more complete than it first appears.** Two
functions exist, both additive/opt-in and both built specifically for
two-age Survivor Scenario:

- `owner_split_starting_balances_two_age()` — partitions the pooled
  accumulation-phase dollar amounts into per-owner buckets at the
  phase-2 boundary (verified by `TestOwnerSplitReconcilesAgainstPooledTotals`
  to sum back to the pooled total exactly), using these rules:
  - Each account's own `owner` field decides its bucket — no invented
    ownership.
  - 401k pretax/roth split uses the same household-level
    `pretax_401k_pct` for every owner bucket (no per-spouse election
    exists in the input schema).
  - Ongoing contributions (401k, RSU, bonus) attribute to whichever
    spouse's paycheck formula produced them.
  - Household-level inflows with no natural individual owner (asset
    sales, life events, surplus allocations, the single HSA
    contribution figure) attribute to a JOINT bucket — an explicit,
    documented approximation, not a claim about real titling.
- `run_owner_split_two_dimensional_projection()` — a FULL year-by-year
  owner-attributed walk through the withdrawal phase, not just a
  starting snapshot. Per its own docstring, reconciliation is **by
  construction**: each year it runs the exact same `simulate_
  withdrawal_year` call the pooled function makes (identical need/
  guaranteed-income/RMD/tax-rate/draw-order — RMD stays aggregate,
  Jason-anchored, matching every other two-age consumer), then
  allocates that single pooled result's per-bucket balance CHANGE
  across the owner buckets via `WITHDRAWAL_OWNER_ORDER` (for a real
  withdrawal) or that year's own income-source proportions (for a
  surplus/RMD-reinvestment inflow) — never recomputing the tax/RMD/draw
  math a second, independent way. Growth is applied per-owner
  afterward, at the identical rate, so the four owner balances always
  sum to the pooled balance exactly. This is what actually powers
  two-age Survivor Scenario's pre-death owner-attributed walk in
  production today (`simulation_engine.py`'s two-age survivor dispatch).

So the honest current state is: owner-attributed balances, INCLUDING
through the withdrawal phase, already exist and are already tested —
but only as a private implementation detail of one consumer (two-age
Survivor Scenario), not as something any other tool can call, and not
surfaced anywhere in the UI as "here's Jason's balance vs Justin's."
Every other consumer (SWR, Monte Carlo, Roth Conversion, Tax
Efficiency, the plain pooled Retirement Projection, and even
single-age Survivor Scenario) still reads the pooled function only.

**What this means concretely**: ask "does Justin's 401k run out before
Jason's Roth IRA" today and the answer is computed correctly, by
construction, inside two-age Survivor Scenario's own internals — but
never returned to the frontend or exposed anywhere a user could see
it, and unavailable to every other tool. The gap isn't "can this be
computed correctly" (it already is) — it's "is it generalized,
exposed, and consistent across tools."

## 4. Revision note (2026-09-10): three categories, not one undifferentiated slice list

Independent review of the first version of this proposal: it "mainly
exposes ownership data and standardizes tax output fields... does not
yet achieve consistent tax calculations across consumers" — true, and
the first version didn't say so clearly enough. The work in sections
5-7 below splits into three categories with genuinely different risk
profiles, and conflating them was the actual problem, not any one
slice's content:

1. **Visibility improvements that preserve existing calculations** —
   zero risk to any currently-returned number, by construction (each
   one is exposing or repackaging a value already computed, never
   computing a new one). "Preserve existing results" applies without
   qualification here.
2. **Actual tax-policy consolidation** — collapsing the 6+ duplicated
   copies of the marginal-bracket-rate formula (section 2's revision
   note) into one shared function. This is NOT risk-free the way
   category 1 is: consolidating duplicated code is exactly how a
   currently-hidden divergence between two copies would surface — and
   section 2 already found zero current divergence across every copy
   checked, but "checked by reading the code" is not the same
   confidence level as "checked by an equivalence test." Every
   consumer's existing numbers must be verified equal before-and-after
   consolidation, not assumed equal because the source read the same.
   "Preserve existing results" applies here CONDITIONALLY: only where
   verification confirms the policies were already equivalent — if
   consolidation surfaces a real pre-existing divergence between two
   consumers (not found yet, but not ruled out by inspection alone
   either), that is a bug to fix and document with a before/after
   example per the brief's own instruction, not something to preserve
   by keeping the old, divergent, unconsolidated code paths.
3. **Owner-specific obligations that can legitimately change household
   totals** — genuinely new calculations (per-person RMDs, inherited-
   account tax treatment) that do NOT exist today in any form, pooled
   or owner-split. These are not preservable by definition — there is
   no existing "true" total to preserve, and implementing them WILL
   change a household's total RMD or survivor-scenario tax figure
   relative to today's aggregate/Jason-anchored approximation, for any
   household where the two spouses' ages actually differ. Explicitly
   NOT bundled with categories 1-2's "no behavior change" framing.

## 5. Category 1 — visibility improvements (preserve existing calculations, unconditionally)

The correct, tested machinery already exists
(`run_owner_split_two_dimensional_projection`) — these slices expose or
repackage it, computing nothing new:

- **Expose the owner-split walk's output.** Today `yearly_detail`'s
  owner-split `{owner: {"pretax":.., "roth":.., "taxable":..,
  "hsa":..}}` shape is computed and then only read internally by the
  death-walk dispatch — it never reaches an API response. Returning it
  (Slice 1 below) lets the frontend render "Jason's balance vs
  Justin's" for the one consumer where the math already exists, with
  zero change to any currently-returned field.
- **Uniform tax-output shape.** A `{federal_marginal_rate, state_rate,
  effective_pretax_rate, effective_taxable_rate, total_tax_owed}` shape
  every consumer's tax computation populates — the MODEL CHOICE stays
  per-consumer and unchanged (marginal-bracket vs. Tax Efficiency's
  flat rate, per section 2's table); only the OUTPUT SHAPE becomes
  uniform, so a future explainer panel can render "how this was taxed"
  identically regardless of which model produced the number. This is
  repackaging an already-computed number into a shared shape — not
  consolidating the computation itself (that's category 2).
- **Disclosure of existing limitations in the UI** (section 8): stating
  in the UI that Tax Efficiency uses a flat rate, or that this shows an
  annual-liability estimate rather than a withholding schedule, changes
  no number — it's pure visibility.

## 6. Category 2 — actual tax-policy consolidation (conditional preservation, verify before assuming)

Collapsing the marginal-bracket-rate formula
(`min(0.90, _marginal_rate(...) + max(0, state_income_tax_rate))`,
independently typed at 6+ call sites across `projection_engine.py` and
`simulation_engine.py`) into one shared function every consumer calls,
instead of each retyping it. Section 2's revision note already checked
by direct reading that every current copy computes the same thing for
the same inputs — this category's job is proving that with an
equivalence test, not re-asserting it from the same reading:

- **Known single-age/two-age differences, checked**: none found (section
  2). Both are held to the exact same marginal-bracket-plus-state
  formula in every consumer that has both forms (Roth Conversion, the
  owner-split/pooled Retirement Projection walk). Tax Efficiency's
  flat-rate model is the one deliberate exception, applied identically
  in its single-age and two-age forms — not a single-age/two-age split
  at all, a Tax-Efficiency-vs-everything-else split (section 2's table).
- **Consolidation is still worth doing** precisely because no
  divergence exists YET — duplication, not divergence, is the actual
  risk being retired. The one confirmed historical bug in this area
  (Roth Conversion's `base_taxable` omitting gross wages, section 2)
  lived in exactly one of several near-identical copies; a shared
  function makes that class of bug structurally impossible to
  reintroduce in only one call site again.
- **What "preserve existing results" means here, precisely**: after
  consolidation, every consumer's tax-related output fields
  (`estimated_tax`, Roth Conversion's `base_taxable_income`/
  `room_in_22_bracket`, etc.) must be byte-for-byte identical to their
  pre-consolidation values, for every existing test fixture and a new
  battery of synthetic households spanning the input ranges each
  consumer's own bracket-capacity logic branches on (zero income, high
  income crossing bracket edges, RMD-triggering ages, both SS timings).
  If any consumer's number changes, that is either a bug the
  consolidation exposed (document it, don't silently keep the old
  wrong number) or proof the two copies were never actually equivalent
  (stop, don't consolidate that pair until the real difference is
  understood and it's clear which one is correct).

## 7. Category 3 — owner-specific obligations that legitimately change totals

Genuinely new calculations, not exposures of existing ones — no
"preserve existing results" framing applies, because there is no
existing result to preserve:

- **Per-person RMDs.** RMDs are legally per-person: each spouse's own
  pretax balance uses the IRS Uniform Lifetime Table factor for THEIR
  OWN age. Today's aggregate/Jason-anchored RMD (section 3, "per-spouse
  RMDs during normal both-alive operation are explicitly out of scope
  per section 37.4") uses one age for the whole pooled pretax balance.
  For any household where the spouses' ages actually differ, a genuine
  per-person calculation produces a DIFFERENT total mandatory
  distribution than today's aggregate approximation — smaller if the
  younger spouse holds a larger share of the pretax balance (their
  factor is more generous), larger if the older spouse does. This is a
  real, documentable numerical change, not a bug fix disguised as one —
  today's aggregate figure was always a stated approximation, not a
  wrong computation of a well-defined "true" figure.
- **Inherited-account/survivor transitions.** When one spouse dies,
  their pretax/taxable/Roth balances transfer to the survivor under
  real inherited-IRA rules, and the household's filing status changes
  from joint to single — which changes the marginal bracket the
  survivor's future withdrawals land in. None of today's Survivor
  Scenario math (pooled or owner-split) attempts this; the owner-split
  walk reports balances up to death and stops. Modeling it would change
  the survivor scenario's post-death tax figures relative to today's
  (undocumented-as-such) implicit assumption that the household's tax
  treatment doesn't change at death.
- Both require their own before/after numeric examples and explicit
  approval before implementation, per the brief's own instruction — not
  bundled into category 1/2's lower-risk work, and not scoped further
  in this document until that approval exists.

## 8. What stays unsupported, and how it should appear in the UI

Per the milestone's own instruction ("identify which advanced tax rules
remain unsupported and how limitations will appear in the UI"):

| Limitation | Category | Where it should be disclosed |
|---|---|---|
| No withholding/quarterly-payment modeling, only an annual-liability estimate | Unsupported (disclose only) | Wherever a tax figure renders (natural fit: Milestone 2's "How this was calculated" panel, once extended to tools other than retirement funding) |
| No filing-status input | Unsupported (disclose only) | A note wherever the marginal-rate estimate is shown |
| Tax Efficiency's flat-rate model doesn't reflect the household's real bracket | Category 1 (already intentional, needs visibility) | Tax Efficiency's own page, ideally its own explainer once Milestone 2 reaches it |
| Owner-attributed withdrawal doesn't exist yet outside Survivor Scenario's starting balances | Category 1 (exists, not exposed elsewhere) | Any page that shows per-owner figures, until that slice ships |
| Per-person RMDs unmodeled (aggregate/Jason-anchored today) | Category 3 (would change totals) | Wherever an RMD figure is shown, until implemented and approved |
| Survivor transitions (inherited-account tax treatment) unmodeled | Category 3 (would change totals) | Survivor Scenario's own existing disclosure text (it already has one methodology note; extend it) |

## 9. Proposed implementation slices

Each slice ships independently, on its own branch, with its own
reference tests — per the overall brief's "start each milestone from
current GitHub main... commit and push for independent review." Grouped
by category (section 4) so approval can be granted per-category, not
all-or-nothing.

**Category 1 (visibility, unconditional preserve) — lowest risk, most defensible to approve first:**

- **Slice 1 — Expose the existing owner-split walk's output.** Return
  two-age Survivor Scenario's already-computed, already-discarded
  per-owner `yearly_detail` from its API response.
  *Acceptance*: no numeric change to any currently-returned field; new
  test asserts the newly-exposed owner-split rows sum to the
  already-returned pooled figures for the same response.
- **Slice 2 — Generalize the owner-split walk beyond Survivor Scenario.**
  Remove the assumptions coupling `run_owner_split_two_dimensional_
  projection` specifically to the death-walk call site so a consumer
  with no death event can call it too, reusing the identical
  by-construction allocation approach — no new withdrawal logic.
  *Acceptance*: `TestOwnerSplitReconcilesAgainstPooledTotals`-style test
  extended to a second call site with no death event; zero behavior
  change for the existing Survivor Scenario caller.
- **Slice 3 — Owner-attributed withdrawal for a second consumer.** Wire
  Slice 2's generalized entry point into one more consumer (candidate:
  the plain pooled two-age Retirement Projection).
  *Acceptance*: household + owner-level reconciliation test (owner
  totals sum to the same pooled total the unmodified function produces,
  for identical inputs).
- **Slice 4 — Shared tax-output shape.** The uniform
  `{federal_marginal_rate, state_rate, effective_pretax_rate,
  effective_taxable_rate, total_tax_owed}` shape (section 5), adopted by
  every consumer without changing which model computes the number.
  *Acceptance*: existing tax-related tests for every adopting consumer
  pass unchanged; new tests assert the output shape's fields reconcile
  to what each consumer's own existing (unchanged) internal tax figure
  was.

**Category 2 (consolidation, conditional preserve) — needs its own equivalence-test pass, not bundled with category 1:**

- **Slice 5 — Shared marginal-bracket-rate function.** Replace the 6+
  independently-typed copies of `min(0.90, _marginal_rate(...) +
  max(0, state_income_tax_rate))` with calls to one shared function.
  *Acceptance*: a new synthetic-household battery (zero income, high
  income crossing bracket edges, RMD-triggering ages, both SS timings)
  run through every adopting consumer before AND after, asserting
  byte-for-byte identical tax-related output fields. Any discrepancy
  found stops the slice — it becomes its own documented bug-fix (with a
  before/after example) rather than being silently absorbed into
  "consolidation."

**Category 3 (owner-specific, changes totals) — requires separate approval, not started by approving categories 1-2:**

- **Slice 6 — Per-person RMDs (if approved as in-scope).** Real
  per-spouse Uniform Lifetime Table calculation instead of the
  aggregate/Jason-anchored approximation.
  *Acceptance*: before/after numeric examples for at least one household
  with meaningfully different spouse ages, showing exactly how the
  total RMD changes and why; TBD pending approval to even scope this
  slice further.
- **Slice 7 — Inherited-account survivor transitions (if approved as
  in-scope).** Model the filing-status change and inherited-IRA tax
  treatment at death.
  *Acceptance*: TBD pending approval; likely the largest single slice in
  this document given it touches both the owner-split ledger and the
  tax-rate computation together.

## 10. Explicitly out of scope for all of this

- No category-1 or category-2 slice changes any number a currently-
  shipped consumer produces, for ANY household — category 1 by
  construction (repackaging only), category 2 by its own required
  equivalence-test gate (section 6). Category 3 is the only place
  numbers can legitimately change, and only there, and only after its
  own separate approval.
- Withholding/quarterly-payment modeling is explicitly NOT proposed —
  section 8 recommends disclosure, not implementation, given the scope
  this would add for a personal (non-payroll-integrated) planning tool.
- Filing-status-as-a-real-input (beyond disclosing today's implicit
  assumption) is not scoped in this revision — the first version's
  "Slice 5" for this is dropped pending a decision on whether it
  belongs in category 2 (if it turns out today's implicit assumption
  really is uniform MFJ everywhere, making this pure consolidation) or
  category 3 (if adding it changes any consumer's current numbers for
  a household that isn't MFJ).

## 11. Approval needed before any of section 9 is implemented

Per the brief's own instruction, nothing in section 9 gets built until
this document itself is reviewed and approved — and per section 4,
approval can reasonably be granted per-category rather than all at
once: category 1 carries essentially no risk to existing calculations,
category 2 requires its own equivalence-test discipline, and category 3
requires a separate decision about whether household totals should
change at all before any slice in it is scoped further.
