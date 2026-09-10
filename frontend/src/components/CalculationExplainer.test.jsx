import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it } from 'vitest'
import CalculationExplainer from './CalculationExplainer'
import { setPrivacyMode } from '../utils/privacy'

// Milestone 2 (2026-09-09, "Explain every major result"), corrected
// 2026-09-10 per acceptance review -- coverage for the shared "How this
// was calculated" panel: assumptions/dollar-basis/annual-flows all
// render from props with no formula recomputation, gross spending is
// distinguished from income offsets and remaining portfolio need (not
// double-counted), and privacy mode masks every dollar figure it shows.

let container, root
beforeEach(() => {
  setPrivacyMode(false)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

const flows = [
  { year: 2031, jasonAge: 60, opening: 900000, grossSpending: 80000, incomeOffsets: 30000, remainingNeed: 50000,
    taxes: 5000, withdrawal: 55000, withdrawalBreakdown: { pretax: 40000, taxable: 10000, roth: 5000, hsa: 0 },
    transfers: 0, growth: 42000, unmetNeed: 0, closing: 875000 },
  { year: 2032, jasonAge: 61, opening: 875000, grossSpending: 81000, incomeOffsets: 30000, remainingNeed: 51000,
    taxes: 5100, withdrawal: 56000, withdrawalBreakdown: { pretax: 41000, taxable: 10000, roth: 5000, hsa: 0 },
    transfers: 6000, growth: 40000, unmetNeed: 0, closing: 850000 },
]

describe('CalculationExplainer', () => {
  it('renders assumptions, dollar-basis both-ways, and per-year flow rows from props alone', async () => {
    await act(async () => root.render(
      <CalculationExplainer
        calcDate="9/9/2026"
        assumptions={[['Retirement age', '60'], ['Social Security', 'take at 62']]}
        dollarBasis={{ label: 'First-year income need', todayValue: 70000, futureValue: 80000 }}
        flows={flows}
        notes={['This tool differs from Monte Carlo on purpose.']}
      />
    ))
    expect(container.textContent).toContain('How this was calculated')
    expect(container.textContent).toContain('Retirement age: 60')
    expect(container.textContent).toContain('$70,000')
    expect(container.textContent).toContain('$80,000')
    expect(container.textContent).toContain('2031 (age 60)')
    expect(container.textContent).toContain('This tool differs from Monte Carlo on purpose.')
    expect(container.textContent).toContain('9/9/2026')
  })

  it('shows gross spending, income offsets, and remaining need as distinct figures, not double-counted', async () => {
    await act(async () => root.render(<CalculationExplainer flows={flows} />))
    // Year 2031: gross spending $80,000, income offsets $30,000,
    // remaining need $50,000 -- each rendered as its own figure, and
    // remaining need is NOT the same number as gross spending (it would
    // be, if the offset were never actually subtracted anywhere).
    expect(container.textContent).toContain('$80,000')
    expect(container.textContent).toContain('$30,000')
    expect(container.textContent).toContain('$50,000')
    expect(50000).toBe(80000 - 30000) // the identity the two figures encode
  })

  it('shows the withdrawal breakdown by account bucket, not just the gross total', async () => {
    await act(async () => root.render(<CalculationExplainer flows={flows} />))
    expect(container.textContent).toContain('pretax $40,000')
    expect(container.textContent).toContain('taxable $10,000')
  })

  it('shows transfers and growth as their own columns, both read from props not derived', async () => {
    await act(async () => root.render(<CalculationExplainer flows={flows} />))
    expect(container.textContent).toContain('$6,000') // the nonzero transfer, year 2032
    expect(container.textContent).toContain('$42,000') // year 2031's growth figure
    expect(container.textContent).toContain('$40,000') // year 2032's growth figure
  })

  it('privacy mode masks every dollar figure, including inside the expanded flows table', async () => {
    setPrivacyMode(true)
    await act(async () => root.render(
      <CalculationExplainer
        dollarBasis={{ label: 'First-year income need', todayValue: 70000, futureValue: 80000 }}
        flows={flows}
      />
    ))
    expect(container.textContent).not.toContain('70,000')
    expect(container.textContent).not.toContain('875,000')
    expect(container.textContent.match(/\$•••,•••/g)?.length).toBeGreaterThan(5)
  })

  it('renders nothing extra when no assumptions/flows/notes are passed', async () => {
    await act(async () => root.render(<CalculationExplainer />))
    expect(container.textContent).toContain('How this was calculated')
    expect(container.textContent).not.toContain('Assumptions used')
    expect(container.textContent).not.toContain('Annual flows')
  })
})
