import { useState, useEffect, useRef, useCallback } from 'react'
import axios from 'axios'
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid } from 'recharts'
import { isPrivacyMode, MASK_CURRENCY, MASK_PERCENT } from '../utils/privacy'

// Portfolio holdings, allocation, and rebalancing (Milestone 4,
// codex/portfolio-holdings-allocation). Decision support only — nothing
// on this page places a trade. Every recommendation the backend returns
// carries its own reason/confidence note; this page renders those
// verbatim rather than summarizing them into something that reads as a
// guarantee.

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(n))
const fmtPct = (n) => isPrivacyMode() ? MASK_PERCENT : (n == null ? '—' : `${n.toFixed(1)}%`)

const ASSET_CLASSES = ['us_stock', 'international_stock', 'bonds', 'cash', 'real_estate', 'alternatives', 'unclassified']
const ASSET_CLASS_LABELS = {
  us_stock: 'US Stock', international_stock: 'Intl Stock', bonds: 'Bonds', cash: 'Cash',
  real_estate: 'Real Estate', alternatives: 'Alternatives', unclassified: 'Unclassified',
}
const ASSET_CLASS_COLORS = {
  us_stock: '#4f9cf9', international_stock: '#5C7CE0', bonds: '#34d399', cash: '#fbbf24',
  real_estate: '#a78bfa', alternatives: '#f97316', unclassified: '#94a3b8',
}

const PORTFOLIO_ACCOUNT_TYPES = [
  'brokerage', 'traditional_401k', 'roth_401k', 'traditional_ira', 'roth_ira',
  'hsa', '529', 'custodial', 'checking', 'savings', 'trust', 'other',
]
const PORTFOLIO_TYPE_LABELS = {
  brokerage: 'Brokerage', traditional_401k: 'Traditional 401(k)', roth_401k: 'Roth 401(k)',
  traditional_ira: 'Traditional IRA', roth_ira: 'Roth IRA', hsa: 'HSA', '529': '529',
  custodial: 'Custodial', checking: 'Checking', savings: 'Savings', trust: 'Trust', other: 'Other',
}

function Card({ title, subtitle, children, style }) {
  return (
    <div className="card" style={{ marginBottom: 24, ...style }}>
      {title && <div className="label" style={{ marginBottom: subtitle ? 4 : 16 }}>{title}</div>}
      {subtitle && <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 16 }}>{subtitle}</div>}
      {children}
    </div>
  )
}

function SetupState({ icon = '◇', title, body, action }) {
  return (
    <div className="card" style={{ textAlign: 'center', padding: '48px 24px', marginBottom: 24 }}>
      <div style={{ fontSize: 32, marginBottom: 12, opacity: 0.6 }}>{icon}</div>
      <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 8 }}>{title}</div>
      <div style={{ fontSize: 13, color: 'var(--text2)', maxWidth: 480, margin: '0 auto 16px' }}>{body}</div>
      {action}
    </div>
  )
}

