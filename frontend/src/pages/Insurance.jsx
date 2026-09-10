import { useState, useEffect } from 'react'
import axios from 'axios'
import TaskPanel from '../components/TaskPanel'
import { usePersonNames } from '../hooks/usePersonNames'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(n)
const fmtK = (n) => isPrivacyMode() ? MASK_CURRENCY : (n >= 1000000 ? `$${(n/1000000).toFixed(2)}M` : `$${(n/1000).toFixed(0)}K`)

const GapBar = ({ label, need, have, color }) => {
  const pct = Math.min(100, Math.round((have/need)*100))
  const surplus = have - need
  return (
    <div style={{ marginBottom:20 }}>
      <div style={{ display:'flex', justifyContent:'space-between', marginBottom:6 }}>
        <span style={{ fontSize:13, fontWeight:500 }}>{label}</span>
        <span style={{ fontSize:13, color: surplus>=0?'var(--green)':'var(--red)', fontWeight:600 }}>
          {surplus>=0 ? `+${fmt(surplus)} surplus` : `${fmt(Math.abs(surplus))} gap`}
        </span>
      </div>
      <div style={{ display:'flex', gap:12, alignItems:'center', marginBottom:6 }}>
        <span style={{ fontSize:11, color:'var(--text3)', minWidth:60 }}>Need: {fmtK(need)}</span>
        <div className="progress-bar-track" style={{ flex:1, height:10 }}>
          <div className="progress-bar-fill" style={{ width:`${pct}%`, background: surplus>=0?'var(--green)':'var(--amber)' }} />
        </div>
        <span style={{ fontSize:11, color:'var(--text3)', minWidth:60, textAlign:'right' }}>Have: {fmtK(have)}</span>
      </div>
    </div>
  )
}

const Row = ({ label, value, sub, highlight }) => (
  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', padding:'10px 0', borderBottom:'1px solid var(--border)' }}>
    <div>
      <div style={{ fontSize:13 }}>{label}</div>
      {sub && <div style={{ fontSize:11, color:'var(--text2)', marginTop:2 }}>{sub}</div>}
    </div>
    <div style={{ fontWeight:600, color: highlight||'var(--text)', fontSize:13 }}>{value}</div>
  </div>
)

