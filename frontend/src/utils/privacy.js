/**
 * Privacy mode — a global, persisted "mask the numbers" toggle for
 * screen-sharing/demoing the app layout without exposing real figures.
 *
 * This is plain module state (not React state) on purpose: fmt()/fmtK()
 * are called from dozens of places across every page as plain functions,
 * not hooks, so they read this synchronously rather than needing every
 * call site threaded with a prop. usePrivacyMode() (see ../hooks) holds
 * the React state that actually drives re-renders when the toggle flips.
 */
const STORAGE_KEY = 'cfo_privacy_mode'

let _privacyMode = false
const listeners = new Set()

export function initPrivacyModeFromStorage() {
  try {
    _privacyMode = localStorage.getItem(STORAGE_KEY) === '1'
  } catch { /* localStorage unavailable — default off */ }
  return _privacyMode
}

export function isPrivacyMode() {
  return _privacyMode
}

export function setPrivacyMode(value) {
  _privacyMode = value
  try { localStorage.setItem(STORAGE_KEY, value ? '1' : '0') } catch { /* ignore */ }
  listeners.forEach(fn => fn(value))
}

export function subscribePrivacyMode(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

// Fixed-width masks — deliberately not sized to the real value, so no
// magnitude/length information leaks even while masked.
export const MASK_CURRENCY = '$•••,•••'
export const MASK_PERCENT  = '••%'
export const MASK_NUMBER   = '•••'

// For free-text fields that mix real numbers into a string a user typed
// (e.g. Risk.jsx policy benefit/premium: "$500,000", "$955/yr" — not run
// through fmt()/fmtK()) — masks every digit, leaves everything else (the
// $ sign, "/yr", commas) intact so the shape of the field still reads.
export function maskDigitsInText(str) {
  if (!isPrivacyMode() || str == null) return str
  return String(str).replace(/\d/g, '•')
}
