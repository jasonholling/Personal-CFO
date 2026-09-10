import { useState, useEffect } from 'react'
import axios from 'axios'
import TaskPanel from '../components/TaskPanel'
import { BarChart, Bar, AreaChart, Area, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { usePersonNames } from '../hooks/usePersonNames'
import { useScenario } from '../hooks/useScenario'
import { useSsAnchors } from '../hooks/useSsAnchors'
import { nearestOf } from '../utils/scenario'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'
import SecondEarnerNote from '../components/SecondEarnerNote'
import ActiveScenarioBanner from '../components/ActiveScenarioBanner'
import ClaimAgeSlider from '../components/ClaimAgeSlider'
import CalculationExplainer from '../components/CalculationExplainer'

const NAVY = '#5C7CE0' // was #1B3A6B — nearly the same luminance as the dark card background, effectively invisible

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n)
const fmtK = (n) => isPrivacyMode() ? MASK_CURRENCY : (n >= 1000000 ? `$${(n/1000000).toFixed(2)}M` : `$${(n/1000).toFixed(0)}K`)

const CustomTooltip = ({ active, payload, label, person1Name }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, padding:'10px 14px', fontSize:12 }}>
      <div style={{ fontWeight:600, marginBottom:6 }}>{person1Name} Age {label}</div>
      {payload.map(p => <div key={p.name} style={{ color:p.color, marginBottom:2 }}>{p.name}: {fmt(p.value)}</div>)}
    </div>
  )
}

// Full 55-67 range, matching what /api/projections/retirement now actually
// returns (previously just [55,56,57,58,59,60,65] — that stale subset used
// to silently snap any age outside it, e.g. 62, down to the nearest one in
// the list (60) via nearestOf below, even though the backend has supported
// every individual age for a while (external audit 2026-09-06). nearestOf
// is still applied as a defensive no-op — sharedRetAge should always
// already be in this range since every selector on every page sources it
// from the same 55-67 buttons, but a stale localStorage value from before
// this fix, or before the backend supported the full range, shouldn't crash
// the page.
const RET_AGES = [55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67]

