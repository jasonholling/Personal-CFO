import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import RothConversion from './RothConversion'
import { setPrivacyMode } from '../utils/privacy'
import { setSsTiming as setGlobalSsTiming } from '../utils/scenario'

// Two-age Roth Conversion UI (backend/docs/CALCULATION_CONTRACT.md
// section 30/32, Milestone 2 of 4) -- rendered-DOM coverage for the
// toggle, the two age inputs replacing the single-age selector, the
// two-age summary line, and the working-spouse income disclosure.

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('../hooks/usePersonNames', () => ({
  usePersonNames: () => ({ person1Name: 'Alex', person2Name: 'Sam' }),
}))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
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

const singleAgeResult = {
  schedule: [],
  total_conversions: 100000, total_tax_cost: 22000, total_tax_avoided: 24000,
  net_lifetime_benefit: 2000, conversion_years: 2, rmd_start_age: 73,
  estimated_rmd_without_conversions: 11321, estimated_rmd_with_conversions: 0,
  pretax_at_rmd_age_no_conversion: 300000, pretax_at_rmd_age_with_conversion: 0,
  roth_at_rmd_age_with_conversion: 100000,
}

const twoAgeResult = {
  mode: 'two_age', jason_ret_age: 61, justin_ret_age: 63,
  phase2_start_age: 61, phase3_start_age: 63, later_retiree: 'justin',
  ss_timing: 'early', schedule: [],
  total_conversions: 150000, total_tax_cost: 33000, total_tax_avoided: 36000,
  net_lifetime_benefit: 3000, conversion_years: 10, rmd_start_age: 73,
  estimated_rmd_without_conversions: 15000, estimated_rmd_with_conversions: 0,
  pretax_at_rmd_age_no_conversion: 400000, pretax_at_rmd_age_with_conversion: 0,
  roth_at_rmd_age_with_conversion: 150000,
  still_working_spouse_income_first_year: 65000, second_earner_net_of_tax_factor: 0.65,
  account_ownership_limitation: 'Portfolio buckets are a single aggregated household total.',
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  setPrivacyMode(false)
  setGlobalSsTiming('early')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  axios.get.mockResolvedValue({ data: singleAgeResult })
  axios.post.mockResolvedValue({ data: twoAgeResult })
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

describe('Two-Age Roth Conversion', () => {
  it('toggling two-age mode replaces the retirement-age buttons with two age inputs, keeping SS visible', async () => {
    await act(async () => root.render(<RothConversion />))
    await flush()
    await click('Use Two Independent Retirement Ages')
    await flush()
    expect(container.textContent).toContain("Alex's Retirement Age")
    expect(container.textContent).toContain("Sam's Retirement Age")
    const buttons = [...container.querySelectorAll('button')].map(b => b.textContent.trim())
    expect(buttons).toContain('SS at 62')
    expect(buttons).toContain('SS at 67')
  })

  it('POSTs both ages instead of GETting the single-age endpoint once two-age mode is on', async () => {
    await act(async () => root.render(<RothConversion />))
    await flush()
    await click('Use Two Independent Retirement Ages')
    await flush()
    expect(axios.post).toHaveBeenCalledWith('/api/simulation/roth-conversion',
      expect.objectContaining({ jason_ret_age: 65, justin_ret_age: 65, ss_timing: 'early' }))
  })

  it('renders the two-age summary line and the working-spouse income disclosure', async () => {
    await act(async () => root.render(<RothConversion />))
    await flush()
    await click('Use Two Independent Retirement Ages')
    await flush()
    expect(container.textContent).toContain('Alex 61')
    expect(container.textContent).toContain('Sam 63')
    expect(container.textContent).toContain('$65,000')
    expect(container.textContent).toContain('65%')
    expect(container.textContent).toContain('Two-age model')
  })
})
