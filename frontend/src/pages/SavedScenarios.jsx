import { useEffect, useState } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'
import { useScenario } from '../hooks/useScenario'
import { SS_OPTS } from './Simulation'

const fmt = n => isPrivacyMode() ? MASK_CURRENCY : new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', maximumFractionDigits: 0,
}).format(n || 0)

// Saving used to always send retirement_age only, and the backend then
// always projected against early SS claiming — so a scenario saved while
// "SS at 67" was selected everywhere else in the app got silently
// recorded as if early claiming had been chosen (external audit
// 2026-09-07, finding #15). ssTiming now comes from the same shared
// scenario module every other page reads/writes (see useScenario), so the
// saved scenario reflects whatever timing is actually currently selected.
//
// Milestone 1 (2026-09-09, "Complete, reproducible saved scenarios"):
// - A save is now insert-only on the backend — re-saving under an
//   existing name 409s instead of silently overwriting, so this page
//   surfaces that as an inline error rather than a generic failure.
// - "Recalculate with current data" creates a new, separately-listed
//   revision instead of mutating the original — both stay visible.
// - Rows saved before this migration carry no full assumptions capture;
//   they're labeled "legacy" rather than presented as reproducible.
// - A lightweight two-scenario compare panel satisfies "support
//   comparison of saved scenarios, showing changed assumptions alongside
//   changed outcomes" without inventing a full diff UI framework.
const ssLabel = t => SS_OPTS.find(o => o.value === t)?.label || t

const SUMMARY_FIELDS = [
  ['percent_funded', '% funded'],
  ['portfolio_at_retirement', 'Portfolio at retirement'],
  ['projected_surplus', 'Projected surplus'],
]

function ScenarioCard({ x, onRecalculate, recalculating, onToggleCompare, compareChecked }) {
  return (
    <div className="card" style={{ position: 'relative' }}>
      <label style={{ position: 'absolute', top: 14, right: 14, display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--text3)' }}>
        <input type="checkbox" checked={compareChecked} onChange={() => onToggleCompare(x.id)} />
        Compare
      </label>
      <div style={{ fontWeight: 650, paddingRight: 70 }}>{x.name}</div>
      {x.is_legacy && (
        <div style={{ display: 'inline-block', marginTop: 4, fontSize: 10, fontWeight: 600, color: 'var(--amber)', border: '1px solid var(--amber)', borderRadius: 4, padding: '1px 6px' }}>
          LEGACY — saved before full assumption capture, not guaranteed reproducible
        </div>
      )}
      {x.revision_of != null && (
        <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>Revision {x.revision_number} — recalculated from an earlier save</div>
      )}
      <div className="label" style={{ marginTop: 12 }}>Retire at {x.summary.retirement_age} · {ssLabel(x.ss_timing || 'early')}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 5, color: x.summary.on_track ? 'var(--green)' : 'var(--amber)' }}>
        {x.summary.percent_funded}% funded
      </div>
      <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 8 }}>Portfolio {fmt(x.summary.portfolio_at_retirement)}</div>
      <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 10 }}>
        Saved {new Date(x.created_at).toLocaleDateString()}
        {x.calculation_version && ` · engine v${x.calculation_version}`}
      </div>
      <button
        className="btn-secondary"
        style={{ marginTop: 10, fontSize: 12, padding: '4px 10px' }}
        disabled={recalculating === x.id}
        onClick={() => onRecalculate(x.id)}
        title="Re-run this scenario's exact retirement age / SS choice against today's household data — the original save is preserved, this creates a new revision"
      >
        {recalculating === x.id ? 'Recalculating…' : 'Recalculate with current data'}
      </button>
    </div>
  )
}

