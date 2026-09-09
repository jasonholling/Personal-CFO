import { useEffect, useState } from 'react'
import axios from 'axios'
import { useScenario } from '../hooks/useScenario'
import { usePersonNames } from '../hooks/usePersonNames'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'
import WhatIf from './WhatIf'
import { MonteCarloSection, StressTestSection, RET_AGES, SS_OPTS } from './Simulation'
import SecondEarnerNote from '../components/SecondEarnerNote'

const fmt = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(n))
const GREEN = '#34d399'
const RED   = '#f87171'
const ACCENT = '#4f9cf9'

// Combines What-If Builder + Monte Carlo + Historical Stress Tests + a
// Survivor Scenario under one nav entry — these were separate top-level
// tabs, but they're really the same question ("pressure-test my plan")
// from different angles: custom assumption overrides, randomized market
// returns, specific historical crashes, and — the one risk none of the
// others test — what happens if one of you dies early.
// Part of the RETIREMENT nav consolidation alongside Retirement Projection.
const TABS = [
  { id:'whatif',      label:'What-If Builder' },
  { id:'monte_carlo', label:'Monte Carlo' },
  { id:'stress',      label:'Historical Stress' },
  { id:'survivor',    label:'Survivor Scenario' },
]

function SurvivorScenarioSection({ retAge }) {
  const { person1Name, person2Name } = usePersonNames()
  const [deceased, setDeceased] = useState('jason')
  const [ages, setAges] = useState(null)
  const [deathAge, setDeathAge] = useState(retAge + 10)
  const [deathAgeTouched, setDeathAgeTouched] = useState(false)
  const [needFactor, setNeedFactor] = useState(75)
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    axios.get('/api/planning-inputs').then(r => {
      const d = r.data || {}
      setAges({ jasonAge: d.jason_age, justinAge: d.justin_age })
    }).catch(() => {})
  }, [])

  // "10 years into retirement" default, computed the same way the
  // backend's own default does (timeline_engine.build_timeline +
  // run_survivor_scenario's death_age default) — independent review,
  // 2026-09-07, fourth follow-up: this field used to default to
  // `retAge + 10` regardless of the household's actual current age or
  // which spouse was selected, and was ALWAYS sent explicitly to the
  // API, so the backend's own (now-corrected) default was never
  // actually reachable through this form. effective_start_age anchors
  // to whichever is later, the selected retirement age or Jason's real
  // current age; "10 years into retirement" is then computed in the
  // SELECTED (deceased) spouse's own age terms, converting to Justin's
  // age via the couple's age gap when Justin is the one selected.
  // Recomputes only until the user deliberately edits the field —
  // afterward their typed value is preserved regardless of later
  // deceased/retirement-age changes.
  useEffect(() => {
    if (deathAgeTouched || !ages || ages.jasonAge == null || ages.justinAge == null) return
    const effectiveStartAge = Math.max(retAge, ages.jasonAge)
    const ageGap = ages.jasonAge - ages.justinAge  // positive: Jason older
    const deceasedEffectiveStartAge = deceased === 'jason' ? effectiveStartAge : effectiveStartAge - ageGap
    setDeathAge(deceasedEffectiveStartAge + 10)
  }, [ages, retAge, deceased, deathAgeTouched])

  const run = () => {
    setLoading(true)
    axios.get('/api/simulation/survivor-scenario', {
      params: { ret_age: retAge, deceased, death_age: deathAge, survivor_need_factor: needFactor / 100 },
    }).then(r => setResult(r.data)).finally(() => setLoading(false))
  }

  return (
    <div>
      <div style={{ padding:'14px 16px', background:'rgba(79,156,249,0.06)', border:'1px solid rgba(79,156,249,0.2)', borderRadius:8, marginBottom:24, fontSize:13, color:'var(--text2)', lineHeight:1.7 }}>
        Life insurance is sized as a point-in-time need elsewhere (Insurance page) — this re-runs the actual
        retirement plan assuming one of you dies during retirement: the payout gets added to the portfolio,
        Social Security switches to the higher of the two benefits (not both), and living costs scale down.
        It doesn't model the tax-bracket jump from filing jointly to filing single — a real added drag not
        captured here.
      </div>

      <div className="card" style={{ marginBottom:24 }}>
        <div className="grid-3" style={{ marginBottom:12 }}>
          <div>
            <div className="label" style={{ marginBottom:8 }}>Who Dies First?</div>
            <div style={{ display:'flex', gap:6 }}>
              <button className={deceased==='jason' ? 'btn-primary' : 'btn-secondary'} onClick={() => setDeceased('jason')}>{person1Name}</button>
              <button className={deceased==='justin' ? 'btn-primary' : 'btn-secondary'} onClick={() => setDeceased('justin')}>{person2Name}</button>
            </div>
          </div>
          <div>
            <div className="label" style={{ marginBottom:8 }}>At Age</div>
            <input type="number" value={deathAge} onChange={e => { setDeathAgeTouched(true); setDeathAge(parseInt(e.target.value) || 0) }} />
          </div>
          <div>
            <div className="label" style={{ marginBottom:8 }}>Survivor's Living Cost (% of couple's target)</div>
            <input type="number" value={needFactor} onChange={e => setNeedFactor(parseInt(e.target.value) || 0)} />
          </div>
        </div>
        <button className="btn-primary" onClick={run} disabled={loading}>{loading ? 'Calculating…' : 'Run Scenario'}</button>
      </div>

      {result?.has_data && (
        <>
          <div className="card" style={{ marginBottom:24, borderTop:`3px solid ${result.survives ? GREEN : RED}` }}>
            <div className="label" style={{ marginBottom:16 }}>Verdict</div>
            <div style={{
              padding:'12px 16px', borderRadius:8, fontSize:13, lineHeight:1.6, marginBottom:16,
              background: result.survives ? 'rgba(52,211,153,0.08)' : 'rgba(248,113,113,0.08)',
            }}>
              <strong style={{ color: result.survives ? GREEN : RED }}>{result.survives ? '✓ Plan holds up' : '⚠ Plan runs out'}</strong> — {result.recommendation}
            </div>
            {deceased !== 'justin' && (
              <SecondEarnerNote amount={result.schedule?.[0]?.justin_gap_income}
                                 years={result.justin_gap_income_years_remaining}
                                 factor={result.second_earner_net_of_tax_factor} personLabel={person2Name} />
            )}
            <div className="grid-3">
              <div>
                <div className="label">Life Insurance Payout</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>{fmt(result.life_insurance_payout)}</div>
              </div>
              <div>
                <div className="label">Portfolio at Death</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>{fmt(result.portfolio_at_death)}</div>
              </div>
              <div>
                <div className="label">{result.survives ? 'Cushion' : 'Additional Insurance Needed'}</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4, color: result.survives ? GREEN : RED }}>
                  {result.survives ? fmt(result.starting_balance_after_payout) : fmt(result.additional_insurance_needed)}
                </div>
              </div>
            </div>
          </div>

          <div className="card" style={{ marginBottom:24 }}>
            <div className="label" style={{ marginBottom:16 }}>Survivor's Guaranteed Income</div>
            <div className="grid-2">
              <div>
                <div className="label">Social Security (higher of the two)</div>
                <div style={{ fontSize:14, marginTop:4 }}>{fmt(result.survivor_ss_annual)}/yr</div>
              </div>
              <div>
                <div className="label">Pension (100% Joint &amp; Survivor)</div>
                <div style={{ fontSize:14, marginTop:4 }}>{fmt(result.pension_annual)}/yr</div>
              </div>
            </div>
          </div>

          {result.schedule?.length > 0 && (
            <div className="card">
              <div className="label" style={{ marginBottom:12 }}>Portfolio Trajectory After Death</div>
              <div style={{ overflowX:'auto' }}>
                <table style={{ width:'100%', borderCollapse:'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom:'1px solid var(--border)' }}>
                      {['Age','Starting Balance','Annual Draw','Ending Balance'].map(h => (
                        <th key={h} style={{ padding:'8px 12px', textAlign:'left', fontSize:11, fontWeight:600, letterSpacing:'0.05em', textTransform:'uppercase', color:'var(--text3)' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.schedule.map(s => (
                      <tr key={s.age} style={{ borderBottom:'1px solid var(--border)' }}>
                        <td style={{ padding:'8px 12px', fontSize:13 }}>{s.age}</td>
                        <td style={{ padding:'8px 12px', fontSize:13 }}>{fmt(s.starting_balance)}</td>
                        <td style={{ padding:'8px 12px', fontSize:13 }}>{fmt(s.draw)}</td>
                        <td style={{ padding:'8px 12px', fontSize:13, color: s.ending_balance <= 0 ? RED : 'var(--text)' }}>{fmt(s.ending_balance)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default function StressTestWhatIf({ onNavigate }) {
  const { person1Name, person2Name } = usePersonNames()
  const { retAge, ssTiming, setRetAge, setSsTiming } = useScenario()
  const [tab, setTab] = useState('whatif')
  // The What-If Builder reports its current slider state up here as it
  // changes, so switching to Monte Carlo/Historical Stress can pass the
  // same modified assumptions into those runs instead of silently
  // discarding them in favor of saved Settings (external audit
  // 2026-09-06). null until the What-If tab has computed at least once.
  const [whatIfAssumptions, setWhatIfAssumptions] = useState(null)

  // Two-age mode (backend/docs/CALCULATION_CONTRACT.md section 22):
  // explicit, independent retirement ages for both spouses, shared by
  // Monte Carlo and Historical Stress since both read the same toggle
  // here rather than tracking it twice. jasonRetAge/justinRetAge are
  // only ever passed down to MonteCarloSection/StressTestSection when
  // twoAgeMode is on -- off (the default) keeps both sections on their
  // existing single-age ret_age/ssTiming behavior, completely unaffected.
  const [twoAgeMode, setTwoAgeMode] = useState(false)
  const [jasonRetAge, setJasonRetAge] = useState(65)
  const [justinRetAge, setJustinRetAge] = useState(65)

  // External audit review of commit 0c1a569, finding 5 (P2): once a
  // spouse has a claim age saved in Settings, resolve_ss_claim_ages'
  // 3-tier resolution (CALCULATION_CONTRACT.md section 49) makes that
  // saved value win over the Early/Delayed buttons below on every page
  // that reads it -- Monte Carlo and Historical Stress included (both
  // single-axis and two-age mode -- CALCULATION_CONTRACT.md section
  // 48). The buttons still render as active/clickable and the response
  // still labels itself "early"/"delayed", so clicking them silently
  // does nothing with no indication why. Surface it explicitly instead
  // of leaving an apparently-live control that's actually inert.
  // Sourced from WhatIf.jsx's own onSettingsLoaded callback (below)
  // rather than a second /api/planning-inputs fetch here -- WhatIf
  // stays mounted on every tab already, so its one fetch is the single
  // source.
  //
  // External audit review of commit aaa3cf5, finding 3 (P2): each
  // spouse's claim age resolves INDEPENDENTLY (resolve_ss_claim_ages,
  // section 49 finding 1) -- with only one spouse's age saved, these
  // buttons keep controlling the OTHER spouse's SS normally. A single
  // "these buttons have no effect" banner was simply false in that
  // case (reproduced: only Justin's claim age saved, toggling Early/
  // Delayed still moved the final balance from $1,423,000 to
  // $1,540,000 via Jason's own SS). Now tracks each spouse's saved age
  // separately and only claims override for the spouse(s) actually
  // overridden; also now shown in two-age mode, which the original
  // banner skipped even though saved claim ages apply there too.
  const [savedClaimAges, setSavedClaimAges] = useState({ jason: null, justin: null })
  const jasonOverridden  = savedClaimAges.jason  != null
  const justinOverridden = savedClaimAges.justin != null
  const anyOverridden = jasonOverridden || justinOverridden
  const overrideNote = jasonOverridden && justinOverridden
    ? `Both ${person1Name} (age ${savedClaimAges.jason}) and ${person2Name} (age ${savedClaimAges.justin}) have a Social Security claim age saved in Settings — these buttons have no effect for either of them.`
    : jasonOverridden
    ? `${person1Name}'s Social Security claim age (${savedClaimAges.jason}) is saved in Settings and overrides these buttons for ${person1Name} — ${person2Name}'s SS still responds normally.`
    : `${person2Name}'s Social Security claim age (${savedClaimAges.justin}) is saved in Settings and overrides these buttons for ${person2Name} — ${person1Name}'s SS still responds normally.`

  return (
    <div>
      <div style={{ marginBottom:28 }}>
        <h1 className="section-title">Stress Test & What-If</h1>
        <p className="section-sub">Pressure-test your plan against custom assumptions, randomized markets, and historical crashes</p>
      </div>

      {/* Tab switcher */}
      <div style={{ display:'flex', gap:4, marginBottom:24, borderBottom:'1px solid var(--border)', paddingBottom:0 }}>
        {TABS.map(t => (
          <button key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              background:'none', border:'none', padding:'10px 20px', cursor:'pointer',
              fontSize:13, fontWeight:600,
              color: tab===t.id ? 'var(--accent)' : 'var(--text2)',
              borderBottom: tab===t.id ? '2px solid var(--accent)' : '2px solid transparent',
              marginBottom:-1, transition:'all 0.15s',
            }}
          >{t.label}</button>
        ))}
      </div>

      {(tab === 'monte_carlo' || tab === 'stress') && whatIfAssumptions && (
        <div style={{ padding:'10px 14px', background:'rgba(79,156,249,0.08)', border:'1px solid rgba(79,156,249,0.2)', borderRadius:8, marginBottom:20, fontSize:12, color:'var(--text2)' }}>
          Reflecting the assumptions currently set in the What-If Builder tab (returns, inflation, healthcare, income target, bridge income, pension/SS multipliers) — not just saved Settings.
        </div>
      )}

      {/* What-If Builder has its own full 55-67 retirement-age slider, and
          Survivor Scenario has its own controls, so the coarse retAge/
          ssTiming selector below is only shown for Monte Carlo/Historical
          Stress, which need it directly. */}
      {(tab === 'monte_carlo' || tab === 'stress') && (
        <div style={{ marginBottom:16 }}>
          <button
            className={twoAgeMode ? 'btn-primary' : 'btn-secondary'}
            onClick={() => setTwoAgeMode(m => !m)}
            style={{ fontSize:12 }}
          >{twoAgeMode ? '✓ Two-Age Mode' : 'Use Two Independent Retirement Ages'}</button>
        </div>
      )}

      {/* What-If Builder has its own full 55-67 retirement-age slider, and
          Survivor Scenario has its own controls, so the coarse retAge/
          ssTiming selector below is only shown for Monte Carlo/Historical
          Stress, which need it directly. Two-age mode replaces it with
          two explicit age inputs instead (backend/docs/
          CALCULATION_CONTRACT.md section 22) -- What-If overrides and SS
          claiming-age timing don't apply in two-age mode v1 (out of
          scope), so that selector is hidden while it's on. */}
      {tab !== 'whatif' && tab !== 'survivor' && !twoAgeMode && (
        <div style={{ display:'flex', gap:24, marginBottom:anyOverridden ? 8 : 28, flexWrap:'wrap', alignItems:'flex-end' }}>
          <div>
            <div className="label" style={{ marginBottom:8 }}>Retirement Age</div>
            <div style={{ display:'flex', flexWrap:'wrap', gap:6, maxWidth:420 }}>
              {RET_AGES.map(age => (
                <button key={age}
                  className={retAge===age ? 'btn-primary' : 'btn-secondary'}
                  onClick={() => setRetAge(age)}
                >{age}</button>
              ))}
            </div>
          </div>
          <div>
            <div className="label" style={{ marginBottom:8 }}>
              Social Security{anyOverridden ? ' (partially overridden by Settings)' : ''}
            </div>
            <div style={{ display:'flex', gap:6 }}>
              {SS_OPTS.map(o => (
                <button key={o.value}
                  className={ssTiming===o.value ? 'btn-primary' : 'btn-secondary'}
                  onClick={() => setSsTiming(o.value)}
                >{o.label}</button>
              ))}
            </div>
          </div>
        </div>
      )}
      {tab !== 'whatif' && tab !== 'survivor' && !twoAgeMode && anyOverridden && (
        <div style={{ padding:'8px 14px', background:'var(--bg3)', borderRadius:8, marginBottom:20, fontSize:12, color:'var(--text2)' }}>
          ℹ {overrideNote} Change or clear it on the Settings page instead.
        </div>
      )}
      {tab !== 'whatif' && tab !== 'survivor' && twoAgeMode && (
        <div style={{ display:'flex', gap:24, marginBottom:anyOverridden ? 8 : 28, flexWrap:'wrap', alignItems:'flex-end' }}>
          <div>
            <div className="label" style={{ marginBottom:8 }}>{person1Name}'s Retirement Age</div>
            <input type="number" value={jasonRetAge} onChange={e => setJasonRetAge(parseInt(e.target.value) || 0)} />
          </div>
          <div>
            <div className="label" style={{ marginBottom:8 }}>{person2Name}'s Retirement Age</div>
            <input type="number" value={justinRetAge} onChange={e => setJustinRetAge(parseInt(e.target.value) || 0)} />
          </div>
          {/* Independent review, 2026-09-08 (P1): two-age mode used to
              hide this entirely, so there was no way to change SS timing
              while it was on -- the request silently kept whatever
              ssTiming happened to already be set (always 'early' on
              first load), not necessarily what the user actually wants
              for a two-age scenario. Kept visible here so the selection
              persists AND stays user-editable across the mode switch. */}
          <div>
            <div className="label" style={{ marginBottom:8 }}>
              Social Security{anyOverridden ? ' (partially overridden by Settings)' : ''}
            </div>
            <div style={{ display:'flex', gap:6 }}>
              {SS_OPTS.map(o => (
                <button key={o.value}
                  className={ssTiming===o.value ? 'btn-primary' : 'btn-secondary'}
                  onClick={() => setSsTiming(o.value)}
                >{o.label}</button>
              ))}
            </div>
          </div>
        </div>
      )}
      {/* External audit review of commit aaa3cf5, finding 3 (P2): this
          note used to be skipped entirely in two-age mode, even though
          saved claim ages apply there too (CALCULATION_CONTRACT.md
          section 48 -- every two-age dispatcher resolves them the same
          way as single-axis). */}
      {tab !== 'whatif' && tab !== 'survivor' && twoAgeMode && anyOverridden && (
        <div style={{ padding:'8px 14px', background:'var(--bg3)', borderRadius:8, marginBottom:20, fontSize:12, color:'var(--text2)' }}>
          ℹ {overrideNote} Change or clear it on the Settings page instead.
        </div>
      )}

      <div hidden={tab !== 'whatif'}>
        <WhatIf onNavigate={onNavigate} onAssumptionsChange={setWhatIfAssumptions}
                onSettingsLoaded={d => setSavedClaimAges({ jason: d?.jason_ss_claim_age ?? null, justin: d?.justin_ss_claim_age ?? null })} />
      </div>
      {tab === 'monte_carlo' && <MonteCarloSection retAge={retAge} ssTiming={ssTiming} overrides={whatIfAssumptions}
                                                     jasonRetAge={twoAgeMode ? jasonRetAge : null}
                                                     justinRetAge={twoAgeMode ? justinRetAge : null} />}
      {tab === 'stress'      && <StressTestSection retAge={retAge} ssTiming={ssTiming} overrides={whatIfAssumptions}
                                                     jasonRetAge={twoAgeMode ? jasonRetAge : null}
                                                     justinRetAge={twoAgeMode ? justinRetAge : null} />}
      {tab === 'survivor'    && <SurvivorScenarioSection retAge={retAge} />}
    </div>
  )
}
