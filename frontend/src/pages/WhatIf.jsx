import { useState, useEffect, useCallback } from 'react'
import axios from 'axios'
import { useScenario } from '../hooks/useScenario'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(n))
const fmtK = (n) => {
  if (isPrivacyMode()) return MASK_CURRENCY
  if (n == null) return '—'
  if (Math.abs(n) >= 1_000_000) return `$${(n/1_000_000).toFixed(1)}M`
  return `$${(n/1000).toFixed(0)}K`
}
const fmtDelta = (n) => {
  if (isPrivacyMode()) return MASK_CURRENCY
  if (n == null) return '—'
  const s = n >= 0 ? '+' : ''
  if (Math.abs(n) >= 1_000_000) return `${s}$${(n/1_000_000).toFixed(1)}M`
  return `${s}$${(n/1000).toFixed(0)}K`
}

const GREEN  = '#34d399'
const AMBER  = '#fbbf24'
const RED    = '#f87171'
const ACCENT = '#4f9cf9'

const SCENARIOS = [
  { id:'age_55_early', label:'Retire 55', color:'#f97316' },
  { id:'age_60_early', label:'Retire 60', color:'#4f9cf9' },
  { id:'age_65_early', label:'Retire 65', color:'#34d399' },
]

function SliderRow({ label, hint, value, min, max, step, format, onChange, delta }) {
  const deltaColor = delta > 0 ? GREEN : delta < 0 ? RED : 'var(--text3)'
  return (
    <div style={{ marginBottom:20 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'baseline', marginBottom:4 }}>
        <div>
          <span style={{ fontSize:13, fontWeight:500 }}>{label}</span>
          {hint && <span style={{ fontSize:11, color:'var(--text3)', marginLeft:8 }}>{hint}</span>}
        </div>
        <div style={{ display:'flex', gap:16, alignItems:'baseline' }}>
          <span style={{ fontSize:15, fontWeight:700, color:ACCENT }}>{format(value)}</span>
          {delta !== undefined && delta !== 0 && (
            <span style={{ fontSize:12, fontWeight:600, color:deltaColor }}>
              {fmtDelta(delta)} surplus
            </span>
          )}
        </div>
      </div>
      <input
        type="range" min={min} max={max} step={step}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        style={{ width:'100%', accentColor:ACCENT, cursor:'pointer' }}
      />
      <div style={{ display:'flex', justifyContent:'space-between', fontSize:10, color:'var(--text3)', marginTop:2 }}>
        <span>{format(min)}</span>
        <span>{format(max)}</span>
      </div>
    </div>
  )
}

