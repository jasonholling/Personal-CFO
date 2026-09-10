# Consistent Household Tax and Account Ownership — Design Proposal (not built)

Status: **design only, no code written**. Milestone 4 of the current
four-milestone backlog brief. Per the brief's own instruction: "Begin
with a design-only proposal. Do not implement the full redesign before
approval." Nothing in this document changes behavior — it's an
inventory and a proposed shared contract, written after inspecting the
actual current implementation (`projection_engine.py`,
`simulation_engine.py`, `annual_engine.py`), not from assumptions about
what it does.

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

## 4. Proposed shared annual tax contract

A `TaxContract` (name is a placeholder) that every tax-computing
consumer reads from instead of each independently assembling its own
rate:

- **Inputs**: gross wage/bonus/RSU income (pre-retirement), 401k/IRA
  pretax contributions (reduce taxable wages), pretax withdrawal amount,
  RMD amount, taxable-bucket gain amount, pension income, SS taxable
  portion, filing status (NEW — doesn't exist today), state tax rate
  (already exists, just needs to move into this shared shape).
- **Output**: `{federal_marginal_rate, state_rate, effective_pretax_rate,
  effective_taxable_rate, total_tax_owed}` — same shape every consumer
  reads, whether it came from the marginal-bracket path or (for Tax
  Efficiency, unchanged) the flat-rate path. The MODEL CHOICE stays
  per-consumer and explicit (item 1 of section 2's table); the OUTPUT
  SHAPE becomes uniform so a future explainer panel (Milestone 2) can
  render "how this was taxed" identically regardless of which model
  produced it.
- **Withholding vs. liability**: out of scope for this contract's v1 —
  documented as unsupported (section 2 above), surfaced in the UI as an
  explicit "this shows estimated tax owed, not a withholding schedule"
  note wherever a tax figure appears, rather than silently implying
  precision that doesn't exist.

## 5. Proposed owner-attributed account model

The correct, tested machinery already exists
(`run_owner_split_two_dimensional_projection`) — this is a generalization
and exposure proposal, not a new withdrawal architecture:

- **Generalize the entry point.** Make the owner-split walk callable by
  any consumer, not hardcoded to two-age Survivor Scenario's dispatch
  path. Its by-construction reconciliation approach (run the pooled
  calculation once, allocate the resulting deltas across owners via
  `WITHDRAWAL_OWNER_ORDER`, apply growth per-owner after allocation) is
  the right architecture to keep — extending it to other consumers
  means removing whatever assumptions currently couple it specifically
  to the two-age Survivor Scenario call site (e.g. its handling of
  `death_jason_age`/`deceased`, which a consumer with no death event has
  no use for), not replacing the allocation approach itself.
- **Expose it to the frontend.** Today `yearly_detail`'s owner-split
  `{owner: {"pretax":.., "roth":.., "taxable":.., "hsa":..}}` shape is
  computed and then only read internally by the death-walk dispatch —
  it never reaches an API response. Surfacing it (even just for
  two-age Survivor Scenario first, where it already exists) is the
  actual "does Justin's 401k run out before Jason's Roth IRA" answer
  users would see.
- **RMDs**: RMDs are per-person, by law (each spouse's own pretax
  accounts have their own RMD schedule). The owner-split walk
  documented above keeps RMD aggregate/Jason-anchored today
  ("per-spouse RMDs during normal both-alive operation are explicitly
  out of scope per section 37.4" — an existing, documented limitation,
  not new). Making RMDs genuinely per-person is real remaining scope,
  layered on top of owner-attributed balances rather than a
  prerequisite for them (the balances are already owner-attributed;
  the RMD calculation itself isn't yet).
- **Transfers/survivor transitions**: when one spouse dies, their
  pretax/taxable/roth balances transfer to the survivor (with real tax
  consequences — inherited IRA rules, a filing-status change from
  joint to single) that NONE of today's Survivor Scenario math
  attempts, pooled or owner-split — the existing owner-split walk
  reports balances up to death but doesn't model the inheritance
  transaction itself. Flagged as unsupported.
- **Migration**: the owner-split walk already reads the existing
  `owner` column with no invented ownership, exactly per the
  milestone's own instruction. A household whose accounts are all
  `owner='joint'` gets the exact same pooled behavior as before opting
  in; this is additive, not a forced migration, and already proven
  true by the existing reconciliation tests.

## 6. What stays unsupported, and how it should appear in the UI

Per the milestone's own instruction ("identify which advanced tax rules
remain unsupported and how limitations will appear in the UI"):

| Limitation | Where it should be disclosed |
|---|---|
| No withholding/quarterly-payment modeling, only an annual-liability estimate | Wherever a tax figure renders (natural fit: Milestone 2's "How this was calculated" panel, once extended to tools other than retirement funding) |
| No filing-status input | Settings/Planning Inputs, if filing status becomes a real input; until then, a note wherever the marginal-rate estimate is shown |
| Tax Efficiency's flat-rate model doesn't reflect the household's real bracket | Tax Efficiency's own page, ideally its own explainer once Milestone 2 reaches it |
| Owner-attributed withdrawal (Slice 2 below) doesn't exist yet outside Survivor Scenario's starting balances | Any page that shows per-owner figures, until Slice 2 ships |
| Survivor transitions (inherited-account tax treatment) unmodeled | Survivor Scenario's own existing disclosure text (it already has one methodology note; extend it) |

## 7. Proposed implementation slices

Each slice ships independently, on its own branch, with its own
reference tests — per the overall brief's "start each milestone from
current GitHub main... commit and push for independent review."

**Slice 1 — Expose the existing owner-split walk's output.**
No new calculation logic: `run_owner_split_two_dimensional_projection`
already computes a correct, tested, by-construction-reconciled
per-owner `yearly_detail`. Return it from two-age Survivor Scenario's
existing API response (it's computed today and discarded after the
death-row lookup) so the frontend can render "Jason's balance vs
Justin's" for the one consumer where the math already exists.
*Acceptance*: no numeric change to any currently-returned field; new
test asserts the newly-exposed owner-split rows sum to the
already-returned pooled figures for the same response.

**Slice 2 — Generalize the owner-split walk beyond Survivor Scenario.**
Remove the assumptions coupling `run_owner_split_two_dimensional_projection`
specifically to the death-walk call site (its `death_jason_age`/
`deceased` handling) so a consumer with no death event can call it too,
reusing the same by-construction allocation approach.
*Acceptance*: `TestOwnerSplitReconcilesAgainstPooledTotals`-style test
extended to a second call site with no death event; zero behavior
change for the existing Survivor Scenario caller.

**Slice 3 — Owner-attributed withdrawal for a second consumer.**
Wire the generalized entry point from Slice 2 into one more consumer
(candidate: the plain pooled two-age Retirement Projection, since it's
the most-used tool and already shares the same underlying pooled
function this walk reconciles against).
*Acceptance*: household + owner-level reconciliation test (owner totals
sum to the same pooled total the unmodified function produces, for
identical inputs) — same verification pattern already used for the
existing split.

**Slice 4 — Shared tax-contract output shape.**
Introduce the uniform `{federal_marginal_rate, state_rate,
effective_pretax_rate, effective_taxable_rate, total_tax_owed}` shape
from section 4, adopted by the marginal-bracket path first (no behavior
change — it's a repackaging of numbers already computed). Tax
Efficiency's flat-rate path adopts the same output shape without
adopting the same model — the shape unifies, the number doesn't.
*Acceptance*: existing tax-related tests for every adopting consumer
pass unchanged; new tests assert the output shape's fields reconcile to
what each consumer's own existing (unchanged) internal tax figure was.

**Slice 5 — Filing status input (if approved as in-scope).**
Add a filing-status field to Planning Inputs, thread it through the
shared tax contract as a real parameter instead of an implicit
married-filing-jointly assumption. Bigger scope decision — should be
approved separately, since it touches the bracket table itself
(`retirement_tools_engine.py` / wherever `_marginal_rate` sources its
brackets), not just the contract's plumbing.
*Acceptance*: TBD pending approval to even scope this slice; likely its
own before/after numeric-difference documentation given it changes real
tax-rate numbers for any household not filing jointly.

## 8. Explicitly out of scope for all of this

- No slice above changes any number a currently-shipped consumer
  produces for a household with `owner='joint'` on every account (the
  common case) — every slice is additive/opt-in until proven otherwise
  by its own reconciliation test.
- Inherited-account/survivor-transition tax treatment (section 6) is
  named as a real gap, not silently promised by any slice here.
- Withholding/quarterly-payment modeling is explicitly NOT proposed —
  section 4 recommends disclosure, not implementation, given the scope
  this would add for a personal (non-payroll-integrated) planning tool.

## 9. Approval needed before any of section 7 is implemented

Per the brief's own instruction, nothing in sections 4–7 gets built
until this document itself is reviewed and approved — including which
slices (if any) are in scope for now versus deferred indefinitely.
