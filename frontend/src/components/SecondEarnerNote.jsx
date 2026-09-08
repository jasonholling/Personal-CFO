import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

// Small, reusable disclosure for second-earner gap income (backlog P2,
// backend/docs/CALCULATION_CONTRACT.md section 17): the backend has
// surfaced justin_gap_income/justin_gap_income_first_year and
// second_earner_net_of_tax_factor in every withdrawal-phase consumer's
// API response since section 16, but nothing in the frontend read them
// until now — an API-output closeout isn't the same as a user-visible
// one. Renders nothing when there's no gap to disclose (amount/years
// falsy), so it's a no-op for the vast majority of households that never
// touch this feature.
//
// The dollar amount respects the app's existing isPrivacyMode()/
// MASK_CURRENCY convention (independent review, 2026-09-08, sixth pass —
// this note formatted the amount directly, exposing the working spouse's
// income even when nearby figures were masked for screen-sharing). The
// 65%-of-gross POLICY FACTOR stays visible either way — it's a documented
// methodology constant, not household-specific financial data.
//
// The trailing model-description line (added TWO_DIMENSIONAL_
// RETIREMENT_DESIGN.md section 7.5, 2026-09-08; made mode-aware in
// CALCULATION_CONTRACT.md section 23 after independent review found it
// still read "Single retirement-age model" even on a result that WAS
// two-age) is documentation-only, not a calculation change: most pages
// this component appears on model one household retirement date, with
// this dollar figure as an income OFFSET during the gap, not a genuine
// second, independently-timed retirement age -- pass `twoAge` (from the
// result's own `mode === 'two_age'`) when that's not the case, so the
// note doesn't misdescribe the very result it's attached to.
export default function SecondEarnerNote({ amount, years, factor, personLabel = 'Justin', twoAge = false }) {
  if (!amount || amount <= 0 || !years) return null
  const pct = Math.round((factor ?? 0.65) * 100)
  const amountText = isPrivacyMode() ? MASK_CURRENCY : `$${Math.round(amount).toLocaleString()}`
  return (
    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6, lineHeight: 1.5 }}>
      Includes ~{amountText}/yr from {personLabel}'s continued income
      for {years} more year{years === 1 ? '' : 's'} — approximated at {pct}% of gross
      (not a full payroll-tax calculation). {twoAge
        ? 'Two-age model — both retirement ages set independently.'
        : "Single retirement-age model — see Retirement Projection's Two-Age Scenario tab for two independent ages."}
    </div>
  )
}
