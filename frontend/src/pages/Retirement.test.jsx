import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import Retirement from './Retirement'
import { setRetAge, setSsTiming } from '../utils/scenario'

// Milestone 2 (2026-09-09, "Explain every major result"): confirms the
// "How this was calculated" panel is actually wired into the retirement
// projection result, and that its numbers are read from the same
// scenario object the KPI cards above it use -- not a second, separately
// fetched copy that could drift out of agreement.

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

const yearlyDetail = Array.from({ length: 5 }, (_, i) => ({
  jason_age: 60 + i, year: 2031 + i, income_need: 80000, healthcare_cost: 5000,
  pension: 10000, social_security: 20000, bridge_income: 0, justin_gap_income: 0,
  rmd: 0, rmd_reinvested: 0, estimated_tax: 4000,
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

const projectionsResponse = { scenarios: [scenario('age_60_early'), scenario('age_60_delayed')] }

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
})
