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
