import { useEffect, useState } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'
import PolicyRestrictions from '../components/PolicyRestrictions'

// Portfolio Setup (codex/portfolio-coach-recommendations) — the data
// entry surface Portfolio Coach reads from: holdings, the household
// investment policy, and per-account investment options (the general,
// NOT 401(k)-only, "what's buyable in this account" model). Deliberately
// a separate page from the Coach's own recommendation queue — this is
// where facts get entered, Coach.jsx is where they get acted on.

const ASSET_CLASSES = [
  'us_large_cap', 'us_mid_cap', 'us_small_cap', 'international_developed', 'emerging_markets',
  'us_bonds', 'international_bonds', 'cash', 'real_estate', 'alternatives', 'unclassified',
]
const POLICY_TARGET_FIELDS = [
  ['target_us_large_cap_pct', 'US large-cap'], ['target_us_mid_cap_pct', 'US mid-cap'],
  ['target_us_small_cap_pct', 'US small-cap'], ['target_international_developed_pct', 'Int\'l developed'],
  ['target_emerging_markets_pct', 'Emerging markets'], ['target_us_bonds_pct', 'US bonds'],
  ['target_international_bonds_pct', 'Int\'l bonds'], ['target_cash_pct', 'Cash'],
  ['target_real_estate_pct', 'Real estate'], ['target_alternatives_pct', 'Alternatives'],
]

const fmt = n => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n))

const TABS = [
  { id: 'holdings', label: 'Holdings' },
  { id: 'policy', label: 'Investment Policy' },
  { id: 'options', label: 'Account Investment Options' },
]

const EMPTY_HOLDING_FORM = {
  account_id: '', security_name: '', ticker: '', market_value: '', asset_class: 'us_large_cap',
  expense_ratio: '', cost_basis: '', multiAsset: false, exposures: [{ asset_class: 'us_large_cap', weight_pct: '' }],
}

