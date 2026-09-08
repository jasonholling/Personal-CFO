import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import StressTestWhatIf from './StressTestWhatIf'

// Independent review, 2026-09-07, fourth follow-up: the Survivor
// Scenario form initialized `deathAge` as `retAge + 10` (the same class
// of bug the backend default had before its own fix — ignoring the
// household's actual current age and which spouse was selected) and
// ALWAYS sent it explicitly to the API, so the backend's corrected
// default was never reachable through the normal UI flow. These tests
// verify the ACTUAL request payload, not just the displayed number.

import { setPrivacyMode } from '../utils/privacy'

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('../hooks/usePersonNames', () => ({
  usePersonNames: () => ({ person1Name: 'Alex', person2Name: 'Sam' }),
}))
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container, root
const projections = { scenarios: [] }
const survivorResult = { has_data: false }
const flush = async () => { await act(async () => { await Promise.resolve() }) }
const click = async text => {
  const button = [...container.querySelectorAll('button')].find(b => b.textContent.trim() === text)
  expect(button, text).toBeTruthy()
  await act(async () => { button.click() })
}

function mockPlanningInputs(jasonAge, justinAge) {
  return {
    jason_age: jasonAge, justin_age: justinAge,
    expected_return_pre_retirement: .07, expected_return_post_retirement: .06,
    inflation_rate: .02, retirement_income_today_dollars: 100000,
    healthcare_pre_medicare: 20000, bridge_income_55: 0,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  setPrivacyMode(false)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  axios.post.mockResolvedValue({ data: projections })
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

async function renderAtRetAge(retAge, jasonAge, justinAge) {
  localStorage.setItem('cfo_scenario_ret_age', String(retAge))
  axios.get.mockImplementation(url => Promise.resolve({
    data: url === '/api/planning-inputs' ? mockPlanningInputs(jasonAge, justinAge)
        : url === '/api/simulation/survivor-scenario' ? survivorResult
        : projections,
  }))
  await act(async () => root.render(<StressTestWhatIf />))
  await flush()
  await click('Survivor Scenario')
  await flush()
}

describe('Survivor Scenario default death age', () => {
  it('defaults Jason (older spouse, selected as deceased) to his own effective-start-age + 10', async () => {
    // Jason 65, Justin 55, requested retirement 55 (already past for
    // Jason) -> effective_start_age = max(55, 65) = 65 -> Jason's
    // default death age = 75.
    await renderAtRetAge(55, 65, 55)
    await click('Run Scenario')
    expect(axios.get).toHaveBeenCalledWith('/api/simulation/survivor-scenario', {
      params: expect.objectContaining({ ret_age: 55, deceased: 'jason', death_age: 75 }),
    })
  })

  it('defaults Justin (younger spouse) in HIS OWN age terms, not Jason\'s', async () => {
    // Same household -- selecting Justin as deceased must default to
    // Justin's own effective-start-age + 10 = (65-10) + 10 = 65, not
    // Jason's 75 and not the old retAge-based 65 (which happened to
    // coincide numerically here but for the wrong reason -- see the
    // unequal-ages case below where they diverge).
    await renderAtRetAge(55, 65, 55)
    await click('Sam')  // person2Name mock -> selects deceased='justin'
    await click('Run Scenario')
    expect(axios.get).toHaveBeenCalledWith('/api/simulation/survivor-scenario', {
      params: expect.objectContaining({ ret_age: 55, deceased: 'justin', death_age: 65 }),
    })
  })

  it('gets the age-gap conversion right for a bigger, uneven spousal age gap', async () => {
    // Jason 70, Justin 50 (20-year gap), retiring at 70 (current age,
    // not past). effective_start_age = 70. Justin's own effective start
    // = 70 - 20 = 50. Justin's default death age = 60.
    await renderAtRetAge(70, 70, 50)
    await click('Sam')
    await click('Run Scenario')
    expect(axios.get).toHaveBeenCalledWith('/api/simulation/survivor-scenario', {
      params: expect.objectContaining({ ret_age: 70, deceased: 'justin', death_age: 60 }),
    })
  })

  it('preserves a deliberately edited death age across a later deceased-spouse change', async () => {
    await renderAtRetAge(55, 65, 55)
    const ageInput = container.querySelectorAll('input[type=number]')[0]
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(ageInput, '99')
      ageInput.dispatchEvent(new Event('input', { bubbles: true }))
      ageInput.dispatchEvent(new Event('change', { bubbles: true }))
    })
    // Switching who dies first must NOT clobber the user's own typed value.
    await click('Sam')
    await click('Run Scenario')
    expect(axios.get).toHaveBeenCalledWith('/api/simulation/survivor-scenario', {
      params: expect.objectContaining({ deceased: 'justin', death_age: 99 }),
    })
  })
})

