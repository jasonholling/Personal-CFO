import { useState, useEffect } from 'react'
import axios from 'axios'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { usePersonNames } from '../hooks/usePersonNames'
import { useScenario } from '../hooks/useScenario'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(n))
const fmtK = (n) => {
  if (isPrivacyMode()) return MASK_CURRENCY
  if (n == null) return '—'
  if (Math.abs(n) >= 1_000_000) return `$${(n/1_000_000).toFixed(1)}M`
  return `$${(n/1000).toFixed(0)}K`
}
// cushion % has no natural bound (surplus / cap_need — cap_need can shrink
// toward $0 at late retirement ages where pension+SS cover nearly all
// income need, producing something like "4,800%"). Clamp the display, not
// the underlying stoplight math.
const fmtPct = (n) => n == null ? '—' : Math.abs(n) > 999 ? `${n > 0 ? '>' : '<'}999%` : `${n}%`

const GREEN  = '#34d399'
const AMBER  = '#fbbf24'
const RED    = '#f87171'
const ACCENT = '#4f9cf9'
const ORANGE = '#f97316'

const getStoplight = (s, swrCushion) => {
  if (!s) return { color: 'var(--text3)', label: 'Unknown', icon: '?' }
  // Use SWR cushion if available (matches Side by Side page), else fall back to surplus %
  const cushionPct = swrCushion !== undefined ? swrCushion : (s.cap_need > 0 ? ((s.surplus / s.cap_need) * 100) : 0)
  if (cushionPct >= 20)  return { color: GREEN, label: 'Comfortable', icon: 'OK' }
  if (cushionPct >= 0)   return { color: AMBER, label: s.ret_age === 55 ? 'Viable' : 'Tight', icon: '!' }
  return { color: RED, label: 'Fails', icon: 'X' }
}

