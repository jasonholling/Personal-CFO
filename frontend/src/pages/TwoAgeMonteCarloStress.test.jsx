import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import StressTestWhatIf from './StressTestWhatIf'
import { setPrivacyMode } from '../utils/privacy'
import { setSsTiming as setGlobalSsTiming } from '../utils/scenario'

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

const swrTwoAgeResult = {
  mode: 'two_age', jason_ret_age: 61, justin_ret_age: 63,
  phase2_start_age: 61, phase3_start_age: 63, later_retiree: 'justin',
  ss_timing: 'early',
  safe_withdrawal_annual: 45000, safe_withdrawal_rate: 5.0,
  total_safe_spend: 55000, income_target: 50000, cushion_pct: 10.0,
  guaranteed_income_steadystate: 10000, ss_start_age: 67,
  pension_annual: 0, jason_ss_annual: 0, justin_ss_annual: 0,
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
  // ../utils/scenario.js keeps ssTiming/retAge as module-level state
  // outside React on purpose (shared across pages) -- localStorage.clear()
  // alone doesn't reset the in-memory value once a prior test has
  // changed it (initScenarioFromStorage only overwrites it when
  // localStorage actually has a stored value), so reset explicitly.
  setGlobalSsTiming('early')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  axios.get.mockResolvedValue({ data: {} })
  axios.post.mockImplementation((url, body) => {
    if (url === '/api/simulation/monte-carlo') return Promise.resolve({ data: mcTwoAgeResult })
    if (url === '/api/simulation/stress-tests') return Promise.resolve({ data: stTwoAgeResult })
    if (url === '/api/simulation/swr') return Promise.resolve({ data: swrTwoAgeResult })
    return Promise.resolve({ data: { scenarios: [] } })
  })
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

describe('Two-Age Monte Carlo', () => {
  it('toggling two-age mode replaces the retirement-age buttons with two age inputs, keeping SS visible', async () => {
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await click('Monte Carlo')
    await flush()
    await click('Use Two Independent Retirement Ages')
    await flush()
    expect(container.textContent).toContain("Alex's Retirement Age")
    expect(container.textContent).toContain("Sam's Retirement Age")
    // Independent review, 2026-09-08 (P1): the SS-claiming-timing
    // selector used to disappear entirely in two-age mode, with no way
    // to change it -- it must stay visible and editable.
    const buttons = [...container.querySelectorAll('button')].map(b => b.textContent.trim())
    expect(buttons).toContain('SS at 62')
    expect(buttons).toContain('SS at 67')
  })

  it('sends both ages, the current ss_timing, and any What-If overrides via POST', async () => {
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await click('Monte Carlo')
    await flush()
    await click('Use Two Independent Retirement Ages')
    await flush()
    await click('SS at 67')
    const inputs = container.querySelectorAll('input[type=number]')
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inputs[0], '61')
      inputs[0].dispatchEvent(new Event('input', { bubbles: true }))
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inputs[1], '63')
      inputs[1].dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click('Run Monte Carlo Simulation')
    await flush()
    // The always-mounted (but hidden until visited) What-If Builder
    // computes its own default assumptions on load, same as it already
    // does for single-age mode -- those are expected here too (that IS
    // "preserve What-If overrides"), so this asserts the two-age-
    // specific fields via objectContaining rather than exact equality.
    expect(axios.post).toHaveBeenCalledWith('/api/simulation/monte-carlo',
      expect.objectContaining({ ss_timing: 'delayed', jason_ret_age: 61, justin_ret_age: 63 }))
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
    // External audit review, 2026-09-09: the old "Ages used: {p1} {age}"
    // line was replaced by AssumptionsUsed's fuller summary (retirement
    // ages, effective SS claim ages, and any active What-If overrides,
    // so a result can be audited from the screen afterward) -- still
    // sourced from the RESPONSE's own echoed jason_ret_age/justin_ret_age,
    // not just the request, so a snapped/adjusted age is still shown
    // accurately rather than the raw request value.
    expect(container.textContent).toContain('Alex retires 61')
    expect(container.textContent).toContain('Sam retires 63')
    expect(container.textContent).toContain('$65,000')
    expect(container.textContent).toContain('65%')
    // Independent review, 2026-09-08 (P2): SecondEarnerNote used to
    // describe every result as "Single retirement-age model" even when
    // the result itself was two-age.
    expect(container.textContent).toContain('Two-age model')
    expect(container.textContent).not.toContain('Single retirement-age model')
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

  it('fetches SWR alongside Monte Carlo and renders the Safe Spending Power card', async () => {
    // Milestone 1 (CALCULATION_CONTRACT.md section 25/26): SWR now
    // supports two-age mode too, fetched alongside Monte Carlo so the
    // existing "Safe Spending Power" card (previously stuck on "Run
    // simulation to calculate" in two-age mode) actually renders.
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await click('Monte Carlo')
    await flush()
    await click('Use Two Independent Retirement Ages')
    await flush()
    await click('Run Monte Carlo Simulation')
    await flush()
    expect(axios.post).toHaveBeenCalledWith('/api/simulation/swr',
      expect.objectContaining({ jason_ret_age: 65, justin_ret_age: 65 }))
    expect(container.textContent).not.toContain('Run simulation to calculate')
    // External audit review, 2026-09-09: the card's headline used to
    // repeat data.success_rate (the exact number the "Probability of
    // Success" card already shows) -- now the safe annual draw dollar
    // amount itself, formatted with fmtK ($45K, not $45,000).
    expect(container.textContent).toContain('$45K')
  })
})

describe('Two-Age Stress Tests', () => {
  it('sends both ages and ss_timing via POST, and renders the base scenario plus the working-spouse note', async () => {
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await click('Historical Stress')
    await flush()
    await click('Use Two Independent Retirement Ages')
    await flush()
    await click('Run Stress Tests')
    await flush()
    expect(axios.post).toHaveBeenCalledWith('/api/simulation/stress-tests',
      expect.objectContaining({ ss_timing: 'early', jason_ret_age: 65, justin_ret_age: 65 }))
    expect(container.textContent).toContain('$65,000')
    expect(container.textContent).toContain('65%')
  })
})
