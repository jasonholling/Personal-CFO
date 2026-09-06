import { useState, useEffect } from 'react'
import { isPrivacyMode, setPrivacyMode, initPrivacyModeFromStorage, subscribePrivacyMode } from '../utils/privacy'

// React-facing half of privacy mode — see ../utils/privacy.js for why the
// actual flag lives outside React state. Any component that renders based
// on privacy mode (or just needs the toggle button) uses this hook so it
// re-renders when the flag flips.
export function usePrivacyMode() {
  const [privacyMode, setLocal] = useState(() => initPrivacyModeFromStorage())

  useEffect(() => subscribePrivacyMode(setLocal), [])

  const toggle = () => setPrivacyMode(!isPrivacyMode())

  return { privacyMode, toggle }
}
