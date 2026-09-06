import { useState, useEffect } from 'react'
import axios from 'axios'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer
} from 'recharts'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'
import { useScenario } from '../hooks/useScenario'

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(n))
const fmtK = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : Math.abs(n)>=1000000?`$${(n/1000000).toFixed(2)}M`:`$${(n/1000).toFixed(0)}K`)

const GREEN  = '#34d399'
const AMBER  = '#fbbf24'
const RED    = '#f87171'
const ACCENT = '#4f9cf9'

// Same retirement-age scenarios as WhatIf.jsx / RetirementSensitivity.jsx —
// this page reads the actual numbers for each from the backend (Settings +
// Accounts), same source of truth as the rest of the app, rather than a
// separate client-side calculator you'd have to re-enter figures into.
const RET_AGES = [
  { value:55, label:'Retire 55' },
  { value:60, label:'Retire 60' },
  { value:65, label:'Retire 65' },
]
// 'early'/'delayed' match the backend's canonical scenario-label values
// (see projection_engine.py) — same values Retirement.jsx and
// Simulation.jsx already send. A prior version of this page used 'late',
// which doesn't match any scenario label and 500'd the endpoint.
const SS_TIMINGS = [
  { value:'early',   label:'SS at 62' },
  { value:'delayed', label:'SS at 67' },
]

const ChartTip = ({ active, payload, label }) => {
  if (!active||!payload?.length) return null
  return (
    <div style={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, padding:'10px 14px', fontSize:11 }}>
      <div style={{ fontWeight:600, marginBottom:6 }}>Age {label}</div>
      {payload.map(p => p.value > 0 && (
        <div key={p.name} style={{ color:p.color, marginBottom:2 }}>{p.name}: {fmtK(p.value)}</div>
      ))}
    </div>
  )
}

