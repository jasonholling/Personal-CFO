import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import TwoAgeScenario from './TwoAgeScenario'
import { setPrivacyMode } from '../utils/privacy'

// Minimal rendered-output coverage for the new two-dimensional-retirement
// UI (backend/docs/TWO_DIMENSIONAL_RETIREMENT_DESIGN.md section 7) --
// asserts against actual DOM text for a mocked API response, not just
// that the request fires with the right params.

vi.mock('axios', () => ({ default: { get: vi.fn() } }))
vi.mock('../hooks/usePersonNames', () => ({
  usePersonNames: () => ({ person1Name: 'Alex', person2Name: 'Sam' }),
}))
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container, root
const flush = async () => { await act(async () => { await Promise.resolve() }) }

const mockResult = {
  has_data: true,
  jason_ret_age: 61, justin_ret_age: 63,
  phase2_start_age: 61, phase3_start_age: 63,
  later_retiree: 'justin',
  portfolio_at_phase2_start: 200000,
  on_track: true,
  account_ownership_limitation: 'Portfolio buckets are a single aggregated household total, not attributed to either spouse.',
  second_earner_net_of_tax_factor: 0.65,
  yearly_detail: [
    { year: 2087, jason_age: 61, justin_age: 61, phase: 'phase2', income_need: 80000, still_working_spouse_income: 65000, draw: 15000, unmet_need: 0, portfolio_balance: 185000 },
    { year: 2088, jason_age: 62, justin_age: 62, phase: 'phase2', income_need: 80000, still_working_spouse_income: 65000, draw: 15000, unmet_need: 0, portfolio_balance: 170000 },
    { year: 2089, jason_age: 63, justin_age: 63, phase: 'phase3', income_need: 80000, still_working_spouse_income: 0, draw: 80000, unmet_need: 0, portfolio_balance: 90000 },
  ],
}

beforeEach(() => {
  vi.clearAllMocks()
  setPrivacyMode(false)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  axios.get.mockResolvedValue({ data: mockResult })
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

const clickRun = async () => {
  const button = [...container.querySelectorAll('button')].find(b => b.textContent.trim() === 'Run Scenario')
  await act(async () => { button.click() })
  await flush()
}

describe('TwoAgeScenario', () => {
  it('sends both ages as explicit query params, not a single-axis fallback', async () => {
    await act(async () => root.render(<TwoAgeScenario />))
    const inputs = container.querySelectorAll('input[type=number]')
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inputs[0], '61')
      inputs[0].dispatchEvent(new Event('input', { bubbles: true }))
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inputs[1], '63')
      inputs[1].dispatchEvent(new Event('input', { bubbles: true }))
    })
    await clickRun()
    expect(axios.get).toHaveBeenCalledWith('/api/projections/two-dimensional-retirement', {
      params: { jason_ret_age: 61, justin_ret_age: 63 },
    })
  })

  it('renders the phase-2/phase-3 split and the still-working income for each year', async () => {
    await act(async () => root.render(<TwoAgeScenario />))
    await clickRun()
    expect(container.textContent).toContain('$185,000')
    expect(container.textContent).toContain('$65,000')
    expect(container.textContent).toContain('One retired')
    expect(container.textContent).toContain('Both retired')
    expect(container.textContent).toContain('$90,000')
  })

  it('renders the account-ownership limitation note from the API response', async () => {
    await act(async () => root.render(<TwoAgeScenario />))
    await clickRun()
    expect(container.textContent).toContain('not attributed to either spouse')
  })

  it('masks dollar amounts in privacy mode', async () => {
    setPrivacyMode(true)
    await act(async () => root.render(<TwoAgeScenario />))
    await clickRun()
    expect(container.textContent).not.toContain('$185,000')
    expect(container.textContent).toContain('$•••,•••')
  })
})
