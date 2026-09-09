import { useState, useEffect } from 'react'
import axios from 'axios'

const DEFAULTS = {
  person1Name: 'Person 1',
  person2Name: 'Person 2',
}

// Shared across the app so every page shows whatever names the current
// user configured in Settings, instead of hardcoding a specific family.
//
// kid1Name/kid2Name used to live here too, back when a household could
// only ever have exactly 2 kids. Kids-variable-count (2026-09-09)
// replaced that with the `kids` table (0-5 rows) — use useKids() (same
// hooks folder) instead for anything that needs the current kid list.
export function usePersonNames() {
  const [names, setNames] = useState(DEFAULTS)

  useEffect(() => {
    axios.get('/api/planning-inputs').then(r => {
      const d = r.data || {}
      setNames({
        person1Name: d.person1_name || DEFAULTS.person1Name,
        person2Name: d.person2_name || DEFAULTS.person2Name,
      })
    }).catch(() => {})
  }, [])

  return names
}
