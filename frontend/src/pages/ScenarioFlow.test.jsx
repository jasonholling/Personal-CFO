import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import StressTestWhatIf from './StressTestWhatIf'
import { MonteCarloSection, StressTestSection } from './Simulation'

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('../hooks/usePersonNames', () => ({
  usePersonNames: () => ({ person1Name: 'Alex', person2Name: 'Sam' }),
}))
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container, root
const settings = {
  expected_return_pre_retirement: .07, expected_return_post_retirement: .06,
  inflation_rate: .02, retirement_income_today_dollars: 100000,
  healthcare_pre_medicare: 20000, bridge_income_55: 0,
}
const projections = { scenarios: [] }
const flush = async () => { await act(async () => { await Promise.resolve() }) }
const click = async text => {
  const button = [...container.querySelectorAll('button')].find(b => b.textContent.trim() === text)
  expect(button, text).toBeTruthy()
  await act(async () => { button.click() })
}
const setSlider = async (index, value) => {
  const slider = container.querySelectorAll('input[type=range]')[index]
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(slider, String(value))
    slider.dispatchEvent(new Event('input', { bubbles: true }))
    slider.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  axios.get.mockImplementation(url => Promise.resolve({
    data: url === '/api/planning-inputs' ? settings : projections,
  }))
  axios.post.mockResolvedValue({ data: projections })
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

describe('What-If scenario continuity', () => {
  it.each([true, false])('initializes after either API response order (settings first: %s)', async settingsFirst => {
    let resolveSettings, resolveProjection
    axios.get.mockImplementation(url => new Promise(resolve => {
      if (url === '/api/planning-inputs') resolveSettings = resolve
      else resolveProjection = resolve
    }))
    await act(async () => root.render(<StressTestWhatIf />))
    await act(async () => {
      if (settingsFirst) resolveSettings({ data: settings })
      else resolveProjection({ data: projections })
    })
    expect(axios.post).not.toHaveBeenCalled()
    await act(async () => {
      if (settingsFirst) resolveProjection({ data: projections })
      else resolveSettings({ data: settings })
    })
    expect(axios.post).toHaveBeenCalledWith('/api/projections/whatif',
      expect.objectContaining({ income_target: 100000, bridge_income: 0 }))
  })

  it('keeps edited sliders when returning from Monte Carlo', async () => {
    await act(async () => root.render(<StressTestWhatIf />))
    await flush()
    await setSlider(0, 150000)
    expect(axios.post).toHaveBeenLastCalledWith('/api/projections/whatif',
      expect.objectContaining({ income_target: 150000 }))
    await click('Monte Carlo')
    await click('What-If Builder')
    expect(container.querySelector('input[type=range]').value).toBe('150000')
    expect(axios.get.mock.calls.filter(([url]) => url === '/api/planning-inputs')).toHaveLength(1)
  })
})

describe('companion requests', () => {
  it.each([
    [MonteCarloSection, 'Run Monte Carlo Simulation',
      ['/api/simulation/monte-carlo', '/api/simulation/swr', '/api/retirement/income-sources']],
    [StressTestSection, 'Run Stress Tests',
      ['/api/simulation/stress-tests', '/api/simulation/roth-conversion', '/api/simulation/contribution-sensitivity']],
  ])('uses identical inputs for every result', async (Component, button, paths) => {
    axios.post.mockImplementation(() => new Promise(() => {}))
    const overrides = { income_target: 150000, salary_growth_pct: .05, post_return: .04 }
    await act(async () => root.render(<Component retAge={62} ssTiming="delayed" overrides={overrides} />))
    await click(button)
    for (const path of paths) {
      expect(axios.post).toHaveBeenCalledWith(path, { ...overrides, ret_age: 62, ss_timing: 'delayed' })
    }
    expect(axios.get).not.toHaveBeenCalled()
  })
})
