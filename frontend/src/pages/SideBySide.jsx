import { useState, useEffect } from 'react'
import axios from 'axios'
import {
  LineChart, Line, AreaChart, Area,
  XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, ReferenceLine
} from 'recharts'
import { usePersonNames } from '../hooks/usePersonNames'
import { useKids } from '../hooks/useKids'
import { isPrivacyMode, MASK_CURRENCY, MASK_PERCENT } from '../utils/privacy'

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(n))
const fmtK = (n) => {
  if (isPrivacyMode()) return MASK_CURRENCY
  if (n == null) return '—'
  if (Math.abs(n) >= 1_000_000) return `$${(n/1_000_000).toFixed(1)}M`
  return `$${(n/1000).toFixed(0)}K`
}
const pct = (n) => isPrivacyMode() ? MASK_PERCENT : (n == null ? '—' : `${n.toFixed(1)}%`)

const GREEN  = '#34d399'
const AMBER  = '#fbbf24'
const RED    = '#f87171'
const ACCENT = '#4f9cf9'
const NAVY   = '#5C7CE0' // was #1B3A6B — nearly the same luminance as the dark card background, effectively invisible
const TEAL   = '#2E7D8C'

const AGE_COLORS = {
  55: '#f97316',
  60: '#4f9cf9',
  65: '#34d399',
}

const SCENARIO_LABELS = {
  age_55_early: 'Retire 55',
  age_60_early: 'Retire 60',
  age_65_early: 'Retire 65',
}

