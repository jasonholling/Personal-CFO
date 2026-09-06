import TaskPanel from '../components/TaskPanel'
import { useState, useEffect } from 'react'
import axios from 'axios'
import { usePersonNames } from '../hooks/usePersonNames'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt = (n) => isPrivacyMode() ? MASK_CURRENCY : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n)

export default function Education({ onNavigate }) {
  const { kid1Name, kid2Name } = usePersonNames()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [continueDuringCollege, setContinueDuringCollege] = useState(false)

  useEffect(() => {
    setLoading(true)
    axios.get(`/api/projections/education?continue_contributions_during_college=${continueDuringCollege}`)
      .then(r => { setData(r.data); setLoading(false) })
      .catch(e => { setError(e.response?.data?.detail || 'Error'); setLoading(false) })
  }, [continueDuringCollege])

  if (loading) return <div className="loading">Calculating 529 projections...</div>
  if (error) return (
    <div>
      <h1 className="section-title">Education Planning</h1>
      <div className="card" style={{ marginTop: 24, textAlign: 'center', padding: '48px 24px' }}>
        <div style={{ color: 'var(--amber)', marginBottom: 12 }}>⚠ {error}</div>
        <button className="btn-primary" onClick={() => onNavigate('settings')}>Set Up Planning Inputs →</button>
      </div>
    </div>
  )

  const goals = data?.goals || []

  return (
    <div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom: 32, flexWrap:'wrap', gap:16 }}>
        <div>
          <h1 className="section-title">Education Planning</h1>
          <p className="section-sub">529 projections for {kid1Name} & {kid2Name} — assumes 7% growth, 4% college inflation</p>
        </div>
        <label style={{ display:'flex', alignItems:'center', gap:8, fontSize:12, color:'var(--text2)', cursor:'pointer', whiteSpace:'nowrap' }}>
          <input type="checkbox" checked={continueDuringCollege} onChange={e => setContinueDuringCollege(e.target.checked)} />
          Keep contributing during college years
        </label>
      </div>

      <div className="grid-2" style={{ marginBottom: 24 }}>
        {goals.map(g => {
          const pct = g.funding_percent
          const color = pct >= 100 ? 'var(--green)' : pct >= 75 ? 'var(--amber)' : 'var(--red)'
          return (
            <div key={g.child} className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
                <div>
                  <div style={{ fontFamily: 'var(--font-display)', fontSize: 22 }}>{g.child_name}</div>
                  <div style={{ color: 'var(--text2)', fontSize: 13, marginTop: 2 }}>
                    Age {g.current_age} · {g.years_to_college} years to college
                  </div>
                  {g.contributions_stop_in_years < g.years_to_college && (
                    <div style={{ color: 'var(--amber)', fontSize: 11, marginTop: 4 }}>
                      ⚠ Contributions modeled to stop in {g.contributions_stop_in_years} {g.contributions_stop_in_years === 1 ? 'year' : 'years'} (parent retirement), before college starts
                    </div>
                  )}
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: 36, fontFamily: 'var(--font-display)', color }}>{pct}%</div>
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>funded</div>
                </div>
              </div>

              <div className="progress-bar-track" style={{ height: 10, marginBottom: 20 }}>
                <div className="progress-bar-fill" style={{ width: `${Math.min(100, pct)}%`, background: color }} />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px 0' }}>
                {[
                  ['Current 529 Balance', fmt(g.current_529_balance)],
                  ['Projected at College', fmt(g.projected_529_at_college)],
                  ['Current Annual Cost (UNL)', fmt(g.current_annual_cost)],
                  ['Projected Total Cost', fmt(g.projected_total_cost)],
                ].map(([label, value]) => (
                  <div key={label}>
                    <div className="label" style={{ marginBottom: 3 }}>{label}</div>
                    <div style={{ fontWeight: 600, fontSize: 15 }}>{value}</div>
                  </div>
                ))}
              </div>

              {/* The literal, bottom-line question: does the money actually
                  run out. This drives both funding_percent/funding_gap
                  above (same simulation) and the recommendation panel
                  below — a gap is only ever shown when the account
                  genuinely would run dry during college. */}
              <div className="divider" />
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div className="label" style={{ marginBottom: 0 }}>Actual Balance After 4 Years of College</div>
                <div style={{ fontWeight: 700, fontSize: 16, color: g.depleted_during_college ? 'var(--red)' : 'var(--green)' }}>
                  {g.depleted_during_college ? '⚠ Runs out during college' : `✓ ${fmt(g.balance_after_college)} left over`}
                </div>
              </div>

              {g.funding_gap > 0 && (
                <>
                  <div className="divider" />
                  <div style={{ background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.2)', borderRadius: 8, padding: '14px 16px' }}>
                    <div style={{ color: 'var(--amber)', fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                      {fmt(g.funding_gap)} short at the low point during college
                    </div>
                    <div style={{ color: 'var(--text2)', fontSize: 11, marginBottom: 8, lineHeight: 1.5 }}>
                      At current contribution levels, the account is projected to run out before college ends — the amounts below would close that shortfall.
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                      <div>
                        <div className="label" style={{ marginBottom: 3 }}>Monthly Savings</div>
                        <div style={{ fontWeight: 700, fontSize: 18, color: 'var(--text)' }}>{fmt(g.monthly_savings_to_close_gap)}/mo</div>
                      </div>
                      <div>
                        <div className="label" style={{ marginBottom: 3 }}>Or Lump Sum Today</div>
                        <div style={{ fontWeight: 700, fontSize: 18, color: 'var(--text)' }}>{fmt(g.lump_sum_to_close_gap)}</div>
                      </div>
                    </div>
                  </div>
                </>
              )}

              {g.funding_gap === 0 && (
                <>
                  <div className="divider" />
                  <div style={{ color: 'var(--green)', fontSize: 13, fontWeight: 500 }}>
                    ✓ Fully funded — projected to cover all 4 years of college at current contribution levels, no additional savings needed
                  </div>
                </>
              )}
            </div>
          )
        })}
      </div>

      <div className="card" style={{ background: 'var(--bg3)', border: '1px solid var(--border)' }}>
        <div className="label" style={{ marginBottom: 8 }}>Assumptions</div>
        <div style={{ color: 'var(--text2)', fontSize: 13, lineHeight: 1.8 }}>
          529 growth rate: 7.0% · College cost inflation: 4.0% · 4 years at UNL · 10% assumed scholarship offset · Matches Creative Planning methodology
          <br />"Funding %"/"gap" and "Actual Balance After 4 Years of College" now come from the same year-by-year simulation, including continued investment growth through all 4 college years — a gap is only ever shown if the account is actually projected to run out.
          <br />Contributions are modeled to stop at age 60 regardless of the toggle above — no more earned income assumed funding them after retirement.
          <br />Update 529 balances monthly in the Accounts tab to keep these projections current.
        </div>
      </div>

      <TaskPanel section="education" />
    </div>
  )
}