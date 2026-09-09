import { useState } from 'react'
import axios from 'axios'
import { usePersonNames } from '../hooks/usePersonNames'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'
import { TWO_AGE_MIN, TWO_AGE_MAX, clampTwoAge, parseTwoAgeInput } from '../utils/scenario'

// Minimal v1 UI for two-dimensional retirement timing (backend/docs/
// TWO_DIMENSIONAL_RETIREMENT_DESIGN.md section 7) -- two explicit ages,
// one scenario at a time, not a sweep/heatmap (that's section 3's
// deferred option (c)). Deliberately its own small tab next to the
// existing single-age tools rather than folded into them, so the two
// models stay visibly distinct instead of quietly merged.
const fmt = (n) => isPrivacyMode() ? MASK_CURRENCY : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n)

export default function TwoAgeScenario() {
  const { person1Name, person2Name } = usePersonNames()
  const [jasonRetAge, setJasonRetAge] = useState(65)
  const [justinRetAge, setJustinRetAge] = useState(65)
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Independent review, 2026-09-08 (P2): editing either age left the
  // PREVIOUS result on screen with no indication it no longer matched
  // the ages in the inputs, and a failed rerun kept the stale result
  // too. Clearing on every age edit and at the start of every run (not
  // just on success) means only a result that actually matches the
  // current request is ever shown -- and the summary below renders the
  // ages FROM THE RESPONSE (result.jason_ret_age/justin_ret_age), not
  // from the current input state, so a result can't silently be
  // mislabeled if the inputs changed again while a request was in flight.
  const updateJasonAge = v => { setJasonRetAge(v); setResult(null) }
  const updateJustinAge = v => { setJustinRetAge(v); setResult(null) }

  const run = () => {
    setLoading(true)
    setError(null)
    setResult(null)
    axios.get('/api/projections/two-dimensional-retirement', {
      params: { jason_ret_age: jasonRetAge, justin_ret_age: justinRetAge },
    }).then(r => setResult(r.data))
      .catch(() => { setResult(null); setError('Could not run this scenario. Check both ages and try again.') })
      .finally(() => setLoading(false))
  }

  return (
    <div>
      <div style={{ padding:'14px 16px', background:'rgba(79,156,249,0.06)', border:'1px solid rgba(79,156,249,0.2)', borderRadius:8, marginBottom:24, fontSize:13, color:'var(--text2)', lineHeight:1.7 }}>
        Models {person1Name} and {person2Name} retiring at two independent ages, including the years in between
        where one of you has retired and the other is still working (that income offsets spending, at the same
        ~65%-of-gross approximation used everywhere else in this app — not a real payroll-tax calculation).
        This is a separate, additive tool from the single-age Overview/Side-by-Side/Sensitivity tabs above — those
        still model only one household retirement date, with the second spouse's continued income treated as a
        simpler offset, not a genuinely two-dimensional timeline. Survivor Scenario, detailed payroll taxes, and
        per-owner account tracking are not part of this tool yet.
      </div>

      <div className="card" style={{ marginBottom:24 }}>
        <div className="grid-3" style={{ marginBottom:12 }}>
          {/* External audit review, 2026-09-09: see parseTwoAgeInput's
              own comment in utils/scenario.js -- lenient while typing,
              clamped only on blur. */}
          <div>
            <div className="label" style={{ marginBottom:8 }}>{person1Name}'s Retirement Age</div>
            <input type="number" min={TWO_AGE_MIN} max={TWO_AGE_MAX} step={1} value={jasonRetAge}
                   onChange={e => updateJasonAge(parseTwoAgeInput(e.target.value))}
                   onBlur={e => updateJasonAge(clampTwoAge(e.target.value))} />
            <div style={{ fontSize:11, color:'var(--text3)', marginTop:4 }}>Ages {TWO_AGE_MIN}-{TWO_AGE_MAX}</div>
          </div>
          <div>
            <div className="label" style={{ marginBottom:8 }}>{person2Name}'s Retirement Age</div>
            <input type="number" min={TWO_AGE_MIN} max={TWO_AGE_MAX} step={1} value={justinRetAge}
                   onChange={e => updateJustinAge(parseTwoAgeInput(e.target.value))}
                   onBlur={e => updateJustinAge(clampTwoAge(e.target.value))} />
            <div style={{ fontSize:11, color:'var(--text3)', marginTop:4 }}>Ages {TWO_AGE_MIN}-{TWO_AGE_MAX}</div>
          </div>
        </div>
        <button className="btn-primary" onClick={run} disabled={loading}>{loading ? 'Calculating…' : 'Run Scenario'}</button>
        {error && <div style={{ color:'#f87171', fontSize:13, marginTop:10 }}>{error}</div>}
      </div>

      {result?.has_data && (
        <>
          <div className="card" style={{ marginBottom:24, borderTop:`3px solid ${result.on_track ? '#34d399' : '#f87171'}` }}>
            <div className="label" style={{ marginBottom:4 }}>Summary</div>
            <div style={{ fontSize:12, color:'var(--text3)', marginBottom:16 }}>
              Results for {person1Name} retiring at {result.jason_ret_age} and {person2Name} retiring at {result.justin_ret_age}
            </div>
            <div className="grid-3">
              <div>
                <div className="label">Portfolio When The First of You Retires</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>{fmt(result.portfolio_at_phase2_start)}</div>
              </div>
              <div>
                <div className="label">Who Retires First</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>
                  {result.later_retiree === null ? 'Same year' : (result.later_retiree === 'justin' ? person1Name : person2Name)}
                </div>
              </div>
              <div>
                <div className="label">Outcome</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4, color: result.on_track ? '#34d399' : '#f87171' }}>
                  {result.on_track ? '✓ Plan holds up' : '⚠ Plan runs short in at least one year'}
                </div>
              </div>
            </div>
            <div style={{ fontSize:11, color:'var(--text3)', marginTop:16, lineHeight:1.5 }}>
              {result.account_ownership_limitation}
            </div>
          </div>

          <div className="card">
            <div className="label" style={{ marginBottom:16 }}>Year by Year</div>
            <div style={{ overflowX:'auto' }}>
              <table style={{ width:'100%', borderCollapse:'collapse', fontSize:13 }}>
                <thead>
                  <tr style={{ textAlign:'left', color:'var(--text3)', borderBottom:'1px solid var(--border)' }}>
                    <th style={{ padding:'6px 8px' }}>{person1Name}'s Age</th>
                    <th style={{ padding:'6px 8px' }}>{person2Name}'s Age</th>
                    <th style={{ padding:'6px 8px' }}>Phase</th>
                    <th style={{ padding:'6px 8px' }}>Spending Need</th>
                    <th style={{ padding:'6px 8px' }}>Still-Working Income</th>
                    <th style={{ padding:'6px 8px' }}>Withdrawal</th>
                    <th style={{ padding:'6px 8px' }}>Ending Balance</th>
                  </tr>
                </thead>
                <tbody>
                  {result.yearly_detail.map(row => (
                    <tr key={row.year} style={{ borderBottom:'1px solid var(--border)' }}>
                      <td style={{ padding:'6px 8px' }}>{row.jason_age}</td>
                      <td style={{ padding:'6px 8px' }}>{row.justin_age}</td>
                      <td style={{ padding:'6px 8px' }}>{row.phase === 'phase2' ? 'One retired' : 'Both retired'}</td>
                      <td style={{ padding:'6px 8px' }}>{fmt(row.income_need)}</td>
                      <td style={{ padding:'6px 8px' }}>{row.still_working_spouse_income > 0 ? fmt(row.still_working_spouse_income) : '—'}</td>
                      {/* Independent review, 2026-09-08 (P2): this used
                          to show row.draw -- net spending need before
                          withdrawal-order/tax mechanics -- labeled as if
                          it were the actual amount leaving the portfolio.
                          row.withdrawal is the real grossed-up total
                          simulate_withdrawal_year actually drew (e.g. an
                          $80,000 need funded from pretax can mean $88,889
                          actually leaves the accounts once the
                          withdrawal is grossed up for tax). */}
                      <td style={{ padding:'6px 8px' }}>{fmt(row.withdrawal)}</td>
                      <td style={{ padding:'6px 8px', color: row.unmet_need > 0 ? '#f87171' : 'inherit' }}>
                        {row.unmet_need > 0 ? `${fmt(row.portfolio_balance)} (short ${fmt(row.unmet_need)})` : fmt(row.portfolio_balance)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
