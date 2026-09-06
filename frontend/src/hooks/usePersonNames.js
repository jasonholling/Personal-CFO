import { useState, useEffect } from 'react'
import axios from 'axios'

const DEFAULTS = {
  person1Name: 'Person 1',
  person2Name: 'Person 2',
  kid1Name: 'Child 1',
  kid2Name: 'Child 2',
}

// Shared across the app so every page shows whatever names the current
// user configured in Settings, instead of hardcoding a specific family.
export function usePersonNames() {
  const [names, setNames] = useState(DEFAULTS)

  useEffect(() => {
    axios.get('/api/planning-inputs').then(r => {
      const d = r.data || {}
      setNames({
        person1Name: d.person1_name || DEFAULTS.person1Name,
        person2Name: d.person2_name || DEFAULTS.person2Name,
        kid1Name: d.kid1_name || DEFAULTS.kid1Name,
        kid2Name: d.kid2_name || DEFAULTS.kid2Name,
      })
    }).catch(() => {})
  }, [])

  return names
}
