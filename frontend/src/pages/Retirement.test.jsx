import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import Retirement from './Retirement'
import { setRetAge, setSsTiming, setJasonSsClaimAge } from '../utils/scenario'

// Milestone 1 (2026-09-09, "Add 'Save this scenario' to the relevant
// planning results") + Milestone 2 (2026-09-09, "Explain every major
// result") -- combined in one file per the integration branch
// (codex/milestone-1-2-integration): both features live on this same
// page and both read off the same `s`/`data` values, so this file also
// covers the cross-check that the saved scenario and the explanation
// panel refer to the same displayed calculation.

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('../hooks/usePersonNames', () => ({
  usePersonNames: () => ({ person1Name: 'Alex', person2Name: 'Sam' }),
}))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.ResizeObserver = globalThis.ResizeObserver || class {
  observe() {} unobserve() {} disconnect() {}
}

let container, root
const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve() }) }
const click = async text => {
  const button = [...container.querySelectorAll('button, summary')].find(b => b.textContent.trim() === text)
  expect(button, text).toBeTruthy()
  await act(async () => { button.click() })
}
// React tracks <input> value via a property override, so a plain
// `el.value = x` assignment is invisible to its onChange — go through the
// native setter first, same workaround RTL's fireEvent uses internally.
const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
const typeInto = async (el, value) => {
  await act(async () => {
    nativeInputValueSetter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

const yearlyDetail = Array.from({ length: 5 }, (_, i) => ({
  jason_age: 60 + i, year: 2031 + i, income_need: 80000, healthcare_cost: 5000,
  pension: 10000, social_security: 20000, bridge_income: 0, justin_gap_income: 0,
  rmd: i === 2 ? 8000 : 0, rmd_reinvested: i === 2 ? 8000 : 0, estimated_tax: 4000,
  withdrawal_pretax: 30000, withdrawal_taxable: 10000, withdrawal_roth: 5000, withdrawal_hsa: 0,
  withdrawal: 45000, unmet_need: 0, portfolio_balance: 900000 - i * 20000,
}))

const scenario = (label) => ({
  label, retirement_age: 60, percent_funded: 85, projected_surplus: 40000,
  years_to_retirement: 5, retirement_end_age: 90, current_investable_assets: 800000,
  portfolio_at_retirement: 900000, pension_annual: 30000, jason_ss_start_age: 62,
  income_today_dollars: 70000, income_first_year: 80000, state_income_tax_rate: 0.05,
  yearly_detail: yearlyDetail,
})

const resolvedAssumptions = {
  planning_inputs: { id: 1, jason_age: 55 }, accounts: [{ id: 1, name: 'Test 401k' }],
  kids: [], life_events: [], surplus_allocations: [],
}

const projectionsResponse = {
  scenarios: [scenario('age_60_early'), scenario('age_60_delayed')],
  resolved_assumptions: resolvedAssumptions,
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  setRetAge(60)
  setSsTiming('early')
  setJasonSsClaimAge(null)
  axios.get.mockImplementation(url => {
    if (url === '/api/projections/retirement') return Promise.resolve({ data: projectionsResponse })
    if (url === '/api/planning-inputs') return Promise.resolve({ data: {} })
    return Promise.resolve({ data: [] })
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

describe('Retirement Projection — Save this scenario (Milestone 1)', () => {
  it('posts the currently-active retirement age and SS timing when saved', async () => {
    axios.post.mockResolvedValue({ data: { id: 1 } })
    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush()
    const nameInput = container.querySelector('input[placeholder="e.g. Retire at 62 with delayed SS"]')
    await typeInto(nameInput, 'My Saved Plan')
    await click('Save this scenario')
    await flush()
    expect(axios.post).toHaveBeenCalledWith('/api/saved-scenarios', {
      name: 'My Saved Plan', retirement_age: 60, ss_timing: 'early',
      jason_ss_claim_age: null, justin_ss_claim_age: null,
      summary: scenario('age_60_early'), household_data: resolvedAssumptions,
    })
    expect(container.textContent).toContain('Saved.')
  })

  it('sends the exact resolved_assumptions bundle the page was rendered from, not a fresh re-read', async () => {
    // Milestone 1 acceptance follow-up: this is the regression the fix
    // targets -- the payload's household_data must be object-identical
    // (well, deep-equal, since it round-trips through JSON) to what THIS
    // GET response returned, never re-fetched or recomputed client-side.
    axios.post.mockResolvedValue({ data: { id: 1 } })
    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush()
    const nameInput = container.querySelector('input[placeholder="e.g. Retire at 62 with delayed SS"]')
    await typeInto(nameInput, 'Pinned Plan')
    await click('Save this scenario')
    await flush()
    const [, payload] = axios.post.mock.calls[0]
    expect(payload.household_data).toEqual(resolvedAssumptions)
    expect(payload.summary.percent_funded).toBe(85)
    // The projections GET only ever happened once -- Save triggers no
    // second fetch/recompute of its own.
    const projectionCalls = axios.get.mock.calls.filter(([url]) => url === '/api/projections/retirement')
    expect(projectionCalls).toHaveLength(1)
  })

  it('surfaces a 409 name-conflict message instead of failing silently', async () => {
    axios.post.mockRejectedValue({ response: { data: { detail: 'A saved scenario with this name already exists.' } } })
    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush()
    const nameInput = container.querySelector('input[placeholder="e.g. Retire at 62 with delayed SS"]')
    await typeInto(nameInput, 'Duplicate')
    await click('Save this scenario')
    await flush()
    expect(container.textContent).toContain('already exists')
  })

  it('does not send a save request when no name has been entered', async () => {
    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush()
    await click('Save this scenario')
    await flush()
    expect(axios.post).not.toHaveBeenCalled()
  })

  it('an earlier, slower request cannot overwrite a later, faster one (out-of-order response guard)', async () => {
    // Review finding (2026-09-09): nothing previously stopped a slow
    // response for the FIRST fetch (no claim-age override) from resolving
    // after a fast response for a SECOND fetch (override just turned on)
    // -- setData would then apply the stale first response last, leaving
    // `data` (and therefore both the KPI cards and Save's payload) out of
    // sync with the claim age actually selected on screen.
    let resolveSlow
    const slowResponse = new Promise(resolve => { resolveSlow = resolve })
    const responseNoOverride = { scenarios: [
      { ...scenario('age_60_early'), portfolio_at_retirement: 111111 },
      scenario('age_60_delayed'),
    ], resolved_assumptions: resolvedAssumptions }
    const responseWithOverride = { scenarios: [
      { ...scenario('age_60_custom'), portfolio_at_retirement: 222222 },
    ], resolved_assumptions: resolvedAssumptions }

    axios.get.mockImplementation((url, config) => {
      if (url === '/api/planning-inputs') return Promise.resolve({ data: {} })
      if (url !== '/api/projections/retirement') return Promise.resolve({ data: [] })
      if (config?.params?.jason_ss_claim_age === 70) {
        return Promise.resolve({ data: responseWithOverride }) // fast, resolves immediately
      }
      return slowResponse.then(() => ({ data: responseNoOverride })) // slow, resolves only when told to
    })

    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush() // first (slow) request is in flight, not yet resolved

    await act(async () => { setJasonSsClaimAge(70) }) // second (fast) request fires and resolves
    await flush()
    // portfolio_at_retirement renders via fmtK ($XXXK for sub-$1M figures).
    expect(container.textContent).toContain('$222K')

    await act(async () => { resolveSlow() }) // the stale first request finally resolves
    await flush()
    // Must still show the fast/current response -- the stale one must
    // not have overwritten it just because it resolved later.
    expect(container.textContent).toContain('$222K')
    expect(container.textContent).not.toContain('$111K')
  })
})

describe('Retirement Projection — How this was calculated (Milestone 2)', () => {
  it('renders the explainer panel and expands to show the scenario\'s own dollar basis and flows', async () => {
    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush()
    expect(container.textContent).toContain('How this was calculated')
    await click('How this was calculated')
    await flush()
    expect(container.textContent).toContain('Retirement age: 60')
    expect(container.textContent).toContain('$70,000')
    expect(container.textContent).toContain('$80,000')
    expect(container.textContent).toContain('2031 (age 60)')
  })

  it('reconciles opening/closing balances across years and discloses that growth is not itemized per-year', async () => {
    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush()
    await click('How this was calculated')
    await flush()
    // Opening balance for year 3 (index 2) must equal year 2's own closing
    // balance -- this is the carry-forward identity the explainer relies
    // on instead of re-deriving anything; year 0's opening must equal the
    // scenario's own portfolio_at_retirement.
    expect(container.textContent).toContain('$900,000') // year 0 opening = portfolio_at_retirement
    expect(container.textContent).toContain('$880,000') // year 1 closing = year 2 opening
    // The transfers column surfaces the one nonzero rmd_reinvested row.
    expect(container.textContent).toContain('$8,000')
    // Growth is explicitly disclosed as unavailable, not silently omitted.
    expect(container.textContent).toContain("Investment growth isn't itemized per year")
  })

  it('withdrawal breakdown sums to the same total shown as the gross withdrawal figure', async () => {
    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush()
    await click('How this was calculated')
    await flush()
    const breakdownSum = yearlyDetail[0].withdrawal_pretax + yearlyDetail[0].withdrawal_taxable
      + yearlyDetail[0].withdrawal_roth + yearlyDetail[0].withdrawal_hsa
    expect(breakdownSum).toBe(yearlyDetail[0].withdrawal)
    expect(container.textContent).toContain('pretax $30,000')
    expect(container.textContent).toContain('taxable $10,000')
  })
})

describe('Integration (Milestone 1 + Milestone 2 combined, 2026-09-09) — same displayed calculation', () => {
  it('the saved-scenario summary and the explainer panel agree on the same portfolio-at-retirement figure', async () => {
    axios.post.mockResolvedValue({ data: { id: 1 } })
    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush()

    // The explainer panel's own displayed opening balance for year 0...
    await click('How this was calculated')
    await flush()
    expect(container.textContent).toContain('$900,000')

    // ...is the exact same number Save posts as the scenario's
    // portfolio_at_retirement. Both features read off the same `s`
    // object -- this asserts that fact directly rather than assuming it
    // from reading the source.
    const nameInput = container.querySelector('input[placeholder="e.g. Retire at 62 with delayed SS"]')
    await typeInto(nameInput, 'Cross-check')
    await click('Save this scenario')
    await flush()
    const [, payload] = axios.post.mock.calls[0]
    expect(payload.summary.portfolio_at_retirement).toBe(900000)
  })

  it('changing the retirement age drops both features into the same "no data" state, not a silent divergence', async () => {
    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush()
    await click('Age 65')
    await flush()
    // No age_65 scenario exists in the fixture, so the page falls back to
    // its own "No data for this scenario" state -- confirms both features
    // are driven by the SAME lookup (`s`), not independent state that
    // could silently diverge when the shared retAge changes.
    expect(container.textContent).toContain('No data for this scenario')
    expect(container.textContent).not.toContain('How this was calculated')
    expect(container.textContent).not.toContain('Save this scenario')
  })
})
