import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import Retirement from './Retirement'
import { setRetAge, setSsTiming } from '../utils/scenario'

// Milestone 1 (2026-09-09, "Add 'Save this scenario' to the relevant
// planning results"): rendered-DOM coverage for the save button added to
// the primary retirement-projection result, confirming it posts exactly
// the assumptions the page is currently showing.

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
  const button = [...container.querySelectorAll('button')].find(b => b.textContent.trim() === text)
  expect(button, text).toBeTruthy()
  await act(async () => { button.click() })
}
const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
const typeInto = async (el, value) => {
  await act(async () => {
    nativeInputValueSetter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

const yearlyDetail = Array.from({ length: 30 }, (_, i) => ({
  jason_age: 60 + i, pension: 10000, social_security: 20000, rmd_reinvested: 0,
  withdrawal: 30000, portfolio_balance: i < 29 ? 500000 : 0,
}))

const scenario = (label) => ({
  label, retirement_age: 60, percent_funded: 85, projected_surplus: 40000,
  years_to_retirement: 5, retirement_end_age: 90, current_investable_assets: 800000,
  portfolio_at_retirement: 900000, pension_annual: 30000, jason_ss_start_age: 62,
  yearly_detail: yearlyDetail,
})

const resolvedAssumptions = {
  planning_inputs: { id: 1, jason_age: 55 }, accounts: [{ id: 1, name: 'Test 401k' }],
  kids: [], life_events: [], surplus_allocations: [],
}

const projectionsResponse = { scenarios: [scenario('age_60_early'), scenario('age_60_delayed')], resolved_assumptions: resolvedAssumptions }

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  setRetAge(60)
  setSsTiming('early')
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
})