describe('Survivor Scenario second-earner gap-income disclosure (backend/docs/CALCULATION_CONTRACT.md sections 17-18, P2)', () => {
  // A backend-output closeout isn't the same as a user-visible one --
  // independent review, 2026-09-08: justin_gap_income/
  // second_earner_net_of_tax_factor were added to the API response but
  // nothing in the frontend read them. This asserts against the actual
  // RENDERED text, not just that the fields exist on the mocked response.
  //
  // `schedule` here deliberately mimics the backend's real [::2]
  // sampling (only alternating ages 62/64/66 shown, even though the real
  // gap spans ages 62-65) -- `justin_gap_income_years_remaining` is the
  // backend's own explicit count over the FULL unsampled schedule
  // (section 18 fix), independent of how many rows happen to be sampled
  // into the displayed array. A frontend that derived "years" by
  // filtering the sampled `schedule` itself would undercount this case
  // at "2 more years" instead of the true 4.
  const gapSurvivorResult = {
    has_data: true, deceased: 'jason', death_age: 61, survivor_end_age: 90,
    portfolio_at_death: 800000, life_insurance_payout: 0,
    starting_balance_after_payout: 800000, survivor_ss_annual: 0, pension_annual: 0,
    income_need_at_death: 75000, survivor_need_factor: 0.75, survives: true,
    depleted_age: null, additional_insurance_needed: 0,
    recommendation: 'Test recommendation text.',
    schedule: [
      { age: 62, starting_balance: 800000, draw: 10000, ending_balance: 795000, justin_gap_income: 65000 },
      { age: 64, starting_balance: 790000, draw: 10000, ending_balance: 785000, justin_gap_income: 65000 },
      { age: 66, starting_balance: 780000, draw: 75000, ending_balance: 710000, justin_gap_income: 0 },
    ],
    justin_gap_income_years_remaining: 4,
    second_earner_net_of_tax_factor: 0.65,
  }

  it('renders the actual gap-income dollar amount when Justin survives', async () => {
    localStorage.setItem('cfo_scenario_ret_age', '60')
    axios.get.mockImplementation(url => Promise.resolve({
      data: url === '/api/planning-inputs' ? mockPlanningInputs(60, 60)
          : url === '/api/simulation/survivor-scenario' ? gapSurvivorResult
          : projections,
    }))
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await click('Survivor Scenario')
    await flush()
    await click('Run Scenario')
    await flush()
    // Reads the FIRST schedule row's dollar amount, and the backend's
    // own explicit remaining-years count -- not a count derived from the
    // (sampled) schedule array itself.
    expect(container.textContent).toContain('$65,000')
    expect(container.textContent).toContain('4 more years')
    expect(container.textContent).toContain('65%')
  })

  it('masks the gap-income dollar amount in privacy mode but keeps the policy factor visible', async () => {
    // Backlog P2 (CALCULATION_CONTRACT.md section 18): SecondEarnerNote
    // used to format the amount directly, bypassing the app's existing
    // isPrivacyMode()/MASK_CURRENCY convention. The 65% factor is a
    // documented methodology constant, not household financial data, so
    // it stays visible even while the dollar amount is masked.
    setPrivacyMode(true)
    localStorage.setItem('cfo_scenario_ret_age', '60')
    axios.get.mockImplementation(url => Promise.resolve({
      data: url === '/api/planning-inputs' ? mockPlanningInputs(60, 60)
          : url === '/api/simulation/survivor-scenario' ? gapSurvivorResult
          : projections,
    }))
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await click('Survivor Scenario')
    await flush()
    await click('Run Scenario')
    await flush()
    expect(container.textContent).not.toContain('$65,000')
    expect(container.textContent).toContain('$•••,•••')
    expect(container.textContent).toContain('4 more years')
    expect(container.textContent).toContain('65%')
  })

  it('does not render the disclosure when Justin is the one deceased', async () => {
    localStorage.setItem('cfo_scenario_ret_age', '60')
    axios.get.mockImplementation(url => Promise.resolve({
      data: url === '/api/planning-inputs' ? mockPlanningInputs(60, 60)
          : url === '/api/simulation/survivor-scenario' ? gapSurvivorResult
          : projections,
    }))
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await click('Survivor Scenario')
    await flush()
    await click('Sam')  // selects deceased='justin'
    await click('Run Scenario')
    await flush()
    // Same mocked schedule (component doesn't re-fetch different data
    // per deceased selection in this mock), but the disclosure must not
    // render at all once Justin is the deceased spouse -- the gating is
    // on the selection itself, not on re-deriving the schedule.
    expect(container.textContent).not.toContain('continued income')
  })
})
