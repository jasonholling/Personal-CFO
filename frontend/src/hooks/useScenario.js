import { useState, useEffect } from 'react'
import { initScenarioFromStorage, setRetAge, setSsTiming, subscribeScenario } from '../utils/scenario'

// React-facing half of the shared retirement-age / SS-timing scenario — see
// ../utils/scenario.js for why the values live outside React state. Any
// page with a "Retire 60 / SS at 62" style selector uses this hook instead
// of its own local useState, so the choice carries over screen to screen.
export function useScenario() {
  const [state, setLocal] = useState(() => initScenarioFromStorage())

  useEffect(() => subscribeScenario(setLocal), [])

  return {
    retAge: state.retAge,
    ssTiming: state.ssTiming,
    setRetAge,
    setSsTiming,
  }
}
