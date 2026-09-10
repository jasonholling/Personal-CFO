import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import {
  AreaChart, Area, LineChart, Line,
  XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, ReferenceLine
} from 'recharts'
import { usePersonNames } from '../hooks/usePersonNames'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'
import SecondEarnerNote from '../components/SecondEarnerNote'

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

// SecondEarnerNote's amount/years/personLabel differ by mode: single-axis
// results carry justin_gap_income_first_year/justin_gap_years (always
// Justin); two-age results (mode === 'two_age') carry
// still_working_spouse_income_first_year plus phase2/phase3_start_age,
// and the still-working spouse can be EITHER person (later_retiree).
// Shared by MonteCarloSection/StressTestSection below so both modes
// render the note the same way instead of two independent branches.
function secondEarnerNoteProps(data, person1Name, person2Name) {
  if (data.mode === 'two_age') {
    return {
      amount: data.still_working_spouse_income_first_year,
      years: data.phase3_start_age - data.phase2_start_age,
      factor: data.second_earner_net_of_tax_factor,
      personLabel: data.later_retiree === 'jason' ? person1Name : person2Name,
      twoAge: true,
    }
  }
  return {
    amount: data.justin_gap_income_first_year, years: data.justin_gap_years,
    factor: data.second_earner_net_of_tax_factor, personLabel: person2Name,
  }
}

