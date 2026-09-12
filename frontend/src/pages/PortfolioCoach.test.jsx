import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import PortfolioCoach from './PortfolioCoach'
import { setPrivacyMode } from '../utils/privacy'

// Reference test #22: privacy mode masks balances, shares, basis,
// gains, taxes, and action amounts. Rendered-DOM coverage against a
// mocked /api/recommendations + /api/portfolio/allocation response.

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container, root
const flush = async () => { await act(async () => { await Promise.resolve() }) }

const recommendationsResponse = {
  has_policy: true,
  recommendations: [
    {
      id: 1, category: 'taxable_rebalance', status: 'proposed',
      payload: {
        title: 'Sell Overweight Large Cap', action_text: 'Sell $12,345 of overweight large-cap stock.',
        current_value: 65, target_value: 50, proposed_change: 'sell $12,345',
        expected_effect: 'Reduces drift.', confidence: 'medium',
        tax_impact: { has_cost_basis: true, estimated_gain: 9876 },
        assumptions: [], recommendation_key: 'k1',
      },
    },
  ],
}

const allocationResponse = {
  has_holdings: true, has_policy: true,
  current_allocation: { total: 543210, by_class: {} },
  unclassified_flags: [], concentration_flags: [],
  comparison: {
    by_class: {
      us_large_cap: { current_pct: 65, target_pct: 50, deviation_pct: 15, within_drift_band: false },
    },
  },
}

beforeEach(() => {
  vi.clearAllMocks()
  setPrivacyMode(false)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  axios.get.mockImplementation(url => {
    if (url === '/api/recommendations') return Promise.resolve({ data: recommendationsResponse })
    if (url === '/api/portfolio/allocation') return Promise.resolve({ data: allocationResponse })
    return Promise.resolve({ data: {} })
  })
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

describe('PortfolioCoach', () => {
  it('renders real dollar figures when privacy mode is off', async () => {
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    expect(container.textContent).toContain('$543,210')
    expect(container.textContent).toContain('$9,876')
  })

  it('masks household total, tax-impact gain, and card figures in privacy mode', async () => {
    setPrivacyMode(true)
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    expect(container.textContent).not.toContain('$543,210')
    expect(container.textContent).not.toContain('$9,876')
    expect(container.textContent).not.toContain('65%')
    expect(container.textContent).toContain('$•••,•••')
  })

  it('masks the current-vs-target allocation table percentages', async () => {
    setPrivacyMode(true)
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    expect(container.textContent).not.toContain('65%')
    expect(container.textContent).not.toContain('50%')
    expect(container.textContent).toContain('••%')
  })
})
