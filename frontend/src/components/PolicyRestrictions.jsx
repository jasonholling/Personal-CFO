import { useEffect, useState } from 'react'
import axios from 'axios'

const CLASSES = ['us_large_cap', 'us_mid_cap', 'us_small_cap', 'international_developed',
  'emerging_markets', 'us_bonds', 'international_bonds', 'cash', 'real_estate', 'alternatives', 'unclassified']
const EXCEPTIONS = [['employer_stock_exceptions', 'Employer stock'], ['legacy_holding_exceptions', 'Legacy holding']]
const sectionStyle = { marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--border)' }

export default function PolicyRestrictions({ accounts, policy, onChange }) {
  const [holdings, setHoldings] = useState([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [selectedHolding, setSelectedHolding] = useState('')
  const [exceptionType, setExceptionType] = useState('employer_stock_exceptions')
  const [draftModes, setDraftModes] = useState({})
  useEffect(() => {
    let active = true
    axios.get('/api/holdings').then(r => { if (active) setHoldings(r.data) })
      .catch(() => { if (active) setError('Could not load holdings. Existing policy selections are preserved. Reopen this tab to retry.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])
  const accountName = id => accounts.find(a => a.id === id)?.name || `Account ${id}`
  const holdingName = h => `${h.security_name}${h.ticker ? ` (${h.ticker})` : ''} — ${accountName(h.account_id)}`
  const describeException = e => [
    e.holding_id != null ? (holdings.find(h => h.id === e.holding_id) ? holdingName(holdings.find(h => h.id === e.holding_id)) : `Holding ${e.holding_id} (unavailable)`) : null,
    e.account_id != null ? `All holdings in ${accountName(e.account_id)}` : null,
    e.ticker ? `${e.ticker} across accounts` : null,
  ].filter(Boolean).join('; ') || 'Existing exception'
  const updateConstraint = (id, change) => onChange(p => {
    const rows = p.account_constraints || []
    const old = rows.find(c => c.account_id === id) || { account_id: id }
    const next = change(old)
    return { ...p, account_constraints: next
      ? [...rows.filter(c => c.account_id !== id), next]
      : rows.filter(c => c.account_id !== id) }
  })
  return <>
    <section style={sectionStyle} aria-label="Holding exclusions">
      <h3 style={{ fontSize: 14 }}>Individual holding exclusions</h3>
      <p style={{ fontSize: 12, color: 'var(--muted)' }}>Excluded positions leave investment allocation and Coach advice. They remain stored and count in the rest of your financial plan. Save policy below to apply changes.</p>
      {loading && <p>Loading holdings…</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && !holdings.length && <p>No holdings entered yet.</p>}
      {holdings.map(h => <label key={h.id} style={{ display: 'block', marginBottom: 8 }}>
        <input type="checkbox" checked={(policy.excluded_holdings || []).includes(h.id)}
          onChange={e => { const checked = e.target.checked; onChange(p => ({ ...p, excluded_holdings: checked
            ? [...new Set([...(p.excluded_holdings || []), h.id])]
            : (p.excluded_holdings || []).filter(id => id !== h.id) })) }} />
        {' '}Exclude {holdingName(h)}
        {(policy.excluded_accounts || []).includes(h.account_id) && <small> — entire account already excluded</small>}
      </label>)}
    </section>
    <section style={sectionStyle} aria-label="Protected holdings">
      <h3 style={{ fontSize: 14 }}>Employer stock and legacy exceptions</h3>
      <p style={{ fontSize: 12, color: 'var(--muted)' }}>Protected positions stay in allocation totals, but do not generate concentration or rebalance-sale recommendations. Exclusion takes precedence if both are selected.</p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <select className="input" aria-label="Holding to protect" value={selectedHolding} onChange={e => setSelectedHolding(e.target.value)}>
          <option value="">Choose a holding…</option>
          {holdings.map(h => <option key={h.id} value={h.id}>{holdingName(h)}</option>)}
        </select>
        <select className="input" aria-label="Exception type" value={exceptionType} onChange={e => setExceptionType(e.target.value)}>
          {EXCEPTIONS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
        <button type="button" className="btn-secondary" disabled={!selectedHolding} onClick={() => {
          const id = Number(selectedHolding)
          onChange(p => ({ ...p, [exceptionType]: (p[exceptionType] || []).some(e => e.holding_id === id)
            ? p[exceptionType] : [...(p[exceptionType] || []), { holding_id: id }] }))
          setSelectedHolding('')
        }}>Protect holding</button>
      </div>
      {EXCEPTIONS.map(([key, label]) => (policy[key] || []).map((e, index) => <div key={`${key}-${index}`} style={{ marginTop: 8 }}>
        {label}: {describeException(e)}
        {e.reason && <small> — {e.reason}</small>}
        <button type="button" className="btn-secondary" aria-label={`Remove ${label} exception ${index + 1}`} style={{ marginLeft: 8 }}
          onClick={() => onChange(p => ({ ...p, [key]: (p[key] || []).filter((_, i) => i !== index) }))}>Remove protection</button>
      </div>))}
    </section>
    <section style={sectionStyle} aria-label="Account constraints">
      <h3 style={{ fontSize: 14 }}>Account asset-class constraints</h3>
      <p style={{ fontSize: 12, color: 'var(--muted)' }}>These rules limit Coach destinations and fund comparisons. They do not remove existing holdings from allocation or establish which funds your provider offers.</p>
      {accounts.map(a => {
        const c = (policy.account_constraints || []).find(row => row.account_id === a.id) || {}
        const mode = draftModes[a.id] || (c.allowed_asset_classes != null ? 'allow' : c.excluded_asset_classes?.length ? 'deny' : 'any')
        const field = mode === 'allow' ? 'allowed_asset_classes' : 'excluded_asset_classes'
        const selected = c[field] || []
        return <fieldset key={a.id} style={{ marginBottom: 12, border: '1px solid var(--border)', padding: 12 }}>
          <legend>{a.name}</legend>
          {(policy.excluded_accounts || []).includes(a.id) && <p>Account excluded: these rules apply only if you include it again.</p>}
          <select className="input" aria-label={`Constraint mode for ${a.name}`} value={mode} onChange={e => {
            const nextMode = e.target.value
            setDraftModes(m => ({ ...m, [a.id]: nextMode }))
            updateConstraint(a.id, old => ({ ...old, allowed_asset_classes: nextMode === 'allow' ? [] : null, excluded_asset_classes: [] }))
          }}>
            <option value="any">No asset-class restriction</option>
            <option value="allow">Allow only selected classes</option>
            <option value="deny">Block selected classes</option>
          </select>
          {mode !== 'any' && <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 8, marginTop: 10 }}>
              {CLASSES.map(assetClass => <label key={assetClass}>
                <input type="checkbox" aria-label={`${mode === 'allow' ? 'Allow' : 'Block'} ${assetClass.replace(/_/g, ' ')} in ${a.name}`}
                  checked={selected.includes(assetClass)} onChange={e => {
                    const checked = e.target.checked
                    updateConstraint(a.id, old => ({ ...old, [field]: checked
                      ? [...new Set([...(old[field] || []), assetClass])]
                      : (old[field] || []).filter(v => v !== assetClass),
                    ...(mode === 'allow' ? { excluded_asset_classes: (old.excluded_asset_classes || []).filter(v => v !== assetClass) } : {}) }))
                  }} /> {assetClass.replace(/_/g, ' ')}
              </label>)}
            </div>
            {mode === 'allow' && !selected.length && <p role="status">No classes allowed: Coach will not recommend purchases in this account.</p>}
          </>}
        </fieldset>
      })}
    </section>
  </>
}
