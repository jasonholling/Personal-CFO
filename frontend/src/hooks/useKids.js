import { useState, useEffect, useCallback } from 'react'
import axios from 'axios'

// Kids-variable-count (2026-09-09) — a household can have 0-5 kids,
// replacing the old fixed kid1Name/kid2Name usePersonNames used to
// expose. Every page that needs the current kid list (Accounts' owner
// dropdown, Risk's "who" dropdown, Education/Dashboard/Estate display,
// the Kids-management UI in Settings) shares this hook instead of each
// re-fetching /api/kids on its own. A kid's stable identity is its own
// database id — account ownership uses f"kid_${id}", not the kid's
// (renameable) name, so a rename never orphans an account.
export function useKids() {
  const [kids, setKids] = useState([])
  const [loading, setLoading] = useState(true)

  const refetch = useCallback(() => {
    return axios.get('/api/kids').then(r => {
      setKids(r.data || [])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  useEffect(() => { refetch() }, [refetch])

  return { kids, loading, refetch }
}

export const kidOwnerKey = (kidId) => `kid_${kidId}`
