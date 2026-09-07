import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import {
  AreaChart, Area, LineChart, Line,
  XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, ReferenceLine
} from 'recharts'
import { usePersonNames } from '../hooks/usePersonNames'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(n))
const fmtK = (n) => {
  if (isPrivacyMode()) return MASK_CURRENCY
  if (n == null) return '—'
  if (Math.abs(n) >= 1_000_000) return `$${(n/1_000_000).toFixed(1)}M`
  return `$${(n/1000).toFixed(0)}K`
}

const GREEN  = '#34d399'
const RED    = '#f87171'
const AMBER  = '#fbbf24'
const NAVY   = '#5C7CE0' // was #1B3A6B — nearly the same luminance as the dark card background, effectively invisible
const ACCENT = '#4f9cf9'
const ORANGE = '#f97316'

export const RET_AGES = [55,56,57,58,59,60,61,62,63,64,65,66,67]
export const SS_OPTS  = [
  { value:'early',   label:'SS at 62' },
  { value:'delayed', label:'SS at 67' },
]

const CustomTooltip = ({ active, payload, label, person1Name }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, padding:'10px 14px', fontSize:11 }}>
      <div style={{ fontWeight:600, marginBottom:6 }}>{person1Name} Age {label}</div>
      {payload.map(p => p.value > 0 && (
        <div key={p.name} style={{ color:p.color||'var(--text2)', marginBottom:2 }}>
          {p.name}: {fmtK(p.value)}
        </div>
      ))}
    </div>
  )
}