function HoldingsTab({ accounts }) {
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(true)
  const [form, setForm] = useState(EMPTY_HOLDING_FORM)
  const [saving, setSaving] = useState(false)
  const [searchResults, setSearchResults] = useState([])
  const [importPreview, setImportPreview] = useState(null)
  const [importError, setImportError] = useState(null)

  const load = () => axios.get('/api/holdings/grouped').then(r => setGroups(r.data.groups)).finally(() => setLoading(false))
  useEffect(() => { load() }, [])

  const submit = async e => {
    e.preventDefault()
    if (!form.account_id || !form.security_name || !form.market_value) return
    setSaving(true)
    try {
      const exposures = form.multiAsset
        ? form.exposures.filter(x => x.asset_class && x.weight_pct).map(x => ({ asset_class: x.asset_class, weight_pct: parseFloat(x.weight_pct) }))
        : []
      await axios.post('/api/holdings', {
        account_id: parseInt(form.account_id, 10), security_name: form.security_name, ticker: form.ticker || null,
        market_value: parseFloat(form.market_value), asset_class: form.asset_class, exposures,
        expense_ratio: form.expense_ratio ? parseFloat(form.expense_ratio) / 100 : null,
        cost_basis: form.cost_basis ? parseFloat(form.cost_basis) : null,
      })
      setForm(EMPTY_HOLDING_FORM)
      await load()
    } finally { setSaving(false) }
  }

  const updateExposureRow = (i, field, value) => {
    setForm(f => ({ ...f, exposures: f.exposures.map((row, idx) => idx === i ? { ...row, [field]: value } : row) }))
  }
  const addExposureRow = () => setForm(f => ({ ...f, exposures: [...f.exposures, { asset_class: 'us_bonds', weight_pct: '' }] }))
  const removeExposureRow = i => setForm(f => ({ ...f, exposures: f.exposures.filter((_, idx) => idx !== i) }))
  const exposureTotal = form.exposures.reduce((sum, x) => sum + (parseFloat(x.weight_pct) || 0), 0)

  const remove = async id => { await axios.delete(`/api/holdings/${id}`); await load() }

  const searchTicker = async () => {
    if (!form.ticker.trim()) return
    const r = await axios.get('/api/securities/search', { params: { q: form.ticker.trim() } })
    setSearchResults(r.data.candidates || [])
  }
  const chooseSecurity = async candidate => {
    await axios.post('/api/securities/confirm', candidate)
    setForm(f => ({ ...f, ticker: candidate.ticker || '', security_name: candidate.security_name,
      asset_class: candidate.asset_class || 'unclassified' }))
    setSearchResults([])
  }
  const previewCsv = async file => {
    if (!file) return
    const body = new FormData()
    body.append('file', file)
    setImportError(null)
    try {
      const r = await axios.post('/api/holdings/import/preview', body)
      setImportPreview(r.data)
    } catch (e) { setImportError(e.response?.data?.detail || 'Could not preview this CSV.') }
  }
  const commitCsv = async () => {
    const valid = (importPreview?.rows || []).filter(r => r.valid)
    await axios.post('/api/holdings/import/commit', valid)
    setImportPreview(null)
    await load()
  }

  if (loading) return <div className="loading">Loading holdings...</div>

  return (
    <div>
      <form onSubmit={submit} className="card" style={{ marginBottom: 20, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <select className="input" value={form.account_id} onChange={e => setForm(f => ({ ...f, account_id: e.target.value }))} style={{ minWidth: 160 }}>
          <option value="">Account…</option>
          {accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <input className="input" placeholder="Security name" value={form.security_name} onChange={e => setForm(f => ({ ...f, security_name: e.target.value }))} style={{ minWidth: 160 }} />
        <input className="input" placeholder="Ticker (optional)" value={form.ticker} onChange={e => { setForm(f => ({ ...f, ticker: e.target.value })); setSearchResults([]) }} style={{ maxWidth: 100 }} />
        <button type="button" className="btn-secondary" onClick={searchTicker}>Look up ticker</button>
        <input className="input" type="number" placeholder="Market value" value={form.market_value} onChange={e => setForm(f => ({ ...f, market_value: e.target.value }))} style={{ maxWidth: 140 }} />
        {!form.multiAsset && (
          <select className="input" value={form.asset_class} onChange={e => setForm(f => ({ ...f, asset_class: e.target.value }))} style={{ minWidth: 160 }}>
            {ASSET_CLASSES.map(c => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)}
          </select>
        )}
        <input className="input" type="number" placeholder="Expense ratio %" value={form.expense_ratio} onChange={e => setForm(f => ({ ...f, expense_ratio: e.target.value }))} style={{ maxWidth: 130 }} />
        <input className="input" type="number" placeholder="Cost basis" value={form.cost_basis} onChange={e => setForm(f => ({ ...f, cost_basis: e.target.value }))} style={{ maxWidth: 130 }} />
        <label style={{ fontSize: 12, display: 'flex', gap: 4, alignItems: 'center' }}>
          <input type="checkbox" checked={form.multiAsset} onChange={e => setForm(f => ({ ...f, multiAsset: e.target.checked }))} />
          Multi-asset (target-date / balanced fund)
        </label>
        <button className="btn-primary" disabled={saving} type="submit">Add holding</button>

        {form.multiAsset && (
          <div style={{ width: '100%', marginTop: 4 }}>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>
              This fund's own asset-class composition (must sum to 100%) — used for look-through instead of forcing it into one category.
            </div>
            {form.exposures.map((row, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
                <select className="input" value={row.asset_class} onChange={e => updateExposureRow(i, 'asset_class', e.target.value)} style={{ minWidth: 160 }}>
                  {ASSET_CLASSES.map(c => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)}
                </select>
                <input className="input" type="number" placeholder="Weight %" value={row.weight_pct}
                       onChange={e => updateExposureRow(i, 'weight_pct', e.target.value)} style={{ maxWidth: 120 }} />
                <button type="button" className="btn-secondary" onClick={() => removeExposureRow(i)}>Remove</button>
              </div>
            ))}
            <button type="button" className="btn-secondary" onClick={addExposureRow}>+ Add asset class</button>
            <span style={{ marginLeft: 10, fontSize: 12, color: exposureTotal === 100 ? 'var(--green)' : 'var(--amber)' }}>
              Total: {exposureTotal}% {exposureTotal !== 100 && '— should sum to 100%'}
            </span>
          </div>
        )}
        {searchResults.length > 0 && (
          <div style={{ width: '100%', fontSize: 12 }}>
            {searchResults.map((candidate, i) => (
              <button key={`${candidate.provider_identifier}-${i}`} type="button" className="btn-secondary"
                      style={{ marginRight: 6, marginTop: 4 }} onClick={() => chooseSecurity(candidate)}>
                Use {candidate.ticker || candidate.security_name} — {candidate.security_name}
              </button>
            ))}
          </div>
        )}
      </form>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="label">Import holdings from CSV</div>
        <p style={{ fontSize: 12, color: 'var(--muted)' }}>Preview and validate every row before anything is saved.</p>
        <input aria-label="Holdings CSV" type="file" accept=".csv,text/csv" onChange={e => previewCsv(e.target.files?.[0])} />
        {importError && <div style={{ color: 'var(--red)', fontSize: 12 }}>{importError}</div>}
        {importPreview && (
          <div style={{ marginTop: 10, fontSize: 12 }}>
            <div>{importPreview.valid_count} valid · {importPreview.invalid_count} need attention</div>
            {(importPreview.errors || []).map((e, i) => <div key={i} style={{ color: 'var(--red)' }}>Row {e.row}: {e.message}</div>)}
            <button className="btn-primary" disabled={!importPreview.valid_count} onClick={commitCsv} style={{ marginTop: 8 }}>
              Import {importPreview.valid_count} valid row{importPreview.valid_count === 1 ? '' : 's'}
            </button>
          </div>
        )}
      </div>

      {groups.map(g => (
        <div key={g.account_id} className="card" style={{ marginBottom: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
            <div>
              <strong>{g.account_name}</strong>
              <span style={{ color: 'var(--muted)', fontSize: 12, marginLeft: 8 }}>{g.portfolio_account_type || g.account_type}</span>
              {g.allocation_blocked && <span style={{ color: 'var(--red)', fontSize: 12, marginLeft: 8 }}>⚠ blocked — unresolved account type</span>}
            </div>
            {g.has_warning && (
              <span style={{ color: 'var(--amber)', fontSize: 12 }}>⚠ {g.warning}</span>
            )}
          </div>
          {g.holdings.length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13 }}>No holdings entered for this account.</div>}
          {g.holdings.map(h => (
            <div key={h.id} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '5px 0', borderTop: '1px solid var(--border)' }}>
              <span>
                {h.security_name} {h.ticker ? `(${h.ticker})` : ''} —{' '}
                {h.exposures?.length > 0
                  ? h.exposures.map(x => `${x.weight_pct}% ${x.asset_class.replace(/_/g, ' ')}`).join(' / ')
                  : h.asset_class.replace(/_/g, ' ')}
              </span>
              <span>
                {fmt(h.market_value)}
                <button className="btn-secondary" style={{ marginLeft: 10, padding: '2px 8px', fontSize: 11 }} onClick={() => remove(h.id)}>Remove</button>
              </span>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function PolicyTab({ accounts }) {
  const [policy, setPolicy] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  const load = () => axios.get('/api/investment-policy').then(r => {
    setPolicy(r.data.has_policy ? r.data.policy : {
      name: 'Household Policy', target_us_large_cap_pct: 0, target_us_mid_cap_pct: 0, target_us_small_cap_pct: 0,
      target_international_developed_pct: 0, target_emerging_markets_pct: 0, target_us_bonds_pct: 0,
      target_international_bonds_pct: 0, target_cash_pct: 0, target_real_estate_pct: 0, target_alternatives_pct: 0,
      drift_band_pct: 5, minimum_cash_reserve: 0, rebalance_cadence: 'annual', use_contributions_before_sales: true,
      excluded_accounts: [], excluded_holdings: [], employer_stock_exceptions: [], legacy_holding_exceptions: [], account_constraints: [],
    })
  }).finally(() => setLoading(false))
  useEffect(() => { load() }, [])

  if (loading || !policy) return <div className="loading">Loading policy...</div>

  const total = POLICY_TARGET_FIELDS.reduce((sum, [field]) => sum + (parseFloat(policy[field]) || 0), 0)
  const hasNegative = POLICY_TARGET_FIELDS.some(([field]) => (parseFloat(policy[field]) || 0) < 0)
  // Same 0.5-point tolerance the backend's InvestmentPolicy validator
  // enforces (external review finding #6) -- keep these in sync.
  const totalValid = Math.abs(total - 100) <= 0.5
  const canSave = totalValid && !hasNegative

  const save = async () => {
    if (!canSave) return
    setSaving(true)
    setSaveError(null)
    try {
      await axios.post('/api/investment-policy', policy)
      await load()
    } catch (e) {
      setSaveError(e.response?.data?.detail ? JSON.stringify(e.response.data.detail) : 'Could not save policy.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="card">
      <div className="label" style={{ marginBottom: 12 }}>Target allocation by asset class</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10 }}>
        {POLICY_TARGET_FIELDS.map(([field, label]) => (
          <div key={field}>
            <label style={{ fontSize: 12, color: 'var(--muted)' }}>{label}</label>
            <input className="input" type="number" value={policy[field] ?? 0}
                   onChange={e => setPolicy(p => ({ ...p, [field]: parseFloat(e.target.value) || 0 }))} />
          </div>
        ))}
      </div>
      <div style={{ marginTop: 10, fontSize: 13, color: totalValid ? 'var(--green)' : 'var(--red)' }}>
        Total: {total.toFixed(1)}% {!totalValid && '— targets must sum to 100% (±0.5) before saving'}
      </div>
      {hasNegative && (
        <div style={{ fontSize: 13, color: 'var(--red)' }}>Target percentages cannot be negative.</div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10, marginTop: 16 }}>
        <div>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>Drift band (±%)</label>
          <input className="input" type="number" value={policy.drift_band_pct ?? 5}
                 onChange={e => setPolicy(p => ({ ...p, drift_band_pct: parseFloat(e.target.value) || 0 }))} />
        </div>
        <div>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>Minimum cash reserve ($)</label>
          <input className="input" type="number" value={policy.minimum_cash_reserve ?? 0}
                 onChange={e => setPolicy(p => ({ ...p, minimum_cash_reserve: parseFloat(e.target.value) || 0 }))} />
        </div>
        <div>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>Maximum single security (%)</label>
          <input className="input" type="number" min="0" max="100" value={policy.max_single_security_pct ?? ''}
                 onChange={e => setPolicy(p => ({ ...p, max_single_security_pct: e.target.value === '' ? null : parseFloat(e.target.value) }))} />
        </div>
        <div>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>Rebalance cadence</label>
          <select className="input" value={policy.rebalance_cadence || 'annual'}
                  onChange={e => setPolicy(p => ({ ...p, rebalance_cadence: e.target.value }))}>
            <option value="annual">Annual</option>
            <option value="semiannual">Semiannual</option>
            <option value="quarterly">Quarterly</option>
          </select>
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6 }}>
          <input type="checkbox" checked={!!policy.use_contributions_before_sales}
                 onChange={e => setPolicy(p => ({ ...p, use_contributions_before_sales: e.target.checked }))} />
          <label style={{ fontSize: 12 }}>Prefer contributions/exchanges before any taxable sale</label>
        </div>
      </div>
      <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
        <div className="label" style={{ marginBottom: 4 }}>Accounts excluded from investing advice</div>
        <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
          Use this for checking, bill-pay cash, or any account the Coach should leave alone. The account still counts in net worth and cash planning.
        </div>
        <div style={{ display: 'grid', gap: 7 }}>
          {accounts.map(account => {
            const excluded = (policy.excluded_accounts || []).includes(account.id)
            return (
              <label key={account.id} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13 }}>
                <input aria-label={`Exclude ${account.name} from investing advice`} type="checkbox" checked={excluded}
                       onChange={e => setPolicy(p => ({ ...p, excluded_accounts: e.target.checked
                         ? [...new Set([...(p.excluded_accounts || []), account.id])]
                         : (p.excluded_accounts || []).filter(id => id !== account.id) }))} />
                {account.name} <span style={{ color: 'var(--muted)' }}>({account.account_type || 'account'}{account.balance != null ? ` · ${fmt(account.balance)}` : ''})</span>
              </label>
            )
          })}
          {!accounts.length && <div style={{ fontSize: 12, color: 'var(--muted)' }}>No accounts entered yet.</div>}
        </div>
      </div>
      <PolicyRestrictions accounts={accounts} policy={policy} onChange={setPolicy} />
      {saveError && <div style={{ fontSize: 13, color: 'var(--red)', marginTop: 8 }}>{saveError}</div>}
      <button className="btn-primary" style={{ marginTop: 16 }} disabled={saving || !canSave} onClick={save}>Save policy</button>
    </div>
  )
}

function OptionsTab({ accounts }) {
  const [accountId, setAccountId] = useState('')
  const [options, setOptions] = useState([])
  const emptyOption = { option_name: '', ticker: '', asset_class: 'us_large_cap', expense_ratio: '', minimum_investment: '',
    minimum_allocation_pct: '', maximum_allocation_pct: '', employer_match_eligible: '', trading_fee: '',
    redemption_restriction: '', settlement_restriction: '', available_for_new_contributions: true, available_for_exchange: true }
  const [form, setForm] = useState(emptyOption)
  const [mixResult, setMixResult] = useState(null)
  const [mixError, setMixError] = useState(null)

  const load = id => {
    if (!id) { setOptions([]); return }
    axios.get('/api/account-investment-options', { params: { account_id: id } }).then(r => setOptions(r.data))
  }
  useEffect(() => { load(accountId); setMixResult(null); setMixError(null) }, [accountId])

  const runCompare = () => {
    setMixError(null)
    axios.post('/api/account-investment-options/compare', { account_id: parseInt(accountId, 10) })
      .then(r => setMixResult(r.data))
      .catch(e => setMixError(e.response?.data?.detail || 'Could not compare — check that a household investment policy is saved.'))
  }

  const submit = async e => {
    e.preventDefault()
    if (!accountId || !form.option_name) return
    const numeric = key => form[key] === '' ? null : parseFloat(form[key])
    await axios.post('/api/account-investment-options', { account_id: parseInt(accountId, 10), ...form, ticker: form.ticker || null,
      expense_ratio: form.expense_ratio === '' ? null : parseFloat(form.expense_ratio) / 100,
      minimum_investment: numeric('minimum_investment'), minimum_allocation_pct: numeric('minimum_allocation_pct'),
      maximum_allocation_pct: numeric('maximum_allocation_pct'), trading_fee: numeric('trading_fee'),
      employer_match_eligible: form.employer_match_eligible === '' ? null : form.employer_match_eligible === 'yes' })
    setForm(emptyOption)
    load(accountId)
  }
  const remove = async id => { await axios.delete(`/api/account-investment-options/${id}`); load(accountId) }

  return (
    <div>
      <select className="input" value={accountId} onChange={e => setAccountId(e.target.value)} style={{ marginBottom: 16, maxWidth: 260 }}>
        <option value="">Select an account…</option>
        {accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
      </select>

      {accountId && (
        <>
          <form onSubmit={submit} className="card" style={{ marginBottom: 16, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <input className="input" placeholder="Option name" value={form.option_name} onChange={e => setForm(f => ({ ...f, option_name: e.target.value }))} style={{ minWidth: 200 }} />
            <input className="input" placeholder="Ticker (optional)" value={form.ticker} onChange={e => setForm(f => ({ ...f, ticker: e.target.value }))} style={{ maxWidth: 100 }} />
            <select className="input" value={form.asset_class} onChange={e => setForm(f => ({ ...f, asset_class: e.target.value }))} style={{ minWidth: 160 }}>
              {ASSET_CLASSES.map(c => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)}
            </select>
            <input className="input" type="number" placeholder="Expense ratio %" value={form.expense_ratio} onChange={e => setForm(f => ({ ...f, expense_ratio: e.target.value }))} style={{ maxWidth: 130 }} />
            <input className="input" type="number" placeholder="Minimum $" value={form.minimum_investment} onChange={e => setForm(f => ({ ...f, minimum_investment: e.target.value }))} style={{ maxWidth: 120 }} />
            <input className="input" type="number" placeholder="Min allocation %" value={form.minimum_allocation_pct} onChange={e => setForm(f => ({ ...f, minimum_allocation_pct: e.target.value }))} style={{ maxWidth: 140 }} />
            <input className="input" type="number" placeholder="Max allocation %" value={form.maximum_allocation_pct} onChange={e => setForm(f => ({ ...f, maximum_allocation_pct: e.target.value }))} style={{ maxWidth: 140 }} />
            <input className="input" type="number" placeholder="Trading fee $" value={form.trading_fee} onChange={e => setForm(f => ({ ...f, trading_fee: e.target.value }))} style={{ maxWidth: 120 }} />
            <select className="input" aria-label="Employer match eligibility" value={form.employer_match_eligible} onChange={e => setForm(f => ({ ...f, employer_match_eligible: e.target.value }))}>
              <option value="">Match eligibility unknown</option><option value="yes">Match eligible</option><option value="no">Not match eligible</option>
            </select>
            <input className="input" placeholder="Redemption restriction" value={form.redemption_restriction} onChange={e => setForm(f => ({ ...f, redemption_restriction: e.target.value }))} />
            <input className="input" placeholder="Settlement restriction" value={form.settlement_restriction} onChange={e => setForm(f => ({ ...f, settlement_restriction: e.target.value }))} />
            <label style={{ fontSize: 12, display: 'flex', gap: 4, alignItems: 'center' }}>
              <input type="checkbox" checked={form.available_for_new_contributions} onChange={e => setForm(f => ({ ...f, available_for_new_contributions: e.target.checked }))} />
              New contributions
            </label>
            <label style={{ fontSize: 12, display: 'flex', gap: 4, alignItems: 'center' }}>
              <input type="checkbox" checked={form.available_for_exchange} onChange={e => setForm(f => ({ ...f, available_for_exchange: e.target.checked }))} />
              Exchange
            </label>
            <button className="btn-primary" type="submit">Add option</button>
          </form>

          <div className="card" style={{ marginBottom: 16 }}>
            {options.length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13 }}>No investment options recorded for this account yet — a closed-menu account (401(k), Roth 401(k), HSA, 529, trust) can only be recommended options recorded here.</div>}
            {options.map(o => (
              <div key={o.id} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '6px 0', borderTop: '1px solid var(--border)' }}>
                <span>
                  {o.option_name} {o.ticker ? `(${o.ticker})` : '(no ticker)'} — {o.asset_class.replace(/_/g, ' ')}
                  {!o.available_for_new_contributions && <span style={{ color: 'var(--muted)' }}> · closed to new money</span>}
                  {o.expense_ratio != null && <span> · {(o.expense_ratio * 100).toFixed(2)}% fee</span>}
                  {o.minimum_investment != null && <span> · {fmt(o.minimum_investment)} minimum</span>}
                  {o.maximum_allocation_pct != null && <span> · max {o.maximum_allocation_pct}%</span>}
                  {o.trading_fee != null && <span> · {fmt(o.trading_fee)} trade fee</span>}
                  {o.employer_match_eligible === true && <span> · match eligible</span>}
                  {o.redemption_restriction && <div style={{ color: 'var(--amber)' }}>Redemption: {o.redemption_restriction}</div>}
                  {o.settlement_restriction && <div style={{ color: 'var(--amber)' }}>Settlement: {o.settlement_restriction}</div>}
                </span>
                <button className="btn-secondary" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => remove(o.id)}>Remove</button>
              </div>
            ))}
          </div>

          <div className="card">
            <div className="label" style={{ marginBottom: 10 }}>Which mix best implements my household policy?</div>
            <button className="btn-primary" onClick={runCompare} disabled={options.length === 0}>Compare eligible options</button>
            {mixError && <div style={{ color: 'var(--amber)', fontSize: 13, marginTop: 10 }}>{mixError}</div>}
            {mixResult && (
              <div style={{ marginTop: 14 }}>
                {mixResult.is_closed_menu_account && (
                  <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8 }}>
                    Closed-menu account — only options recorded above were considered.
                  </div>
                )}
                {mixResult.mix.length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13 }}>No eligible option covers any target asset class in this account.</div>}
                {mixResult.mix.map((m, i) => (
                  <div key={i} style={{ fontSize: 13, padding: '6px 0', borderTop: i ? '1px solid var(--border)' : 'none' }}>
                    <strong>{m.pct}%</strong> — {m.option_name} {m.ticker ? `(${m.ticker})` : ''}
                    {m.expense_ratio != null && <span style={{ color: 'var(--muted)' }}> · {(m.expense_ratio * 100).toFixed(2)}% expense ratio</span>}
                    <div style={{ color: 'var(--muted)', fontSize: 12 }}>{m.reason}</div>
                  </div>
                ))}
                {mixResult.unavailable_classes?.length > 0 && (
                  <div style={{ fontSize: 12, color: 'var(--amber)', marginTop: 10 }}>
                    Not covered by any option in this account: {mixResult.unavailable_classes.map(c => c.replace(/_/g, ' ')).join(', ')} — consider whether another account should hold this exposure.
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

export default function PortfolioSetup() {
  const [tab, setTab] = useState('holdings')
  const [accounts, setAccounts] = useState([])

  useEffect(() => { axios.get('/api/accounts').then(r => setAccounts(r.data)) }, [])

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 className="section-title">Portfolio Setup</h1>
        <p className="section-sub">Holdings, investment policy, and account investment options — the facts Portfolio Coach reads from.</p>
      </div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        {TABS.map(t => (
          <button key={t.id} className={tab === t.id ? 'btn-primary' : 'btn-secondary'} onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
      </div>
      {tab === 'holdings' && <HoldingsTab accounts={accounts} />}
      {tab === 'policy' && <PolicyTab accounts={accounts} />}
      {tab === 'options' && <OptionsTab accounts={accounts} />}
    </div>
  )
}
