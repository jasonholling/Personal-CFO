import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer
} from 'recharts'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'
import { useScenario } from '../hooks/useScenario'
import { usePersonNames } from '../hooks/usePersonNames'
import { useSsAnchors } from '../hooks/useSsAnchors'
import { TWO_AGE_MIN, TWO_AGE_MAX, clampTwoAge, parseTwoAgeInput } from '../utils/scenario'
import SecondEarnerNote from '../components/SecondEarnerNote'
import ClaimAgeSlider from '../components/ClaimAgeSlider'
import ActiveScenarioBanner from '../components/ActiveScenarioBanner'

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

// Two-age mode (backend/docs/CALCULATION_CONTRACT.md section 30/32,
// Milestone 2 of 4): explicit, independent retirement ages for both
// spouses, same toggle wording/pattern StressTestWhatIf.jsx already
// established for Monte Carlo/Historical Stress -- off (the default)
// keeps this page on its existing single-age ret_age/ssTiming behavior,
// completely unaffected.
export default function RothConversion() {
  const {
    retAge, ssTiming, setRetAge, setSsTiming,
    jasonSsClaimAge, justinSsClaimAge, setJasonSsClaimAge, setJustinSsClaimAge,
  } = useScenario()
  const { person1Name, person2Name } = usePersonNames()
  const ssAnchors = useSsAnchors()
  const [data, setData]         = useState(null)
  const [loading, setLoading]   = useState(true)
  const [twoAgeMode, setTwoAgeMode]   = useState(false)
  const [jasonRetAge, setJasonRetAge] = useState(65)
  const [justinRetAge, setJustinRetAge] = useState(65)

  // Bumped on every effect firing (any age/timing/mode change) -- a
  // response is only applied if this counter still matches the value
  // captured when its request was fired, same staleness guard
  // Simulation.jsx's own genRef already established (independent
  // review, 2026-09-08, Roth Conversion follow-up, P2: changing ages,
  // SS timing, or mode started another request with no cancellation or
  // generation check at all -- an older, slower response landing after
  // a newer one could overwrite the newer result, or clear loading
  // incorrectly after a mode switch had already superseded it).
  const genRef = useRef(0)

  // External audit follow-up, 2026-09-09 (CALCULATION_CONTRACT.md
  // section 54): see MonteCarloSection's identical comment in
  // Simulation.jsx -- omit the key entirely when unset so the
  // backend's own fallback to the saved Settings value still applies.
  const ssClaimAgeParams = {
    ...(jasonSsClaimAge  != null ? { jason_ss_claim_age: jasonSsClaimAge } : {}),
    ...(justinSsClaimAge != null ? { justin_ss_claim_age: justinSsClaimAge } : {}),
  }
  // Jason's effective claim age -- this page's own slider, or (unlike
  // Retirement.jsx) whatever's saved in Settings, since this endpoint
  // DOES fall back there. Either way, the SS Timing buttons below have
  // no effect on Jason once one is set, so they're hidden entirely
  // rather than shown disabled (2026-09-09 follow-up: "i just want the
  // slider and not the override thing with buttons still below").
  const jasonOverridden = jasonSsClaimAge != null || ssAnchors.savedJasonClaimAge != null

  useEffect(() => {
    const gen = ++genRef.current
    setLoading(true)
    const request = twoAgeMode
      ? axios.post('/api/simulation/roth-conversion', {
          ...ssClaimAgeParams, ss_timing: ssTiming, jason_ret_age: jasonRetAge, justin_ret_age: justinRetAge,
        })
      : axios.get('/api/simulation/roth-conversion', { params: { ret_age: retAge, ss_timing: ssTiming, ...ssClaimAgeParams } })
    request
      .then(r => { if (gen === genRef.current) setData(r.data) })
      .catch(() => { if (gen === genRef.current) setData(null) })
      .finally(() => { if (gen === genRef.current) setLoading(false) })
  }, [retAge, ssTiming, twoAgeMode, jasonRetAge, justinRetAge, jasonSsClaimAge, justinSsClaimAge])

  // SecondEarnerNote's amount/years/personLabel differ by mode, same
  // convention Simulation.jsx's own secondEarnerNoteProps already uses
  // for Monte Carlo/Stress -- two-age results carry
  // still_working_spouse_income_first_year/phase2_start_age/
  // phase3_start_age instead of a single-axis justin_gap_income_first_year/
  // justin_gap_years pair, and the still-working spouse can be EITHER
  // person (later_retiree).
  const secondEarnerNoteProps = data && data.mode === 'two_age' ? {
    amount: data.still_working_spouse_income_first_year,
    years: data.phase3_start_age - data.phase2_start_age,
    factor: data.second_earner_net_of_tax_factor,
    personLabel: data.later_retiree === 'jason' ? person1Name : person2Name,
    twoAge: true,
  } : null

  return (
    <div>
      <div style={{ marginBottom:28 }}>
        <h1 className="section-title">Roth Conversion Ladder</h1>
        <p className="section-sub">Optimal pre-tax to Roth conversions before RMDs start at 73 — computed from your real balances and Settings, no re-entry</p>
      </div>

      <ActiveScenarioBanner />

      {/* Why this matters callout */}
      <div style={{ padding:'14px 16px', background:'rgba(79,156,249,0.06)', border:'1px solid rgba(79,156,249,0.2)', borderRadius:8, marginBottom:24, fontSize:13 }}>
        <div style={{ fontWeight:600, marginBottom:6, color:'var(--accent)' }}>💡 The Opportunity</div>
        <div style={{ color:'var(--text2)', lineHeight:1.7 }}>
          Between retirement and age 73, the gap between your guaranteed income (pension + Social Security) and the top of the
          22% bracket is room to convert pre-tax dollars to Roth cheaply. At 73, forced RMDs push that same money into ordinary
          income whether you want it or not — usually at a higher bracket. <b style={{ color:'var(--text)' }}>Converting into the gap now saves the spread.</b>
        </div>
      </div>

      <div style={{ marginBottom:16 }}>
        <button
          className={twoAgeMode ? 'btn-primary' : 'btn-secondary'}
          onClick={() => setTwoAgeMode(m => !m)}
          style={{ fontSize:12 }}
        >{twoAgeMode ? '✓ Two-Age Mode' : 'Use Two Independent Retirement Ages'}</button>
      </div>

      {/* Custom claim age (2026-09-09, CALCULATION_CONTRACT.md section
          54): shared jasonSsClaimAge/justinSsClaimAge scenario state --
          same values carry over from/to Retirement.jsx, Monte Carlo,
          Historical Stress, and Survivor Scenario. */}
      {/* Milestone 3 (progressive disclosure, 2026-09-09): collapsed by
          default -- see Retirement.jsx's identical comment. */}
      <details className="card" style={{ marginBottom:20, padding:'16px 20px' }} open={jasonSsClaimAge != null || justinSsClaimAge != null}>
        <summary style={{ cursor:'pointer', fontSize:13, fontWeight:600, padding:'4px 0', marginBottom:4 }}>Custom Social Security Claim Age (62-70)</summary>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:24, marginTop:8 }}>
          <ClaimAgeSlider
            label={`${person1Name}'s claim age`}
            claimAge={jasonSsClaimAge}
            onChange={setJasonSsClaimAge}
            benefit62={ssAnchors.jason.b62}
            benefit67={ssAnchors.jason.b67}
            benefit70={ssAnchors.jason.b70}
            benefitType="worker"
            offHint="off = falls back to your saved Settings claim age if you have one, otherwise the Early/Delayed toggle"
            compact
          />
          <ClaimAgeSlider
            label={`${person2Name}'s claim age`}
            claimAge={justinSsClaimAge}
            onChange={setJustinSsClaimAge}
            benefit62={ssAnchors.justin.b62}
            benefit67={ssAnchors.justin.b67}
            benefit70={ssAnchors.justin.b70}
            benefitType="spousal"
            checkEarlyAnchor
            offHint="off = falls back to your saved Settings claim age if you have one, otherwise the Early/Delayed toggle"
            compact
          />
        </div>
      </details>

      {/* Scenario controls */}
      <div className="card" style={{ marginBottom:24 }}>
        {!twoAgeMode ? (
          <div className="grid-2">
            <div>
              <div className="label" style={{ marginBottom:8 }}>Retirement Age</div>
              <div style={{ display:'flex', gap:8 }}>
                {RET_AGES.map(o => (
                  <button key={o.value} className={o.value === retAge ? 'btn-primary' : 'btn-secondary'} onClick={() => setRetAge(o.value)}>{o.label}</button>
                ))}
              </div>
            </div>
            {!jasonOverridden && (
              <div>
                <div className="label" style={{ marginBottom:8 }}>Social Security Timing</div>
                <div style={{ display:'flex', gap:8 }}>
                  {SS_TIMINGS.map(o => (
                    <button key={o.value} className={o.value === ssTiming ? 'btn-primary' : 'btn-secondary'} onClick={() => setSsTiming(o.value)}>{o.label}</button>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div style={{ display:'flex', gap:24, flexWrap:'wrap', alignItems:'flex-end' }}>
            {/* External audit review, 2026-09-09: see parseTwoAgeInput's
                own comment in utils/scenario.js -- lenient while
                typing, clamped only on blur. */}
            <div>
              <div className="label" style={{ marginBottom:8 }}>{person1Name}'s Retirement Age</div>
              <input type="number" min={TWO_AGE_MIN} max={TWO_AGE_MAX} step={1} value={jasonRetAge}
                     onChange={e => setJasonRetAge(parseTwoAgeInput(e.target.value))}
                     onBlur={e => setJasonRetAge(clampTwoAge(e.target.value))} />
              <div style={{ fontSize:11, color:'var(--text3)', marginTop:4 }}>Ages {TWO_AGE_MIN}-{TWO_AGE_MAX}</div>
            </div>
            <div>
              <div className="label" style={{ marginBottom:8 }}>{person2Name}'s Retirement Age</div>
              <input type="number" min={TWO_AGE_MIN} max={TWO_AGE_MAX} step={1} value={justinRetAge}
                     onChange={e => setJustinRetAge(parseTwoAgeInput(e.target.value))}
                     onBlur={e => setJustinRetAge(clampTwoAge(e.target.value))} />
              <div style={{ fontSize:11, color:'var(--text3)', marginTop:4 }}>Ages {TWO_AGE_MIN}-{TWO_AGE_MAX}</div>
            </div>
            {!jasonOverridden && (
              <div>
                <div className="label" style={{ marginBottom:8 }}>Social Security Timing</div>
                <div style={{ display:'flex', gap:8 }}>
                  {SS_TIMINGS.map(o => (
                    <button key={o.value} className={o.value === ssTiming ? 'btn-primary' : 'btn-secondary'} onClick={() => setSsTiming(o.value)}>{o.label}</button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
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
            <div className="label" style={{ marginBottom:16 }}>
              {data.mode === 'two_age'
                ? `Summary — ${person1Name} ${data.jason_ret_age} · ${person2Name} ${data.justin_ret_age} (${data.conversion_years} years to age ${data.rmd_start_age})`
                : `Summary — Ages ${retAge}–${data.rmd_start_age - 1} (${data.conversion_years} years)`}
            </div>
            {secondEarnerNoteProps && <SecondEarnerNote {...secondEarnerNoteProps} />}
            <div className="grid-4" style={{ marginTop: secondEarnerNoteProps ? 16 : 0 }}>
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
