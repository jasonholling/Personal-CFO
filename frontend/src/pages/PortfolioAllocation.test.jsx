import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import PortfolioAllocation from './PortfolioAllocation'
import { setPrivacyMode } from '../utils/privacy'

// Milestone 4 (codex/portfolio-holdings-allocation) -- rendered-DOM
// coverage for the explicit setup states, grouped-holdings/reconciliation
// display, manual entry, CSV preview/validation, current-vs-target
// visualization, the two named workflows ("where should my next
// contribution go" / "how should I rebalance"), privacy mode, and
// stale-result clearing after an edit. Full calc-correctness is covered
// in test_holdings_engine.py; these verify the page wires the API
// responses into the DOM correctly.

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
// The current-vs-target BarChart (recharts' ResponsiveContainer) needs a
// ResizeObserver, absent in jsdom -- same polyfill precedent as
// Retirement.test.jsx/RothConversion.test.jsx/TwoAgeMonteCarloStress.test.jsx.
globalThis.ResizeObserver = globalThis.ResizeObserver || class {
  observe() {} unobserve() {} disconnect() {}
}

let container, root
const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve() }) }
const click = async text => {
  const el = [...container.querySelectorAll('button')].find(b => b.textContent.trim() === text)
  expect(el, `button "${text}" not found`).toBeTruthy()
  await act(async () => { el.click() })
}
const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
const typeInto = async (el, value) => {
  await act(async () => {
    nativeInputValueSetter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

const groupedResponse = {
  groups: [
    {
      account_id: 1, account_name: 'Brokerage', account_type: 'taxable', owner: 'jason',
      portfolio_account_type: 'brokerage', allocation_blocked: false,
      holdings: [
        { id: 10, account_id: 1, name: 'VTI', asset_class: 'us_stock', market_value: 60000, cost_basis: 50000, expense_ratio: 0.0003, shares: 300 },
      ],
      account_id_: null,
      account_balance: 65000, holdings_total: 60000, unreconciled_remainder: 5000, has_warning: true,
      warning: 'Holdings total $60,000 does not match the account balance $65,000 -- $5,000 is shown as an unclassified remainder rather than assumed.',
    },
  ],
}

const allocationResponse = {
  has_holdings: true,
  current_allocation: { total: 60000, by_class: { us_stock: 60000, international_stock: 0, bonds: 0, cash: 0, real_estate: 0, alternatives: 0, unclassified: 0 },
    pct_by_class: { us_stock: 100.0, international_stock: 0, bonds: 0, cash: 0, real_estate: 0, alternatives: 0, unclassified: 0 } },
  hsa_allocation: { total: 0, by_class: {}, pct_by_class: {} },
  child_specific_total: 0, liquidity_total: 0,
  blocked_holdings: [], review_required_accounts: [],
  concentration_flags: [], expense_ratio_flags: [], duplicate_exposure_flags: [], unclassified_flags: [],
  has_policy: true,
  comparison: {
    drift_band_pct: 5, any_outside_band: true,
    by_class: {
      us_stock: { current_pct: 100.0, target_pct: 50.0, deviation_pct: 50.0, deviation_dollars: 30000, within_drift_band: false },
      international_stock: { current_pct: 0, target_pct: 0, deviation_pct: 0, deviation_dollars: 0, within_drift_band: true },
      bonds: { current_pct: 0, target_pct: 30.0, deviation_pct: -30.0, deviation_dollars: -18000, within_drift_band: false },
      cash: { current_pct: 0, target_pct: 20.0, deviation_pct: -20.0, deviation_dollars: -12000, within_drift_band: false },
      real_estate: { current_pct: 0, target_pct: 0, deviation_pct: 0, deviation_dollars: 0, within_drift_band: true },
      alternatives: { current_pct: 0, target_pct: 0, deviation_pct: 0, deviation_dollars: 0, within_drift_band: true },
      unclassified: { current_pct: 0, target_pct: 0, deviation_pct: 0, deviation_dollars: 0, within_drift_band: true },
    },
  },
}

const policyResponse = {
  has_policy: true,
  policy: {
    id: 1, name: 'Household Policy', target_us_stock_pct: 50, target_international_stock_pct: 0,
    target_bonds_pct: 30, target_cash_pct: 20, target_real_estate_pct: 0, target_alternatives_pct: 0,
    drift_band_pct: 5, rebalance_cadence: 'annual', minimum_cash_reserve: 0,
    use_contributions_before_sales: true, account_constraints: [], notes: null,
  },
}

function mockDefaultGets() {
  axios.get.mockImplementation(url => {
    if (url === '/api/holdings/grouped') return Promise.resolve({ data: groupedResponse })
    if (url === '/api/portfolio/allocation') return Promise.resolve({ data: allocationResponse })
    if (url === '/api/investment-policy') return Promise.resolve({ data: policyResponse })
    return Promise.resolve({ data: {} })
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  setPrivacyMode(false)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  setPrivacyMode(false)
})

describe('Portfolio Allocation — setup states', () => {
  it('shows a missing-holdings setup state when nothing has been entered', async () => {
    axios.get.mockImplementation(url => {
      if (url === '/api/holdings/grouped') return Promise.resolve({ data: { groups: [] } })
      if (url === '/api/portfolio/allocation') return Promise.resolve({ data: { has_holdings: false } })
      if (url === '/api/investment-policy') return Promise.resolve({ data: { has_policy: false } })
      return Promise.resolve({ data: {} })
    })
    await act(async () => root.render(<PortfolioAllocation />))
    await flush()
    expect(container.textContent).toContain('No holdings entered yet')
  })

  it('shows a missing-policy state (policy editor) when holdings exist but no policy is saved', async () => {
    axios.get.mockImplementation(url => {
      if (url === '/api/holdings/grouped') return Promise.resolve({ data: groupedResponse })
      if (url === '/api/portfolio/allocation') return Promise.resolve({ data: { ...allocationResponse, has_policy: false, comparison: undefined } })
      if (url === '/api/investment-policy') return Promise.resolve({ data: { has_policy: false } })
      return Promise.resolve({ data: {} })
    })
    await act(async () => root.render(<PortfolioAllocation />))
    await flush()
    expect(container.textContent).toContain('Save policy')
  })
})

describe('Portfolio Allocation — grouped holdings display', () => {
  it('shows account type, owner, balance, holdings total, and the unreconciled remainder warning', async () => {
    mockDefaultGets()
    await act(async () => root.render(<PortfolioAllocation />))
    await flush()
    expect(container.textContent).toContain('Brokerage')
    expect(container.textContent).toContain('jason')
    expect(container.textContent).toContain('VTI')
    expect(container.textContent).toContain('$65,000')
    expect(container.textContent).toContain('$60,000')
    expect(container.textContent).toContain('unclassified remainder')
  })

  it('account type filter narrows the grouped display', async () => {
    mockDefaultGets()
    await act(async () => root.render(<PortfolioAllocation />))
    await flush()
    const select = container.querySelector('select')
    const nativeSelectValueSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set
    await act(async () => {
      nativeSelectValueSetter.call(select, 'checking')
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await flush()
    // "Brokerage" itself still legitimately appears as a filter dropdown
    // option label -- assert on something only present inside an actual
    // account card (the owner line) instead of the ambiguous substring.
    expect(container.textContent).not.toContain('Brokerage · jason')
  })
})

describe('Portfolio Allocation — manual holding entry', () => {
  it('opens the add-holding form and posts the entered values', async () => {
    mockDefaultGets()
    axios.post.mockResolvedValue({ data: { id: 99 } })
    await act(async () => root.render(<PortfolioAllocation />))
    await flush()
    await click('+ Add holding')
    const nameInput = [...container.querySelectorAll('input')].find(i => i.placeholder === 'e.g. VTI')
    await typeInto(nameInput, 'BND')
    const valueInputs = [...container.querySelectorAll('input[type="number"]')]
    await typeInto(valueInputs[0], '10000')
    await click('Save holding')
    await flush()
    expect(axios.post).toHaveBeenCalledWith('/api/holdings', expect.objectContaining({ name: 'BND', market_value: 10000 }))
  })
})

describe('Portfolio Allocation — CSV import preview/validation', () => {
  it('shows the preview with per-row validity before anything is committed', async () => {
    mockDefaultGets()
    axios.post.mockImplementation((url) => {
      if (url === '/api/holdings/import/preview') {
        return Promise.resolve({ data: {
          rows: [
            { row: 2, account_id: 1, name: 'VTI', market_value: 50000, asset_class: 'us_stock', valid: true, errors: [] },
            { row: 3, account_id: 999, name: 'Bad', market_value: 1000, asset_class: 'us_stock', valid: false, errors: ['account_id 999 does not match any existing account'] },
          ],
          valid_count: 1, invalid_count: 1, errors: [{ row: 3, message: 'account_id 999 does not match any existing account' }],
        } })
      }
      return Promise.resolve({ data: {} })
    })
    await act(async () => root.render(<PortfolioAllocation />))
    await flush()
    const fileInput = container.querySelector('input[type="file"]')
    const file = new File(['account_id,name,market_value,asset_class\n1,VTI,50000,us_stock\n'], 'holdings.csv', { type: 'text/csv' })
    await act(async () => {
      Object.defineProperty(fileInput, 'files', { value: [file] })
      fileInput.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await flush()
    expect(container.textContent).toContain('1 valid row')
    expect(container.textContent).toContain('1 invalid')
    expect(container.textContent).toContain('does not match any existing account')
  })

  it('commit only sends valid rows', async () => {
    mockDefaultGets()
    axios.post.mockImplementation((url) => {
      if (url === '/api/holdings/import/preview') {
        return Promise.resolve({ data: {
          rows: [
            { row: 2, account_id: 1, name: 'VTI', market_value: 50000, asset_class: 'us_stock', valid: true, errors: [] },
            { row: 3, account_id: 999, name: 'Bad', market_value: 1000, asset_class: 'us_stock', valid: false, errors: ['bad'] },
          ],
          valid_count: 1, invalid_count: 1, errors: [{ row: 3, message: 'bad' }],
        } })
      }
      if (url === '/api/holdings/import/commit') return Promise.resolve({ data: { created: 1, skipped: [] } })
      return Promise.resolve({ data: {} })
    })
    await act(async () => root.render(<PortfolioAllocation />))
    await flush()
    const fileInput = container.querySelector('input[type="file"]')
    const file = new File(['x'], 'holdings.csv', { type: 'text/csv' })
    await act(async () => {
      Object.defineProperty(fileInput, 'files', { value: [file] })
      fileInput.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await flush()
    await click('Import 1 valid row')
    await flush()
    const commitCall = axios.post.mock.calls.find(c => c[0] === '/api/holdings/import/commit')
    expect(commitCall[1]).toHaveLength(1)
    expect(commitCall[1][0].name).toBe('VTI')
  })
})

describe('Portfolio Allocation — current vs target visualization', () => {
  it('shows current and target percentages plus drift-band status per asset class', async () => {
    mockDefaultGets()
    await act(async () => root.render(<PortfolioAllocation />))
    await flush()
    expect(container.textContent).toContain('Current vs. target allocation')
    expect(container.textContent).toContain('outside band')
  })
})

describe('Portfolio Allocation — contribution/rebalance workflows', () => {
  it('renders the contribution destination recommendation', async () => {
    mockDefaultGets()
    axios.post.mockImplementation(url => {
      if (url === '/api/portfolio/contribution-destination') {
        return Promise.resolve({ data: { actions: [{ asset_class: 'bonds', amount: 5000, reason: 'Underweight by $18,000 against target -- directing new contributions here closes the gap without selling anything.' }] } })
      }
      return Promise.resolve({ data: {} })
    })
    await act(async () => root.render(<PortfolioAllocation />))
    await flush()
    const amountInput = [...container.querySelectorAll('input[type="number"]')].find(i => i.parentElement?.textContent?.includes('Pending contribution'))
    await typeInto(amountInput, '5000')
    await click('Where should my next contribution go?')
    await flush()
    expect(container.textContent).toContain('Contribution destination')
    expect(container.textContent).toContain('Bonds')
    expect(container.textContent).toContain('closes the gap without selling anything')
  })

  it('renders the rebalance trade checklist with tax warnings', async () => {
    mockDefaultGets()
    axios.post.mockImplementation(url => {
      if (url === '/api/portfolio/rebalance') {
        return Promise.resolve({ data: {
          contribution_actions: [],
          rebalance_actions: [{
            account_id: 1, holding_id: 10, holding_name: 'VTI', action: 'sell', asset_class: 'us_stock',
            amount: 18000, pct_of_holding: 30, reason: 'US Stock is overweight by $30,000 against target.',
            is_taxable_sale: true, tax_warning: { has_cost_basis: true, estimated_gain: 3000, message: 'Estimated taxable gain on this sale: $3,000.' },
            confidence_note: 'Taxable brokerage account -- see tax_warning for the estimated gain.',
          }],
          projected_allocation: { total: 60000, by_class: { us_stock: 42000, international_stock: 0, bonds: 18000, cash: 0, real_estate: 0, alternatives: 0, unclassified: 0 },
            pct_by_class: { us_stock: 70.0, international_stock: 0, bonds: 30.0, cash: 0, real_estate: 0, alternatives: 0, unclassified: 0 } },
        } })
      }
      return Promise.resolve({ data: {} })
    })
    await act(async () => root.render(<PortfolioAllocation />))
    await flush()
    await click('How should I rebalance?')
    await flush()
    expect(container.textContent).toContain('Rebalance / trade checklist')
    expect(container.textContent).toContain('VTI')
    expect(container.textContent).toContain('Taxable')
    expect(container.textContent).toContain('$3,000')
    expect(container.textContent).toContain('Projected allocation after these actions')
  })
})

describe('Portfolio Allocation — privacy mode', () => {
  it('masks dollar figures when privacy mode is on', async () => {
    mockDefaultGets()
    setPrivacyMode(true)
    await act(async () => root.render(<PortfolioAllocation />))
    await flush()
    expect(container.textContent).toContain('$•••,•••')
    expect(container.textContent).not.toContain('$65,000')
  })
})

describe('Portfolio Allocation — stale-result clearing', () => {
  it('clears a computed rebalance recommendation after a holding is edited', async () => {
    mockDefaultGets()
    axios.post.mockImplementation(url => {
      if (url === '/api/portfolio/rebalance') {
        return Promise.resolve({ data: {
          contribution_actions: [], rebalance_actions: [],
          projected_allocation: { total: 60000, by_class: {}, pct_by_class: {} },
        } })
      }
      if (url === '/api/holdings') return Promise.resolve({ data: { id: 99 } })
      return Promise.resolve({ data: {} })
    })
    axios.put.mockResolvedValue({ data: {} })
    await act(async () => root.render(<PortfolioAllocation />))
    await flush()
    await click('How should I rebalance?')
    await flush()
    expect(container.textContent).toContain('No trades needed')

    // Editing the holding should clear the stale rebalance result.
    await click('Edit')
    const valueInputs = [...container.querySelectorAll('input[type="number"]')]
    const marketValueInput = valueInputs.find(i => i.parentElement?.textContent?.includes('Market value'))
    await typeInto(marketValueInput, '70000')
    await click('Save holding')
    await flush()
    expect(container.textContent).not.toContain('No trades needed')
  })
})
