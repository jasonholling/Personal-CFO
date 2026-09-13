import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import PortfolioSetup from './PortfolioSetup'

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container, root
const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve() }) }
const setInput = async (el, value) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
})

beforeEach(() => {
  vi.clearAllMocks()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  axios.get.mockImplementation((url) => {
    if (url === '/api/accounts') return Promise.resolve({ data: [{ id: 1, name: '401k' }] })
    if (url === '/api/holdings/grouped') return Promise.resolve({ data: { groups: [] } })
    if (url === '/api/holdings') return Promise.resolve({ data: [] })
    if (url === '/api/account-investment-options') return Promise.resolve({ data: [] })
    if (url === '/api/investment-policy') return Promise.resolve({ data: { has_policy: false } })
    if (url === '/api/securities/search') return Promise.resolve({ data: { candidates: [{
      provider_identifier: 'MOCK:BND', ticker: 'BND', security_name: 'Bond Index',
      exchange: 'NASDAQ', currency: 'USD', security_type: 'etf', status: 'active',
      asset_class: 'us_bonds', asset_class_source: 'provider',
    }] } })
    return Promise.resolve({ data: {} })
  })
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

describe('PortfolioSetup workflows', () => {
  it('hides excluded accounts by default and reveals their preserved holdings without deleting them', async () => {
    axios.get.mockImplementation(url => Promise.resolve({ data:
      url === '/api/accounts' ? [{ id: 1, name: 'IRA' }, { id: 2, name: 'Reserve' }] :
      url === '/api/investment-policy' ? { has_policy: true, policy: { excluded_accounts: [2] } } :
      url === '/api/holdings/grouped' ? { groups: [{ account_id: 2, account_name: 'Reserve', holdings: [{ id: 7, security_name: 'Reserve fund', asset_class: 'cash', market_value: 1000 }] }] } : []
    }))
    await act(async () => root.render(<PortfolioSetup />))
    await flush()
    expect(container.textContent).not.toContain('Reserve fund')
    const toggle = [...container.querySelectorAll('label')].find(l => l.textContent.includes('Show excluded accounts')).querySelector('input')
    await act(async () => toggle.click())
    expect(container.textContent).toContain('Reserve fund')
    const excluded = container.querySelector('details.setup-excluded')
    expect(excluded.open).toBe(false)
    expect(excluded.textContent).toContain('Excluded from Coach')
    expect(axios.delete).not.toHaveBeenCalled()
    await act(async () => [...excluded.querySelectorAll('button')].find(b => b.textContent === 'Manage inclusion in policy').click())
    await flush()
    expect(container.querySelector('input[aria-label="Exclude Reserve from investing advice"]').checked).toBe(true)
  })

  it('reveals excluded fund menus for reference but disables comparison and clears them when hidden', async () => {
    axios.get.mockImplementation(url => Promise.resolve({ data:
      url === '/api/accounts' ? [{ id: 2, name: 'Reserve' }] :
      url === '/api/investment-policy' ? { has_policy: true, policy: { excluded_accounts: [2] } } :
      url === '/api/holdings/grouped' ? { groups: [] } :
      url === '/api/account-investment-options' ? [{ id: 1, option_name: 'Reserve fund', asset_class: 'cash' }] : []
    }))
    await act(async () => root.render(<PortfolioSetup />))
    await flush()
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Account Investment Options').click())
    await flush()
    expect(container.querySelector('option[value="2"]')).toBeNull()
    const toggle = [...container.querySelectorAll('label')].find(l => l.textContent.includes('Show excluded accounts')).querySelector('input')
    await act(async () => toggle.click())
    await act(async () => { const select = container.querySelector('select'); select.value = '2'; select.dispatchEvent(new Event('change', { bubbles: true })) })
    await flush()
    expect(container.textContent).toContain('not used for recommendations')
    expect([...container.querySelectorAll('button')].find(b => b.textContent === 'Compare eligible options').disabled).toBe(true)
    await act(async () => toggle.click())
    expect(container.textContent).not.toContain('Reserve fund')
    expect(axios.delete).not.toHaveBeenCalled()
  })

  it('saves and reloads holding protections and account constraints with allocation intact', async () => {
    let saved = { target_us_large_cap_pct: 98, target_cash_pct: 2,
      employer_stock_exceptions: [{ ticker: 'TEST', reason: 'Keep existing exception' }] }
    axios.get.mockImplementation(url => Promise.resolve({ data:
      url === '/api/accounts' ? [{ id: 1, name: 'IRA' }] :
      url === '/api/holdings/grouped' ? { groups: [] } :
      url === '/api/holdings' ? [{ id: 7, account_id: 1, security_name: 'Index', ticker: 'TEST' }] :
      url === '/api/investment-policy' ? { has_policy: true, policy: saved } : []
    }))
    axios.post.mockImplementation((url, body) => { saved = structuredClone(body); return Promise.resolve({ data: {} }) })
    const button = text => [...container.querySelectorAll('button')].find(b => b.textContent === text)
    const select = async (label, value) => act(async () => {
      const el = container.querySelector(`select[aria-label="${label}"]`)
      el.value = value
      el.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await act(async () => root.render(<PortfolioSetup />))
    await act(async () => button('Investment Policy').click())
    await flush()
    await act(async () => container.querySelector('section[aria-label="Holding exclusions"] input').click())
    await select('Holding to protect', '7')
    await select('Exception type', 'legacy_holding_exceptions')
    await act(async () => button('Protect holding').click())
    await select('Constraint mode for IRA', 'deny')
    await act(async () => container.querySelector('input[aria-label="Block us bonds in IRA"]').click())
    await act(async () => button('Save policy').click())
    await flush()
    expect(saved).toMatchObject({ target_us_large_cap_pct: 98, target_cash_pct: 2,
      excluded_holdings: [7], legacy_holding_exceptions: [{ holding_id: 7 }],
      employer_stock_exceptions: [{ ticker: 'TEST', reason: 'Keep existing exception' }],
      account_constraints: [{ account_id: 1, excluded_asset_classes: ['us_bonds'] }] })
    await act(async () => root.unmount())
    root = createRoot(container)
    await act(async () => root.render(<PortfolioSetup />))
    await act(async () => button('Investment Policy').click())
    await flush()
    expect(container.querySelector('section[aria-label="Holding exclusions"] input').checked).toBe(true)
    expect(container.querySelector('input[aria-label="Block us bonds in IRA"]').checked).toBe(true)
    expect(container.textContent).toContain('Legacy holding: Index')
    expect(container.textContent).toContain('Keep existing exception')
  })

  it('searches for a ticker and confirms the selected candidate', async () => {
    axios.post.mockResolvedValue({ data: { security_id: 1 } })
    await act(async () => root.render(<PortfolioSetup />))
    await flush()
    const ticker = container.querySelector('input[placeholder="Ticker (optional)"]')
    await setInput(ticker, 'BND')
    const lookup = [...container.querySelectorAll('button')].find(b => b.textContent === 'Look up ticker')
    await act(async () => { lookup.click(); await Promise.resolve(); await Promise.resolve() })
    const use = [...container.querySelectorAll('button')].find(b => b.textContent.includes('Use BND'))
    await act(async () => { use.click(); await Promise.resolve() })
    expect(axios.post).toHaveBeenCalledWith('/api/securities/confirm', expect.objectContaining({ ticker: 'BND' }))
    expect(container.querySelector('input[placeholder="Security name"]').value).toBe('Bond Index')
  })

  it('previews and commits only valid CSV rows', async () => {
    axios.post.mockImplementation(url => {
      if (url === '/api/holdings/import/preview') return Promise.resolve({ data: {
        rows: [{ account_id: 1, security_name: 'Fund', market_value: 100, asset_class: 'us_bonds', valid: true }],
        errors: [], valid_count: 1, invalid_count: 0,
      } })
      if (url === '/api/holdings/import/commit') return Promise.resolve({ data: { created: 1 } })
      return Promise.resolve({ data: {} })
    })
    await act(async () => root.render(<PortfolioSetup />))
    await flush()
    const input = container.querySelector('input[aria-label="Holdings CSV"]')
    const file = new File(['account_id,security_name,market_value,asset_class\n1,Fund,100,us_bonds'], 'holdings.csv', { type: 'text/csv' })
    await act(async () => { Object.defineProperty(input, 'files', { value: [file] }); input.dispatchEvent(new Event('change', { bubbles: true })); await Promise.resolve() })
    const commit = [...container.querySelectorAll('button')].find(b => b.textContent.includes('Import 1 valid row'))
    await act(async () => { commit.click(); await Promise.resolve() })
    expect(axios.post).toHaveBeenCalledWith('/api/holdings/import/commit', [expect.objectContaining({ security_name: 'Fund' })])
  })

  it('shows the account-option limits and restrictions controls', async () => {
    await act(async () => root.render(<PortfolioSetup />))
    await flush()
    const optionsTab = [...container.querySelectorAll('button')].find(b => b.textContent === 'Account Investment Options')
    await act(async () => optionsTab.click())
    await flush()
    const account = container.querySelector('select')
    await act(async () => { account.value = '1'; account.dispatchEvent(new Event('change', { bubbles: true })) })
    await flush()
    expect(container.querySelector('input[placeholder="Minimum $"]')).not.toBeNull()
    expect(container.querySelector('input[placeholder="Max allocation %"]')).not.toBeNull()
    expect(container.querySelector('input[placeholder="Trading fee $"]')).not.toBeNull()
    expect(container.querySelector('input[placeholder="Redemption restriction"]')).not.toBeNull()
  })

  it('saves a cash-only account as excluded from investing advice', async () => {
    axios.get.mockImplementation((url) => {
      if (url === '/api/accounts') return Promise.resolve({ data: [
        { id: 1, name: '401k', account_type: 'traditional_401k', balance: 100000 },
        { id: 2, name: 'Checking', account_type: 'checking', balance: 25000 },
      ] })
      if (url === '/api/holdings/grouped') return Promise.resolve({ data: { groups: [] } })
      if (url === '/api/investment-policy') return Promise.resolve({ data: { has_policy: true, policy: {
        target_us_large_cap_pct: 60, target_us_bonds_pct: 40, excluded_accounts: [],
      } } })
      return Promise.resolve({ data: [] })
    })
    axios.post.mockResolvedValue({ data: {} })
    await act(async () => root.render(<PortfolioSetup />))
    await flush()
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Investment Policy').click())
    await flush()
    const checking = container.querySelector('input[aria-label="Exclude Checking from investing advice"]')
    expect(checking).not.toBeNull()
    await act(async () => checking.click())
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent === 'Save policy').click(); await Promise.resolve() })
    expect(axios.post).toHaveBeenCalledWith('/api/investment-policy', expect.objectContaining({ excluded_accounts: [2] }))
  })
})