export default function SideBySide({ onNavigate }) {
  const { person1Name, person2Name } = usePersonNames()
  const [data, setData]     = useState(null)
  const [swr, setSwr]       = useState({})
  const [inputs, setInputs] = useState(null)
  const { kids } = useKids()
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState(null)

  useEffect(() => {
    Promise.all([
      axios.get('/api/projections/retirement'),
      axios.get('/api/simulation/swr?ret_age=55&ss_timing=early'),
      axios.get('/api/simulation/swr?ret_age=60&ss_timing=early'),
      axios.get('/api/simulation/swr?ret_age=65&ss_timing=early'),
      axios.get('/api/planning-inputs'),
    ]).then(([ret, swr55, swr60, swr65, planningInputs]) => {
      setData(ret.data)
      setSwr({
        55: swr55.data,
        60: swr60.data,
        65: swr65.data,
      })
      setInputs(planningInputs.data)
      setLoading(false)
    }).catch(e => { setError(e.message); setLoading(false) })
  }, [])

  if (loading) return <div className="loading">Comparing scenarios...</div>
  if (error)   return <div className="card" style={{ color:'var(--red)' }}>Error: {error}</div>
  if (!data)   return null

  // Pull the three early SS scenarios
  const scenarios = [55, 60, 65].map(age => {
    const s = data.scenarios.find(s => s.label === `age_${age}_early`)
    return { age, ...s, swr: swr[age] }
  }).filter(Boolean)

  // Build portfolio timeline chart — keyed by calendar year so all three align on same age axis
  // Find the range of Jason ages across all scenarios
  const allAges = new Set()
  scenarios.forEach(s => {
    s.yearly_detail?.forEach(y => allAges.add(y.jason_age))
  })
  const sortedAges = Array.from(allAges).sort((a,b) => a-b).filter((_, i) => i % 2 === 0)
  const timelineData = sortedAges.map(jasonAge => {
    const point = { jasonAge }
    scenarios.forEach(s => {
      const y = s.yearly_detail?.find(y => y.jason_age === jasonAge)
      if (y) point[`age${s.age}`] = y.portfolio_balance
    })
    return point
  })

  // Build income sources comparison — show year 1 only (what's active at retirement)
  const incomeData = scenarios.map(s => {
    const jason_ss_age = 62  // always early SS on this page
    // Configurable via Settings (justin_ss_age) — read from the scenario
    // response instead of hardcoding 67, since a non-default value here
    // would otherwise silently mismatch what /api/projections/retirement
    // actually used to compute this scenario.
    const justin_ss_age = s.justin_ss_start_age ?? 67
    // Only include SS if already claimable at retirement age
    const jason_ss_yr1  = s.age >= jason_ss_age  ? (s.jason_ss_annual || 0) : 0
    const justin_ss_yr1 = s.age >= justin_ss_age ? (s.swr?.justin_ss_annual || 0) : 0
    const total = (s.pension_annual || 0) + jason_ss_yr1 + justin_ss_yr1 + (s.swr?.safe_withdrawal_annual || 0)
    return {
      age:          s.age,
      pension:      s.pension_annual,
      jason_ss:     jason_ss_yr1,
      jason_ss_note: s.age < jason_ss_age ? `starts age 62 (+${62 - s.age}yr)` : 'active',
      justin_ss:    justin_ss_yr1,
      justin_ss_note: s.age < justin_ss_age ? `starts age ${justin_ss_age} (+${justin_ss_age - s.age}yr)` : 'active',
      safe_draw:    s.swr?.safe_withdrawal_annual || 0,
      total,
    }
  })

  const tradeoffs = [
    {
      label: 'Years of Freedom Gained',
      values: { 55: '10 extra years vs 65', 60: '5 extra years vs 65', 65: 'baseline' },
      highlight: 55,
    },
    {
      label: 'Portfolio at Retirement',
      values: { 55: fmtK(scenarios[0]?.portfolio_at_retirement), 60: fmtK(scenarios[1]?.portfolio_at_retirement), 65: fmtK(scenarios[2]?.portfolio_at_retirement) },
      highlight: 65,
    },
    {
      label: 'Projected Surplus',
      values: { 55: fmtK(scenarios[0]?.projected_surplus), 60: fmtK(scenarios[1]?.projected_surplus), 65: fmtK(scenarios[2]?.projected_surplus) },
      highlight: 65,
    },
    {
      label: 'Safe Withdrawal Rate',
      values: { 55: pct(swr[55]?.safe_withdrawal_rate), 60: pct(swr[60]?.safe_withdrawal_rate), 65: pct(swr[65]?.safe_withdrawal_rate) },
      highlight: 65,
    },
    {
      label: 'Total Safe Spending/yr',
      values: { 55: fmtK(swr[55]?.total_safe_spend), 60: fmtK(swr[60]?.total_safe_spend), 65: fmtK(swr[65]?.total_safe_spend) },
      highlight: 65,
    },
    {
      label: 'SWR Cushion vs Target',
      values: {
        55: `${swr[55]?.cushion_pct > 0 ? '+' : ''}${swr[55]?.cushion_pct}%`,
        60: `${swr[60]?.cushion_pct > 0 ? '+' : ''}${swr[60]?.cushion_pct}%`,
        65: `${swr[65]?.cushion_pct > 0 ? '+' : ''}${swr[65]?.cushion_pct}%`,
      },
      highlight: 65,
    },
    {
      label: 'Healthcare Gap Years',
      values: { 55: '10 years pre-Medicare', 60: '5 years pre-Medicare', 65: '0 years (Medicare day 1)' },
      highlight: 60,
    },
    {
      label: 'SS Gap (no income)',
      values: { 55: '7 years before SS at 62', 60: '2 years before SS at 62', 65: 'SS starts immediately' },
      highlight: 65,
    },
    {
      label: 'Bridge Job Required',
      values: {
        55: `${fmt(inputs?.bridge_income_55)}/yr job age 55-60`,
        60: 'None needed', 65: 'None needed',
      },
      highlight: 60,
    },
    {
      label: 'Kids Still at Home',
      values: {
        55: (() => {
          if (!kids.length) return '—'
          const yrs = scenarios[0]?.years_to_retirement ?? 0
          const ages = kids.map(k => `${k.name} (${k.age + yrs})`).join(', ')
          return `Yes — ${ages} at retirement`
        })(),
        60: 'Launched by retirement', 65: 'Launched by retirement',
      },
      highlight: 60,
    },
    {
      label: 'Pension Amount',
      values: {
        55: `${fmt(scenarios[0]?.pension_annual)}/yr`,
        60: `${fmt(scenarios[1]?.pension_annual)}/yr`,
        65: `${fmt(scenarios[2]?.pension_annual)}/yr`,
      },
      highlight: 60,
    },
    {
      label: 'Rule of 55 Applies',
      values: { 55: '✓ 401k accessible penalty-free', 60: 'No (need age 59.5)', 65: 'No (need age 59.5)' },
      highlight: 55,
    },
  ]

  return (
    <div>
      <div style={{ marginBottom:28 }}>
        <p className="section-sub" style={{ margin:0 }}>Side by side — SS at 62 · All scenarios assume early Social Security</p>
      </div>

      {/* Summary cards */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:16, marginBottom:32 }}>
        {scenarios.map(s => {
          const sw = swr[s.age]
          const cushion = sw?.cushion_pct || 0
          const onTrack = cushion >= 0
          return (
            <div key={s.age} className="card" style={{ borderTop:`3px solid ${AGE_COLORS[s.age]}` }}>
              <div style={{ fontSize:24, fontWeight:800, color:AGE_COLORS[s.age], marginBottom:4 }}>
                Retire at {s.age}
              </div>
              <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>
                {s.age === 55 ? 'Bridge job + kids at home' : s.age === 60 ? 'Clean break, kids launched' : 'Maximum accumulation'}
              </div>

              <div style={{ fontSize:13, lineHeight:2 }}>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>Portfolio at retirement</span>
                  <span style={{ fontWeight:600 }}>{fmtK(s.portfolio_at_retirement)}</span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>Projected surplus</span>
                  <span style={{ fontWeight:600, color: s.projected_surplus >= 0 ? GREEN : RED }}>{fmtK(s.projected_surplus)}</span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>Safe total spend</span>
                  <span style={{ fontWeight:600 }}>{fmtK(sw?.total_safe_spend)}/yr</span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>SWR cushion</span>
                  <span style={{ fontWeight:600, color: onTrack ? GREEN : AMBER }}>
                    {cushion > 0 ? '+' : ''}{cushion}%
                  </span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>Pension</span>
                  <span>{fmt(s.pension_annual)}/yr</span>
                </div>
              </div>

              <div style={{ marginTop:12, padding:'8px 12px', borderRadius:6,
                background: onTrack ? 'rgba(52,211,153,0.1)' : 'rgba(251,191,36,0.1)',
                border: `1px solid ${onTrack ? GREEN : AMBER}`,
                fontSize:12, color: onTrack ? GREEN : AMBER, fontWeight:600 }}>
                {s.age === 55
                  ? onTrack ? '✓ Viable with bridge job' : '⚠ Tight — bridge job critical'
                  : onTrack ? '✓ Comfortable' : '⚠ Review needed'}
              </div>
            </div>
          )
        })}
      </div>

      {/* Portfolio trajectory chart */}
      <div className="card" style={{ marginBottom:24 }}>
        <div className="label" style={{ marginBottom:4 }}>Portfolio Balance Through Retirement</div>
        <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>
          Portfolio balance by {person1Name}'s age — each line starts at its retirement date and runs to age {scenarios[0]?.retirement_end_age ?? 99}
        </div>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={timelineData} margin={{ top:0, right:0, bottom:0, left:10 }}>
            <XAxis dataKey="jasonAge" tickFormatter={v => `Age ${v}`} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} />
            <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={60} />
            <Tooltip
              contentStyle={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, fontSize:11 }}
              formatter={(v, n) => [fmtK(v), n]}
              labelFormatter={l => `${person1Name} Age ${l}`}
            />
            <Legend wrapperStyle={{ fontSize:11 }} />
            <ReferenceLine y={0} stroke={RED} strokeDasharray="3 3" />
            <Line type="monotone" dataKey="age55" name="Retire at 55" stroke={AGE_COLORS[55]} strokeWidth={2.5} dot={false} />
            <Line type="monotone" dataKey="age60" name="Retire at 60" stroke={AGE_COLORS[60]} strokeWidth={2.5} dot={false} />
            <Line type="monotone" dataKey="age65" name="Retire at 65" stroke={AGE_COLORS[65]} strokeWidth={2.5} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Income sources comparison */}
      <div className="card" style={{ marginBottom:24 }}>
        <div className="label" style={{ marginBottom:16 }}>Income Sources at Retirement (Year 1)</div>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:16 }}>
          {incomeData.map(s => (
            <div key={s.age} style={{ background:'var(--bg3)', borderRadius:8, padding:'12px 16px' }}>
              <div style={{ fontWeight:700, color:AGE_COLORS[s.age], marginBottom:8, fontSize:13 }}>Age {s.age}</div>
              <div style={{ fontSize:12, lineHeight:1.9 }}>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>Pension</span>
                  <span>{fmt(s.pension)}/yr</span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>{person1Name} SS (at 62)</span>
                  <span style={{ color: s.jason_ss === 0 ? AMBER : 'inherit' }}>
                    {s.jason_ss === 0 ? s.jason_ss_note : `${fmt(s.jason_ss)}/yr`}
                  </span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>{person2Name} spousal SS</span>
                  <span style={{ color: s.justin_ss === 0 ? AMBER : 'inherit' }}>
                    {s.justin_ss === 0 ? s.justin_ss_note : `${fmt(s.justin_ss)}/yr`}
                  </span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>Safe portfolio draw</span>
                  <span style={{ color:ACCENT }}>{fmt(s.safe_draw)}/yr</span>
                </div>
                <div style={{ height:1, background:'var(--border)', margin:'4px 0' }} />
                <div style={{ display:'flex', justifyContent:'space-between', fontWeight:700 }}>
                  <span>Total</span>
                  <span style={{ color:GREEN }}>{fmt(s.total)}/yr</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Tradeoff table */}
      <div className="card" style={{ marginBottom:24 }}>
        <div className="label" style={{ marginBottom:16 }}>Full Tradeoff Comparison</div>
        <div style={{ overflowX:'auto' }}>
          <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
            <thead>
              <tr style={{ borderBottom:'1px solid var(--border)' }}>
                <th style={{ textAlign:'left', padding:'8px 12px', color:'var(--text2)', fontWeight:500 }}>Factor</th>
                {[55, 60, 65].map(age => (
                  <th key={age} style={{ textAlign:'center', padding:'8px 12px', color:AGE_COLORS[age], fontWeight:700 }}>
                    Retire {age}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tradeoffs.map((row, i) => (
                <tr key={row.label} style={{ background: i%2===0 ? 'transparent' : 'var(--bg3)', borderBottom:'1px solid var(--border)' }}>
                  <td style={{ padding:'7px 12px', fontWeight:500 }}>{row.label}</td>
                  {[55, 60, 65].map(age => (
                    <td key={age} style={{
                      textAlign:'center', padding:'7px 12px',
                      fontWeight: row.highlight === age ? 700 : 400,
                      color: row.highlight === age ? AGE_COLORS[age] : 'var(--text)',
                    }}>
                      {row.values[age]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Bottom line */}
      <div className="card" style={{ background:'var(--bg3)' }}>
        <div className="label" style={{ marginBottom:12 }}>The Core Tradeoff</div>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:16, fontSize:12, lineHeight:1.7 }}>
          <div>
            <div style={{ fontWeight:700, color:AGE_COLORS[55], marginBottom:6 }}>Retire at 55</div>
            <div style={{ color:'var(--text2)' }}>
              10 extra years of freedom but requires bridge job, kids costs, and tight cash flow early.
              Portfolio is smallest but Rule of 55 gives immediate 401k access.
              Viable but leaves least margin for error.
            </div>
          </div>
          <div>
            <div style={{ fontWeight:700, color:AGE_COLORS[60], marginBottom:6 }}>Retire at 60</div>
            <div style={{ color:'var(--text2)' }}>
              Clean break — kids launched, no bridge job needed, pension jumps to {fmt(scenarios[1]?.pension_annual)}.
              {swr[60]?.cushion_pct != null && ` ${swr[60].cushion_pct > 0 ? '+' : ''}${swr[60].cushion_pct}% cushion above spending target gives real flexibility.`}
              Best balance of freedom and financial security.
            </div>
          </div>
          <div>
            <div style={{ fontWeight:700, color:AGE_COLORS[65], marginBottom:6 }}>Retire at 65</div>
            <div style={{ color:'var(--text2)' }}>
              Maximum accumulation and Medicare on day one.
              Surplus is largest but trades 10 years of retirement years vs age 55.
              Only meaningful if health or enjoyment requires working longer.
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