// ── Manual holding entry form ───────────────────────────────────────────
function HoldingForm({ accounts, holding, onSaved, onCancel }) {
  const [form, setForm] = useState(holding || {
    account_id: accounts[0]?.account_id ?? '', name: '', description: '',
    shares: '', market_value: '', asset_class: 'us_stock',
    expense_ratio: '', cost_basis: '', notes: '',
  })
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)

  const submit = async () => {
    setSaving(true); setError(null)
    const payload = {
      account_id: Number(form.account_id),
      name: form.name,
      description: form.description || null,
      shares: form.shares === '' ? null : Number(form.shares),
      market_value: Number(form.market_value),
      asset_class: form.asset_class,
      expense_ratio: form.expense_ratio === '' ? null : Number(form.expense_ratio),
      cost_basis: form.cost_basis === '' ? null : Number(form.cost_basis),
      notes: form.notes || null,
    }
    try {
      if (holding?.id) {
        await axios.put(`/api/holdings/${holding.id}`, payload)
      } else {
        await axios.post('/api/holdings', payload)
      }
      onSaved()
    } catch (e) {
      setError(e.response?.data?.detail || 'Could not save this holding — check the values above.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ background: 'var(--bg3)', borderRadius: 8, padding: 16, marginBottom: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
        <label style={{ fontSize: 12 }}>
          Account
          <select value={form.account_id} onChange={e => setForm({ ...form, account_id: e.target.value })}
                  style={{ width: '100%', marginTop: 4 }}>
            {accounts.map(a => <option key={a.account_id} value={a.account_id}>{a.account_name}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 12 }}>
          Asset class
          <select value={form.asset_class} onChange={e => setForm({ ...form, asset_class: e.target.value })}
                  style={{ width: '100%', marginTop: 4 }}>
            {ASSET_CLASSES.map(c => <option key={c} value={c}>{ASSET_CLASS_LABELS[c]}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 12 }}>
          Ticker or fund name
          <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
                 placeholder="e.g. VTI" style={{ width: '100%', marginTop: 4 }} />
        </label>
        <label style={{ fontSize: 12 }}>
          Market value
          <input type="number" value={form.market_value} onChange={e => setForm({ ...form, market_value: e.target.value })}
                 style={{ width: '100%', marginTop: 4 }} />
        </label>
        <label style={{ fontSize: 12 }}>
          Shares (optional)
          <input type="number" value={form.shares} onChange={e => setForm({ ...form, shares: e.target.value })}
                 style={{ width: '100%', marginTop: 4 }} />
        </label>
        <label style={{ fontSize: 12 }}>
          Cost basis (optional)
          <input type="number" value={form.cost_basis} onChange={e => setForm({ ...form, cost_basis: e.target.value })}
                 style={{ width: '100%', marginTop: 4 }} />
        </label>
        <label style={{ fontSize: 12 }}>
          Expense ratio (optional, e.g. 0.0003)
          <input type="number" step="0.0001" value={form.expense_ratio} onChange={e => setForm({ ...form, expense_ratio: e.target.value })}
                 style={{ width: '100%', marginTop: 4 }} />
        </label>
        <label style={{ fontSize: 12 }}>
          Description (optional)
          <input value={form.description} onChange={e => setForm({ ...form, description: e.target.value })}
                 style={{ width: '100%', marginTop: 4 }} />
        </label>
      </div>
      {error && <div style={{ color: 'var(--red)', fontSize: 12, marginBottom: 8 }}>⚠ {error}</div>}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn-primary" disabled={saving || !form.name || form.market_value === ''} onClick={submit}>
          {saving ? 'Saving…' : 'Save holding'}
        </button>
        <button className="btn-secondary" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

// ── CSV import (preview → validate → commit) ────────────────────────────
function CsvImport({ onImported }) {
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState(null)
  const [committing, setCommitting] = useState(false)
  const inputRef = useRef()

  const handleFile = async (file) => {
    if (!file) return
    setError(null); setPreview(null)
    const form = new FormData()
    form.append('file', file)
    try {
      const r = await axios.post('/api/holdings/import/preview', form, { headers: { 'Content-Type': 'multipart/form-data' } })
      setPreview(r.data)
    } catch (e) {
      setError(e.response?.data?.detail || 'Could not read that file.')
    }
  }

  const commit = async () => {
    if (!preview) return
    setCommitting(true)
    const validRows = preview.rows.filter(r => r.valid).map(r => ({
      account_id: r.account_id, name: r.name, description: r.description,
      shares: r.shares, market_value: r.market_value, asset_class: r.asset_class,
      expense_ratio: r.expense_ratio, cost_basis: r.cost_basis, notes: r.notes,
    }))
    try {
      await axios.post('/api/holdings/import/commit', validRows)
      setPreview(null)
      onImported()
    } catch {
      setError('Import failed while saving.')
    } finally {
      setCommitting(false)
    }
  }

  return (
    <Card title="Import holdings from CSV" subtitle="Columns: account_id, name, market_value, asset_class (required); description, shares, expense_ratio, cost_basis, notes (optional). Nothing is saved until you review the preview below.">
      <input ref={inputRef} type="file" accept=".csv" style={{ display: 'none' }}
             onChange={e => handleFile(e.target.files[0])} />
      <button className="btn-secondary" onClick={() => inputRef.current?.click()}>Choose CSV file</button>
      {error && <div style={{ color: 'var(--red)', fontSize: 12, marginTop: 8 }}>⚠ {error}</div>}
      {preview && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 13, marginBottom: 8 }}>
            <strong>{preview.valid_count}</strong> valid row{preview.valid_count === 1 ? '' : 's'}
            {preview.invalid_count > 0 && <span style={{ color: 'var(--red)' }}> · {preview.invalid_count} invalid (won't be imported)</span>}
          </div>
          {preview.rows.length > 0 && (
            <div style={{ overflowX: 'auto', maxHeight: 300, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)' }}>
                    {['Row', 'Account', 'Name', 'Value', 'Asset class', 'Status'].map(h => (
                      <th key={h} style={{ padding: '6px 8px', textAlign: 'left', color: 'var(--text3)' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map(r => (
                    <tr key={r.row} style={{ borderBottom: '1px solid var(--border)', opacity: r.valid ? 1 : 0.6 }}>
                      <td style={{ padding: '6px 8px' }}>{r.row}</td>
                      <td style={{ padding: '6px 8px' }}>{r.account_id ?? '—'}</td>
                      <td style={{ padding: '6px 8px' }}>{r.name ?? '—'}</td>
                      <td style={{ padding: '6px 8px' }}>{r.market_value != null ? fmt(r.market_value) : '—'}</td>
                      <td style={{ padding: '6px 8px' }}>{r.asset_class ?? '—'}</td>
                      <td style={{ padding: '6px 8px', color: r.valid ? 'var(--green)' : 'var(--red)' }}>
                        {r.valid ? '✓ valid' : r.errors.join('; ')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
            <button className="btn-primary" disabled={committing || preview.valid_count === 0} onClick={commit}>
              {committing ? 'Importing…' : `Import ${preview.valid_count} valid row${preview.valid_count === 1 ? '' : 's'}`}
            </button>
            <button className="btn-secondary" onClick={() => setPreview(null)}>Cancel</button>
          </div>
        </div>
      )}
    </Card>
  )
}

// ── Investment policy editor ─────────────────────────────────────────────
function PolicyEditor({ policy, onSaved }) {
  const [form, setForm] = useState(policy || {
    name: 'Household Policy', target_us_stock_pct: 50, target_international_stock_pct: 10,
    target_bonds_pct: 30, target_cash_pct: 5, target_real_estate_pct: 0, target_alternatives_pct: 5,
    drift_band_pct: 5, rebalance_cadence: 'annual', minimum_cash_reserve: 0,
    use_contributions_before_sales: true, notes: '',
  })
  const [saving, setSaving] = useState(false)

  const sum = ASSET_CLASSES.filter(c => c !== 'unclassified').reduce((s, c) => s + Number(form[`target_${c}_pct`] || 0), 0)

  const submit = async () => {
    setSaving(true)
    try {
      await axios.post('/api/investment-policy', {
        ...form,
        target_us_stock_pct: Number(form.target_us_stock_pct),
        target_international_stock_pct: Number(form.target_international_stock_pct),
        target_bonds_pct: Number(form.target_bonds_pct),
        target_cash_pct: Number(form.target_cash_pct),
        target_real_estate_pct: Number(form.target_real_estate_pct),
        target_alternatives_pct: Number(form.target_alternatives_pct),
        drift_band_pct: Number(form.drift_band_pct),
        minimum_cash_reserve: Number(form.minimum_cash_reserve),
      })
      onSaved()
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card title="Investment policy" subtitle="Target allocation, drift band, and rebalance rules — saved policies never overwrite each other, the most recent one is always what's active.">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginBottom: 12 }}>
        {['us_stock', 'international_stock', 'bonds', 'cash', 'real_estate', 'alternatives'].map(c => (
          <label key={c} style={{ fontSize: 12 }}>
            {ASSET_CLASS_LABELS[c]} target %
            <input type="number" value={form[`target_${c}_pct`]}
                   onChange={e => setForm({ ...form, [`target_${c}_pct`]: e.target.value })}
                   style={{ width: '100%', marginTop: 4 }} />
          </label>
        ))}
      </div>
      <div style={{ fontSize: 12, color: sum === 100 ? 'var(--text2)' : 'var(--amber)', marginBottom: 12 }}>
        Targets sum to {sum.toFixed(1)}% {sum !== 100 && '(doesn\'t need to be exactly 100%, but usually should be)'}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginBottom: 12 }}>
        <label style={{ fontSize: 12 }}>
          Drift band (%)
          <input type="number" value={form.drift_band_pct} onChange={e => setForm({ ...form, drift_band_pct: e.target.value })}
                 style={{ width: '100%', marginTop: 4 }} />
        </label>
        <label style={{ fontSize: 12 }}>
          Rebalance cadence
          <select value={form.rebalance_cadence} onChange={e => setForm({ ...form, rebalance_cadence: e.target.value })}
                  style={{ width: '100%', marginTop: 4 }}>
            <option value="annual">Annual</option>
            <option value="semiannual">Semiannual</option>
            <option value="quarterly">Quarterly</option>
            <option value="threshold">Threshold-only (drift band, no fixed schedule)</option>
          </select>
        </label>
        <label style={{ fontSize: 12 }}>
          Minimum cash reserve ($)
          <input type="number" value={form.minimum_cash_reserve} onChange={e => setForm({ ...form, minimum_cash_reserve: e.target.value })}
                 style={{ width: '100%', marginTop: 4 }} />
        </label>
      </div>
      <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <input type="checkbox" checked={form.use_contributions_before_sales}
               onChange={e => setForm({ ...form, use_contributions_before_sales: e.target.checked })} />
        Prefer new contributions / tax-advantaged exchanges over taxable sales
      </label>
      <button className="btn-primary" disabled={saving} onClick={submit}>
        {saving ? 'Saving…' : 'Save policy'}
      </button>
    </Card>
  )
}

// ── Rebalance action row ─────────────────────────────────────────────────
function ActionRow({ a }) {
  return (
    <tr style={{ borderBottom: '1px solid var(--border)' }}>
      <td style={{ padding: '8px 12px', fontSize: 13, textTransform: 'capitalize', fontWeight: 600 }}>{a.action}</td>
      <td style={{ padding: '8px 12px', fontSize: 13 }}>{a.holding_name ?? '—'}</td>
      <td style={{ padding: '8px 12px', fontSize: 13 }}>{ASSET_CLASS_LABELS[a.asset_class] ?? a.asset_class ?? '—'}</td>
      <td style={{ padding: '8px 12px', fontSize: 13, fontWeight: 600 }}>{fmt(a.amount)}</td>
      <td style={{ padding: '8px 12px', fontSize: 13 }}>
        {a.is_taxable_sale ? <span style={{ color: 'var(--amber)' }}>⚠ Taxable</span> : <span style={{ color: 'var(--green)' }}>Tax-advantaged</span>}
      </td>
      <td style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text2)', maxWidth: 320 }}>
        {a.reason}
        {a.tax_warning && (
          <div style={{ marginTop: 4, color: a.tax_warning.has_cost_basis ? 'var(--text2)' : 'var(--red)' }}>
            {a.tax_warning.message}
          </div>
        )}
        {a.confidence_note && <div style={{ marginTop: 4, fontStyle: 'italic' }}>{a.confidence_note}</div>}
      </td>
    </tr>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────
export default function PortfolioAllocation() {
  const [grouped, setGrouped] = useState(null)
  const [allocation, setAllocation] = useState(null)
  const [policy, setPolicy] = useState(null)
  const [loading, setLoading] = useState(true)
  const [addingTo, setAddingTo] = useState(null) // account_id currently showing the add-holding form, or 'new'
  const [editingHolding, setEditingHolding] = useState(null)
  const [typeFilter, setTypeFilter] = useState('all')
  const [showPolicyEditor, setShowPolicyEditor] = useState(false)

  // "Where should my next contribution go?" / "How should I rebalance?"
  // workflow state — deliberately reset (stale-result clearing) whenever
  // holdings or the policy change, via the `refreshKey`-driven reload
  // below, so a recommendation computed against the OLD holdings/policy
  // never lingers on screen looking current after an edit.
  const [contributionAmount, setContributionAmount] = useState('')
  const [contributionResult, setContributionResult] = useState(null)
  const [rebalanceResult, setRebalanceResult] = useState(null)
  const [workflowLoading, setWorkflowLoading] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      axios.get('/api/holdings/grouped'),
      axios.get('/api/portfolio/allocation'),
      axios.get('/api/investment-policy'),
    ]).then(([g, a, p]) => {
      setGrouped(g.data)
      setAllocation(a.data)
      setPolicy(p.data)
    }).catch(() => {}).finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load, refreshKey])

  // Stale-result clearing: any holdings/policy edit bumps refreshKey
  // (via onDataChanged below), which both reloads the read-only views
  // above AND clears any previously-computed contribution/rebalance
  // recommendation, since those are only valid against the data that
  // produced them.
  const onDataChanged = () => {
    setContributionResult(null)
    setRebalanceResult(null)
    setAddingTo(null)
    setEditingHolding(null)
    setRefreshKey(k => k + 1)
  }

  const runContributionDestination = async () => {
    setWorkflowLoading(true)
    try {
      const r = await axios.post('/api/portfolio/contribution-destination', { amount: Number(contributionAmount) || 0 })
      setContributionResult(r.data)
    } catch (e) {
      setContributionResult({ error: e.response?.data?.detail || 'Could not compute a recommendation.' })
    } finally {
      setWorkflowLoading(false)
    }
  }

  const runRebalance = async () => {
    setWorkflowLoading(true)
    try {
      const r = await axios.post('/api/portfolio/rebalance', { amount: Number(contributionAmount) || 0 })
      setRebalanceResult(r.data)
    } catch (e) {
      setRebalanceResult({ error: e.response?.data?.detail || 'Could not compute a rebalance plan.' })
    } finally {
      setWorkflowLoading(false)
    }
  }

  const deleteHolding = async (id) => {
    await axios.delete(`/api/holdings/${id}`)
    onDataChanged()
  }

  if (loading) return <div className="loading">Loading your portfolio…</div>

  const groups = (grouped?.groups || []).filter(g => typeFilter === 'all' || g.portfolio_account_type === typeFilter)
  const hasAnyHoldings = (grouped?.groups || []).some(g => g.holdings.length > 0)

  const chartData = allocation?.has_holdings
    ? ASSET_CLASSES.map(c => ({
        class: ASSET_CLASS_LABELS[c],
        current: allocation.current_allocation.pct_by_class[c] || 0,
        target: allocation.has_policy ? (allocation.comparison?.by_class[c]?.target_pct || 0) : null,
      }))
    : []

  return (
    <div>
      <div style={{ marginBottom: 28 }}>
        <h1 className="section-title">Portfolio Allocation</h1>
        <p className="section-sub">
          Holdings, target allocation, and tax-aware rebalancing — decision support only. Nothing here places a
          trade or is guaranteed financial advice; verify any recommendation before acting on it.
        </p>
      </div>

      {!hasAnyHoldings && (
        <SetupState
          title="No holdings entered yet"
          body="Add the individual funds/stocks inside each account below, or import them from a CSV, to see your real current allocation instead of an account-level guess."
        />
      )}

      <Card title="Accounts &amp; holdings" subtitle="Grouped by account, with each account's own reconciliation against its total balance.">
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 12 }}>
            Filter by account type:{' '}
            <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}>
              <option value="all">All types</option>
              {PORTFOLIO_ACCOUNT_TYPES.map(t => <option key={t} value={t}>{PORTFOLIO_TYPE_LABELS[t]}</option>)}
            </select>
          </label>
        </div>
        {groups.map(g => (
          <div key={g.account_id} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14, marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{g.account_name}</div>
                <div style={{ fontSize: 12, color: 'var(--text2)' }}>
                  {PORTFOLIO_TYPE_LABELS[g.portfolio_account_type] || g.portfolio_account_type} · {g.owner}
                  {g.allocation_blocked && <span style={{ color: 'var(--red)' }}> · ⚠ classify this account's type before it can be allocated</span>}
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 13 }}>Balance: {fmt(g.account_balance)}</div>
                <div style={{ fontSize: 12, color: 'var(--text2)' }}>Holdings: {fmt(g.holdings_total)}</div>
              </div>
            </div>
            {g.has_warning && (
              <div style={{ background: 'rgba(251,191,36,0.08)', color: 'var(--amber)', borderRadius: 6, padding: '6px 10px', fontSize: 12, marginBottom: 8 }}>
                {/* Built from the already-masked numeric fields, not
                    g.warning's raw string -- the backend bakes real
                    dollar figures directly into that message text (see
                    holdings_engine.reconcile_account_holdings), which
                    privacy mode's fmt() has no way to mask after the
                    fact. */}
                ⚠ Holdings total {fmt(g.holdings_total)} does not match the account balance {fmt(g.account_balance)} —{' '}
                {fmt(g.unreconciled_remainder)} is shown as an unclassified remainder rather than assumed.
              </div>
            )}
            {g.holdings.length > 0 && (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, marginBottom: 8 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)' }}>
                    {['Name', 'Asset class', 'Value', 'Cost basis', 'Expense ratio', ''].map(h => (
                      <th key={h} style={{ padding: '4px 8px', textAlign: 'left', color: 'var(--text3)' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {g.holdings.map(h => (
                    <tr key={h.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '4px 8px' }}>{h.name}</td>
                      <td style={{ padding: '4px 8px' }}>{ASSET_CLASS_LABELS[h.asset_class] || h.asset_class}</td>
                      <td style={{ padding: '4px 8px' }}>{fmt(h.market_value)}</td>
                      <td style={{ padding: '4px 8px' }}>{h.cost_basis != null ? fmt(h.cost_basis) : <span style={{ color: 'var(--text3)' }}>not set</span>}</td>
                      <td style={{ padding: '4px 8px' }}>{h.expense_ratio != null ? `${(h.expense_ratio * 100).toFixed(2)}%` : '—'}</td>
                      <td style={{ padding: '4px 8px', textAlign: 'right' }}>
                        <button className="btn-secondary" onClick={() => setEditingHolding(h)}>Edit</button>{' '}
                        <button className="btn-secondary" onClick={() => deleteHolding(h.id)}>Delete</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {editingHolding?.id && g.holdings.some(h => h.id === editingHolding.id) && (
              <HoldingForm accounts={[{ account_id: g.account_id, account_name: g.account_name }]}
                           holding={editingHolding} onSaved={onDataChanged} onCancel={() => setEditingHolding(null)} />
            )}
            {addingTo === g.account_id ? (
              <HoldingForm accounts={[{ account_id: g.account_id, account_name: g.account_name }]}
                           onSaved={onDataChanged} onCancel={() => setAddingTo(null)} />
            ) : (
              <button className="btn-secondary" onClick={() => setAddingTo(g.account_id)}>+ Add holding</button>
            )}
          </div>
        ))}
      </Card>

      <CsvImport onImported={onDataChanged} />

      {allocation?.has_holdings && (
        <>
          <Card title="Current vs. target allocation" subtitle={allocation.has_policy ? undefined : 'No investment policy saved yet — set target percentages below to see drift.'}>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={chartData} margin={{ top: 0, right: 0, bottom: 0, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="class" tick={{ fill: 'var(--text3)', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={v => `${v}%`} tick={{ fill: 'var(--text3)', fontSize: 11 }} axisLine={false} tickLine={false} width={45} />
                <Tooltip formatter={(v, n) => [`${Number(v).toFixed(1)}%`, n]} contentStyle={{ background: 'var(--bg3)', border: '1px solid var(--border2)', borderRadius: 8, fontSize: 11 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="current" name="Current" fill="#4f9cf9" radius={[4, 4, 0, 0]} />
                {allocation.has_policy && <Bar dataKey="target" name="Target" fill="#34d399" radius={[4, 4, 0, 0]} />}
              </BarChart>
            </ResponsiveContainer>
            {allocation.has_policy && (
              <div style={{ overflowX: 'auto', marginTop: 12 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      {['Asset class', 'Current', 'Target', 'Deviation', 'Within drift band?'].map(h => (
                        <th key={h} style={{ padding: '6px 8px', textAlign: 'left', color: 'var(--text3)' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {ASSET_CLASSES.map(c => {
                      const d = allocation.comparison.by_class[c]
                      return (
                        <tr key={c} style={{ borderBottom: '1px solid var(--border)' }}>
                          <td style={{ padding: '6px 8px' }}>{ASSET_CLASS_LABELS[c]}</td>
                          <td style={{ padding: '6px 8px' }}>{fmtPct(d.current_pct)}</td>
                          <td style={{ padding: '6px 8px' }}>{fmtPct(d.target_pct)}</td>
                          <td style={{ padding: '6px 8px', color: d.within_drift_band ? 'var(--text2)' : 'var(--amber)' }}>
                            {d.deviation_pct > 0 ? '+' : ''}{fmtPct(d.deviation_pct)}
                          </td>
                          <td style={{ padding: '6px 8px' }}>{d.within_drift_band ? '✓' : '⚠ outside band'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {(allocation.blocked_holdings?.length > 0 || allocation.unclassified_flags?.length > 0 ||
            allocation.concentration_flags?.length > 0 || allocation.expense_ratio_flags?.length > 0 ||
            allocation.duplicate_exposure_flags?.length > 0) && (
            <Card title="Portfolio health flags">
              {allocation.blocked_holdings?.length > 0 && (
                <div style={{ fontSize: 13, color: 'var(--red)', marginBottom: 8 }}>
                  ⚠ {allocation.blocked_holdings.length} holding(s) in an unclassified/blocked account — classify the account's type before it counts toward allocation.
                </div>
              )}
              {allocation.unclassified_flags?.length > 0 && (
                <div style={{ fontSize: 13, color: 'var(--amber)', marginBottom: 8 }}>
                  ⚠ {allocation.unclassified_flags.length} holding(s) have no asset class set: {allocation.unclassified_flags.map(f => f.name).join(', ')}
                </div>
              )}
              {allocation.concentration_flags?.length > 0 && (
                <div style={{ fontSize: 13, marginBottom: 8 }}>
                  {allocation.concentration_flags.map(f => (
                    <div key={f.holding_id} style={{ color: f.severity === 'severe' ? 'var(--red)' : 'var(--amber)' }}>
                      ⚠ {f.name} is {f.pct_of_portfolio}% of your portfolio ({f.severity})
                    </div>
                  ))}
                </div>
              )}
              {allocation.expense_ratio_flags?.length > 0 && (
                <div style={{ fontSize: 13, marginBottom: 8 }}>
                  {allocation.expense_ratio_flags.map(f => (
                    <div key={f.holding_id} style={{ color: 'var(--amber)' }}>
                      ⚠ {f.name}: {f.expense_ratio_pct}% expense ratio (~{fmt(f.annual_fee_dollars)}/yr)
                    </div>
                  ))}
                </div>
              )}
              {allocation.duplicate_exposure_flags?.length > 0 && (
                <div style={{ fontSize: 13 }}>
                  {allocation.duplicate_exposure_flags.map(f => (
                    <div key={f.name} style={{ color: 'var(--text2)' }}>
                      {f.name} held across {f.accounts.length} accounts — {fmt(f.total_market_value)} total
                    </div>
                  ))}
                </div>
              )}
            </Card>
          )}
        </>
      )}

      {!showPolicyEditor && policy?.has_policy && (
        <Card title="Investment policy">
          <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 8 }}>
            Target: {ASSET_CLASSES.filter(c => c !== 'unclassified').map(c => `${ASSET_CLASS_LABELS[c]} ${policy.policy[`target_${c}_pct`]}%`).join(' · ')}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>
            Drift band ±{policy.policy.drift_band_pct}% · {policy.policy.rebalance_cadence} cadence
          </div>
          <button className="btn-secondary" onClick={() => setShowPolicyEditor(true)}>Edit policy</button>
        </Card>
      )}
      {(showPolicyEditor || !policy?.has_policy) && (
        <PolicyEditor policy={policy?.has_policy ? policy.policy : null}
                       onSaved={() => { setShowPolicyEditor(false); onDataChanged() }} />
      )}

      {allocation?.has_holdings && policy?.has_policy && (
        <Card title="What should happen next?" subtitle="Enter a pending contribution amount (0 if none) to see where new money should go and, separately, a full rebalance plan.">
          <label style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
            Pending contribution amount ($)
            <input type="number" value={contributionAmount} onChange={e => setContributionAmount(e.target.value)}
                   style={{ width: 200, display: 'block', marginTop: 4 }} />
          </label>
          <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
            <button className="btn-secondary" disabled={workflowLoading} onClick={runContributionDestination}>
              Where should my next contribution go?
            </button>
            <button className="btn-secondary" disabled={workflowLoading} onClick={runRebalance}>
              How should I rebalance?
            </button>
          </div>

          {contributionResult && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Contribution destination</div>
              {contributionResult.error ? (
                <div style={{ color: 'var(--red)', fontSize: 13 }}>⚠ {contributionResult.error}</div>
              ) : (
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                  {contributionResult.actions.map((a, i) => (
                    <li key={i} style={{ marginBottom: 4 }}>
                      {a.asset_class ? <strong>{ASSET_CLASS_LABELS[a.asset_class]}</strong> : <strong>No specific class</strong>}: {fmt(a.amount)} — {a.reason}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {rebalanceResult && (
            <div>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Rebalance / trade checklist</div>
              {rebalanceResult.error ? (
                <div style={{ color: 'var(--red)', fontSize: 13 }}>⚠ {rebalanceResult.error}</div>
              ) : rebalanceResult.rebalance_actions.length === 0 ? (
                <div style={{ fontSize: 13, color: 'var(--text2)' }}>No trades needed — the contribution above (or your current holdings) already fund every underweight class within the drift band.</div>
              ) : (
                <>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid var(--border)' }}>
                          {['Action', 'Holding', 'Asset class', 'Amount', 'Tax impact', 'Reason / notes'].map(h => (
                            <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontSize: 11, fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text3)' }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {rebalanceResult.rebalance_actions.map((a, i) => <ActionRow key={i} a={a} />)}
                      </tbody>
                    </table>
                  </div>
                  <div style={{ marginTop: 16 }}>
                    <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Projected allocation after these actions</div>
                    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                      {ASSET_CLASSES.map(c => (
                        <div key={c} style={{ fontSize: 12 }}>
                          <span style={{ color: ASSET_CLASS_COLORS[c] }}>●</span> {ASSET_CLASS_LABELS[c]}: {fmtPct(rebalanceResult.projected_allocation.pct_by_class[c])}
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
