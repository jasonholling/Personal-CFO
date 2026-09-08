import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import StressTestWhatIf from './StressTestWhatIf'
import { setPrivacyMode } from '../utils/privacy'

// Two-age Monte Carlo/Stress Tests UI (backend/docs/
// CALCULATION_CONTRACT.md section 22) -- rendered-DOM coverage for the
// toggle, the two age inputs replacing the single-age selector, the
// working-spouse income/65% disclosure, privacy masking, the age-pair
// label, and stale-result clearing.

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('../hooks/usePersonNames', () => ({
  usePersonNames: () => ({ person1Name: 'Alex', person2Name: 'Sam' }),
}))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
// recharts' ResponsiveContainer (used by MonteCarloSection/
// StressTestSection's charts) needs a ResizeObserver, absent in jsdom.
globalThis.ResizeObserver = globalThis.ResizeObserver || class {
  observe() {} unobserve() {} disconnect() {}
}

let container, root
const flush = async () => { await act(async () => { await Promise.resolve() }) }
const click = async text => {
  const button = [...container.querySelectorAll('button')].find(b => b.textContent.trim() === text)
  expect(button, text).toBeTruthy()
  await act(async () => { button.click() })
}

const mcTwoAgeResult = {
  success_rate: 88.5, mode: 'two_age', jason_ret_age: 61, justin_ret_age: 63,
  phase2_start_age: 61, phase3_start_age: 63, later_retiree: 'justin',
  retirement_end_age: 90, ss_timing: 'early', portfolio_at_retirement: 900000,
  median_final_balance: 400000, median_depletion_age: 90, simulations: 1000,
  chart: [{ age: 61, p10: 100000, p25: 200000, p50: 300000, p75: 400000, p90: 500000 }],
  still_working_spouse_income_first_year: 65000, second_earner_net_of_tax_factor: 0.65,
  account_ownership_limitation: 'Portfolio buckets are a single aggregated household total.',
}

const stTwoAgeResult = {
  mode: 'two_age', jason_ret_age: 61, justin_ret_age: 63,
  phase2_start_age: 61, phase3_start_age: 63, later_retiree: 'justin',
  retirement_end_age: 90, ss_timing: 'early', portfolio_at_retirement: 900000,
  still_working_spouse_income_first_year: 65000, second_earner_net_of_tax_factor: 0.65,
  account_ownership_limitation: 'Portfolio buckets are a single aggregated household total.',
  scenarios: {
    base: { label: 'Base Case', survived: true, final_balance: 400000, chart: [{ age: 61, balance: 400000 }] },
    crash_2008: { label: '2008 Market Crash', description: 'desc', survived: true, final_balance: 350000,
      depletion_age: 90, lowest_balance: 300000, lowest_balance_age: 62,
      chart: [{ age: 61, balance: 350000, base: 400000 }] },
    lost_decade: { label: 'Lost Decade', description: 'desc', survived: true, final_balance: 320000,
      depletion_age: 90, lowest_balance: 280000, lowest_balance_age: 62,
      chart: [{ age: 61, balance: 320000, base: 400000 }] },
    early_sequence: { label: 'Early Retirement Sequence Risk', description: 'desc', survived: true, final_balance: 310000,
      depletion_age: 90, lowest_balance: 270000, lowest_balance_age: 62,
      chart: [{ age: 61, balance: 310000, base: 400000 }] },
  },
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  setPrivacyMode(false)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  axios.get.mockImplementation(url => {
    if (url === '/api/simulation/monte-carlo') return Promise.resolve({ data: mcTwoAgeResult })
    if (url === '/api/simulation/stress-tests') return Promise.resolve({ data: stTwoAgeResult })
    return Promise.resolve({ data: {} })
  })
  axios.post.mockResolvedValue({ data: { scenarios: [] } })
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

describe('Two-Age Monte Carlo', () => {
  it('toggling two-age mode replaces the single-age selector with two age inputs', async () => {
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await click('Monte Carlo')
    await flush()
    await click('Use Two Independent Retirement Ages')
    await flush()
    expect(container.textContent).toContain("Alex's Retirement Age")
    expect(container.textContent).toContain("Sam's Retirement Age")
    // The single-age SS-claiming-timing selector (its own labeled
    // buttons, not the What-If Builder's always-mounted-but-hidden
    // "Social Security" slider) must not render in two-age mode.
    const ssButtons = [...container.querySelectorAll('button')].map(b => b.textContent.trim())
    expect(ssButtons).not.toContain('SS at 62')
    expect(ssButtons).not.toContain('SS at 67')
  })

  it('sends both ages as explicit query params when running in two-age mode', async () => {
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await click('Monte Carlo')
    await flush()
    await click('Use Two Independent Retirement Ages')
    await flush()
    const inputs = container.querySelectorAll('input[type=number]')
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inputs[0], '61')
      inputs[0].dispatchEvent(new Event('input', { bubbles: true }))
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inputs[1], '63')
      inputs[1].dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click('Run Monte Carlo Simulation')
    await flush()
    expect(axios.get).toHaveBeenCalledWith('/api/simulation/monte-carlo', {
      params: { jason_ret_age: 61, justin_ret_age: 63 },
    })
  })

  it('renders the age pair used, the working-spouse income, and the 65% factor', async () => {
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await click('Monte Carlo')
    await flush()
    await click('Use Two Independent Retirement Ages')
    await flush()
    await click('Run Monte Carlo Simulation')
    await flush()
    expect(container.textContent).toContain('Alex 61')
    expect(container.textContent).toContain('Sam 63')
    expect(container.textContent).toContain('$65,000')
    expect(container.textContent).toContain('65%')
  })

  it('masks the working-spouse income in privacy mode', async () => {
    setPrivacyMode(true)
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await click('Monte Carlo')
    await flush()
    await click('Use Two Independent Retirement Ages')
    await flush()
    await click('Run Monte Carlo Simulation')
    await flush()
    expect(container.textContent).not.toContain('$65,000')
    expect(container.textContent).toContain('$•••,•••')
  })

  it('clears the stale result when either age input changes', async () => {
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await click('Monte Carlo')
    await flush()
    await click('Use Two Independent Retirement Ages')
    await flush()
    await click('Run Monte Carlo Simulation')
    await flush()
    expect(container.textContent).toContain('88.5%')
    const inputs = container.querySelectorAll('input[type=number]')
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inputs[0], '62')
      inputs[0].dispatchEvent(new Event('input', { bubbles: true }))
    })
    await flush()
    expect(container.textContent).not.toContain('88.5%')
  })
})

describe('Two-Age Stress Tests', () => {
  it('sends both ages and renders the base scenario plus the working-spouse note', async () => {
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await click('Historical Stress')
    await flush()
    await click('Use Two Independent Retirement Ages')
    await flush()
    await click('Run Stress Tests')
    await flush()
    expect(axios.get).toHaveBeenCalledWith('/api/simulation/stress-tests', {
      params: { jason_ret_age: 65, justin_ret_age: 65 },
    })
    expect(container.textContent).toContain('$65,000')
    expect(container.textContent).toContain('65%')
  })
})
