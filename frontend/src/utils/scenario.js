/**
 * Shared "which scenario am I looking at" state — retirement age and Social
 * Security timing — so picking Retire 60 / SS at 62 on one page carries
 * over to the next instead of every page resetting to its own default.
 *
 * Same module-state-outside-React pattern as ../utils/privacy.js and for
 * the same reason: some pages read these as plain values inside effects/
 * calculations, not just render-time. useScenario() (see ../hooks) holds
 * the React state that re-renders when either value changes.
 */
const STORAGE_KEY_RET_AGE   = 'cfo_scenario_ret_age'
const STORAGE_KEY_SS_TIMING = 'cfo_scenario_ss_timing'
// CALCULATION_CONTRACT.md section 54 (2026-09-09): a page-local, ad hoc
// claim age -- NOT the same as the value saved in Settings. Shared here
// (not per-page useState) so picking "Jason at 68" on one page carries
// over to the next, same as retAge/ssTiming already do, letting a
// household try one claim age across Monte Carlo, Stress Tests, Roth
// Conversion, Survivor Scenario, etc. without re-entering it each time.
// Stays null by default -- every backend endpoint's own 3-tier
// resolution (explicit override > saved Settings > legacy ss_timing)
// means a null override here still correctly falls through to whatever
// IS saved in Settings; this is purely an additional, temporary
// override on top, never a replacement for the Settings value.
const STORAGE_KEY_JASON_SS_CLAIM_AGE  = 'cfo_scenario_jason_ss_claim_age'
const STORAGE_KEY_JUSTIN_SS_CLAIM_AGE = 'cfo_scenario_justin_ss_claim_age'

let _retAge   = 60
let _ssTiming = 'early'
let _jasonSsClaimAge  = null
let _justinSsClaimAge = null
const listeners = new Set()

function _snapshot() {
  return { retAge: _retAge, ssTiming: _ssTiming, jasonSsClaimAge: _jasonSsClaimAge, justinSsClaimAge: _justinSsClaimAge }
}

export function initScenarioFromStorage() {
  try {
    const storedAge = parseInt(localStorage.getItem(STORAGE_KEY_RET_AGE))
    if (!isNaN(storedAge)) _retAge = storedAge
    const storedTiming = localStorage.getItem(STORAGE_KEY_SS_TIMING)
    // The rest of the app (SS_OPTS in Simulation.jsx, Retirement.jsx's
    // toggle, every backend ss_timing param) uses "delayed", not "late" --
    // this stored-value allowlist was checking for the wrong string, so a
    // saved "delayed" selection silently failed validation and reverted to
    // the "early" default on every restart (external audit 2026-09-06).
    if (storedTiming === 'early' || storedTiming === 'delayed') _ssTiming = storedTiming
    const storedJason = parseInt(localStorage.getItem(STORAGE_KEY_JASON_SS_CLAIM_AGE))
    if (!isNaN(storedJason)) _jasonSsClaimAge = storedJason
    const storedJustin = parseInt(localStorage.getItem(STORAGE_KEY_JUSTIN_SS_CLAIM_AGE))
    if (!isNaN(storedJustin)) _justinSsClaimAge = storedJustin
  } catch { /* localStorage unavailable — default 60/early/null/null */ }
  return _snapshot()
}

export function getRetAge()   { return _retAge }
export function getSsTiming() { return _ssTiming }
export function getJasonSsClaimAge()  { return _jasonSsClaimAge }
export function getJustinSsClaimAge() { return _justinSsClaimAge }

export function setRetAge(value) {
  _retAge = value
  try { localStorage.setItem(STORAGE_KEY_RET_AGE, String(value)) } catch { /* ignore */ }
  listeners.forEach(fn => fn(_snapshot()))
}

export function setSsTiming(value) {
  _ssTiming = value
  try { localStorage.setItem(STORAGE_KEY_SS_TIMING, value) } catch { /* ignore */ }
  listeners.forEach(fn => fn(_snapshot()))
}

// value=null clears the override (back to "no page-local override,
// defer to Settings/legacy toggle").
export function setJasonSsClaimAge(value) {
  _jasonSsClaimAge = value
  try {
    if (value == null) localStorage.removeItem(STORAGE_KEY_JASON_SS_CLAIM_AGE)
    else localStorage.setItem(STORAGE_KEY_JASON_SS_CLAIM_AGE, String(value))
  } catch { /* ignore */ }
  listeners.forEach(fn => fn(_snapshot()))
}

export function setJustinSsClaimAge(value) {
  _justinSsClaimAge = value
  try {
    if (value == null) localStorage.removeItem(STORAGE_KEY_JUSTIN_SS_CLAIM_AGE)
    else localStorage.setItem(STORAGE_KEY_JUSTIN_SS_CLAIM_AGE, String(value))
  } catch { /* ignore */ }
  listeners.forEach(fn => fn(_snapshot()))
}

export function subscribeScenario(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

// A few pages (Retirement, Simulation, Roth Conversion) only have backend
// data for ages 55/60/65, while others (WhatIf, Age Sensitivity) support
// any age 55-67. When the shared retAge comes from one of the fine-grained
// pages, a coarse page should snap to its nearest supported option instead
// of failing to find a matching scenario.
export function nearestOf(age, options) {
  return options.reduce((best, o) => Math.abs(o - age) < Math.abs(best - age) ? o : best, options[0])
}
