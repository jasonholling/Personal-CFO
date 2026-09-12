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
const setInput = async (el, value) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
})

const recommendationsResponse = {
  has_policy: true,
  recommendations: [
    {
      id: 1, category: 'taxable_rebalance', status: 'proposed',
      payload: {
        title: 'Sell Overweight Large Cap', action_text: 'Sell $12,345 of overweight large-cap stock.',
        current_value: 65, target_value: 50, value_unit: 'percent', proposed_change: 'sell $12,345',
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

  it('renders an account-and-fund rebalance checklist with its tax warning', async () => {
    axios.post.mockResolvedValueOnce({ data: { rebalance_actions: [{
      action: 'buy', amount: 10000, asset_class: 'us_bonds', holding_name: 'Bond Index',
      destination: { account_name: 'Workplace 401k', option_name: 'Bond Index', ticker: 'BND' },
      reason: 'Closes the bond gap.', tax_warning: { message: 'Review tax lots.' },
    }] } })
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    const button = [...container.querySelectorAll('button')].find(b => b.textContent === 'Generate rebalance checklist')
    await act(async () => { button.click(); await Promise.resolve() })
    expect(container.textContent).toContain('Workplace 401k')
    expect(container.textContent).toContain('Bond Index (BND)')
    expect(container.textContent).toContain('Review tax lots')
  })

  it('clears a stale contribution result when the amount changes', async () => {
    axios.post.mockResolvedValueOnce({ data: { actions: [{ amount: 1000, asset_class: 'us_bonds', reason: 'Gap' }] } })
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    const amount = container.querySelector('input[placeholder="Amount to invest"]')
    await setInput(amount, '1000')
    const button = [...container.querySelectorAll('button')].find(b => b.textContent === 'Get contribution recommendation')
    await act(async () => { button.click(); await Promise.resolve(); await Promise.resolve() })
    expect(container.textContent).toContain('$1,000')
    await setInput(amount, '2000')
    expect(container.textContent).not.toContain('$1,000')
  })

  it('sends a review date when a recommendation is deferred', async () => {
    axios.post.mockResolvedValue({ data: {} })
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    const date = container.querySelector('input[aria-label="Review date"]')
    await setInput(date, '2030-01-15')
    const button = [...container.querySelectorAll('button')].find(b => b.textContent === 'Defer until date')
    await act(async () => { button.click(); await Promise.resolve() })
    expect(axios.post).toHaveBeenCalledWith('/api/recommendations/1/decide', expect.objectContaining({
      status: 'deferred', review_date: '2030-01-15',
    }))
  })
})

describe('PortfolioCoach value_unit rendering (external review finding #11)', () => {
  const currencyCardResponse = {
    has_policy: true,
    recommendations: [{
      id: 2, category: 'concentration_or_liquidity_risk', status: 'proposed',
      payload: {
        title: 'Cash reserve is below your policy\'s minimum', action_text: 'Rebuild the cash reserve.',
        current_value: 50, target_value: 10000, value_unit: 'currency',
        proposed_change: 'Rebuild the cash reserve', expected_effect: 'Restores minimum liquidity.',
        confidence: 'high', assumptions: [], recommendation_key: 'k2',
      },
    }],
  }

  beforeEach(() => {
    vi.clearAllMocks()
    setPrivacyMode(false)
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    axios.get.mockImplementation(url => {
      if (url === '/api/recommendations') return Promise.resolve({ data: currencyCardResponse })
      if (url === '/api/portfolio/allocation') return Promise.resolve({ data: { has_holdings: false } })
      return Promise.resolve({ data: {} })
    })
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
  })

  it('renders a $50 currency value as $50, never as 50% (external review finding #11)', async () => {
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    expect(container.textContent).toContain('$50')
    expect(container.textContent).not.toContain('50%')
  })
})