// ── Monte Carlo section ───────────────────────────────────────────────────────
// `overrides`: the What-If Builder's current modified assumptions (see
// StressTestWhatIf.jsx), or null/undefined when running standalone (e.g.
// no such caller today, but keeps this component safe to reuse). When
// present, Monte Carlo is POSTed with those overrides layered on top of
// saved Settings instead of GET-ing the plain Settings-only figures —
// previously switching to this tab always discarded whatever the What-If
// Builder had just been changed to (external audit 2026-09-06). The
// Companion results receive the same overrides, age, and claiming timing.
export function MonteCarloSection({ retAge, ssTiming, overrides }) {
  const { person1Name, person2Name } = usePersonNames()
  const [data, setData]         = useState(null)
  const [swr, setSwr]           = useState(null)
  const [incSrc, setIncSrc]     = useState(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState(null)
  // Bumped on every run() and every input change. A response is only
  // applied if this counter still matches the value captured when the
  // request was fired — otherwise the user has since changed age/timing/
  // overrides or started a newer run, and applying the stale response
  // would render an old scenario's numbers under the new selection
  // (external audit 2026-09-07, finding #13). Also resets `loading` on
  // input change instead of leaving it stuck true forever, since a stale
  // response now takes the early-return path instead of clearing it.
  const genRef = useRef(0)

  useEffect(() => {
    genRef.current++
    setData(null); setSwr(null); setIncSrc(null); setError(null); setLoading(false)
  }, [retAge, ssTiming, overrides])

  const run = () => {
    const gen = ++genRef.current
    setLoading(true)
    setError(null)
    setData(null); setSwr(null); setIncSrc(null)
    const mcCall = overrides
      ? axios.post('/api/simulation/monte-carlo', { ...overrides, ret_age: retAge, ss_timing: ssTiming })
      : axios.get(`/api/simulation/monte-carlo?ret_age=${retAge}&ss_timing=${ssTiming}`)
    Promise.all([
      mcCall,
      axios.post('/api/simulation/swr', { ...overrides, ret_age: retAge, ss_timing: ssTiming }),
      axios.post('/api/retirement/income-sources', { ...overrides, ret_age: retAge, ss_timing: ssTiming })
    ]).then(([mc, sw, inc]) => {
      if (gen !== genRef.current) return // stale — selection changed or a newer run superseded this one
      setData(mc.data)
      setSwr(sw.data)
      setIncSrc(inc.data)
      setLoading(false)
    }).catch(() => {
      if (gen !== genRef.current) return
      setLoading(false)
      setError('Simulation failed to run. Check your connection and try again.')
    })
  }

  if (loading) return (
    <div style={{ padding:'40px 0', textAlign:'center', color:'var(--text2)', fontSize:13 }}>
      Running 1,000 simulations...
    </div>
  )
  if (!data) return (
    <div style={{ textAlign:'center', padding:'48px 24px' }}>
      <div style={{ fontSize:32, marginBottom:16 }}>◎</div>
      <div style={{ fontSize:15, fontWeight:600, marginBottom:8 }}>Ready to simulate</div>
      <div style={{ fontSize:13, color:'var(--text2)', marginBottom:24 }}>1,000 random market scenarios across your full retirement</div>
      {error && <div style={{ fontSize:13, color:'var(--red)', marginBottom:16 }}>{error}</div>}
      <button className="btn-primary" onClick={run} style={{ padding:'12px 32px', fontSize:14 }}>
        Run Monte Carlo Simulation
      </button>
    </div>
  )

  const rate = data.success_rate
  const rateColor = rate >= 95 ? GREEN : rate >= 85 ? AMBER : RED

  return (
    <div>
      {/* Success rate hero */}
      <div className="grid-4" style={{ marginBottom:24 }}>
        <div className="card" style={{ gridColumn:'span 1' }}>
          <div className="label">Probability of Success</div>
          <div style={{ fontFamily:'var(--font-display)', fontSize:48, fontWeight:400, color:rateColor, marginTop:8 }}>
            {rate}%
          </div>
          <div style={{ fontSize:12, color:'var(--text2)', marginTop:4 }}>
            {data.simulations.toLocaleString()} simulations to age {data.retirement_end_age ?? 99}
          </div>
          <div style={{ marginTop:12 }}>
            <div className="progress-bar-track" style={{ height:8 }}>
              <div className="progress-bar-fill" style={{ width:`${rate}%`, background:rateColor }} />
            </div>
          </div>
        </div>
        <div className="card">
          <div className="label">Portfolio at Retirement</div>
          <div className="number-lg" style={{ color:ACCENT, marginTop:8 }}>{fmtK(data.portfolio_at_retirement)}</div>
        </div>
        <div className="card">
          <div className="label">Safe Spending Power</div>
          {swr ? (<>
            <div className="number-lg" style={{ color: data.success_rate >= 95 ? GREEN : data.success_rate >= 85 ? AMBER : RED, marginTop:8 }}>
              {data.success_rate}%
            </div>
            <div style={{ fontSize:12, color:'var(--text2)', marginTop:4 }}>
              of 1,000 scenarios fund your planned lifestyle to age {data.retirement_end_age ?? 99}
            </div>
            {retAge === 55 ? (
              <div style={{ marginTop:12, fontSize:12, lineHeight:1.7 }}>
                <div style={{ color:'var(--text2)', marginBottom:8 }}>Phased plan modeled — see Settings for your bridge income/years inputs:</div>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>Age 55–60 (bridge job)</span>
                  <span>Net draw reduced by bridge income</span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>Kids-at-home years</span>
                  <span>Includes kids annual cost</span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>Age 65+ (Medicare)</span>
                  <span>Healthcare cost drops post-Medicare</span>
                </div>
                <div style={{ height:1, background:'var(--border)', margin:'6px 0' }} />
                <div style={{ display:'flex', justifyContent:'space-between', fontWeight:600 }}>
                  <span>Plan confidence</span>
                  <span style={{ color: data.success_rate >= 95 ? GREEN : AMBER }}>
                    {data.success_rate >= 95 ? '✓ On track' : '⚠ Review needed'}
                  </span>
                </div>
              </div>
            ) : (
              <div style={{ marginTop:12, fontSize:12, lineHeight:1.7 }}>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>Safe portfolio draw</span>
                  <span>{fmt(swr.safe_withdrawal_annual)}/yr ({swr.safe_withdrawal_rate}%)</span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>Total safe spend</span>
                  <span>{fmtK(swr.total_safe_spend)}/yr</span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>vs target</span>
                  <span>{fmtK(swr.income_target)}/yr</span>
                </div>
                <div style={{ height:1, background:'var(--border)', margin:'6px 0' }} />
                <div style={{ display:'flex', justifyContent:'space-between', fontWeight:600 }}>
                  <span>Cushion</span>
                  <span style={{ color: data.success_rate >= 95 ? GREEN : AMBER }}>
                    {swr.cushion_pct > 0 ? '+' : ''}{swr.cushion_pct}%
                  </span>
                </div>
              </div>
            )}
          </>) : <div style={{ fontSize:12, color:'var(--text2)', marginTop:8 }}>Run simulation to calculate</div>}
        </div>
        <div className="card">
          <div className="label">Guaranteed Income Floor</div>
          {swr ? (<>
            <div className="number-lg" style={{ color:NAVY, marginTop:8, fontSize:22 }}>
              {fmtK(swr.guaranteed_income_steadystate)}/yr
            </div>
            <div style={{ fontSize:12, color:'var(--text2)', marginTop:4 }}>
              Once all sources active at age {swr.ss_start_age}
            </div>
            <div style={{ marginTop:12, fontSize:12, lineHeight:1.7 }}>
              <div style={{ display:'flex', justifyContent:'space-between' }}>
                <span style={{ color:'var(--text2)' }}>Pension</span>
                <span>{fmt(swr.pension_annual)}/yr</span>
              </div>
              <div style={{ display:'flex', justifyContent:'space-between' }}>
                <span style={{ color:'var(--text2)' }}>{person1Name} SS</span>
                <span>{fmt(swr.jason_ss_annual)}/yr</span>
              </div>
              <div style={{ display:'flex', justifyContent:'space-between' }}>
                <span style={{ color:'var(--text2)' }}>{person2Name} spousal SS</span>
                <span>{fmt(swr.justin_ss_annual)}/yr</span>
              </div>
            </div>
          </>) : <div style={{ fontSize:12, color:'var(--text2)', marginTop:8 }}>Run simulation to calculate</div>}
        </div>
      </div>

      {/* Fan chart */}
      <div className="card">
        <div className="label" style={{ marginBottom:4 }}>Portfolio Range Across 1,000 Simulations</div>
        <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>
          Shaded areas show the range of outcomes — darker = more likely
        </div>
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart data={data.chart} margin={{ top:0, right:0, bottom:0, left:10 }}>
            <defs>
              <linearGradient id="mcRange" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={ACCENT} stopOpacity={0.15}/>
                <stop offset="95%" stopColor={ACCENT} stopOpacity={0.02}/>
              </linearGradient>
              <linearGradient id="mcMid" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={ACCENT} stopOpacity={0.3}/>
                <stop offset="95%" stopColor={ACCENT} stopOpacity={0.05}/>
              </linearGradient>
            </defs>
            <XAxis dataKey="age" tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} />
            <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={60} />
            <Tooltip content={<CustomTooltip person1Name={person1Name} />} />
            <Area type="monotone" dataKey="p90" name="90th percentile" stroke="none" fill="url(#mcRange)" fillOpacity={1} />
            <Area type="monotone" dataKey="p75" name="75th percentile" stroke="none" fill="url(#mcMid)"   fillOpacity={1} />
            <Area type="monotone" dataKey="p50" name="Median"          stroke={ACCENT} strokeWidth={2.5} fill="none" />
            <Area type="monotone" dataKey="p25" name="25th percentile" stroke="none" fill="var(--bg)" fillOpacity={0.6} />
            <Area type="monotone" dataKey="p10" name="10th percentile" stroke={RED} strokeWidth={1} strokeDasharray="4 2" fill="none" />
            <ReferenceLine y={0} stroke="var(--border2)" strokeDasharray="3 3" />
          </AreaChart>
        </ResponsiveContainer>
        <div style={{ display:'flex', gap:20, marginTop:12, fontSize:11, color:'var(--text3)', flexWrap:'wrap' }}>
          <span style={{ color:ACCENT }}>── Median outcome</span>
          <span style={{ color:'var(--text3)' }}>░░ Middle 50% of outcomes</span>
          <span style={{ color:RED }}>- - 10th percentile (worst 10%)</span>
        </div>
      </div>

      {/* Income sources stacked chart */}
      {incSrc && (
        <div className="card" style={{ marginTop:24 }}>
          <div className="label" style={{ marginBottom:4 }}>Income Sources by Year</div>
          <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>
            What covers your spending each year — pension, SS, bridge job, and portfolio draw
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={incSrc.chart} margin={{ top:0, right:0, bottom:0, left:10 }}>
              <XAxis dataKey="age" tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={60} />
              <Tooltip
                contentStyle={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, fontSize:11 }}
                formatter={(v, n) => [fmt(v), n]}
                labelFormatter={l => `${person1Name} Age ${l}`}
              />
              <Legend wrapperStyle={{ fontSize:11, paddingTop:8 }} />
              <Area type="monotone" dataKey="pension"         stackId="1" name="Pension"        stroke={NAVY} fill={NAVY} fillOpacity={0.8} />
              <Area type="monotone" dataKey="social_security" stackId="1" name="Social Security" stroke="#2E7D8C" fill="#2E7D8C" fillOpacity={0.8} />
              <Area type="monotone" dataKey="bridge_income"   stackId="1" name="Bridge Job"     stroke="#34d399" fill="#34d399" fillOpacity={0.8} />
              <Area type="monotone" dataKey="portfolio_draw"  stackId="1" name="Portfolio Draw"  stroke="#f97316" fill="#f97316" fillOpacity={0.8} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}

// ── Stress tests section ──────────────────────────────────────────────────────
// `overrides`: see MonteCarloSection's comment above — same What-If
// Builder wiring, including Roth and contribution comparisons.
export function StressTestSection({ retAge, ssTiming, overrides }) {
  const { person1Name } = usePersonNames()
  const [data, setData]       = useState(null)
  const [roth, setRoth]       = useState(null)
  const [contrib, setContrib] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)
  // See MonteCarloSection's genRef comment above — same stale-response
  // guard and error surfacing (external audit 2026-09-07, finding #13).
  const genRef = useRef(0)

  useEffect(() => {
    genRef.current++
    setData(null); setRoth(null); setContrib(null); setError(null); setLoading(false)
  }, [retAge, ssTiming, overrides])
  const [active, setActive]   = useState('crash_2008')

  const run = () => {
    const gen = ++genRef.current
    setLoading(true)
    setError(null)
    setData(null); setRoth(null); setContrib(null)
    const stCall = overrides
      ? axios.post('/api/simulation/stress-tests', { ...overrides, ret_age: retAge, ss_timing: ssTiming })
      : axios.get(`/api/simulation/stress-tests?ret_age=${retAge}&ss_timing=${ssTiming}`)
    Promise.all([
      stCall,
      axios.post('/api/simulation/roth-conversion', { ...overrides, ret_age: retAge, ss_timing: ssTiming }),
      axios.post('/api/simulation/contribution-sensitivity', { ...overrides, ret_age: retAge, ss_timing: ssTiming })
    ]).then(([st, rc, cs]) => {
      if (gen !== genRef.current) return // stale — selection changed or a newer run superseded this one
      setData(st.data)
      setRoth(rc.data)
      setContrib(cs.data)
      setLoading(false)
    }).catch(() => {
      if (gen !== genRef.current) return
      setLoading(false)
      setError('Stress test failed to run. Check your connection and try again.')
    })
  }

  if (loading) return (
    <div style={{ padding:'40px 0', textAlign:'center', color:'var(--text2)', fontSize:13 }}>
      Running stress tests...
    </div>
  )
  if (!data) return (
    <div style={{ textAlign:'center', padding:'48px 24px' }}>
      <div style={{ fontSize:32, marginBottom:16 }}>⊗</div>
      <div style={{ fontSize:15, fontWeight:600, marginBottom:8 }}>Ready to stress test</div>
      <div style={{ fontSize:13, color:'var(--text2)', marginBottom:24 }}>Run your portfolio through 2008, 1970s stagflation, and the lost decade</div>
      {error && <div style={{ fontSize:13, color:'var(--red)', marginBottom:16 }}>{error}</div>}
      <button className="btn-primary" onClick={run} style={{ padding:'12px 32px', fontSize:14 }}>
        Run Stress Tests
      </button>
    </div>
  )

  const scenarios = data.scenarios
  const base = scenarios.base
  const stressKeys = ['crash_2008', 'stagflation_1970s', 'lost_decade']
  const newStressKeys = ['early_sequence', 'bridge_job_loss', 'ss_reduction']
  const current = scenarios[active]

  return (
    <div>
      {/* Summary cards */}
      <div className="grid-3" style={{ marginBottom:24 }}>
        {stressKeys.map(key => {
          const s = scenarios[key]
          const ok = s.survived
          return (
            <div key={key}
              className="card"
              onClick={() => setActive(key)}
              style={{ cursor:'pointer', borderColor: active===key ? 'var(--accent)' : 'var(--border)', transition:'border-color 0.15s' }}
            >
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:8 }}>
                <div style={{ fontWeight:600, fontSize:13 }}>{s.label}</div>
                <span className={`tag ${ok ? 'tag-green' : 'tag-red'}`}>
                  {ok ? '✓ Survives' : `Depletes age ${s.depletion_age}`}
                </span>
              </div>
              <div style={{ fontSize:11, color:'var(--text2)', marginBottom:12 }}>{s.description}</div>
              <div style={{ fontSize:12, lineHeight:1.8 }}>
                <div>Lowest balance: <b style={{ color:ok?AMBER:RED }}>{fmtK(s.lowest_balance)}</b> at age {s.lowest_balance_age}</div>
                <div>Final balance: <b style={{ color:ok?GREEN:RED }}>{fmtK(s.final_balance)}</b></div>
              </div>
            </div>
          )
        })}
      </div>

      {/* New scenario cards */}
      <div style={{ marginBottom:8, marginTop:24 }}>
        <div className="label" style={{ marginBottom:12 }}>Additional Scenarios</div>
        <div className="grid-3" style={{ marginBottom:24 }}>
          {newStressKeys.map(key => {
            const s = scenarios[key]
            if (!s) return null
            const ok = s.survived
            return (
              <div key={key}
                className="card"
                onClick={() => setActive(key)}
                style={{ cursor:'pointer', borderColor: active===key ? 'var(--accent)' : 'var(--border)', transition:'border-color 0.15s' }}
              >
                <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:8 }}>
                  <div style={{ fontWeight:600, fontSize:13 }}>{s.label}</div>
                  <span className={`tag ${ok ? 'tag-green' : 'tag-red'}`}>
                    {ok ? '✓ Survives' : `Depletes age ${s.depletion_age}`}
                  </span>
                </div>
                <div style={{ fontSize:11, color:'var(--text2)', marginBottom:12 }}>{s.description}</div>
                <div style={{ fontSize:12, lineHeight:1.8 }}>
                  <div>Lowest balance: <b style={{ color:ok?AMBER:RED }}>{fmtK(s.lowest_balance)}</b> at age {s.lowest_balance_age}</div>
                  <div>Final balance: <b style={{ color:ok?GREEN:RED }}>{fmtK(s.final_balance)}</b></div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Roth conversion schedule */}
      {roth && (
        <div className="card" style={{ marginTop:24, marginBottom:24 }}>
          <div className="label" style={{ marginBottom:4 }}>Roth Conversion Optimizer</div>
          <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>
            Fill the 22% bracket each year from retirement to RMD age 73 — reduces forced RMDs and lifetime tax bill
          </div>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr 1fr', gap:12, marginBottom:20 }}>
            <div style={{ background:'var(--bg3)', borderRadius:8, padding:'12px 16px' }}>
              <div className="label">Total to Convert</div>
              <div style={{ fontSize:20, fontWeight:700, color:'var(--accent)', marginTop:4 }}>{fmtK(roth.total_conversions)}</div>
              <div style={{ fontSize:11, color:'var(--text2)', marginTop:2 }}>over {roth.conversion_years} years</div>
            </div>
            <div style={{ background:'var(--bg3)', borderRadius:8, padding:'12px 16px' }}>
              <div className="label">Tax Cost Now</div>
              <div style={{ fontSize:20, fontWeight:700, color:AMBER, marginTop:4 }}>{fmtK(roth.total_tax_cost)}</div>
              <div style={{ fontSize:11, color:'var(--text2)', marginTop:2 }}>at 22% bracket today</div>
            </div>
            <div style={{ background:'var(--bg3)', borderRadius:8, padding:'12px 16px' }}>
              <div className="label">Tax Avoided at 73+</div>
              <div style={{ fontSize:20, fontWeight:700, color:GREEN, marginTop:4 }}>{fmtK(roth.total_tax_avoided)}</div>
              <div style={{ fontSize:11, color:'var(--text2)', marginTop:2 }}>at 24% RMD bracket</div>
            </div>
            <div style={{ background:'var(--bg3)', borderRadius:8, padding:'12px 16px', border:'1px solid var(--green)' }}>
              <div className="label">Net Lifetime Benefit</div>
              <div style={{ fontSize:20, fontWeight:700, color:GREEN, marginTop:4 }}>{fmtK(roth.net_lifetime_benefit)}</div>
              <div style={{ fontSize:11, color:'var(--text2)', marginTop:2 }}>
                RMD: {fmtK(roth.estimated_rmd_without_conversions)} → {fmtK(roth.estimated_rmd_with_conversions)}/yr
              </div>
            </div>
          </div>
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
              <thead>
                <tr style={{ borderBottom:'1px solid var(--border)' }}>
                  {['Age','Year','Pre-tax Balance','Room in 22%','Convert','Tax Cost','Roth FV@73','Tax Avoided','Net Benefit','Pre-tax After'].map(h => (
                    <th key={h} style={{ textAlign:'right', padding:'6px 10px', color:'var(--text2)', fontWeight:500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {roth.schedule.map((r, i) => (
                  <tr key={r.age} style={{ background: i%2===0 ? 'transparent' : 'var(--bg3)', borderBottom:'1px solid var(--border)' }}>
                    <td style={{ textAlign:'right', padding:'5px 10px' }}>{r.age}</td>
                    <td style={{ textAlign:'right', padding:'5px 10px' }}>{r.year}</td>
                    <td style={{ textAlign:'right', padding:'5px 10px' }}>{fmtK(r.pretax_balance)}</td>
                    <td style={{ textAlign:'right', padding:'5px 10px', color:'var(--accent)' }}>{fmtK(r.room_in_22_bracket)}</td>
                    <td style={{ textAlign:'right', padding:'5px 10px', color:GREEN, fontWeight:600 }}>{fmtK(r.optimal_conversion)}</td>
                    <td style={{ textAlign:'right', padding:'5px 10px', color:AMBER }}>{fmtK(r.tax_cost)}</td>
                    <td style={{ textAlign:'right', padding:'5px 10px', color:'var(--text2)' }}>{fmtK(r.roth_fv_at_73)}</td>
                    <td style={{ textAlign:'right', padding:'5px 10px', color:GREEN }}>{fmtK(r.tax_avoided_at_73)}</td>
                    <td style={{ textAlign:'right', padding:'5px 10px', color:GREEN, fontWeight:600 }}>{fmtK(r.net_benefit)}</td>
                    <td style={{ textAlign:'right', padding:'5px 10px' }}>{fmtK(r.pretax_after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Contribution sensitivity chart */}
      {contrib && (
        <div className="card" style={{ marginTop:24, marginBottom:24 }}>
          <div className="label" style={{ marginBottom:4 }}>Contribution Rate Sensitivity</div>
          <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>
            Cost vs benefit of increasing 401k contributions in remaining working years — extra goes to Roth
          </div>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:16, marginBottom:20 }}>
            <div style={{ background:'var(--bg3)', borderRadius:8, padding:'12px 16px' }}>
              <div className="label">Years to Retirement (age {contrib.retirement_age})</div>
              <div style={{ fontSize:22, fontWeight:700, color:'var(--accent)', marginTop:4 }}>{contrib.years_to_retire} years</div>
              <div style={{ fontSize:11, color:'var(--text2)', marginTop:2 }}>Base portfolio at {contrib.retirement_age}: {fmtK(contrib.base_portfolio)}</div>
            </div>
            <div style={{ background:'var(--bg3)', borderRadius:8, padding:'12px 16px' }}>
              <div className="label">Key Insight</div>
              <div style={{ fontSize:13, fontWeight:600, color:'var(--text2)', marginTop:4, lineHeight:1.5 }}>
                Max catch-up adds {fmtK(contrib.scenarios[contrib.scenarios.length-1].portfolio_delta || 0)} at retirement
                for {isPrivacyMode() ? MASK_CURRENCY : `$${contrib.scenarios[contrib.scenarios.length-1].monthly_spending_cut.toLocaleString()}`}/mo spending cut
              </div>
            </div>
          </div>
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
              <thead>
                <tr style={{ borderBottom:'1px solid var(--border)' }}>
                  {['Scenario','Rate','Extra/yr','Monthly Spending Cut',`Portfolio at ${contrib.retirement_age}`,'Δ Portfolio','Δ Surplus','Breakeven'].map(h => (
                    <th key={h} style={{ textAlign:'right', padding:'6px 10px', color:'var(--text2)', fontWeight:500, whiteSpace:'nowrap' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {contrib.scenarios.map((s, i) => (
                  <tr key={s.label} style={{ background: i%2===0 ? 'transparent' : 'var(--bg3)', borderBottom:'1px solid var(--border)' }}>
                    <td style={{ textAlign:'left', padding:'5px 10px', fontWeight: i===0 ? 400 : 600 }}>{s.label}</td>
                    <td style={{ textAlign:'right', padding:'5px 10px' }}>{s.employee_pct}%</td>
                    <td style={{ textAlign:'right', padding:'5px 10px', color:'var(--text2)' }}>{fmt(s.annual_employee - (contrib.scenarios[0]?.annual_employee || 0))}/yr</td>
                    <td style={{ textAlign:'right', padding:'5px 10px', color: s.monthly_spending_cut > 0 ? AMBER : 'var(--text2)' }}>
                      {s.monthly_spending_cut > 0 ? `-${fmt(s.monthly_spending_cut)}/mo` : '—'}
                    </td>
                    <td style={{ textAlign:'right', padding:'5px 10px' }}>{fmtK(s.portfolio_at_ret)}</td>
                    <td style={{ textAlign:'right', padding:'5px 10px', color: s.portfolio_delta > 0 ? GREEN : 'var(--text2)' }}>
                      {s.portfolio_delta > 0 ? `+${fmtK(s.portfolio_delta)}` : '—'}
                    </td>
                    <td style={{ textAlign:'right', padding:'5px 10px', color: s.surplus_delta > 0 ? GREEN : 'var(--text2)' }}>
                      {s.surplus_delta > 0 ? `+${fmtK(s.surplus_delta)}` : '—'}
                    </td>
                    <td style={{ textAlign:'right', padding:'5px 10px', color:'var(--text2)' }}>
                      {s.breakeven_years > 0 ? `${s.breakeven_years}yr` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ marginTop:16, padding:'10px 14px', background:'var(--bg3)', borderRadius:8, fontSize:12, color:'var(--text2)', lineHeight:1.6 }}>
            ⚡ With {contrib.years_to_retire} years to retirement at age {contrib.retirement_age}, extra contributions have {contrib.years_to_retire < 6 ? 'limited' : 'meaningful'} compounding time. Consider whether the spending cut today is worth the portfolio boost at retirement.
          </div>
        </div>
      )}

      {/* Detail chart */}
      {current && (
        <div className="card">
          <div className="label" style={{ marginBottom:4 }}>{current.label} — vs Base Case</div>
          <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>{current.description}</div>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={current.chart} margin={{ top:0, right:0, bottom:0, left:10 }}>
              <XAxis dataKey="age" tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={60} />
              <Tooltip content={<CustomTooltip person1Name={person1Name} />} />
              <Legend wrapperStyle={{ fontSize:11 }} />
              <ReferenceLine y={0} stroke={RED} strokeDasharray="3 3" label={{ value:'$0', fill:RED, fontSize:10 }} />
              <Line type="monotone" dataKey="base"    name="Base Case (6%/yr)" stroke="var(--text3)" strokeWidth={1.5} strokeDasharray="4 2" dot={false} />
              <Line type="monotone" dataKey="balance" name={current.label}     stroke={current.survived ? ACCENT : RED} strokeWidth={2.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
          <div style={{ marginTop:12, padding:'10px 14px', background:'var(--bg3)', borderRadius:8, fontSize:12, color:'var(--text2)' }}>
            {current.survived
              ? `✓ Portfolio survives to age ${data.retirement_end_age ?? 99} even under this scenario. Final balance of ${fmtK(current.final_balance)} remains.`
              : `⚠ Portfolio depletes at age ${current.depletion_age}. Consider increasing safe withdrawal buffer or reducing early retirement spend.`
            }
          </div>
        </div>
      )}
    </div>
  )
}

// Note: this file no longer has a default-exported page component — its
// former standalone page (header + retAge/ssTiming controls + Monte Carlo/
// Stress tab switcher) was folded into pages/StressTestWhatIf.jsx, which
// combines it with What-If Builder as three tabs under one nav entry
// instead of two separate ones. MonteCarloSection/StressTestSection above
// are consumed directly by that page.