export default function RothConversion() {
  const { retAge, ssTiming, setRetAge, setSsTiming } = useScenario()
  const [data, setData]         = useState(null)
  const [loading, setLoading]   = useState(true)

  useEffect(() => {
    setLoading(true)
    axios.get(`/api/simulation/roth-conversion?ret_age=${retAge}&ss_timing=${ssTiming}`)
      .then(r => setData(r.data))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [retAge, ssTiming])

  return (
    <div>
      <div style={{ marginBottom:28 }}>
        <h1 className="section-title">Roth Conversion Ladder</h1>
        <p className="section-sub">Optimal pre-tax to Roth conversions before RMDs start at 73 — computed from your real balances and Settings, no re-entry</p>
      </div>

      {/* Why this matters callout */}
      <div style={{ padding:'14px 16px', background:'rgba(79,156,249,0.06)', border:'1px solid rgba(79,156,249,0.2)', borderRadius:8, marginBottom:24, fontSize:13 }}>
        <div style={{ fontWeight:600, marginBottom:6, color:'var(--accent)' }}>💡 The Opportunity</div>
        <div style={{ color:'var(--text2)', lineHeight:1.7 }}>
          Between retirement and age 73, the gap between your guaranteed income (pension + Social Security) and the top of the
          22% bracket is room to convert pre-tax dollars to Roth cheaply. At 73, forced RMDs push that same money into ordinary
          income whether you want it or not — usually at a higher bracket. <b style={{ color:'var(--text)' }}>Converting into the gap now saves the spread.</b>
        </div>
      </div>

      {/* Scenario controls */}
      <div className="card" style={{ marginBottom:24 }}>
        <div className="grid-2">
          <div>
            <div className="label" style={{ marginBottom:8 }}>Retirement Age</div>
            <div style={{ display:'flex', gap:8 }}>
              {RET_AGES.map(o => (
                <button key={o.value} className={o.value === retAge ? 'btn-primary' : 'btn-secondary'} onClick={() => setRetAge(o.value)}>{o.label}</button>
              ))}
            </div>
          </div>
          <div>
            <div className="label" style={{ marginBottom:8 }}>Social Security Timing</div>
            <div style={{ display:'flex', gap:8 }}>
              {SS_TIMINGS.map(o => (
                <button key={o.value} className={o.value === ssTiming ? 'btn-primary' : 'btn-secondary'} onClick={() => setSsTiming(o.value)}>{o.label}</button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {loading && <div className="loading">Calculating conversion ladder...</div>}

      {!loading && !data && (
        <div className="card" style={{ fontSize:13, color:'var(--text2)' }}>
          Couldn't calculate a conversion ladder — make sure Planning Inputs are set in Settings.
        </div>
      )}

      {!loading && data && (
        <>
          {/* Summary */}
          <div className="card" style={{ marginBottom:24, borderTop:`3px solid ${ACCENT}` }}>
            <div className="label" style={{ marginBottom:16 }}>Summary — Ages {retAge}–{data.rmd_start_age - 1} ({data.conversion_years} years)</div>
            <div className="grid-4">
              <div>
                <div className="label">Total Converted</div>
                <div style={{ fontSize:18, fontWeight:600, marginTop:4, color:ACCENT }}>{fmtK(data.total_conversions)}</div>
              </div>
              <div>
                <div className="label">Tax Paid on Conversions</div>
                <div style={{ fontSize:18, fontWeight:600, marginTop:4, color:AMBER }}>{fmt(data.total_tax_cost)}</div>
              </div>
              <div>
                <div className="label">RMD Tax Avoided (est.)</div>
                <div style={{ fontSize:18, fontWeight:600, marginTop:4, color:GREEN }}>{fmt(data.total_tax_avoided)}</div>
              </div>
              <div>
                <div className="label">Net Lifetime Benefit</div>
                <div style={{ fontSize:18, fontWeight:600, marginTop:4, color: data.net_lifetime_benefit > 0 ? GREEN : RED }}>{fmt(data.net_lifetime_benefit)}</div>
              </div>
            </div>
            <div style={{ marginTop:12, fontSize:11, color:'var(--text3)', fontStyle:'italic' }}>
              Estimate assumes a 24% marginal rate on avoided RMDs and fills the 22% bracket each year. Consult a tax advisor before converting.
            </div>
          </div>

          {/* Pretax balance at RMD age: with vs without conversions */}
          <div className="card" style={{ marginBottom:24 }}>
            <div className="label" style={{ marginBottom:16 }}>Pre-Tax Balance at Age {data.rmd_start_age}</div>
            <div className="grid-2">
              <div>
                <div className="label">Without Conversions</div>
                <div style={{ fontSize:18, fontWeight:600, marginTop:4, color:RED }}>{fmt(data.pretax_at_rmd_age_no_conversion)}</div>
                <div style={{ fontSize:11, color:'var(--text3)', marginTop:2 }}>RMD ≈ {fmt(data.estimated_rmd_without_conversions)}/yr</div>
              </div>
              <div>
                <div className="label">With Conversions</div>
                <div style={{ fontSize:18, fontWeight:600, marginTop:4, color:GREEN }}>{fmt(data.pretax_at_rmd_age_with_conversion)}</div>
                <div style={{ fontSize:11, color:'var(--text3)', marginTop:2 }}>RMD ≈ {fmt(data.estimated_rmd_with_conversions)}/yr</div>
              </div>
            </div>
          </div>

          {/* Conversion schedule chart */}
          <div className="card" style={{ marginBottom:24 }}>
            <div className="label" style={{ marginBottom:4 }}>Annual Conversion Amount</div>
            <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>Blue = amount converted · Amber = tax cost that year</div>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={data.schedule} margin={{top:0,right:0,bottom:0,left:0}}>
                <XAxis dataKey="age" tick={{fill:'var(--text3)',fontSize:10}} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={fmtK} tick={{fill:'var(--text3)',fontSize:10}} axisLine={false} tickLine={false} width={56} />
                <Tooltip content={<ChartTip />} />
                <Legend wrapperStyle={{fontSize:10}} />
                <Bar dataKey="optimal_conversion" name="Convert Amount" fill={ACCENT} stackId="a" />
                <Bar dataKey="tax_cost"           name="Tax Cost"       fill={AMBER}  stackId="a" />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Year by year table */}
          <div className="card">
            <div className="label" style={{ marginBottom:12 }}>Year-by-Year Conversion Schedule</div>
            <div style={{ overflowX:'auto' }}>
              <table style={{ width:'100%', borderCollapse:'collapse', fontSize:11 }}>
                <thead><tr style={{ borderBottom:'2px solid var(--border)' }}>
                  {['Age','Taxable Income','Room in 22%','Convert','Tax Cost','Rate','Pre-tax Bal'].map(h=>(
                    <th key={h} style={{ padding:'5px 7px', textAlign:'right', color:'var(--text3)', fontWeight:600, fontSize:10, textTransform:'uppercase' }}>{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {data.schedule.map((r,i) => {
                    const rate = r.optimal_conversion > 0 ? (r.tax_cost / r.optimal_conversion * 100) : 0
                    return (
                      <tr key={r.age} style={{ background:i%2===0?'transparent':'var(--bg3)', borderBottom:'1px solid var(--border)' }}>
                        <td style={{ padding:'5px 7px', textAlign:'right', fontWeight:600 }}>{r.age}</td>
                        <td style={{ padding:'5px 7px', textAlign:'right' }}>{fmt(r.base_taxable_income)}</td>
                        <td style={{ padding:'5px 7px', textAlign:'right' }}>{fmt(r.room_in_22_bracket)}</td>
                        <td style={{ padding:'5px 7px', textAlign:'right', fontWeight:600, color:ACCENT }}>{fmt(r.optimal_conversion)}</td>
                        <td style={{ padding:'5px 7px', textAlign:'right', color:AMBER }}>{fmt(r.tax_cost)}</td>
                        <td style={{ padding:'5px 7px', textAlign:'right', color: rate <= 22 ? GREEN : AMBER }}>{rate.toFixed(1)}%</td>
                        <td style={{ padding:'5px 7px', textAlign:'right' }}>{fmtK(r.pretax_balance)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
