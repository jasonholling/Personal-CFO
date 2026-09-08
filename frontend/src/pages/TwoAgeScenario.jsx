import { useState } from 'react'
import axios from 'axios'
import { usePersonNames } from '../hooks/usePersonNames'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

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

  const run = () => {
    setLoading(true)
    setError(null)
    axios.get('/api/projections/two-dimensional-retirement', {
      params: { jason_ret_age: jasonRetAge, justin_ret_age: justinRetAge },
    }).then(r => setResult(r.data))
      .catch(() => setError('Could not run this scenario. Check both ages and try again.'))
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
          <div>
            <div className="label" style={{ marginBottom:8 }}>{person1Name}'s Retirement Age</div>
            <input type="number" value={jasonRetAge} onChange={e => setJasonRetAge(parseInt(e.target.value) || 0)} />
          </div>
          <div>
            <div className="label" style={{ marginBottom:8 }}>{person2Name}'s Retirement Age</div>
            <input type="number" value={justinRetAge} onChange={e => setJustinRetAge(parseInt(e.target.value) || 0)} />
          </div>
        </div>
        <button className="btn-primary" onClick={run} disabled={loading}>{loading ? 'Calculating…' : 'Run Scenario'}</button>
        {error && <div style={{ color:'#f87171', fontSize:13, marginTop:10 }}>{error}</div>}
      </div>

      {result?.has_data && (
        <>
          <div className="card" style={{ marginBottom:24, borderTop:`3px solid ${result.on_track ? '#34d399' : '#f87171'}` }}>
            <div className="label" style={{ marginBottom:16 }}>Summary</div>
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
                    <th style={{ padding:'6px 8px' }}>Portfolio Draw</th>
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
                      <td style={{ padding:'6px 8px' }}>{fmt(row.draw)}</td>
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
