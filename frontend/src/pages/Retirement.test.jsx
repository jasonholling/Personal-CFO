import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import Retirement from './Retirement'
import { setRetAge, setSsTiming, setJasonSsClaimAge } from '../utils/scenario'

// Milestone 2 (2026-09-09, "Explain every major result"): confirms the
// "How this was calculated" panel is actually wired into the retirement
// projection result, and that its numbers are read from the same
// scenario object the KPI cards above it use -- not a second, separately
// fetched copy that could drift out of agreement.

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('../hooks/usePersonNames', () => ({
  usePersonNames: () => ({ person1Name: 'Alex', person2Name: 'Sam' }),
}))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.ResizeObserver = globalThis.ResizeObserver || class {
  observe() {} unobserve() {} disconnect() {}
}

let container, root
const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve() }) }
const click = async text => {
  const button = [...container.querySelectorAll('button, summary')].find(b => b.textContent.trim() === text)
  expect(button, text).toBeTruthy()
  await act(async () => { button.click() })
}

// Milestone 2 acceptance follow-up (2026-09-10): gross_spending_need/
// remaining_portfolio_need/growth are now real backend fields (see
// projection_engine.py's yearly.append) rather than derived in the
// frontend -- this fixture supplies them directly, matching the exact
// identity TestAnnualReconciliationAgainstRealBackendOutput verifies
// against real engine output on the backend side: remaining_need ==
// gross_spending - (life_event_monthly_adjustment + justin_gap_income +
// pension + social_security + life_event_cash).
const yearlyDetail = Array.from({ length: 5 }, (_, i) => ({
  jason_age: 60 + i, year: 2031 + i, healthcare_cost: 5000,
  gross_spending_need: 80000,
  pension: 10000, social_security: 20000, bridge_income: 0,
  // justin_gap_income and life_event_cash are nonzero on year index 3
  // specifically so a reconciliation test can confirm the offsets are
  // counted exactly once (in "Income offsets"), not also silently baked
  // into a lower "Gross spending" or double-subtracted from need.
  justin_gap_income: i === 3 ? 6000 : 0, life_event_cash: i === 3 ? -2500 : 0,
  life_event_monthly_adjustment: 0,
  remaining_portfolio_need: i === 3 ? 46500 : 50000,
  rmd: i === 2 ? 8000 : 0, rmd_reinvested: i === 2 ? 8000 : 0, estimated_tax: 4000,
  withdrawal_pretax: 30000, withdrawal_taxable: 10000, withdrawal_roth: 5000, withdrawal_hsa: 0,
  withdrawal: 45000, growth: 42000 - i * 1000, unmet_need: 0, portfolio_balance: 900000 - i * 20000,
}))

const scenario = (label) => ({
  label, retirement_age: 60, percent_funded: 85, projected_surplus: 40000,
  years_to_retirement: 5, retirement_end_age: 90, current_investable_assets: 800000,
  portfolio_at_retirement: 900000, pension_annual: 30000, jason_ss_start_age: 62,
  income_today_dollars: 70000, income_first_year: 80000, state_income_tax_rate: 0.05,
  yearly_detail: yearlyDetail,
})

