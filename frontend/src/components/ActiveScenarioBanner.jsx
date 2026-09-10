import { useState, useEffect } from 'react'
import axios from 'axios'
import { useScenario } from '../hooks/useScenario'
import { usePersonNames } from '../hooks/usePersonNames'

// Milestone 3 ("one clearly identified active scenario across relevant
// tools"): retAge/ssTiming/SS claim-age overrides already share state
// across pages via useScenario (see utils/scenario.js) -- picking
// "Retire at 58" on one page already carries over to the next. What
// was missing was ever telling the user so; each page just showed its
// own controls with no indication they're the SAME choice, not a
// fresh default, on every page it appears. This banner is that
// visible, consistent indicator -- same component, same wording,
// mounted at the top of every page that reads useScenario().
//
// Distinguishes a temporary override from a saved Settings default
// only where that distinction actually exists in the data model: SS
// claim age (jasonSsClaimAge/justinSsClaimAge are page-local overrides
// layered on top of whatever's saved in Settings -- see
// CALCULATION_CONTRACT.md section 54's 3-tier resolution). retAge/
// ssTiming have no separate "Settings default" to contrast against --
// the shared scenario value IS the persistent choice already, so it's
// labeled as the active scenario rather than invented as an override
// of something that doesn't exist.
// savedJasonClaimAge/savedJustinClaimAge: optional. A page that's
// already fetched planning-inputs for its own purposes (StressTestWhatIf
// already does, for AssumptionsUsed) should pass those values through
// instead of letting this component fetch its own redundant copy on
// every mount -- caught by ScenarioFlow.test.jsx's own call-count
// assertion when this component was first added there. Pages that
// haven't already fetched it (Retirement/RothConversion/
// RetirementSensitivity) leave both undefined and this component
// fetches for itself.
export default function ActiveScenarioBanner({ savedJasonClaimAge, savedJustinClaimAge } = {}) {
  const { retAge, ssTiming, jasonSsClaimAge, justinSsClaimAge } = useScenario()
  const { person1Name, person2Name } = usePersonNames()
  const hasSavedProps = savedJasonClaimAge !== undefined || savedJustinClaimAge !== undefined
  const [fetched, setFetched] = useState({ jason: null, justin: null })

  useEffect(() => {
    if (hasSavedProps) return
    axios.get('/api/planning-inputs').then(r => {
      const d = r.data || {}
      setFetched({ jason: d.jason_ss_claim_age ?? null, justin: d.justin_ss_claim_age ?? null })
    }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const saved = hasSavedProps
    ? { jason: savedJasonClaimAge ?? null, justin: savedJustinClaimAge ?? null }
    : fetched

  const ssLabel = ssTiming === 'delayed' ? 'wait until 67' : 'take at 62'
  const overrides = []
  if (jasonSsClaimAge != null && jasonSsClaimAge !== saved.jason) {
    overrides.push(`${person1Name} claiming SS at ${jasonSsClaimAge} — this session only, not saved`)
  }
  if (justinSsClaimAge != null && justinSsClaimAge !== saved.justin) {
    overrides.push(`${person2Name} claiming SS at ${justinSsClaimAge} — this session only, not saved`)
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
      padding: '8px 14px', marginBottom: 20, borderRadius: 8,
      background: 'rgba(79,156,249,0.06)', border: '1px solid rgba(79,156,249,0.2)',
      fontSize: 12, color: 'var(--text2)',
    }}>
      <span style={{ fontWeight: 600, color: 'var(--accent)' }}>Active scenario</span>
      <span>Retire at {retAge} · SS {ssLabel}</span>
      {overrides.map(o => (
        <span key={o} style={{ color: 'var(--amber)' }}>· {o}</span>
      ))}
      <span style={{ color: 'var(--text3)', marginLeft: 'auto' }}>Carries over to every retirement/scenario page</span>
    </div>
  )
}
