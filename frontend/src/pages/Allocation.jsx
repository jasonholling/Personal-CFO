import { useState, useEffect } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(n))

const GREEN  = '#34d399'
const AMBER  = '#fbbf24'
const RED    = '#f87171'

export default function Allocation() {
  const [concentration, setConcentration] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Asset Allocation / Rebalancing (/api/allocation/analysis) and
    // Investment Fee Audit (/api/allocation/fees) are deliberately not
    // fetched here — see the comments where those cards used to render,
    // below.
    axios.get('/api/allocation/concentration')
      .then(c => setConcentration(c.data))
      .catch(() => {}).finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="loading">Analyzing your portfolio...</div>

  return (
    <div>
      <div style={{ marginBottom:28 }}>
        <h1 className="section-title">Concentration Risk</h1>
        <p className="section-sub">Flags any single taxable/custodial account that's an outsized share of your portfolio (401k/IRA/Roth/HSA excluded — those are diversified fund lineups, not a single security)</p>
      </div>

      {/* Asset Allocation / Rebalancing — hidden for now. Without a real
          per-account stock/bond breakdown, this defaulted every account
          to an assumed 80% stocks and built a specific dollar-amount
          "shift $X from stocks to bonds" recommendation on top of that
          guess. Re-enable once there's an account-statement upload +
          holdings-analysis feature to derive real splits, rather than
          asking for manual entry account by account. The backend
          (allocation_engine.analyze_allocation / /api/allocation/analysis)
          is untouched and ready to use real data the moment it exists. */}

      {/* Investment Fee Audit — hidden alongside Asset Allocation &
          Rebalancing above for the same reason: it needs a real
          per-account expense_ratio, which nothing here derives from just
          a balance. It wasn't fabricating anything (it correctly showed
          "no data entered yet" for every account, since none has an
          expense ratio), but it's the same underlying gap — a feature
          waiting on manual entry or a future statement-upload/holdings-
          analysis feature. Re-enable once that exists. Backend
          (allocation_engine.analyze_fees / /api/allocation/fees) is
          untouched. */}

      {/* Concentrated Stock Risk */}
      <div className="card">
        <div className="label" style={{ marginBottom:16 }}>Concentrated Stock Risk</div>
        {!concentration?.has_data ? (
          <div style={{ fontSize:13, color:'var(--text2)' }}>
            No investment accounts found in Accounts to check for concentration.
          </div>
        ) : (
          <>
            <div style={{
              padding:'12px 16px', borderRadius:8, fontSize:13, lineHeight:1.6, marginBottom:16,
              background: concentration.flagged_positions.length > 0 ? 'rgba(251,191,36,0.08)' : 'rgba(52,211,153,0.08)',
            }}>
              {concentration.flagged_positions.length > 0 && <span style={{ color:AMBER, fontWeight:600 }}>⚠ Concentration flagged: </span>}
              {concentration.flagged_positions.length === 0 && <span style={{ color:GREEN, fontWeight:600 }}>✓ Diversified: </span>}
              {concentration.recommendation}
            </div>
            {concentration.flagged_positions.length > 0 && (
              <div style={{ overflowX:'auto' }}>
                <table style={{ width:'100%', borderCollapse:'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom:'1px solid var(--border)' }}>
                      {['Account','Balance','% of Investable Assets','Severity'].map(h => (
                        <th key={h} style={{ padding:'8px 12px', textAlign:'left', fontSize:11, fontWeight:600, letterSpacing:'0.05em', textTransform:'uppercase', color:'var(--text3)' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {concentration.flagged_positions.map(p => (
                      <tr key={p.id} style={{ borderBottom:'1px solid var(--border)' }}>
                        <td style={{ padding:'8px 12px', fontSize:13, fontWeight:500 }}>{p.name}</td>
                        <td style={{ padding:'8px 12px', fontSize:13 }}>{fmt(p.balance)}</td>
                        <td style={{ padding:'8px 12px', fontSize:13 }}>{p.pct_of_portfolio}%</td>
                        <td style={{ padding:'8px 12px', fontSize:13, color: p.severity === 'severe' ? RED : AMBER, textTransform:'capitalize' }}>{p.severity}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
