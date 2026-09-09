import { useState, useEffect } from 'react'
import axios from 'axios'

export const SS_ANCHORS_DEFAULTS = {
  jason:  { b62: 0, b67: 0, b70: 0 },
  justin: { b62: 0, b67: 0, b70: 0 },
  savedJasonClaimAge: null,
  savedJustinClaimAge: null,
}

// Shapes a raw /api/planning-inputs response into the anchors object
// below. Exported separately so a page that ALREADY fetches planning-
// inputs for another reason (StressTestWhatIf.jsx reuses WhatIf.jsx's
// own single fetch, via its onSettingsLoaded callback) can derive the
// same shape without a second network call -- a second, independent
// /api/planning-inputs fetch broke ScenarioFlow.test.jsx's "exactly
// one call across a tab-switch flow" invariant the first time this
// was tried (external audit follow-up, 2026-09-09), the same failure
// mode section 51's finding 5 fix had already hit once before.
export function ssAnchorsFromPlanningInputs(d) {
  d = d || {}
  return {
    jason:  { b62: d.jason_social_security ?? 0, b67: d.jason_ss_delayed ?? 0, b70: d.jason_ss_70 ?? 0 },
    justin: { b62: d.justin_ss_early ?? 0, b67: d.justin_social_security ?? 0, b70: d.justin_ss_70 ?? 0 },
    savedJasonClaimAge: d.jason_ss_claim_age ?? null,
    savedJustinClaimAge: d.justin_ss_claim_age ?? null,
  }
}

// Shared fetch of the SS claiming-age anchor fields (CALCULATION_CONTRACT.md
// section 44/54) so a page with a claim-age slider that has no OTHER
// reason to fetch planning-inputs already (Retirement.jsx) can show a
// live "benefit at this age" preview without hand-rolling the fetch --
// same pattern as usePersonNames. A page that already fetches
// planning-inputs for another reason should use
// ssAnchorsFromPlanningInputs(d) on that existing response instead of
// calling this hook (a second, independent fetch of the same data).
export function useSsAnchors() {
  const [anchors, setAnchors] = useState(SS_ANCHORS_DEFAULTS)

  useEffect(() => {
    axios.get('/api/planning-inputs').then(r => setAnchors(ssAnchorsFromPlanningInputs(r.data)))
      .catch(() => {})
  }, [])

  return anchors
}