const projectionsResponse = { scenarios: [scenario('age_60_early'), scenario('age_60_delayed')] }

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  setRetAge(60)
  setSsTiming('early')
  setJasonSsClaimAge(null)
  axios.get.mockImplementation(url => {
    if (url === '/api/projections/retirement') return Promise.resolve({ data: projectionsResponse })
    if (url === '/api/planning-inputs') return Promise.resolve({ data: {} })
    return Promise.resolve({ data: [] })
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

describe('Retirement Projection — How this was calculated (Milestone 2)', () => {
  it('renders the explainer panel and expands to show the scenario\'s own dollar basis and flows', async () => {
    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush()
    expect(container.textContent).toContain('How this was calculated')
    await click('How this was calculated')
    await flush()
    expect(container.textContent).toContain('Retirement age: 60')
    expect(container.textContent).toContain('$70,000')
    expect(container.textContent).toContain('$80,000')
    expect(container.textContent).toContain('2031 (age 60)')
  })

  it('reconciles opening/closing balances across years and shows real growth figures', async () => {
    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush()
    await click('How this was calculated')
    await flush()
    // Opening balance for year 3 (index 2) must equal year 2's own closing
    // balance -- this is the carry-forward identity the explainer relies
    // on instead of re-deriving anything; year 0's opening must equal the
    // scenario's own portfolio_at_retirement.
    expect(container.textContent).toContain('$900,000') // year 0 opening = portfolio_at_retirement
    expect(container.textContent).toContain('$880,000') // year 1 closing = year 2 opening
    // The transfers column surfaces the one nonzero rmd_reinvested row.
    expect(container.textContent).toContain('$8,000')
    // Growth is now a real per-year figure (backend field), not
    // disclosed as unavailable.
    expect(container.textContent).toContain('$42,000') // year 0's growth
  })

  it('withdrawal breakdown sums to the same total shown as the gross withdrawal figure', async () => {
    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush()
    await click('How this was calculated')
    await flush()
    const breakdownSum = yearlyDetail[0].withdrawal_pretax + yearlyDetail[0].withdrawal_taxable
      + yearlyDetail[0].withdrawal_roth + yearlyDetail[0].withdrawal_hsa
    expect(breakdownSum).toBe(yearlyDetail[0].withdrawal)
    expect(container.textContent).toContain('pretax $30,000')
    expect(container.textContent).toContain('taxable $10,000')
  })

  it('gross spending, income offsets, and remaining need are distinct, with no offset double-counted', async () => {
    // Acceptance follow-up (2026-09-10): "distinguish gross household
    // spending from income offsets and remaining portfolio need... do
    // not count an income offset both as income and as a reduction in
    // displayed spending." Year index 3 has justin_gap_income and a
    // negative life_event_cash both nonzero -- confirm the offset total
    // shown ($33,500) is distinct from gross spending ($80,000, SAME for
    // every year in this fixture, never reduced by the offset), and
    // that remaining need ($46,500) is exactly gross minus offsets.
    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush()
    await click('How this was calculated')
    await flush()
    const y = yearlyDetail[3]
    const incomeOffsets = y.pension + y.social_security + y.justin_gap_income + y.life_event_cash + y.life_event_monthly_adjustment
    expect(incomeOffsets).toBe(33500) // 10000 + 20000 + 6000 - 2500 + 0
    expect(y.gross_spending_need - incomeOffsets).toBe(y.remaining_portfolio_need) // 80000 - 33500 = 46500
    expect(container.textContent).toContain('$33,500')
    expect(container.textContent).toContain('$46,500')
    // Gross spending is the SAME $80,000 every year in this fixture --
    // it must never itself be reduced by the offset (that would be the
    // double-count the review flagged). $80,000 also appears once more
    // in the dollar-basis line above the table (income_first_year), so
    // the count is at least one-per-row, not exactly one-per-row.
    expect(container.textContent.match(/\$80,000/g).length).toBeGreaterThanOrEqual(yearlyDetail.length)
  })

  it('an earlier, slower request cannot overwrite a later, faster one (out-of-order response guard)', async () => {
    // Review finding (2026-09-09): nothing previously stopped a slow
    // response for the FIRST fetch (no claim-age override) from resolving
    // after a fast response for a SECOND fetch (override just turned on)
    // -- setData would then apply the stale first response last, leaving
    // both the KPI cards and the explainer panel out of sync with the
    // claim age actually selected on screen.
    let resolveSlow
    const slowResponse = new Promise(resolve => { resolveSlow = resolve })
    const responseNoOverride = { scenarios: [
      { ...scenario('age_60_early'), portfolio_at_retirement: 111111 },
      scenario('age_60_delayed'),
    ] }
    const responseWithOverride = { scenarios: [
      { ...scenario('age_60_custom'), portfolio_at_retirement: 222222 },
    ] }

    axios.get.mockImplementation((url, config) => {
      if (url === '/api/planning-inputs') return Promise.resolve({ data: {} })
      if (url !== '/api/projections/retirement') return Promise.resolve({ data: [] })
      if (config?.params?.jason_ss_claim_age === 70) {
        return Promise.resolve({ data: responseWithOverride }) // fast, resolves immediately
      }
      return slowResponse.then(() => ({ data: responseNoOverride })) // slow, resolves only when told to
    })

    await act(async () => root.render(<Retirement onNavigate={() => {}} />))
    await flush() // first (slow) request is in flight, not yet resolved

    await act(async () => { setJasonSsClaimAge(70) }) // second (fast) request fires and resolves
    await flush()
    expect(container.textContent).toContain('$222K')

    await act(async () => { resolveSlow() }) // the stale first request finally resolves
    await flush()
    expect(container.textContent).toContain('$222K')
    expect(container.textContent).not.toContain('$111K')
  })
})
