import { useState, useEffect } from 'react'
import {
  initScenarioFromStorage, setRetAge, setSsTiming, subscribeScenario,
  setJasonSsClaimAge, setJustinSsClaimAge,
} from '../utils/scenario'

// React-facing half of the shared retirement-age / SS-timing scenario — see
// ../utils/scenario.js for why the values live outside React state. Any
// page with a "Retire 60 / SS at 62" style selector uses this hook instead
// of its own local useState, so the choice carries over screen to screen.
//
// jasonSsClaimAge/justinSsClaimAge (2026-09-09, CALCULATION_CONTRACT.md
// section 54): a page-local, ad hoc claim-age override -- separate from
// whatever's saved in Settings -- shared the same way, so trying "Jason
// at 68" on Monte Carlo carries over to Historical Stress/Roth
// Conversion/Survivor Scenario too. null means "no override," which
// still correctly falls back to the saved Settings value via every
// endpoint's own 3-tier resolution.
export function useScenario() {
  const [state, setLocal] = useState(() => initScenarioFromStorage())

  useEffect(() => subscribeScenario(setLocal), [])

  return {
    retAge: state.retAge,
    ssTiming: state.ssTiming,
    jasonSsClaimAge: state.jasonSsClaimAge,
    justinSsClaimAge: state.justinSsClaimAge,
    setRetAge,
    setSsTiming,
    setJasonSsClaimAge,
    setJustinSsClaimAge,
  }
}