export default function WhatIf({ onNavigate }) {
  const [base, setBase]       = useState(null)
  const [result, setResult]   = useState(null)
  const [loading, setLoading] = useState(true)
  const [computing, setComputing] = useState(false)

  // Toggleable assumptions
  const { retAge, setRetAge } = useScenario()
  const [salaryGrowthPct, setSalaryGrowthPct] = useState(0.0) // %/yr, compounds until your retirement age
  const [preReturn,   setPreReturn]   = useState(7.0)
  const [postReturn,  setPostReturn]  = useState(6.0)
  const [inflation,   setInflation]   = useState(2.0)
  const [pensionMult, setPensionMult] = useState(1.0)
  const [ssMult,      setSsMult]      = useState(1.0)
  const [healthcare,  setHealthcare]  = useState(24000)
  const [income,      setIncome]      = useState(120000)
  const [bridgeIncome, setBridgeIncome] = useState(45000)

  // Pre-fill the assumption sliders from your real Settings instead of
  // generic hardcoded defaults — still editable, this is a what-if tool.
  // Reusable (not just on mount) so "Use My Current Settings" below can
  // re-sync to your real numbers on demand, e.g. after fiddling with the
  // sliders, without wiping the what-if toggles (salary growth, pension/SS
  // multipliers, retirement age) that don't have a "current" value to
  // pull — those are pure hypotheticals, left as you set them.
  const pullCurrentSettings = () => {
    axios.get('/api/planning-inputs').then(r => {
      const d = r.data
      // Rounded to the slider's own precision (1 decimal, step 0.5) — a
      // bare *100 on a stored fraction like 0.07 lands on 7.000000000000001
      // in floating point, which then rendered raw ("pre-ret return
      // 7.000000000000001%") in the "Changes from base" summary below.
      if (d.expected_return_pre_retirement)  setPreReturn(Math.round(d.expected_return_pre_retirement * 1000) / 10)
      if (d.expected_return_post_retirement) setPostReturn(Math.round(d.expected_return_post_retirement * 1000) / 10)
      if (d.inflation_rate)                  setInflation(Math.round(d.inflation_rate * 1000) / 10)
      if (d.retirement_income_today_dollars) setIncome(d.retirement_income_today_dollars)
      if (d.healthcare_pre_medicare)         setHealthcare(d.healthcare_pre_medicare)
      if (d.bridge_income_55)                setBridgeIncome(d.bridge_income_55)
    }).catch(() => {})
  }

  // Load base projections on mount
  useEffect(() => {
    axios.get('/api/projections/retirement')
      .then(r => {
        setBase(r.data)
        setResult(r.data)
        setLoading(false)
      })
      .catch(() => setLoading(false))
    pullCurrentSettings()
  }, [])

  // Recompute when any assumption changes
  const recompute = useCallback(() => {
    setComputing(true)
    axios.post('/api/projections/whatif', {
      ret_age:      retAge,
      salary_growth_pct: salaryGrowthPct / 100,
      pre_return:   preReturn / 100,
      post_return:  postReturn / 100,
      inflation:    inflation / 100,
      pension_mult: pensionMult,
      ss_mult:      ssMult,
      healthcare_pre: healthcare,
      income_target:  income,
      bridge_income:  bridgeIncome,
    }).then(r => {
      setResult(r.data)
      setComputing(false)
    }).catch(() => setComputing(false))
  }, [retAge, salaryGrowthPct, preReturn, postReturn, inflation, pensionMult, ssMult, healthcare, income, bridgeIncome])

  useEffect(() => {
    if (!loading) recompute()
  }, [retAge, salaryGrowthPct, preReturn, postReturn, inflation, pensionMult, ssMult, healthcare, income, bridgeIncome])

  const reset = () => {
    setRetAge(60); setSalaryGrowthPct(0.0); setPreReturn(7.0); setPostReturn(6.0)
    setInflation(2.0); setPensionMult(1.0); setSsMult(1.0)
    setHealthcare(24000); setIncome(120000); setBridgeIncome(45000)
  }

  const getSurplusDelta = (scenarioId) => {
    if (!base || !result) return 0
    const b = base.scenarios?.find(s => s.label === scenarioId)
    const r = result.scenarios?.find(s => s.label === scenarioId)
    if (!b || !r) return 0
    return r.projected_surplus - b.projected_surplus
  }

  const isChanged = salaryGrowthPct !== 0.0 || preReturn !== 7.0 || postReturn !== 6.0 ||
    inflation !== 2.0 || pensionMult !== 1.0 || ssMult !== 1.0 ||
    healthcare !== 24000 || income !== 120000 || bridgeIncome !== 45000

  if (loading) return <div className="loading">Loading base projections...</div>

  return (
    <div>
      {/* No page header here — this component is nested inside
          StressTestWhatIf.jsx's "What-If Builder" tab, which owns the
          page title. */}
      <div style={{ marginBottom:20, display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <p className="section-sub" style={{ margin:0 }}>Adjust any assumption and see the impact on all three retirement ages in real time</p>
        <div style={{ display:'flex', gap:8 }}>
          <button className="btn-secondary" onClick={pullCurrentSettings} style={{ fontSize:12, padding:'8px 16px' }}>
            Use My Current Settings
          </button>
          {isChanged && (
            <button className="btn-secondary" onClick={reset} style={{ fontSize:12, padding:'8px 16px' }}>
              Reset to Base
            </button>
          )}
        </div>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'380px 1fr', gap:24 }}>

        {/* Left: controls */}
        <div>
          <div className="card" style={{ marginBottom:16 }}>
            <div className="label" style={{ marginBottom:16 }}>Retirement & Income</div>

            <div style={{ marginBottom:16 }}>
              <div style={{ fontSize:13, fontWeight:500, marginBottom:6 }}>Retirement Age</div>
              <div style={{ display:'flex', flexWrap:'wrap', gap:8 }}>
                {[55,56,57,58,59,60,61,62,63,64,65,66,67].map(age => (
                  <button key={age}
                    onClick={() => setRetAge(age)}
                    style={{
                      padding:'4px 8px', fontSize:11, borderRadius:4, cursor:'pointer',
                      background: retAge===age ? ACCENT : 'var(--bg3)',
                      color: retAge===age ? '#fff' : 'var(--text2)',
                      border: `1px solid ${retAge===age ? ACCENT : 'var(--border)'}`,
                    }}
                  >{age}</button>
                ))}
              </div>
            </div>

            <SliderRow label="Income Target" hint="today's dollars"
              value={income} min={80000} max={200000} step={5000}
              format={v => `$${(v/1000).toFixed(0)}k/yr`}
              onChange={setIncome}
              delta={getSurplusDelta(`age_${retAge}_early`)} />

            <SliderRow label="Bridge Job Income" hint="age 55-60 only"
              value={bridgeIncome} min={0} max={100000} step={5000}
              format={v => v === 0 ? 'None' : `$${(v/1000).toFixed(0)}k/yr`}
              onChange={setBridgeIncome} />
          </div>

          <div className="card" style={{ marginBottom:16 }}>
            <div className="label" style={{ marginBottom:16 }}>Market & Economic</div>

            <SliderRow label="Pre-Retirement Return"
              value={preReturn} min={4} max={12} step={0.5}
              format={v => `${v.toFixed(1)}%`}
              onChange={setPreReturn} />

            <SliderRow label="Post-Retirement Return"
              value={postReturn} min={3} max={10} step={0.5}
              format={v => `${v.toFixed(1)}%`}
              onChange={setPostReturn} />

            <SliderRow label="Inflation Rate"
              value={inflation} min={1} max={6} step={0.5}
              format={v => `${v.toFixed(1)}%`}
              onChange={setInflation} />

            <SliderRow label="Healthcare Cost Pre-Medicare"
              value={healthcare} min={12000} max={60000} step={2000}
              format={v => `$${(v/1000).toFixed(0)}k/yr`}
              onChange={setHealthcare} />
          </div>

          <div className="card">
            <div className="label" style={{ marginBottom:16 }}>Income Sources</div>

            <SliderRow label="Pension" hint="% of projected"
              value={Math.round(pensionMult * 100)} min={0} max={100} step={5}
              format={v => `${v}%`}
              onChange={v => setPensionMult(v/100)} />

            <SliderRow label="Social Security" hint="% of projected"
              value={Math.round(ssMult * 100)} min={50} max={100} step={5}
              format={v => `${v}%`}
              onChange={v => setSsMult(v/100)} />

            <SliderRow label="Salary Growth" hint="assumed annual raise, compounds to retirement"
              value={salaryGrowthPct} min={0} max={8} step={0.5}
              format={v => `${v.toFixed(1)}%/yr`}
              onChange={setSalaryGrowthPct} />
          </div>
        </div>

        {/* Right: results */}
        <div>
          {computing && (
            <div style={{ padding:'8px 12px', background:'rgba(79,156,249,0.1)', borderRadius:8, fontSize:12, color:ACCENT, marginBottom:16 }}>
              Recalculating...
            </div>
          )}

          {/* Impact summary for selected retirement age */}
          {result && (
            <>
              <div className="card" style={{ marginBottom:16 }}>
                <div className="label" style={{ marginBottom:16 }}>
                  Impact on Retire at {retAge} — SS at 62
                  {isChanged && <span style={{ marginLeft:8, fontSize:11, color:AMBER }}>● Modified</span>}
                </div>
                {(() => {
                  const b = base?.scenarios?.find(s => s.label === `age_${retAge}_early`)
                  const r = result?.scenarios?.find(s => s.label === `age_${retAge}_early`)
                  if (!b || !r) return null
                  const rows = [
                    ['Portfolio at Retirement', b.portfolio_at_retirement, r.portfolio_at_retirement],
                    ['Total Capitalized Need',  b.total_capitalized_need,  r.total_capitalized_need],
                    ['Capitalized Income Sources', b.capitalized_income_sources, r.capitalized_income_sources],
                    ['Needed from Assets',      b.capitalized_needed_from_assets, r.capitalized_needed_from_assets],
                    ['Projected Surplus',       b.projected_surplus,       r.projected_surplus],
                  ]
                  return (
                    <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
                      <thead>
                        <tr style={{ borderBottom:'1px solid var(--border)' }}>
                          <th style={{ textAlign:'left',  padding:'6px 10px', color:'var(--text2)', fontWeight:500 }}>Metric</th>
                          <th style={{ textAlign:'right', padding:'6px 10px', color:'var(--text2)', fontWeight:500 }}>Base</th>
                          <th style={{ textAlign:'right', padding:'6px 10px', color:'var(--text2)', fontWeight:500 }}>What-If</th>
                          <th style={{ textAlign:'right', padding:'6px 10px', color:'var(--text2)', fontWeight:500 }}>Delta</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map(([label, bv, rv], i) => {
                          const delta = rv - bv
                          const isPositive = label.includes('Need') || label.includes('Needed') ? delta <= 0 : delta >= 0
                          const deltaColor = delta === 0 ? 'var(--text3)' : isPositive ? GREEN : RED
                          return (
                            <tr key={label} style={{ background: i%2===0 ? 'transparent' : 'var(--bg3)', borderBottom:'1px solid var(--border)' }}>
                              <td style={{ padding:'6px 10px', fontWeight: label==='Projected Surplus' ? 700 : 400 }}>{label}</td>
                              <td style={{ textAlign:'right', padding:'6px 10px', color:'var(--text2)' }}>{fmtK(bv)}</td>
                              <td style={{ textAlign:'right', padding:'6px 10px', fontWeight: label==='Projected Surplus' ? 700 : 400 }}>{fmtK(rv)}</td>
                              <td style={{ textAlign:'right', padding:'6px 10px', fontWeight:600, color:deltaColor }}>
                                {delta === 0 ? '—' : fmtDelta(delta)}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  )
                })()}
              </div>

              {/* All three ages side by side */}
              <div className="card">
                <div className="label" style={{ marginBottom:16 }}>Surplus Across All Retirement Ages</div>
                <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:12 }}>
                  {SCENARIOS.map(sc => {
                    const b = base?.scenarios?.find(s => s.label === sc.id)
                    const r = result?.scenarios?.find(s => s.label === sc.id)
                    if (!b || !r) return null
                    const delta = r.projected_surplus - b.projected_surplus
                    const deltaColor = delta > 0 ? GREEN : delta < 0 ? RED : 'var(--text3)'
                    return (
                      <div key={sc.id} style={{ background:'var(--bg3)', borderRadius:8, padding:'12px 16px', borderTop:`3px solid ${sc.color}` }}>
                        <div style={{ fontWeight:700, color:sc.color, marginBottom:8, fontSize:13 }}>{sc.label}</div>
                        <div style={{ fontSize:11, color:'var(--text2)', marginBottom:4 }}>Base surplus</div>
                        <div style={{ fontSize:16, fontWeight:600, marginBottom:8 }}>{fmtK(b.projected_surplus)}</div>
                        <div style={{ fontSize:11, color:'var(--text2)', marginBottom:4 }}>What-if surplus</div>
                        <div style={{ fontSize:18, fontWeight:700, color:sc.color, marginBottom:8 }}>{fmtK(r.projected_surplus)}</div>
                        <div style={{ height:1, background:'var(--border)', margin:'8px 0' }} />
                        <div style={{ display:'flex', justifyContent:'space-between', fontSize:12 }}>
                          <span style={{ color:'var(--text2)' }}>Delta</span>
                          <span style={{ fontWeight:700, color:deltaColor }}>{delta===0 ? '—' : fmtDelta(delta)}</span>
                        </div>
                      </div>
                    )
                  })}
                </div>

                {isChanged && (
                  <div style={{ marginTop:16, padding:'10px 14px', background:'var(--bg3)', borderRadius:8, fontSize:12, color:'var(--text2)' }}>
                    💡 Changes from base: {[
                      preReturn !== 7.0 && `pre-ret return ${preReturn.toFixed(1)}%`,
                      postReturn !== 6.0 && `post-ret return ${postReturn.toFixed(1)}%`,
                      inflation !== 2.0 && `inflation ${inflation.toFixed(1)}%`,
                      pensionMult !== 1.0 && `pension ${Math.round(pensionMult*100)}%`,
                      ssMult !== 1.0 && `SS ${Math.round(ssMult*100)}%`,
                      healthcare !== 24000 && `healthcare $${(healthcare/1000).toFixed(0)}k`,
                      income !== 120000 && `income target $${(income/1000).toFixed(0)}k`,
                      salaryGrowthPct !== 0.0 && `salary growth ${salaryGrowthPct.toFixed(1)}%/yr`,
                    ].filter(Boolean).join(' · ')}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
