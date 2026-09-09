import { ssBenefitForClaimAge, SS_CLAIM_AGE_MIN, SS_CLAIM_AGE_MAX, SS_FRA_AGE } from '../utils/ssBenefit'

// Shared SS claiming-age slider control (CALCULATION_CONTRACT.md
// section 44/49/54) -- originally built inline in Settings.jsx, pulled
// out here (2026-09-09) so it can also render inline on Retirement,
// Monte Carlo, Historical Stress, Roth Conversion, and Survivor
// Scenario, per the user's own request: "I want on all the pages
// where I can pick what age I am taking it." A page embedding this
// passes its OWN local claimAge/onChange (typically the shared
// jasonSsClaimAge/justinSsClaimAge from useScenario, section 54) --
// this component has no opinion on whether that's a page-local
// override or the value saved in Settings; both use the identical UI.
//
// off = null (no override) -- offHint customizes what "off" means for
// the page embedding this (Settings: "use the Early/Delayed toggle";
// a page WITH that toggle already visible: "use the toggle above" or
// similar), since the same wording doesn't fit everywhere this is used.
export default function ClaimAgeSlider({
  label, claimAge, onChange, benefit62, benefit67, benefit70, benefitType,
  checkEarlyAnchor = false, scopeNote, offHint, compact = false,
}) {
  const enabled = claimAge != null
  const age = claimAge ?? SS_FRA_AGE
  // External audit review of commit 0c1a569, finding 2 (P1): a 0-valued
  // (untouched) anchor field must fall back to the FRA figure, not be
  // read as a real $0 anchor -- see the identical backend fix in
  // resolve_ss_benefits (CALCULATION_CONTRACT.md section 51).
  // checkEarlyAnchor also flags the 62 field -- only Justin's spousal
  // fields fall back this way; Jason's worker benefit62
  // (jason_social_security) has always been required, no fallback.
  const anchorMissingLate  = !benefit70
  const anchorMissingEarly = checkEarlyAnchor && !benefit62
  const anchorMissing = anchorMissingLate || anchorMissingEarly
  const resolvedBenefit70 = benefit70 || benefit67
  const resolvedBenefit62 = anchorMissingEarly ? benefit67 : benefit62
  const computed = ssBenefitForClaimAge(resolvedBenefit62 ?? 0, benefit67 ?? 0, resolvedBenefit70 ?? 0, age, benefitType)
  return (
    <div style={compact ? {} : { padding:'10px 0', borderBottom:'1px solid var(--border)' }}>
      <label style={{ display:'flex', alignItems:'center', gap:8, fontSize:13, cursor:'pointer' }}>
        <input type="checkbox" checked={enabled} onChange={e => onChange(e.target.checked ? SS_FRA_AGE : null)} />
        {label}
        {offHint !== false && (
          <span style={{ fontSize:11, color:'var(--text3)' }}>
            ({offHint || 'off = use the Early/Delayed toggle on Retirement/Simulation pages instead'})
          </span>
        )}
      </label>
      {enabled && (
        <div style={{ marginTop:10, paddingLeft:24 }}>
          {scopeNote && (
            <div style={{ fontSize:11, color:'var(--text3)', marginBottom:8 }}>ℹ {scopeNote}</div>
          )}
          {anchorMissing && (
            <div style={{ padding:'8px 10px', background:'rgba(251,191,36,0.08)', borderRadius:6, marginBottom:8, fontSize:11, color:'var(--amber)' }}>
              ⚠ {anchorMissingEarly && anchorMissingLate ? 'The age-62 and age-70 fields are $0' :
                 anchorMissingEarly ? 'The age-62 field is $0' : 'The age-70 field is $0'} in Settings — this
              slider is an ESTIMATE using the FRA benefit flat (no real anchor entered), not the real
              SSA-statement figure. Fill in the field(s) in Settings for an accurate number.
            </div>
          )}
          <div style={{ display:'flex', alignItems:'center', gap:12 }}>
            <input
              type="range"
              min={SS_CLAIM_AGE_MIN}
              max={SS_CLAIM_AGE_MAX}
              step={1}
              value={age}
              onChange={e => onChange(parseInt(e.target.value))}
              style={{ flex:1 }}
            />
            <span style={{ fontSize:13, fontWeight:700, width:28, textAlign:'right' }}>{age}</span>
          </div>
          <div style={{ fontSize:12, color:'var(--text2)', marginTop:4 }}>
            Benefit at {age}: <strong>${Math.round(computed).toLocaleString('en-US')}/yr</strong> {anchorMissing ? '(estimated)' : '(computed)'}
          </div>
        </div>
      )}
    </div>
  )
}

export { SS_FRA_AGE }