export default function RetirementSensitivity({ onNavigate }) {
  const { person1Name, person2Name } = usePersonNames()
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const { retAge: selAge, setRetAge: setSelAge } = useScenario()

  const [swrData, setSwrData] = useState({})

  useEffect(() => {
    Promise.all([
      axios.get('/api/projections/retirement-sensitivity'),
      axios.get('/api/simulation/swr-batch'),
    ]).then(([sens, swr]) => {
      setData(sens.data)
      setSwrData(swr.data.swr || {})
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="loading">Computing all retirement ages...</div>
  if (!data)   return <div className="card">Failed to load</div>

  const { sensitivity, jason_current_age } = data
  const selected = sensitivity.find(s => s.ret_age === selAge) || sensitivity[0]

  // Chart data — all ages for the line charts
  const chartData = sensitivity.map(s => {
    const sw = swrData[String(s.ret_age)] || swrData[s.ret_age]
    return {
      age:       s.ret_age,
      portfolio: s.portfolio,
      surplus:   s.surplus,
      monthly:   sw ? Math.round(sw.total_safe_spend/12) : null,
      pension:   s.pension_annual,
    }
  })

  const cushionPct = selected ? Math.round((selected.surplus / selected.cap_need) * 100) : 0

  return (
    <div>
      <div style={{ marginBottom:28 }}>
        <p className="section-sub" style={{ margin:0 }}>Drag the slider to see how every retirement age from 55 to 67 changes your outcomes — SS at 62</p>
      </div>

      {/* Slider */}
      <div className="card" style={{ marginBottom:24 }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
          <div style={{ fontSize:13, color:'var(--text2)' }}>Retirement Age</div>
          <div style={{ fontSize:32, fontWeight:800, color:ACCENT }}>Age {selAge}</div>
          <div style={{ fontSize:13, color:'var(--text2)' }}>{selAge - jason_current_age} years from now</div>
        </div>
        <input
          type="range" min={55} max={67} step={1}
          value={selAge}
          onChange={e => setSelAge(Number(e.target.value))}
          style={{ width:'100%', accentColor:ACCENT, cursor:'pointer', height:6 }}
        />
        <div style={{ display:'flex', justifyContent:'space-between', fontSize:11, color:'var(--text3)', marginTop:4 }}>
          {Array.from({length:13}, (_,i) => i+55).map(age => (
            <span key={age} style={{ fontWeight: age===selAge ? 700 : 400, color: age===selAge ? ACCENT : 'var(--text3)' }}>
              {age}
            </span>
          ))}
        </div>
      </div>

      {/* Selected age summary cards */}
      {selected && (() => {
        const selectedSwr = swrData[String(selAge)] || swrData[selAge]
        const light = getStoplight(selected, selectedSwr?.cushion_pct)
        return (
        <div style={{ display:'grid', gridTemplateColumns:'repeat(5, 1fr)', gap:16, marginBottom:24 }}>
          <div className="card" style={{ borderTop:`3px solid ${light.color}`, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center' }}>
            <div style={{ fontSize:40, marginBottom:4 }}>
              <span style={{ color: light.color }}>{light.icon}</span>
            </div>
            <div style={{ fontSize:15, fontWeight:700, color:light.color }}>{light.label}</div>
            <div style={{ fontSize:11, color:'var(--text2)', marginTop:4 }}>
              {selectedSwr ? `${fmtPct(selectedSwr.cushion_pct)} SWR cushion` : `${fmtPct(Math.round(selected.cap_need > 0 ? (selected.surplus/selected.cap_need)*100 : 0))} cushion`}
            </div>
          </div>
          <div className="card" style={{ borderTop:`3px solid ${ACCENT}` }}>
            <div className="label">Portfolio at Retirement</div>
            <div style={{ fontSize:24, fontWeight:800, color:ACCENT, marginTop:6 }}>{fmtK(selected.portfolio)}</div>
            <div style={{ fontSize:11, color:'var(--text2)', marginTop:2 }}>at age {selAge}</div>
          </div>
          <div className="card" style={{ borderTop:`3px solid ${GREEN}` }}>
            <div className="label">Projected Surplus</div>
            <div style={{ fontSize:24, fontWeight:800, color:GREEN, marginTop:6 }}>{fmtK(selected.surplus)}</div>
            <div style={{ fontSize:11, color:'var(--text2)', marginTop:2 }}>above what assets need to cover</div>
          </div>
          <div className="card" style={{ borderTop:`3px solid ${ORANGE}` }}>
            <div className="label">Monthly Income Yr 1</div>
            <div style={{ fontSize:24, fontWeight:800, color:ORANGE, marginTop:6 }}>{fmt(selectedSwr ? selectedSwr.total_safe_spend/12 : 0)}/mo</div>
            <div style={{ fontSize:11, color:'var(--text2)', marginTop:2 }}>
              {selAge < 62 ? `SS starts at 62 (+${62-selAge}yr)` : 'SS active'}
            </div>
          </div>
          <div className="card" style={{ borderTop:`3px solid ${AMBER}` }}>
            <div className="label">Pension</div>
            <div style={{ fontSize:24, fontWeight:800, color:AMBER, marginTop:6 }}>{fmt(selected.pension_annual)}/yr</div>
            <div style={{ fontSize:11, color:'var(--text2)', marginTop:2 }}>
              {selected.healthcare_gap_yrs > 0 ? `${selected.healthcare_gap_yrs}yr healthcare gap` : 'Medicare day 1'}
            </div>
          </div>
        </div>
        )
      })()}

      {/* Key inflection points */}
      <div className="card" style={{ marginBottom:24, padding:'12px 20px' }}>
        <div style={{ display:'flex', gap:24, flexWrap:'wrap', fontSize:12 }}>
          <div style={{ display:'flex', alignItems:'center', gap:6 }}>
            <div style={{ width:10, height:10, borderRadius:'50%', background: selAge >= 55 ? GREEN : 'var(--border)' }} />
            <span style={{ color: selAge >= 55 ? GREEN : 'var(--text3)' }}>Age 55 — Rule of 55 (401k access)</span>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:6 }}>
            <div style={{ width:10, height:10, borderRadius:'50%', background: selAge >= 60 ? GREEN : 'var(--border)' }} />
            <span style={{ color: selAge >= 60 ? GREEN : 'var(--text3)' }}>Age 60 — Pension jumps to $38k</span>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:6 }}>
            <div style={{ width:10, height:10, borderRadius:'50%', background: selAge >= 62 ? GREEN : 'var(--border)' }} />
            <span style={{ color: selAge >= 62 ? GREEN : 'var(--text3)' }}>Age 62 — SS starts</span>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:6 }}>
            <div style={{ width:10, height:10, borderRadius:'50%', background: selAge >= 65 ? GREEN : 'var(--border)' }} />
            <span style={{ color: selAge >= 65 ? GREEN : 'var(--text3)' }}>Age 65 — Medicare (no ACA gap)</span>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:6 }}>
            <div style={{ width:10, height:10, borderRadius:'50%', background: selAge >= 67 ? GREEN : 'var(--border)' }} />
            <span style={{ color: selAge >= 67 ? GREEN : 'var(--text3)' }}>Age 67 — {person2Name} SS starts</span>
          </div>
        </div>
      </div>

      {/* Charts */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:24, marginBottom:24 }}>
        <div className="card">
          <div className="label" style={{ marginBottom:16 }}>Portfolio at Retirement by Age</div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={chartData} margin={{ top:0, right:0, bottom:0, left:10 }}>
              <XAxis dataKey="age" tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={55} />
              <Tooltip
                contentStyle={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, fontSize:11 }}
                formatter={(v) => [fmtK(v), 'Portfolio']}
                labelFormatter={l => `Retire at ${l}`}
              />
              <ReferenceLine x={selAge} stroke={ACCENT} strokeDasharray="4 2" strokeWidth={2} />
              <Line type="monotone" dataKey="portfolio" stroke={ACCENT} strokeWidth={2.5} dot={(p) => p.payload.age === selAge ? <circle key={p.key} cx={p.cx} cy={p.cy} r={5} fill={ACCENT} /> : null} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <div className="label" style={{ marginBottom:16 }}>Projected Surplus by Age</div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={chartData} margin={{ top:0, right:0, bottom:0, left:10 }}>
              <XAxis dataKey="age" tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={55} />
              <Tooltip
                contentStyle={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, fontSize:11 }}
                formatter={(v) => [fmtK(v), 'Surplus']}
                labelFormatter={l => `Retire at ${l}`}
              />
              <ReferenceLine x={selAge} stroke={GREEN} strokeDasharray="4 2" strokeWidth={2} />
              <Line type="monotone" dataKey="surplus" stroke={GREEN} strokeWidth={2.5} dot={(p) => p.payload.age === selAge ? <circle key={p.key} cx={p.cx} cy={p.cy} r={5} fill={GREEN} /> : null} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <div className="label" style={{ marginBottom:16 }}>Monthly Income Year 1 by Retirement Age</div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={chartData} margin={{ top:0, right:0, bottom:0, left:10 }}>
              <XAxis dataKey="age" tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={55} />
              <Tooltip
                contentStyle={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, fontSize:11 }}
                formatter={(v) => [fmt(v)+'/mo', 'Monthly Income']}
                labelFormatter={l => `Retire at ${l}`}
              />
              <ReferenceLine x={selAge} stroke={ORANGE} strokeDasharray="4 2" strokeWidth={2} />
              <ReferenceLine y={10000} stroke="var(--text3)" strokeDasharray="3 3" label={{ value:'$10k/mo target', fill:'var(--text3)', fontSize:10 }} />
              <Line type="monotone" dataKey="monthly" stroke={ORANGE} strokeWidth={2.5} dot={(p) => p.payload.age === selAge ? <circle key={p.key} cx={p.cx} cy={p.cy} r={5} fill={ORANGE} /> : null} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <div className="label" style={{ marginBottom:16 }}>Pension by Retirement Age</div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={chartData} margin={{ top:0, right:0, bottom:0, left:10 }}>
              <XAxis dataKey="age" tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={55} />
              <Tooltip
                contentStyle={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, fontSize:11 }}
                formatter={(v) => [fmt(v)+'/yr', 'Pension']}
                labelFormatter={l => `Retire at ${l}`}
              />
              <ReferenceLine x={selAge} stroke={AMBER} strokeDasharray="4 2" strokeWidth={2} />
              <Line type="monotone" dataKey="pension" stroke={AMBER} strokeWidth={2.5} dot={(p) => p.payload.age === selAge ? <circle key={p.key} cx={p.cx} cy={p.cy} r={5} fill={AMBER} /> : null} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Full data table */}
      <div className="card">
        <div className="label" style={{ marginBottom:16 }}>All Ages Summary</div>
        <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
          <thead>
            <tr style={{ borderBottom:'1px solid var(--border)' }}>
              {['Age','Years Away','Portfolio','Surplus','Monthly Yr1','Pension','HC Gap','SS Active'].map(h => (
                <th key={h} style={{ textAlign:'right', padding:'6px 10px', color:'var(--text2)', fontWeight:500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sensitivity.map((s, i) => (
              <tr
                key={s.ret_age}
                onClick={() => setSelAge(s.ret_age)}
                style={{
                  background: s.ret_age === selAge ? 'rgba(79,156,249,0.1)' : i%2===0 ? 'transparent' : 'var(--bg3)',
                  borderBottom:'1px solid var(--border)',
                  cursor:'pointer',
                  borderLeft: s.ret_age === selAge ? `3px solid ${ACCENT}` : '3px solid transparent',
                }}
              >
                <td style={{ textAlign:'right', padding:'6px 10px', fontWeight: s.ret_age===selAge ? 700 : 400, color: s.ret_age===selAge ? ACCENT : 'inherit' }}>
                  {s.ret_age}
                  {(() => { const sw = swrData[String(s.ret_age)] || swrData[s.ret_age]; const l = getStoplight(s, sw?.cushion_pct); return <span style={{ marginLeft:6, color:l.color, fontSize:10 }}>{l.icon}</span> })()}
                </td>
                <td style={{ textAlign:'right', padding:'6px 10px', color:'var(--text2)' }}>{s.years_to_retire}yr</td>
                <td style={{ textAlign:'right', padding:'6px 10px' }}>{fmtK(s.portfolio)}</td>
                <td style={{ textAlign:'right', padding:'6px 10px', color:GREEN }}>{fmtK(s.surplus)}</td>
                <td style={{ textAlign:'right', padding:'6px 10px', color:ORANGE }}>
                  {(() => { const sw = swrData[String(s.ret_age)] || swrData[s.ret_age]; return fmt(sw ? sw.total_safe_spend/12 : 0) })()}
                </td>
                <td style={{ textAlign:'right', padding:'6px 10px', color:AMBER }}>{fmt(s.pension_annual)}/yr</td>
                <td style={{ textAlign:'right', padding:'6px 10px', color: s.healthcare_gap_yrs > 0 ? AMBER : GREEN }}>
                  {s.healthcare_gap_yrs > 0 ? `${s.healthcare_gap_yrs}yr gap` : '✓ Medicare'}
                </td>
                <td style={{ textAlign:'right', padding:'6px 10px', color: s.ret_age >= 62 ? GREEN : 'var(--text3)' }}>
                  {s.ret_age >= 67 ? '✓ Both' : s.ret_age >= 62 ? `✓ ${person1Name}` : `62 (+${62-s.ret_age}yr)`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
