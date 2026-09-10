import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

// Milestone 2 (2026-09-09, "Explain every major result"): a shared
// "How this was calculated" panel. Every value shown here is read
// straight off the calculation's own returned data (a projection's
// yearly_detail rows, a scenario's summary fields) -- nothing here
// recomputes tax, withdrawal, or growth formulas; it only labels,
// sums, and carries forward numbers the engine already produced, so
// this can never disagree with the number it's explaining.
//
// Reused across whichever result pages adopt it (Retirement Projection
// first; Monte Carlo/Roth Conversion/Insurance are follow-up slices --
// see CALCULATION_CONTRACT.md section 67's "explicitly out of scope").
const fmt = n => isPrivacyMode() ? MASK_CURRENCY : new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', maximumFractionDigits: 0,
}).format(n || 0)

/**
 * assumptions: array of [label, value] pairs, already formatted strings —
 *   the caller decides what's relevant for its own result, this component
 *   doesn't guess.
 * dollarBasis: { todayValue, futureValue, label } — e.g. spending goal
 *   shown both in today's dollars and the inflated first-retirement-year
 *   figure, so "today's $ vs future $" is explicit rather than implied by
 *   which single number happens to be on screen.
 * flows: array of { year, jasonAge, opening, income, spending, taxes,
 *   withdrawal, withdrawalBreakdown, unmetNeed, closing } — one row per
 *   already-computed projection year. withdrawalBreakdown is an optional
 *   { pretax, taxable, roth, hsa } object for "distinguish gross
 *   withdrawals... " without re-deriving it.
 * notes: array of strings — "explain intentional differences between
 *   tools" free text, e.g. why this tool's number differs from another
 *   page's for what looks like the same question.
 */
export default function CalculationExplainer({ assumptions = [], dollarBasis, flows = [], notes = [], calcDate }) {
  return (
    <details className="card" style={{ marginBottom: 24, padding: '16px 20px' }}>
      <summary style={{ cursor: 'pointer', fontWeight: 600 }}>How this was calculated</summary>
      <div style={{ marginTop: 16 }}>
        {calcDate && (
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 12 }}>
            Calculated {calcDate} against your current planning inputs and account balances.
          </div>
        )}
        {assumptions.length > 0 && (
          <>
            <div className="label" style={{ marginBottom: 6, fontSize: 11 }}>Assumptions used</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 20px', fontSize: 13, marginBottom: 16 }}>
              {assumptions.map(([label, value]) => (
                <span key={label}><span style={{ color: 'var(--text2)' }}>{label}:</span> {value}</span>
              ))}
            </div>
          </>
        )}
        {dollarBasis && (
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 16 }}>
            {dollarBasis.label}: {fmt(dollarBasis.todayValue)} in today's dollars → {fmt(dollarBasis.futureValue)} in the year it's actually spent, after inflation.
          </div>
        )}
        {flows.length > 0 && (
          <>
            <div className="label" style={{ marginBottom: 6, fontSize: 11 }}>Annual flows (first {Math.min(flows.length, 10)} years shown)</div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse', minWidth: 640 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border2)', textAlign: 'right' }}>
                    <th style={{ textAlign: 'left', padding: '4px 6px' }}>Year</th>
                    <th style={{ padding: '4px 6px' }}>Opening</th>
                    <th style={{ padding: '4px 6px' }}>Income</th>
                    <th style={{ padding: '4px 6px' }}>Spending need</th>
                    <th style={{ padding: '4px 6px' }}>Taxes</th>
                    <th style={{ padding: '4px 6px' }}>Withdrawal</th>
                    <th style={{ padding: '4px 6px' }}>Unmet need</th>
                    <th style={{ padding: '4px 6px' }}>Closing</th>
                  </tr>
                </thead>
                <tbody>
                  {flows.slice(0, 10).map(f => (
                    <tr key={f.year} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '4px 6px' }}>{f.year}{f.jasonAge != null ? ` (age ${f.jasonAge})` : ''}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right' }}>{fmt(f.opening)}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right', color: 'var(--green)' }}>{fmt(f.income)}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right' }}>{fmt(f.spending)}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right' }}>{fmt(f.taxes)}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right' }}>
                        {fmt(f.withdrawal)}
                        {f.withdrawalBreakdown && (
                          <div style={{ fontSize: 10, color: 'var(--text3)' }}>
                            pretax {fmt(f.withdrawalBreakdown.pretax)} · taxable {fmt(f.withdrawalBreakdown.taxable)} · roth {fmt(f.withdrawalBreakdown.roth)} · hsa {fmt(f.withdrawalBreakdown.hsa)}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: '4px 6px', textAlign: 'right', color: f.unmetNeed > 0 ? 'var(--red)' : 'var(--text3)' }}>{fmt(f.unmetNeed)}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right', fontWeight: 600 }}>{fmt(f.closing)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        {notes.length > 0 && (
          <div style={{ marginTop: 16, fontSize: 12, color: 'var(--text3)' }}>
            {notes.map((n, i) => <div key={i} style={{ marginBottom: 4 }}>ℹ {n}</div>)}
          </div>
        )}
      </div>
    </details>
  )
}