function CompareDiff({ a, b }) {
  if (!a || !b) return null
  const aq = a.assumptions?.planning_inputs || {}
  const bq = b.assumptions?.planning_inputs || {}
  const inputKeys = Array.from(new Set([...Object.keys(aq), ...Object.keys(bq)])).filter(
    k => k !== 'id' && JSON.stringify(aq[k]) !== JSON.stringify(bq[k])
  )
  return (
    <div className="card" style={{ marginTop: 20 }}>
      <div className="label" style={{ marginBottom: 10 }}>Comparing "{a.name}" vs "{b.name}"</div>
      {(a.is_legacy || b.is_legacy) && (
        <div style={{ fontSize: 12, color: 'var(--amber)', marginBottom: 10 }}>
          One or both scenarios are legacy saves — their assumption diff below may be incomplete.
        </div>
      )}
      <div className="label" style={{ marginTop: 10, marginBottom: 6, fontSize: 11 }}>Outcomes</div>
      <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
        <tbody>
          {SUMMARY_FIELDS.map(([key, label]) => (
            <tr key={key} style={{ borderBottom: '1px solid var(--border)' }}>
              <td style={{ padding: '4px 0', color: 'var(--text2)' }}>{label}</td>
              <td style={{ padding: '4px 8px', textAlign: 'right' }}>
                {key === 'percent_funded' ? `${a.summary[key]}%` : fmt(a.summary[key])}
              </td>
              <td style={{ padding: '4px 8px', textAlign: 'right' }}>
                {key === 'percent_funded' ? `${b.summary[key]}%` : fmt(b.summary[key])}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="label" style={{ marginTop: 16, marginBottom: 6, fontSize: 11 }}>Changed assumptions</div>
      {inputKeys.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--text3)' }}>No differing planning-input fields captured between these two.</div>
      ) : (
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <tbody>
            {inputKeys.map(k => (
              <tr key={k} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '4px 0', color: 'var(--text2)' }}>{k}</td>
                <td style={{ padding: '4px 8px', textAlign: 'right' }}>{String(aq[k])}</td>
                <td style={{ padding: '4px 8px', textAlign: 'right' }}>{String(bq[k])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export default function SavedScenarios() {
  const [d, setD] = useState([])
  const [name, setName] = useState('')
  const [age, setAge] = useState(60)
  const [saveError, setSaveError] = useState(null)
  const [recalculating, setRecalculating] = useState(null)
  const [compareIds, setCompareIds] = useState([])
  const { ssTiming } = useScenario()

  const load = () => axios.get('/api/saved-scenarios').then(r => setD(r.data))
  useEffect(() => { load() }, [])

  const save = async () => {
    if (!name.trim()) return
    setSaveError(null)
    try {
      await axios.post('/api/saved-scenarios', { name, retirement_age: Number(age), ss_timing: ssTiming })
      setName('')
      load()
    } catch (e) {
      setSaveError(e.response?.data?.detail || 'Could not save this scenario.')
    }
  }

  const recalculate = async id => {
    setRecalculating(id)
    try {
      await axios.post(`/api/saved-scenarios/${id}/recalculate`)
      await load()
    } finally {
      setRecalculating(null)
    }
  }

  const toggleCompare = id => setCompareIds(prev =>
    prev.includes(id) ? prev.filter(x => x !== id) : prev.length >= 2 ? [prev[1], id] : [...prev, id]
  )
  const [cmpA, cmpB] = compareIds.map(id => d.find(x => x.id === id))

  return (
    <div>
      <div style={{ marginBottom: 28 }}>
        <h1 className="section-title">Saved Scenarios</h1>
        <p className="section-sub">Keep named retirement tradeoffs side by side as assumptions change.</p>
      </div>
      <div className="card" style={{ marginBottom: 20, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Retire at 58" />
        <input type="number" value={age} onChange={e => setAge(e.target.value)} style={{ width: 100 }} />
        <span style={{ fontSize: 12, color: 'var(--text2)' }}>{ssLabel(ssTiming)}</span>
        <button className="btn-primary" onClick={save}>Save scenario</button>
        {saveError && <span style={{ fontSize: 12, color: 'var(--amber)' }}>{saveError}</span>}
      </div>
      {compareIds.length === 2 && <CompareDiff a={cmpA} b={cmpB} />}
      {compareIds.length === 1 && (
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>Pick one more scenario to compare.</div>
      )}
      <div className="grid-3" style={{ marginTop: compareIds.length === 2 ? 20 : 0 }}>
        {d.map(x => (
          <ScenarioCard
            key={x.id} x={x}
            onRecalculate={recalculate} recalculating={recalculating}
            onToggleCompare={toggleCompare} compareChecked={compareIds.includes(x.id)}
          />
        ))}
      </div>
    </div>
  )
}