export default function Insurance({ onNavigate }) {
  const { person1Name, person2Name } = usePersonNames()
  const [data, setData]     = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState(null)

  useEffect(() => {
    axios.get('/api/projections/insurance')
      .then(r => { setData(r.data); setLoading(false) })
      .catch(e => { setError(e.response?.data?.detail||'Error'); setLoading(false) })
  }, [])

  if (loading) return <div className="loading">Analyzing coverage...</div>
  if (error)   return (
    <div>
      <h1 className="section-title">Insurance Analysis</h1>
      <div className="card" style={{ marginTop:24, textAlign:'center', padding:'48px 24px' }}>
        <div style={{ color:'var(--amber)', marginBottom:12 }}>⚠ {error}</div>
        <button className="btn-primary" onClick={() => onNavigate('settings')}>Set Up Planning Inputs →</button>
      </div>
    </div>
  )

  const { jason, justin, property, disability, ltc } = data

  return (
    <div>
      <div style={{ marginBottom:32 }}>
        <h1 className="section-title">Insurance Analysis</h1>
        <p className="section-sub">Coverage gaps and needs across life, property, disability, and LTC</p>
      </div>

      {/* Life insurance needs */}
      <div className="grid-2" style={{ marginBottom:24 }}>
        <div className="card">
          <div className="label" style={{ marginBottom:4 }}>In the Event of {person1Name}'s Death</div>
          <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>Need to cover debt + college + {person2Name}'s income to retirement</div>

          <GapBar label="Total Coverage Need vs Have" need={jason.total_need} have={jason.current_coverage} />

          <Row label="Pay off all debt" value={fmt(jason.debt_payoff)} sub="Mortgages" />
          <Row label="Fund 529s to 100%" value={fmt(jason.college_funding)} sub="Remaining college gaps" />
          <Row label={`Income for ${person2Name} to retirement`} value={fmt(jason.income_replacement)} sub="Capitalized at 6%" />
          <div style={{ height:1, background:'var(--border)', margin:'8px 0' }} />
          <Row label="Total Need" value={fmt(jason.total_need)} highlight="var(--text)" />
          <Row label="Current Coverage" value={fmt(jason.current_coverage)} sub={`All ${person1Name} policies combined`} highlight="var(--accent)" />
          <div style={{ height:1, background:'var(--border)', margin:'8px 0' }} />
          {/* External audit follow-up, 2026-09-09 (P1): this row used to
              trust the backend's separate on_track boolean for both the
              label AND the color, while displaying Math.abs(surplus_gap)
              -- on_track carried its own $100,000 tolerance (Justin's
              side, since removed), so a real shortfall inside that
              tolerance rendered as a GREEN "Surplus." Derived directly
              from surplus_gap's own sign now, so this can never disagree
              with the number it's showing regardless of what threshold
              a backend field might use in the future. */}
          <Row
            label={jason.surplus_gap >= 0 ? "Surplus" : "Gap"}
            value={fmt(Math.abs(jason.surplus_gap))}
            highlight={jason.surplus_gap >= 0 ? 'var(--green)' : 'var(--red)'}
          />
        </div>

        <div className="card">
          <div className="label" style={{ marginBottom:4 }}>In the Event of {person2Name}'s Death</div>
          <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>{person1Name} keeps earning — need covers debt + college only</div>

          <GapBar label="Total Coverage Need vs Have" need={justin.total_need} have={justin.current_coverage} />

          <Row label="Pay off all debt" value={fmt(justin.debt_payoff)} sub="Mortgages" />
          <Row label="Fund 529s to 100%" value={fmt(justin.college_funding)} sub="Remaining college gaps" />
          <Row label="Income replacement" value="$0" sub={`${person1Name} continues earning`} />
          <div style={{ height:1, background:'var(--border)', margin:'8px 0' }} />
          <Row label="Total Need" value={fmt(justin.total_need)} highlight="var(--text)" />
          <Row label="Current Coverage" value={fmt(justin.current_coverage)} sub={`All ${person2Name} policies combined`} highlight="var(--accent)" />
          <div style={{ height:1, background:'var(--border)', margin:'8px 0' }} />
          <Row
            label={justin.surplus_gap >= 0 ? "Surplus" : "Gap"}
            value={fmt(Math.abs(justin.surplus_gap))}
            highlight={justin.surplus_gap >= 0 ? 'var(--green)' : 'var(--red)'}
          />
        </div>
      </div>

      {/* Property */}
      <div className="card" style={{ marginBottom:24 }}>
        <div className="label" style={{ marginBottom:16 }}>Property Coverage</div>
        <div className="grid-3">
          <div>
            <div style={{ fontSize:12, color:'var(--text2)', marginBottom:4 }}>Primary Home</div>
            <div style={{ fontWeight:600 }}>{fmt(property.primary_home_insured)} insured</div>
            <div style={{ fontSize:12, color:'var(--text2)' }}>Value: {fmt(property.primary_home_value)}</div>
            {property.primary_home_gap > 0 && (
              <div style={{ fontSize:12, color:'var(--amber)', marginTop:4 }}>
                ⚠ {fmt(property.primary_home_gap)} underinsured
              </div>
            )}
          </div>
          <div>
            <div style={{ fontSize:12, color:'var(--text2)', marginBottom:4 }}>Rental Property</div>
            <div style={{ fontWeight:600 }}>{fmt(property.rental_insured)} insured</div>
            <div style={{ fontSize:12, color:'var(--text2)' }}>Value: {fmt(property.rental_value)}</div>
            {property.rental_gap > 0 && (
              <div style={{ fontSize:12, color:'var(--amber)', marginTop:4 }}>
                ⚠ {fmt(property.rental_gap)} underinsured
              </div>
            )}
          </div>
          <div>
            <div style={{ fontSize:12, color:'var(--text2)', marginBottom:4 }}>Umbrella Policy</div>
            <div style={{ fontWeight:600, color: property.umbrella_adequate ? 'var(--green)' : 'var(--amber)' }}>{fmt(property.umbrella)}</div>
            <div style={{ fontSize:12, color:'var(--text2)' }}>Recommended: {fmt(property.recommended_umbrella)} (≈ net worth)</div>
            {!property.umbrella_adequate && (
              <div style={{ fontSize:12, color:'var(--amber)', marginTop:4 }}>
                ⚠ {fmt(property.umbrella_gap)} short of net worth
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Disability + LTC */}
      <div className="grid-2" style={{ marginBottom:24 }}>
        <div className="card">
          <div className="label" style={{ marginBottom:12 }}>Disability Coverage</div>
          <Row label="Monthly Benefit" value={fmt(disability.monthly_benefit)+'/mo'} highlight={disability.monthly_benefit > 0 ? 'var(--green)' : 'var(--amber)'} />
          <Row label="Duration" value={`To age ${disability.to_age}`} />
          <Row label="Funded By" value={disability.funded_by} />
          {disability.monthly_benefit > 0 ? (
            <div style={{ marginTop:12, fontSize:12, color:'var(--green)' }}>✓ {disability.funded_by}</div>
          ) : (
            <div style={{ marginTop:12, fontSize:12, color:'var(--amber)' }}>⚠ No disability coverage on file — add it in Settings</div>
          )}
        </div>

        <div className="card">
          <div className="label" style={{ marginBottom:12 }}>Long Term Care</div>
          <Row label="Daily Benefit" value={isPrivacyMode() ? MASK_CURRENCY : `$${ltc.daily_benefit}/day`} highlight="var(--green)" />
          <Row label="Maximum Benefit" value={fmt(ltc.max_benefit)} highlight={ltc.max_benefit > 0 ? 'var(--green)' : 'var(--amber)'} />
          <Row label="Annual Premium" value={fmt(ltc.premium_annual)} />
          <Row label="Local Average Cost" value={isPrivacyMode() ? MASK_CURRENCY : `$${ltc.omaha_daily_cost_low}–$${ltc.omaha_daily_cost_high}/day`} />
          {ltc.max_benefit > 0 ? (
            <div style={{ marginTop:12, fontSize:12, color:'var(--green)' }}>✓ Coverage in place · monitor premiums annually</div>
          ) : (
            <div style={{ marginTop:12, fontSize:12, color:'var(--amber)' }}>⚠ No LTC maximum benefit on file — add it in Settings</div>
          )}
        </div>
      </div>

      <TaskPanel section="risk" />
    </div>
  )
}
