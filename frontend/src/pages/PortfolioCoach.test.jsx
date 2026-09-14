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
        affected_accounts: [1],
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
    if (url === '/api/accounts') return Promise.resolve({ data: [{ id: 1, name: 'Retirement IRA' }] })
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
    expect(container.textContent).toContain('Retirement IRA')
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
    expect(container.textContent).toContain('Allocation chart is hidden while privacy mode is on.')
  })

  it('renders a visual current-versus-target allocation comparison', async () => {
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    expect(container.querySelector('[aria-label="Current versus target allocation chart"]')).not.toBeNull()
    expect(container.textContent).toContain('Current')
    expect(container.textContent).toContain('Target')
    expect(container.textContent).toContain('65%')
    expect(container.textContent).toContain('→ 50%')
    expect(container.textContent).toContain('+15%')
    expect(container.querySelectorAll('.allocation-donut svg').length).toBe(2)
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

  it('ignores an in-flight contribution response after the amount changes', async () => {
    let finish
    axios.post.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    const amount = container.querySelector('input[placeholder="Amount to invest"]')
    await setInput(amount, '1000')
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Get contribution recommendation').click())
    await setInput(amount, '2000')
    await act(async () => finish({ data: { actions: [{ amount: 1000, asset_class: 'us_bonds', reason: 'Outdated advice' }] } }))
    expect(container.textContent).not.toContain('Outdated advice')
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

  it('shows the annual portfolio review summary and expands its detail sections (item 3)', async () => {
    const annualReviewResponse = {
      as_of: '2026-09-13', has_policy: true,
      open_recommendations: { count: 1, items: [] },
      deferred_reviews_due: { count: 1, items: [{ id: 9, title: 'Establish your investment policy', review_date: '2026-08-01' }] },
      stale_holding_values: [{ id: 10, title: 'Refresh the value of Old Fund' }],
      unreconciled_accounts: [],
      allocation_drift: [{ id: 11, title: 'Us Bonds is overweight vs. target' }],
      concentrated_positions: [],
      taxable_loss_candidates: [],
    }
    axios.get.mockImplementation(url => {
      if (url === '/api/recommendations') return Promise.resolve({ data: recommendationsResponse })
      if (url === '/api/portfolio/allocation') return Promise.resolve({ data: allocationResponse })
      if (url === '/api/accounts') return Promise.resolve({ data: [{ id: 1, name: 'Retirement IRA' }] })
      if (url === '/api/portfolio/annual-review') return Promise.resolve({ data: annualReviewResponse })
      return Promise.resolve({ data: {} })
    })
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    expect(container.textContent).toContain('Annual portfolio review')
    expect(container.textContent).toContain('1 deferred reviews due')
    expect(container.textContent).not.toContain('Establish your investment policy')

    const toggle = [...container.querySelectorAll('button')].find(b => b.textContent === 'Show details')
    await act(async () => { toggle.click(); await Promise.resolve() })
    expect(container.textContent).toContain('Establish your investment policy')
    expect(container.textContent).toContain('Review by 2026-08-01')
    expect(container.textContent).toContain('Refresh the value of Old Fund')
    expect(container.textContent).toContain('Us Bonds is overweight vs. target')
  })

  it('shows an asset-location tax warning verbatim instead of claiming cost basis is missing', async () => {
    const assetLocation = structuredClone(recommendationsResponse)
    assetLocation.recommendations[0].payload.tax_impact = 'Selling in taxable may realize a gain or loss; Coach does not estimate it here without a complete replacement plan.'
    axios.get.mockImplementation(url => {
      if (url === '/api/recommendations') return Promise.resolve({ data: assetLocation })
      if (url === '/api/portfolio/allocation') return Promise.resolve({ data: allocationResponse })
      if (url === '/api/accounts') return Promise.resolve({ data: [{ id: 1, name: 'Retirement IRA' }] })
      return Promise.resolve({ data: {} })
    })
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    expect(container.textContent).toContain('Selling in taxable may realize a gain or loss')
    expect(container.textContent).not.toContain('cost basis is missing')
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

describe('PortfolioCoach lot-aware tax-loss review card (item 1)', () => {
  const lotReviewResponse = {
    has_policy: true,
    recommendations: [{
      id: 3, category: 'minor_optimization', status: 'proposed',
      payload: {
        title: 'Review lot-level loss on VTI (2020-01-01)',
        action_text: 'Lot acquired 2020-01-01 (100 shares, long-term) is worth an estimated $8,000 against a cost basis of $10,000 -- an unrealized loss of $2,000 (-20.0%) as of 2024-08-01. Review wash-sale exposure and your intended replacement investment before acting.',
        current_value: 8000, target_value: 10000, value_unit: 'currency',
        proposed_change: 'Review, do not automatically sell',
        expected_effect: 'Potential tax-loss review opportunity at the individual tax-lot level.',
        tax_impact: { has_cost_basis: true, estimated_gain: -2000 },
        assumptions: [
          'This is a review candidate, not an instruction to sell or harvest this lot.',
          'Possible wash-sale conflict -- review before acting: this app found a recorded purchase of VTI within 30 days of the potential sale date in Other Brokerage.',
        ],
        confidence: 'low', recommendation_key: 'tax_lot_loss_review:1:1:None:lot1',
        affected_accounts: [1],
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
      if (url === '/api/recommendations') return Promise.resolve({ data: lotReviewResponse })
      if (url === '/api/portfolio/allocation') return Promise.resolve({ data: { has_holdings: false } })
      if (url === '/api/accounts') return Promise.resolve({ data: [{ id: 1, name: 'Brokerage' }] })
      return Promise.resolve({ data: {} })
    })
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
  })

  it('shows the specific lot, its loss, and never claims a gain', async () => {
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    expect(container.textContent).toContain('2020-01-01')
    expect(container.textContent).toContain('long-term')
    expect(container.textContent).toContain('$8,000')
    expect(container.textContent).toContain('$10,000')
    expect(container.textContent).toContain('Estimated taxable loss')
    expect(container.textContent).not.toContain('Estimated taxable gain')
  })

  it('surfaces the wash-sale caution and never claims a sale is wash-sale-safe', async () => {
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    expect(container.textContent).toContain('Possible wash-sale conflict')
    expect(container.textContent.toLowerCase()).not.toContain('wash-sale safe')
  })

  it('never shows a sell instruction for a loss-review card', async () => {
    await act(async () => root.render(<PortfolioCoach />))
    await flush()
    expect(container.textContent).toContain('Review, do not automatically sell')
  })
})