// External audit review, 2026-09-09: a result on this page previously
// showed two-age retirement ages (if two-age mode) but nothing else --
// not the effective SS claim ages, return/inflation assumptions, income
// target, bridge income, or SS multiplier. The blue "reflecting What-If
// assumptions" notice said overrides were active, but a result couldn't
// be audited from the screen afterward -- which assumption produced
// THIS number. Compact by default (native <details>, no extra state),
// expandable to the full list. jasonSsClaimAge/justinSsClaimAge here are
// EFFECTIVE values (this page's own slider, else null meaning "resolved
// server-side from saved Settings or the Early/Delayed toggle" -- the
// UI can't know which without asking the backend, so it's labeled
// accordingly rather than guessed).
function AssumptionsUsed({ retAge, ssTiming, jasonRetAge, justinRetAge, jasonSsClaimAge, justinSsClaimAge, savedJasonClaimAge, savedJustinClaimAge, overrides, person1Name, person2Name, data }) {
  const twoAge = jasonRetAge != null && justinRetAge != null
  // Prefer the RESPONSE's own echoed ages over the request props when
  // available -- the authoritative "what was actually computed," not
  // just "what was asked for" (they can differ, e.g. a snapped age).
  const displayJasonAge  = data?.jason_ret_age  ?? jasonRetAge
  const displayJustinAge = data?.justin_ret_age ?? justinRetAge
  const ageLine = twoAge
    ? `${person1Name} retires ${displayJasonAge} · ${person2Name} retires ${displayJustinAge}`
    : `Retire at ${retAge}`
  // External audit follow-up, 2026-09-09: with the page-local slider
  // off, this used to say "Jason SS: take at 62 (or Settings, if
  // saved)" -- a completed result should say exactly what it USED, not
  // a conditional maybe. resolvedJasonAge/resolvedJustinAge resolve
  // the same 3-tier chain the backend applies (explicit override >
  // saved Settings > legacy ss_timing toggle) so the displayed line
  // states the actual effective age and its source. Justin's own line
  // used to be omitted entirely when his slider was off -- now always
  // shown, symmetric with Jason's.
  const jasonSource  = jasonSsClaimAge  != null ? 'this page'
                      : savedJasonClaimAge  != null ? 'Settings'
                      : null
  const justinSource = justinSsClaimAge != null ? 'this page'
                      : savedJustinClaimAge != null ? 'Settings'
                      : null
  const resolvedJasonAge  = jasonSsClaimAge  ?? savedJasonClaimAge
  const resolvedJustinAge = justinSsClaimAge ?? savedJustinClaimAge
  const ssLine = [
    resolvedJasonAge != null
      ? `${person1Name}: ${resolvedJasonAge}, from ${jasonSource}`
      : `${person1Name}: ${ssTiming === 'delayed' ? '67 (wait until 67 toggle)' : '62 (take at 62 toggle)'}`,
    resolvedJustinAge != null
      ? `${person2Name}: ${resolvedJustinAge}, from ${justinSource}`
      : `${person2Name}: 50% of ${person1Name}'s toggle age above`,
  ].join(' · ')
  const pct = (v) => v == null ? null : `${(v * 100).toFixed(1)}%`
  // External audit follow-up, 2026-09-09: the whole list used to sit
  // behind one collapsed <details> -- "the actual result depends on
  // those overrides, but the user has to discover and expand the
  // disclosure." Split into PRIMARY rows (ages, SS, returns, inflation,
  // income target -- the assumptions that most directly explain the
  // headline numbers) shown immediately, and SECONDARY rows (bridge
  // income, pension/SS multipliers -- narrower-impact, opt-in What-If
  // toggles) still behind a collapsible details for anyone who wants
  // the full picture without the primary view getting cluttered.
  const primaryRows = [
    ['Retirement age(s)', ageLine],
    ['Social Security', ssLine],
    overrides?.pre_return  != null && ['Pre-retirement return', pct(overrides.pre_return)],
    overrides?.post_return != null && ['Post-retirement return', pct(overrides.post_return)],
    overrides?.inflation   != null && ['Inflation', pct(overrides.inflation)],
    overrides?.income_target != null && ['Income target', `${fmtK(overrides.income_target)}/yr`],
  ].filter(Boolean)
  const secondaryRows = overrides ? [
    overrides.bridge_income  != null && overrides.bridge_income > 0 && ['Bridge income', `${fmtK(overrides.bridge_income)}/yr`],
    overrides.pension_mult != null && overrides.pension_mult !== 1 && ['Pension multiplier', `${Math.round(overrides.pension_mult * 100)}%`],
    overrides.ss_mult != null && overrides.ss_mult !== 1 && ['SS multiplier', `${Math.round(overrides.ss_mult * 100)}% of projected`],
  ].filter(Boolean) : []
  return (
    <div style={{ marginBottom:16, fontSize:12, color:'var(--text3)' }}>
      <div style={{ color:'var(--text2)', marginBottom:4, fontWeight:600 }}>Assumptions used</div>
      <div style={{ display:'grid', gridTemplateColumns:'auto auto', gap:'4px 16px', maxWidth:420 }}>
        {primaryRows.map(([k, v]) => (
          <span key={k} style={{ display:'contents' }}><span>{k}</span><span style={{ color:'var(--text2)' }}>{v}</span></span>
        ))}
      </div>
      {secondaryRows.length > 0 && (
        <details style={{ marginTop:6 }}>
          <summary style={{ cursor:'pointer' }}>
            {secondaryRows.length} more What-If override{secondaryRows.length > 1 ? 's' : ''}
          </summary>
          <div style={{ marginTop:6, display:'grid', gridTemplateColumns:'auto auto', gap:'4px 16px', maxWidth:420 }}>
            {secondaryRows.map(([k, v]) => (
              <span key={k} style={{ display:'contents' }}><span>{k}</span><span style={{ color:'var(--text2)' }}>{v}</span></span>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

// External audit review, 2026-09-09: changing any control here silently
// cleared the previous result back to a generic "Ready to simulate"
// state, with nothing on screen saying WHY -- a user moving a slider
// had no visible confirmation the app even noticed, just an
// unexplained reset. Compares the previous render's snapshot to the
// current one and returns a short, specific description of whichever
// single input changed (checked in a fixed, most-to-least-specific
// order so only one reason is ever shown even if several changed at
// once, e.g. a two-age-mode toggle that changes several deps together).
function describeAssumptionChange(prev, next, person1Name, person2Name) {
  // External audit follow-up, 2026-09-09: two real bugs here. (1)
  // `prev` used to be the PREVIOUS RENDER's props, not the last
  // COMPLETED RUN's -- on first mount (no run has happened yet) that
  // meant comparing against the mount-time defaults, producing "null
  // to 65" the instant two-age mode was toggled on (jasonRetAge starts
  // null when single-axis). Callers now pass the last successful
  // run's own snapshot (or null if no run has completed), and this
  // returns null in that case -- "don't say a previous result was
  // cleared when none exists." (2) toggling two-age mode changes
  // jasonRetAge/justinRetAge from null to a number (or back) as a
  // PAIR, which read as a meaningless raw diff ("retirement age
  // changed from null to 65") -- detected explicitly as its own case
  // now, worded as a mode change instead of an age change.
  if (!prev) return null
  const prevTwoAge = prev.jasonRetAge != null && prev.justinRetAge != null
  const nextTwoAge = next.jasonRetAge != null && next.justinRetAge != null
  if (prevTwoAge !== nextTwoAge) return nextTwoAge ? 'changed to two-age mode' : 'changed to single retirement age'
  if (prev.retAge !== next.retAge) return `retirement age changed from ${prev.retAge} to ${next.retAge}`
  if (prev.jasonRetAge !== next.jasonRetAge) return `${person1Name}'s retirement age changed from ${prev.jasonRetAge} to ${next.jasonRetAge}`
  if (prev.justinRetAge !== next.justinRetAge) return `${person2Name}'s retirement age changed from ${prev.justinRetAge} to ${next.justinRetAge}`
  if (prev.ssTiming !== next.ssTiming) return `Social Security timing changed from "${prev.ssTiming === 'delayed' ? 'wait until 67' : 'take at 62'}" to "${next.ssTiming === 'delayed' ? 'wait until 67' : 'take at 62'}"`
  if (prev.jasonSsClaimAge !== next.jasonSsClaimAge) return `${person1Name}'s SS claim age changed from ${prev.jasonSsClaimAge ?? 'off'} to ${next.jasonSsClaimAge ?? 'off'}`
  if (prev.justinSsClaimAge !== next.justinSsClaimAge) return `${person2Name}'s SS claim age changed from ${prev.justinSsClaimAge ?? 'off'} to ${next.justinSsClaimAge ?? 'off'}`
  if (prev.overrides !== next.overrides) return 'What-If Builder assumptions changed'
  return null
}

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

// Custom tooltip for the Income Sources by Year chart (as opposed to
// CustomTooltip's generic per-series rendering above) — reads the raw
// data row directly rather than only the two rendered Area series
// (rmd/discretionary_withdrawal), so it can also surface rmd_reinvested
// and withdrawal_pretax, which aren't chart series themselves. Auditor
// review (2026-09-10): a large forced RMD year and a large genuinely-
// discretionary withdrawal year looked visually identical in the old
// single "Portfolio Draw" band — this makes the split, and the amount
// of the RMD that's immediately reinvested rather than spent, explicit.
const IncomeSourcesTooltip = ({ active, payload, label, person1Name }) => {
  if (!active || !payload?.length) return null
  const row = payload[0]?.payload
  if (!row) return null
  return (
    <div style={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, padding:'10px 14px', fontSize:11 }}>
      <div style={{ fontWeight:600, marginBottom:6 }}>{person1Name} Age {label}</div>
      {row.pension > 0 && <div style={{ color:NAVY, marginBottom:2 }}>Pension: {fmtK(row.pension)}</div>}
      {row.social_security > 0 && <div style={{ color:'#2E7D8C', marginBottom:2 }}>Social Security: {fmtK(row.social_security)}</div>}
      {row.bridge_income > 0 && <div style={{ color:'#34d399', marginBottom:2 }}>Bridge Job: {fmtK(row.bridge_income)}</div>}
      {row.discretionary_withdrawal > 0 && (
        <div style={{ color:'#f97316', marginBottom:2 }}>Discretionary withdrawal: {fmtK(row.discretionary_withdrawal)}</div>
      )}
      {row.rmd > 0 && (
        <div style={{ color:'#c2410c', marginBottom:2 }}>RMD (gross, forced by law): {fmtK(row.rmd)}</div>
      )}
      {row.rmd_reinvested > 0 && (
        <div style={{ color:'var(--text3)', marginBottom:2, paddingLeft:10 }}>
          ↳ of which reinvested, not spent: {fmtK(row.rmd_reinvested)}
        </div>
      )}
      <div style={{ borderTop:'1px solid var(--border2)', marginTop:6, paddingTop:6, fontWeight:600 }}>
        Total gross cash flow this year: {fmtK(row.pension + row.social_security + row.bridge_income + row.portfolio_draw)}
      </div>
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
export function MonteCarloSection({ retAge, ssTiming, overrides, jasonRetAge, justinRetAge, jasonSsClaimAge, justinSsClaimAge, savedJasonClaimAge, savedJustinClaimAge }) {
  const { person1Name, person2Name } = usePersonNames()
  const [data, setData]         = useState(null)
  const [swr, setSwr]           = useState(null)
  const [incSrc, setIncSrc]     = useState(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState(null)
  // Two-age mode (both jasonRetAge/justinRetAge set, backend/docs/
  // CALCULATION_CONTRACT.md section 22) uses the explicit two-age
  // endpoint directly instead of the single-axis GET/POST -- What-If
  // overrides don't carry into two-age mode in this v1 (out of scope),
  // and SWR/income-sources (single-axis-only companions) aren't fetched
  // alongside it.
  const twoAge = jasonRetAge != null && justinRetAge != null
  // Bumped on every run() and every input change. A response is only
  // applied if this counter still matches the value captured when the
  // request was fired — otherwise the user has since changed age/timing/
  // overrides or started a newer run, and applying the stale response
  // would render an old scenario's numbers under the new selection
  // (external audit 2026-09-07, finding #13). Also resets `loading` on
  // input change instead of leaving it stuck true forever, since a stale
  // response now takes the early-return path instead of clearing it.
  const genRef = useRef(0)
  // See describeAssumptionChange's own comment above -- the LAST
  // COMPLETED RUN's own snapshot, set only inside run()'s success
  // handlers below, not on every render/keystroke. Stays null until a
  // run actually finishes, so no "cleared" message shows before one
  // ever existed.
  const lastRunSnapshotRef = useRef(null)
  const [clearReason, setClearReason] = useState(null)

  useEffect(() => {
    const snapshot = { retAge, ssTiming, overrides, jasonRetAge, justinRetAge, jasonSsClaimAge, justinSsClaimAge }
    setClearReason(describeAssumptionChange(lastRunSnapshotRef.current, snapshot, person1Name, person2Name))
    genRef.current++
    setData(null); setSwr(null); setIncSrc(null); setError(null); setLoading(false)
  }, [retAge, ssTiming, overrides, jasonRetAge, justinRetAge, jasonSsClaimAge, justinSsClaimAge])

  // External audit follow-up, 2026-09-09 (CALCULATION_CONTRACT.md
  // section 54): jasonSsClaimAge/justinSsClaimAge are an explicit
  // per-request claim age -- when unset (null), every backend call
  // below already falls back to whatever's saved in Settings via its
  // own 3-tier resolution, so omitting these keys entirely (not
  // sending `null`) preserves that exact fallback rather than
  // overriding it with an explicit null.
  const ssClaimAgeParams = {
    ...(jasonSsClaimAge  != null ? { jason_ss_claim_age: jasonSsClaimAge } : {}),
    ...(justinSsClaimAge != null ? { justin_ss_claim_age: justinSsClaimAge } : {}),
  }

  const run = () => {
    const gen = ++genRef.current
    setLoading(true)
    setError(null)
    setData(null); setSwr(null); setIncSrc(null)
    if (twoAge) {
      // Independent review, 2026-09-08 (P1): this used to always GET the
      // plain endpoint, silently dropping ss_timing and any What-If
      // Builder overrides -- POSTing (same as the single-age branch
      // below) with both threaded through fixes that; the POST endpoint
      // applies overrides==null the same as its own GET does when no
      // What-If assumptions have been set yet.
      // SWR now also supports two-age mode (CALCULATION_CONTRACT.md
      // section 25/26, Milestone 1) -- fetched alongside Monte Carlo so
      // the existing "Safe Spending Power" card (already reused for
      // two-age mode) is actually populated instead of permanently
      // showing "Run simulation to calculate".
      Promise.all([
        axios.post('/api/simulation/monte-carlo', { ...overrides, ...ssClaimAgeParams, ss_timing: ssTiming, jason_ret_age: jasonRetAge, justin_ret_age: justinRetAge }),
        axios.post('/api/simulation/swr', { ...overrides, ...ssClaimAgeParams, ss_timing: ssTiming, jason_ret_age: jasonRetAge, justin_ret_age: justinRetAge }),
      ]).then(([mc, sw]) => {
          if (gen !== genRef.current) return
          // See lastRunSnapshotRef's own comment above -- this is the
          // ONLY place a completed Monte Carlo run's snapshot is
          // recorded, so the next "cleared" message compares against
          // what actually produced the numbers on screen a moment ago.
          lastRunSnapshotRef.current = { retAge, ssTiming, overrides, jasonRetAge, justinRetAge, jasonSsClaimAge, justinSsClaimAge }
          setData(mc.data)
          setSwr(sw.data)
          setLoading(false)
        }).catch(() => {
          if (gen !== genRef.current) return
          setLoading(false)
          setError('Simulation failed to run. Check both ages and try again.')
        })
      return
    }
    const mcCall = overrides
      ? axios.post('/api/simulation/monte-carlo', { ...overrides, ...ssClaimAgeParams, ret_age: retAge, ss_timing: ssTiming })
      : axios.get('/api/simulation/monte-carlo', { params: { ret_age: retAge, ss_timing: ssTiming, ...ssClaimAgeParams } })
    Promise.all([
      mcCall,
      axios.post('/api/simulation/swr', { ...overrides, ...ssClaimAgeParams, ret_age: retAge, ss_timing: ssTiming }),
      axios.post('/api/retirement/income-sources', { ...overrides, ...ssClaimAgeParams, ret_age: retAge, ss_timing: ssTiming })
    ]).then(([mc, sw, inc]) => {
      if (gen !== genRef.current) return // stale — selection changed or a newer run superseded this one
      lastRunSnapshotRef.current = { retAge, ssTiming, overrides, jasonRetAge, justinRetAge, jasonSsClaimAge, justinSsClaimAge }
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
      {clearReason && (
        <div style={{ fontSize:12, color:'var(--text3)', marginBottom:16 }}>
          Cleared the previous result — {clearReason}. Run again to see the updated numbers.
        </div>
      )}
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
      <AssumptionsUsed retAge={retAge} ssTiming={ssTiming} jasonRetAge={jasonRetAge} justinRetAge={justinRetAge}
                        jasonSsClaimAge={jasonSsClaimAge} justinSsClaimAge={justinSsClaimAge}
                        savedJasonClaimAge={savedJasonClaimAge} savedJustinClaimAge={savedJustinClaimAge} overrides={overrides}
                        person1Name={person1Name} person2Name={person2Name} data={data} />
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
          <SecondEarnerNote {...secondEarnerNoteProps(data, person1Name, person2Name)} />
        </div>
        <div className="card">
          <div className="label">Safe Portfolio Withdrawal</div>
          {swr ? (<>
            {/* External audit review, 2026-09-09: this headline used to
                repeat data.success_rate -- the exact same number as the
                "Probability of Success" card immediately to the left.
                The actually-useful figure (the safe annual draw) was
                buried in the detail rows below. Headline is now the
                dollar amount, with rate + cushion right underneath it.
                Card title and the "target" line below were also both
                renamed (external audit follow-up, 2026-09-09, finding
                #5) to say plainly what each number is: this card is a
                SIMPLIFIED single-withdrawal-rate check (SWR search),
                separate from the full Monte Carlo plan above -- the two
                can disagree, since SWR doesn't model market-return
                sequencing year by year the way Monte Carlo does. */}
            <div className="number-lg" style={{ color: data.success_rate >= 95 ? GREEN : data.success_rate >= 85 ? AMBER : RED, marginTop:8 }}>
              {fmtK(swr.safe_withdrawal_annual)}/yr
            </div>
            <div style={{ fontSize:12, color:'var(--text2)', marginTop:4 }}>
              {swr.safe_withdrawal_rate}% draw rate · {swr.cushion_pct > 0 ? '+' : ''}{swr.cushion_pct}% cushion vs {fmtK(swr.income_target)}/yr target (including healthcare, in retirement-year dollars)
            </div>
            <div style={{ fontSize:11, color:'var(--text3)', marginTop:6, lineHeight:1.5 }}>
              A simplified single-rate check, separate from the Monte Carlo simulation above — the two can disagree.
            </div>
            {retAge === 55 && data.mode !== 'two_age' ? (
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
                {/* External audit review, 2026-09-09: this figure and
                    the "Guaranteed Income Floor" card's headline use
                    DIFFERENT timing bases -- this one is day-one
                    (grown only to the retirement start year), the
                    other is steady-state (grown further, to whenever
                    all income sources including SS have actually
                    started). A household could see this "safe spend"
                    number LOWER than the "floor" and read it as
                    contradictory, when it's really two different
                    points in time. Labeled explicitly rather than
                    silently sharing an ambiguous "annual" framing. */}
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'var(--text2)' }}>Total spending capacity (day-one)</span>
                  <span>{fmtK(swr.total_safe_spend)}/yr</span>
                </div>
                <SecondEarnerNote {...secondEarnerNoteProps(swr, person1Name, person2Name)} />
              </div>
            )}
          </>) : <div style={{ fontSize:12, color:'var(--text2)', marginTop:8 }}>Run simulation to calculate</div>}
        </div>
        <div className="card">
          <div className="label">Guaranteed Income Floor (steady-state)</div>
          {swr ? (<>
            <div className="number-lg" style={{ color:NAVY, marginTop:8, fontSize:22 }}>
              {fmtK(swr.guaranteed_income_steadystate)}/yr
            </div>
            <div style={{ fontSize:12, color:'var(--text2)', marginTop:4 }}>
              Once all sources active at age {swr.ss_start_age} — not directly comparable to "Total spending capacity" above, which is a day-one figure
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
          <div className="label" style={{ marginBottom:4 }}>Income Sources by Year — Gross Cash Flows</div>
          <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>
            What covers your spending each year — pension, SS, bridge job, and portfolio withdrawal.
            Figures are GROSS cash flows, not net spending: once RMDs start (age 73 or 75, by birth
            year), the amount shown includes the full forced distribution even in years most of it
            is immediately reinvested rather than spent — see "RMD (forced)" vs. "Discretionary
            withdrawal" below and the reinvested amount in each year's tooltip.
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={incSrc.chart} margin={{ top:0, right:0, bottom:0, left:10 }}>
              <XAxis dataKey="age" tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={60} />
              <Tooltip content={<IncomeSourcesTooltip person1Name={person1Name} />} />
              <Legend wrapperStyle={{ fontSize:11, paddingTop:8 }} />
              <Area type="monotone" dataKey="pension"         stackId="1" name="Pension"        stroke={NAVY} fill={NAVY} fillOpacity={0.8} />
              <Area type="monotone" dataKey="social_security" stackId="1" name="Social Security" stroke="#2E7D8C" fill="#2E7D8C" fillOpacity={0.8} />
              <Area type="monotone" dataKey="bridge_income"   stackId="1" name="Bridge Job"     stroke="#34d399" fill="#34d399" fillOpacity={0.8} />
              <Area type="monotone" dataKey="discretionary_withdrawal" stackId="1" name="Discretionary Withdrawal" stroke="#f97316" fill="#f97316" fillOpacity={0.8} />
              <Area type="monotone" dataKey="rmd"              stackId="1" name="RMD (Forced)"   stroke="#c2410c" fill="#c2410c" fillOpacity={0.8} />
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
export function StressTestSection({ retAge, ssTiming, overrides, jasonRetAge, justinRetAge, jasonSsClaimAge, justinSsClaimAge, savedJasonClaimAge, savedJustinClaimAge }) {
  const { person1Name, person2Name } = usePersonNames()
  const [data, setData]       = useState(null)
  const [roth, setRoth]       = useState(null)
  const [contrib, setContrib] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)
  // See MonteCarloSection's identical comment/flag above.
  const twoAge = jasonRetAge != null && justinRetAge != null
  // See MonteCarloSection's genRef comment above — same stale-response
  // guard and error surfacing (external audit 2026-09-07, finding #13).
  const genRef = useRef(0)
  // See MonteCarloSection's identical describeAssumptionChange/
  // lastRunSnapshotRef comment above.
  const lastRunSnapshotRef = useRef(null)
  const [clearReason, setClearReason] = useState(null)

  useEffect(() => {
    const snapshot = { retAge, ssTiming, overrides, jasonRetAge, justinRetAge, jasonSsClaimAge, justinSsClaimAge }
    setClearReason(describeAssumptionChange(lastRunSnapshotRef.current, snapshot, person1Name, person2Name))
    genRef.current++
    setData(null); setRoth(null); setContrib(null); setError(null); setLoading(false)
  }, [retAge, ssTiming, overrides, jasonRetAge, justinRetAge, jasonSsClaimAge, justinSsClaimAge])
  const [active, setActive]   = useState('crash_2008')

  // See MonteCarloSection's identical comment (2026-09-09, section 54).
  const ssClaimAgeParams = {
    ...(jasonSsClaimAge  != null ? { jason_ss_claim_age: jasonSsClaimAge } : {}),
    ...(justinSsClaimAge != null ? { justin_ss_claim_age: justinSsClaimAge } : {}),
  }

  const run = () => {
    const gen = ++genRef.current
    setLoading(true)
    setError(null)
    setData(null); setRoth(null); setContrib(null)
    if (twoAge) {
      // See MonteCarloSection's identical comment above -- POST with
      // ss_timing/overrides threaded through instead of the plain GET.
      axios.post('/api/simulation/stress-tests', { ...overrides, ...ssClaimAgeParams, ss_timing: ssTiming, jason_ret_age: jasonRetAge, justin_ret_age: justinRetAge })
        .then(st => {
          if (gen !== genRef.current) return
          lastRunSnapshotRef.current = { retAge, ssTiming, overrides, jasonRetAge, justinRetAge, jasonSsClaimAge, justinSsClaimAge }
          setData(st.data)
          setLoading(false)
        }).catch(() => {
          if (gen !== genRef.current) return
          setLoading(false)
          setError('Stress test failed to run. Check both ages and try again.')
        })
      return
    }
    const stCall = overrides
      ? axios.post('/api/simulation/stress-tests', { ...overrides, ...ssClaimAgeParams, ret_age: retAge, ss_timing: ssTiming })
      : axios.get('/api/simulation/stress-tests', { params: { ret_age: retAge, ss_timing: ssTiming, ...ssClaimAgeParams } })
    Promise.all([
      stCall,
      axios.post('/api/simulation/roth-conversion', { ...overrides, ...ssClaimAgeParams, ret_age: retAge, ss_timing: ssTiming }),
      axios.post('/api/simulation/contribution-sensitivity', { ...overrides, ret_age: retAge, ss_timing: ssTiming })
    ]).then(([st, rc, cs]) => {
      if (gen !== genRef.current) return // stale — selection changed or a newer run superseded this one
      lastRunSnapshotRef.current = { retAge, ssTiming, overrides, jasonRetAge, justinRetAge, jasonSsClaimAge, justinSsClaimAge }
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
      {clearReason && (
        <div style={{ fontSize:12, color:'var(--text3)', marginBottom:16 }}>
          Cleared the previous result — {clearReason}. Run again to see the updated numbers.
        </div>
      )}
      {error && <div style={{ fontSize:13, color:'var(--red)', marginBottom:16 }}>{error}</div>}
      <button className="btn-primary" onClick={run} style={{ padding:'12px 32px', fontSize:14 }}>
        Run Stress Tests
      </button>
    </div>
  )

  const scenarios = data.scenarios
  const base = scenarios.base
  // All 7 scenarios run in both single-age and two-age mode
  // (CALCULATION_CONTRACT.md section 23 -- stagflation_1970s/
  // bridge_job_loss/ss_reduction used to be skipped in two-age mode's
  // first cut). .filter() stays as a defensive no-op rather than an
  // unguarded index, in case a future response is ever missing a key.
  const stressKeys = ['crash_2008', 'stagflation_1970s', 'lost_decade'].filter(k => scenarios[k])
  const newStressKeys = ['early_sequence', 'bridge_job_loss', 'ss_reduction'].filter(k => scenarios[k])
  const current = scenarios[active] || scenarios[stressKeys[0]]

  return (
    <div>
      <AssumptionsUsed retAge={retAge} ssTiming={ssTiming} jasonRetAge={jasonRetAge} justinRetAge={justinRetAge}
                        jasonSsClaimAge={jasonSsClaimAge} justinSsClaimAge={justinSsClaimAge}
                        savedJasonClaimAge={savedJasonClaimAge} savedJustinClaimAge={savedJustinClaimAge} overrides={overrides}
                        person1Name={person1Name} person2Name={person2Name} data={data} />
      <SecondEarnerNote {...secondEarnerNoteProps(data, person1Name, person2Name)} />
      {/* External audit review, 2026-09-09: cards changed border color
          when selected, but nothing said they were clickable or
          explained why the chart below moves when you click one --
          "there's no affordance" and "it isn't obvious why clicking a
          card changes the chart below." Explicit instruction line, and
          a "▸ Viewing this scenario" label on whichever card is
          currently selected, added below. */}
      <div style={{ fontSize:12, color:'var(--text2)', marginBottom:12 }}>
        Click a scenario below to see its detail chart.
      </div>
      {/* Summary cards */}
      <div className="grid-3" style={{ marginBottom:24 }}>
        {stressKeys.map(key => {
          const s = scenarios[key]
          const ok = s.survived
          const selected = active === key
          return (
            <div key={key}
              className="card"
              onClick={() => setActive(key)}
              style={{ cursor:'pointer', borderColor: selected ? 'var(--accent)' : 'var(--border)', borderWidth: selected ? 2 : 1, transition:'border-color 0.15s' }}
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
              {selected && (
                <div style={{ marginTop:10, fontSize:11, color:'var(--accent)', fontWeight:600 }}>▸ Viewing this scenario's chart below</div>
              )}
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
            const selected = active === key
            return (
              <div key={key}
                className="card"
                onClick={() => setActive(key)}
                style={{ cursor:'pointer', borderColor: selected ? 'var(--accent)' : 'var(--border)', borderWidth: selected ? 2 : 1, transition:'border-color 0.15s' }}
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
                {selected && (
                  <div style={{ marginTop:10, fontSize:11, color:'var(--accent)', fontWeight:600 }}>▸ Viewing this scenario's chart below</div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* External audit review, 2026-09-09: this chart is the direct
          answer to "what does the scenario I just clicked actually do
          to my portfolio" -- it used to render LAST on the page, after
          both the Roth Conversion Optimizer and Contribution Rate
          Sensitivity sections, so clicking a card and scrolling down
          to see its effect meant passing two unrelated tools first
          ("the page continues into ... unrelated analysis," "buries
          its primary answer"). Moved to render immediately after the
          scenario cards instead. */}
      {current && (
        <div className="card" style={{ marginBottom:24 }}>
          <div className="label" style={{ marginBottom:4 }}>{current.label} — vs Base Case</div>
          <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>{current.description}</div>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={current.chart} margin={{ top:0, right:0, bottom:0, left:10 }}>
              <XAxis dataKey="age" tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={60} />
              <Tooltip content={<CustomTooltip person1Name={person1Name} />} />
              <Legend wrapperStyle={{ fontSize:11 }} />
              <ReferenceLine y={0} stroke={RED} strokeDasharray="3 3" label={{ value:'$0', fill:RED, fontSize:10 }} />
              {/* External audit follow-up, 2026-09-09: this legend
                  label was hardcoded "(6%/yr)" regardless of the
                  actual post-retirement return in effect -- with a
                  What-If override active (e.g. 7%), the assumptions
                  panel correctly showed 7% while this legend still
                  said 6%, reading as contradictory. base.label is the
                  backend's own dynamically-computed base-case label
                  (f"Base Case ({post_ret*100:g}% every year)"),
                  already correct -- just wasn't being used here. */}
              <Line type="monotone" dataKey="base"    name={base?.label || 'Base Case'} stroke="var(--text3)" strokeWidth={1.5} strokeDasharray="4 2" dot={false} />
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

      {/* External audit review, 2026-09-09: Roth Conversion Optimizer
          and Contribution Rate Sensitivity are useful, related tools,
          but rendering them inline made the Historical Stress tab
          "feel endless" and obscured the question the tab actually
          answers. Collapsed by default behind a single disclosure --
          still one click away, no longer competing with the stress
          result for attention. */}
      {(roth || contrib) && (
        <details style={{ marginTop:24, marginBottom:24 }}>
          <summary style={{ cursor:'pointer', fontSize:13, color:'var(--text2)', padding:'4px 0' }}>
            More tools: Roth Conversion Optimizer &amp; Contribution Rate Sensitivity
          </summary>
      {/* Roth conversion schedule */}
      {roth && (
        <div className="card" style={{ marginTop:16, marginBottom:24 }}>
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
          <div>
            <SecondEarnerNote amount={roth.schedule?.[0]?.justin_gap_income}
                               years={roth.schedule?.filter(r => r.justin_gap_income > 0).length}
                               factor={roth.second_earner_net_of_tax_factor} personLabel={person2Name} />
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
        </details>
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