export default function Retirement({ onNavigate }) {
  const { person1Name, person2Name } = usePersonNames()
  const [data, setData]         = useState(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)
  const {
    retAge: sharedRetAge, ssTiming, setRetAge, setSsTiming,
    jasonSsClaimAge, justinSsClaimAge, setJasonSsClaimAge, setJustinSsClaimAge,
  } = useScenario()
  const ssAnchors = useSsAnchors()
  const retAge = nearestOf(sharedRetAge, RET_AGES)
  // External audit follow-up, 2026-09-09 (CALCULATION_CONTRACT.md
  // section 54): this page previously had NO claim-age control at all
  // and just showed a banner pointing elsewhere -- the user's own
  // request was "I want [claim age] on all the pages where I can pick
  // what age I am taking it... so I should be able to vary that and
  // run tests against it." get_retirement_projections deliberately
  // does NOT fall back to the saved Settings value on its own (see its
  // own docstring) to protect WhatIf.jsx's early/delayed-pair
  // dependency, so this page's own slider below is the only way to
  // see a custom claim age reflected here -- it's a genuine, explicit
  // per-request override, not a read of the Settings value.

  useEffect(() => {
    const params = {}
    if (jasonSsClaimAge  != null) params.jason_ss_claim_age  = jasonSsClaimAge
    if (justinSsClaimAge != null) params.justin_ss_claim_age = justinSsClaimAge
    setLoading(true)
    axios.get('/api/projections/retirement', { params })
      .then(r => { setData(r.data); setLoading(false) })
      .catch(e => { setError(e.response?.data?.detail || 'Could not load projections'); setLoading(false) })
  }, [jasonSsClaimAge, justinSsClaimAge])

  if (loading) return <div className="loading">Running projections...</div>
  if (error)   return (
    <div>
      <div className="card" style={{ marginTop:24, textAlign:'center', padding:'48px 24px' }}>
        <div style={{ color:'var(--amber)', marginBottom:12 }}>⚠ {error}</div>
        <button className="btn-primary" onClick={() => onNavigate('settings')}>Set Up Planning Inputs →</button>
      </div>
    </div>
  )

  // With the override on, run_retirement_projection replaces the
  // early/delayed pair with a single "custom" scenario per spouse
  // resolution (CALCULATION_CONTRACT.md section 44) -- the label is
  // still driven by Jason's own claim age specifically (section 52,
  // finding 2), so "custom" only appears when Jason's slider is on;
  // with only Justin's slider on, the label stays early/delayed as
  // usual and Justin's own benefit is reflected within it.
  const label = jasonSsClaimAge != null ? `age_${retAge}_custom` : `age_${retAge}_${ssTiming}`
  const s = data?.scenarios?.find(sc => sc.label === label)
  if (!s) return <div className="loading">No data for this scenario</div>

  const earlyScenario   = data.scenarios.find(sc => sc.label === `age_${retAge}_early`)
  const delayedScenario = data.scenarios.find(sc => sc.label === `age_${retAge}_delayed`)

  const chartData = s.yearly_detail
    .filter((_, i) => i % 2 === 0)
    .slice(0, 30)
    .map(y => ({
      age:                        y.jason_age,
      'Pension':                   y.pension,
      'Social Security':           y.social_security,
      'Required Min. Dist.':        y.rmd_reinvested > 0 ? y.rmd_reinvested : 0,
      'Withdrawals from Assets':   y.withdrawal,
    }))

  const pct          = s.percent_funded
  const surplusColor = s.projected_surplus >= 0 ? 'var(--green)' : 'var(--red)'
  const yearsToRet   = s.years_to_retirement

  // Years portfolio lasts
  const lastYear = s.yearly_detail.findIndex(y => y.portfolio_balance === 0)
  const portfolioLasts = lastYear === -1 ? `Beyond age ${s.retirement_end_age}` : `Until ${person1Name} age ${s.yearly_detail[lastYear]?.jason_age}`

  // Milestone 2 (2026-09-09, "Explain every major result"): every value
  // fed to CalculationExplainer below is read straight from `s` / the
  // per-year rows already returned by /api/projections/retirement --
  // opening balance is the only derived value, and it's just a carry-
  // forward of the previous row's own closing balance (or
  // portfolio_at_retirement for year 0), not a recomputed formula.
  const explainerFlows = s.yearly_detail.map((y, i) => ({
    year: y.year, jasonAge: y.jason_age,
    opening: i === 0 ? s.portfolio_at_retirement : s.yearly_detail[i - 1].portfolio_balance,
    income: y.pension + y.social_security + y.bridge_income,
    spending: y.income_need,
    taxes: y.estimated_tax,
    withdrawal: y.withdrawal,
    withdrawalBreakdown: { pretax: y.withdrawal_pretax, taxable: y.withdrawal_taxable, roth: y.withdrawal_roth, hsa: y.withdrawal_hsa },
    unmetNeed: y.unmet_need,
    closing: y.portfolio_balance,
  }))
  const explainerAssumptions = [
    ['Retirement age', `${retAge}`],
    ['Social Security', jasonSsClaimAge != null ? `custom claim age ${jasonSsClaimAge}` : (ssTiming === 'delayed' ? 'wait until 67' : 'take at 62')],
    [`${person2Name}'s SS`, justinSsClaimAge != null ? `custom claim age ${justinSsClaimAge}` : 'spousal, tied to the toggle above'],
    ['Modeled through age', `${s.retirement_end_age}`],
    ['State tax rate on distributions', `${((s.state_income_tax_rate || 0) * 100).toFixed(1)}%`],
  ]

  return (
    <div>
      <div style={{ marginBottom:28 }}>
        <p className="section-sub" style={{ margin:0 }}>Three scenarios · Toggle SS timing · Modeled to age {s.retirement_end_age}</p>
      </div>

      <ActiveScenarioBanner />

      <CalculationExplainer
        calcDate={new Date().toLocaleDateString()}
        assumptions={explainerAssumptions}
        dollarBasis={{ label: 'First-year retirement income need', todayValue: s.income_today_dollars, futureValue: s.income_first_year }}
        flows={explainerFlows}
        notes={[
          "Monte Carlo, Stress Test, and this page can show different funded percentages for what looks like the same household — each runs its own return sequence/assumptions set, not a shared single number. See each page's own methodology if two figures disagree.",
        ]}
      />

      {/* Scenario selector */}
      <div style={{ display:'flex', gap:24, marginBottom:24, alignItems:'flex-start', flexWrap:'wrap' }}>
        <div>
          <div className="label" style={{ marginBottom:8 }}>Retirement Age</div>
          <div style={{ display:'flex', gap:6, flexWrap:'wrap' }}>
            {RET_AGES.map(age => (
              <button key={age}
                className={retAge === age ? 'btn-primary' : 'btn-secondary'}
                onClick={() => setRetAge(age)}
              >
                Age {age}
              </button>
            ))}
          </div>
        </div>
        {/* External audit follow-up, 2026-09-09: shown-but-disabled
            buttons next to a still-visible toggle read as confusing
            broken UI once the slider below is active ("i just want
            the slider and not the override thing with buttons still
            below") -- hidden entirely instead, same fix as
            StressTestWhatIf.jsx. */}
        {jasonSsClaimAge == null && (
          <div>
            <div className="label" style={{ marginBottom:8 }}>Social Security</div>
            <div style={{ display:'flex', gap:6 }}>
              <button
                className={ssTiming === 'early' ? 'btn-primary' : 'btn-secondary'}
                onClick={() => setSsTiming('early')}
              >
                Take at 62{earlyScenario?.jason_ss_annual ? ` · ${fmt(earlyScenario.jason_ss_annual / 12)}/mo` : ''}
              </button>
              <button
                className={ssTiming === 'delayed' ? 'btn-primary' : 'btn-secondary'}
                onClick={() => setSsTiming('delayed')}
              >
                Wait until 67{delayedScenario?.jason_ss_annual ? ` · ${fmt(delayedScenario.jason_ss_annual / 12)}/mo` : ''}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Custom claim age (2026-09-09, CALCULATION_CONTRACT.md section
          54): a page-local, ad hoc override that also carries to Monte
          Carlo/Stress Tests/Roth Conversion/Survivor Scenario (shared
          scenario state, same as retAge/ssTiming above). Unlike those
          pages, this one's own claim age does NOT read from Settings
          automatically -- it's only reflected here once this slider is
          turned on. */}
      {/* Milestone 3 (progressive disclosure, 2026-09-09): collapsed by
          default via <details> -- this is an ADVANCED, optional override
          most visits never touch (the Take at 62/Wait until 67 toggle
          above covers the common case). Stays open automatically once
          either slider is actually on, so an active override is never
          hidden. */}
      <details className="card" style={{ marginBottom:24, padding:'16px 20px' }} open={jasonSsClaimAge != null || justinSsClaimAge != null}>
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
            offHint="off = use the Take at 62 / Wait until 67 toggle above"
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
            offHint="off = 50% of the toggle's own selected age above"
            compact
          />
        </div>
        {(ssAnchors.savedJasonClaimAge != null || ssAnchors.savedJustinClaimAge != null) && jasonSsClaimAge == null && justinSsClaimAge == null && (
          <div style={{ marginTop:10, fontSize:11, color:'var(--text3)' }}>
            ℹ You have a claim age saved in Settings ({person1Name}: {ssAnchors.savedJasonClaimAge ?? '—'},
            {' '}{person2Name}: {ssAnchors.savedJustinClaimAge ?? '—'}) — it's used automatically on Monte Carlo,
            Stress Tests, Roth Conversion, and Survivor Scenario, but not on this page unless you turn a slider
            on above.
          </div>
        )}
      </details>

      {/* KPI row */}
      <div className="grid-4" style={{ marginBottom:24 }}>
        <div className="card">
          <div className="label">Years to Retirement</div>
          <div className="number-lg" style={{ marginTop:8 }}>{yearsToRet}</div>
          <div style={{ fontSize:12, color:'var(--text2)', marginTop:4 }}>
            {yearsToRet === 0 ? 'Now' : `${person1Name} at ${retAge} · ${2026 + yearsToRet}`}
          </div>
        </div>
        <div className="card">
          <div className="label">Pension Income</div>
          <div className="number-lg" style={{ color:'var(--green)', marginTop:8 }}>{fmtK(s.pension_annual)}/yr</div>
          <div style={{ fontSize:12, color:'var(--text2)', marginTop:4 }}>100% Joint & Survivor</div>
        </div>
        <div className="card">
          <div className="label">Portfolio at Retirement</div>
          <div className="number-lg" style={{ color:'var(--accent)', marginTop:8 }}>{fmtK(s.portfolio_at_retirement)}</div>
          <div style={{ fontSize:12, color:'var(--text2)', marginTop:4 }}>From {fmtK(s.current_investable_assets)} today</div>
          <SecondEarnerNote amount={s.yearly_detail?.[0]?.justin_gap_income}
                             years={s.yearly_detail?.filter(y => y.justin_gap_income > 0).length}
                             factor={s.second_earner_net_of_tax_factor} personLabel={person2Name} />
        </div>
        <div className="card">
          <div className="label">Projected Surplus</div>
          <div className="number-lg" style={{ color:surplusColor, marginTop:8 }}>{fmtK(Math.abs(s.projected_surplus))}</div>
          <span className={`tag ${s.on_track ? 'tag-green' : 'tag-red'}`} style={{ marginTop:6, display:'inline-block' }}>
            {s.on_track ? '✓ On track' : '⚠ Shortfall'}
          </span>
        </div>
      </div>

      {/* Healthcare callout */}
      <div style={{ padding:'12px 16px', background:'rgba(79,156,249,0.06)', border:'1px solid rgba(79,156,249,0.15)', borderRadius:8, marginBottom:16, display:'grid', gridTemplateColumns:'1fr auto', gap:'4px 24px', fontSize:13 }}>
        <div style={{ color:'var(--text2)', fontWeight:600, gridColumn:'1/-1', marginBottom:4 }}>🏥 Healthcare Costs Included in Projection</div>
        <span style={{ color:'var(--text3)' }}>Pre-Medicare gap (age 60–65) · ACA marketplace</span>
        <span style={{ fontWeight:600, color:'var(--accent)', textAlign:'right' }}>{fmt(s.healthcare_pre_annual)}/yr · {fmt(s.healthcare_gap_total)} total</span>
        <span style={{ color:'var(--text3)' }}>Post-Medicare (age 65+) · Medicare + supplement</span>
        <span style={{ fontWeight:600, color:'var(--accent)', textAlign:'right' }}>{fmt(s.healthcare_post_annual)}/yr</span>
        <span style={{ color:'var(--text3)', fontSize:11, gridColumn:'1/-1', marginTop:2 }}>Adjust these amounts in Planning Inputs → Healthcare section</span>
      </div>

      {/* Capitalized summary */}
      <div className="card" style={{ marginBottom:24 }}>
        <div className="label" style={{ marginBottom:16 }}>Capitalized Value Summary</div>
        <div style={{ display:'grid', gridTemplateColumns:'1fr auto auto', gap:'10px 24px', alignItems:'center' }}>
          <div style={{ color:'var(--text2)', fontSize:13 }}>Total Capitalized Income Objective</div>
          <div style={{ textAlign:'right', fontWeight:500 }}>{fmt(s.total_capitalized_need)}</div>
          <div />

          <div style={{ color:'var(--text2)', fontSize:13 }}>Pension (100% J&S)</div>
          <div style={{ textAlign:'right', fontWeight:500 }}>{fmt(s.pension_annual)}/yr</div>
          <div />

          <div style={{ color:'var(--text2)', fontSize:13 }}>
            {person1Name} SS · starts at {s.jason_ss_start_age} ({jasonSsClaimAge != null ? `claimed at ${jasonSsClaimAge}` : ssTiming === 'early' ? 'early' : 'delayed'})
          </div>
          <div style={{ textAlign:'right', fontWeight:500 }}>{fmt(s.jason_ss_annual)}/yr</div>
          <div />
          <div style={{ color:'var(--text2)', fontSize:13 }}>
            {person2Name} spousal SS · starts at {s.justin_ss_start_age} (50% of {person1Name} FRA)
          </div>
          <div style={{ textAlign:'right', fontWeight:500 }}>{fmt(s.justin_ss_annual)}/yr</div>
          <div />

          <div style={{ color:'var(--text2)', fontSize:13 }}>Capitalized Applied Income Sources</div>
          <div style={{ textAlign:'right', fontWeight:500 }}>{fmt(s.capitalized_income_sources)}</div>
          <div style={{ textAlign:'right' }}>
            <span className="tag tag-blue">
              {Math.round(s.capitalized_income_sources / s.total_capitalized_need * 100)}%
            </span>
          </div>

          <div className="divider" style={{ gridColumn:'1/-1', margin:'4px 0' }} />

          <div style={{ fontWeight:600 }}>Amount Needed From Portfolio</div>
          <div style={{ textAlign:'right', fontWeight:700 }}>{fmt(s.capitalized_needed_from_assets)}</div>
          <div style={{ textAlign:'right' }}>
            <span className="tag tag-amber">
              {Math.round(s.capitalized_needed_from_assets / s.total_capitalized_need * 100)}%
            </span>
          </div>

          <div style={{ color:'var(--text2)', fontSize:13 }}>Portfolio at Retirement</div>
          <div style={{ textAlign:'right', fontWeight:500 }}>{fmt(s.portfolio_at_retirement)}</div>
          <div />

          <div className="divider" style={{ gridColumn:'1/-1', margin:'4px 0' }} />

          <div style={{ fontWeight:600 }}>Projected Surplus</div>
          <div style={{ textAlign:'right', fontWeight:700, color:surplusColor }}>{fmt(s.projected_surplus)}</div>
          <div style={{ textAlign:'right' }}>
            <span className={`tag ${s.projected_surplus >= 0 ? 'tag-green' : 'tag-red'}`}>
              {portfolioLasts}
            </span>
          </div>
        </div>

        <div style={{ marginTop:20 }}>
          <div style={{ display:'flex', justifyContent:'space-between', marginBottom:6 }}>
            <span style={{ fontSize:12, color:'var(--text2)' }}>Retirement funded</span>
            <span style={{ fontSize:12, fontWeight:600, color: pct >= 100 ? 'var(--green)' : 'var(--amber)' }}>{pct}%</span>
          </div>
          <div className="progress-bar-track" style={{ height:10 }}>
            <div className="progress-bar-fill" style={{
              width:`${Math.min(100, pct)}%`,
              background: pct >= 100 ? 'var(--green)' : pct >= 75 ? 'var(--amber)' : 'var(--red)'
            }} />
          </div>
        </div>
      </div>

      {/* Portfolio burndown chart */}
      <div className="card" style={{ marginBottom:24 }}>
        <div className="label" style={{ marginBottom:4 }}>Portfolio Balance Over Time</div>
        <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>Does the money last? Draw order: taxable first · pre-tax (RMDs at 73) · Roth last</div>
        <ResponsiveContainer width="100%" height={240}>
          <AreaChart data={s.yearly_detail.filter((_,i) => i%2===0)} margin={{ top:0, right:0, bottom:0, left:10 }}>
            <defs>
              <linearGradient id="gPretax" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#4f9cf9" stopOpacity={0.4}/><stop offset="95%" stopColor="#4f9cf9" stopOpacity={0}/>
              </linearGradient>
              <linearGradient id="gRoth" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#34d399" stopOpacity={0.4}/><stop offset="95%" stopColor="#34d399" stopOpacity={0}/>
              </linearGradient>
              <linearGradient id="gTaxable" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#fbbf24" stopOpacity={0.3}/><stop offset="95%" stopColor="#fbbf24" stopOpacity={0}/>
              </linearGradient>
            </defs>
            <XAxis dataKey="jason_age" tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} label={{ value:'Age', position:'insideBottom', offset:-2, fill:'var(--text3)', fontSize:11 }} />
            <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={56} />
            <Tooltip
              contentStyle={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, fontSize:12 }}
              formatter={(v, n) => [fmt(v), n]}
              labelFormatter={l => `${person1Name} Age ${l}`}
            />
            <Legend wrapperStyle={{ fontSize:11 }} />
            <Area type="monotone" dataKey="taxable_balance"  name="Taxable"    stroke="#fbbf24" strokeWidth={2} fill="url(#gTaxable)" stackId="a" />
            <Area type="monotone" dataKey="pretax_balance"   name="Pre-Tax"    stroke="#4f9cf9" strokeWidth={2} fill="url(#gPretax)" stackId="a" />
            <Area type="monotone" dataKey="roth_balance"     name="Roth"       stroke="#34d399" strokeWidth={2} fill="url(#gRoth)"   stackId="a" />
          </AreaChart>
        </ResponsiveContainer>
        <div style={{ fontSize:11, color:'var(--text3)', marginTop:8 }}>
          Yellow = taxable brokerage · Blue = pre-tax 401k/IRA (RMDs start age 73) · Green = Roth (drawn last)
        </div>
      </div>

      {/* Income sources chart */}
      <div className="card">
        <div className="label" style={{ marginBottom:16 }}>Income Sources in Retirement (every 2 years)</div>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={chartData} margin={{ top:0, right:0, bottom:0, left:10 }}>
            <XAxis dataKey="age" tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} />
            <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={56} />
            <Tooltip content={<CustomTooltip person1Name={person1Name} />} />
            <Legend wrapperStyle={{ fontSize:12, color:'var(--text2)' }} />
            <Bar dataKey="Pension"                stackId="a" fill={NAVY} />
            <Bar dataKey="Social Security"        stackId="a" fill="#2E7D8C" />
            <Bar dataKey="Required Min. Dist."    stackId="a" fill="#E8C49A" />
            <Bar dataKey="Withdrawals from Assets" stackId="a" fill="#C45C1A" />
          </BarChart>
        </ResponsiveContainer>
        <div style={{ fontSize:11, color:'var(--text3)', marginTop:8 }}>
          Blue = pension income · Teal = Social Security · Tan = required min. distributions · Orange = portfolio withdrawals needed
        </div>
      </div>

      <TaskPanel section="financial_independence" />
    </div>
  )
}