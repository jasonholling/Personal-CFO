import { useEffect, useState } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

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

function HoldingsTab({ accounts }) {
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(true)
  const [form, setForm] = useState({ account_id: '', security_name: '', ticker: '', market_value: '', asset_class: 'us_large_cap', expense_ratio: '', cost_basis: '' })
  const [saving, setSaving] = useState(false)

  const load = () => axios.get('/api/holdings/grouped').then(r => setGroups(r.data.groups)).finally(() => setLoading(false))
  useEffect(() => { load() }, [])

  const submit = async e => {
    e.preventDefault()
    if (!form.account_id || !form.security_name || !form.market_value) return
    setSaving(true)
    try {
      await axios.post('/api/holdings', {
        account_id: parseInt(form.account_id, 10), security_name: form.security_name, ticker: form.ticker || null,
        market_value: parseFloat(form.market_value), asset_class: form.asset_class,
        expense_ratio: form.expense_ratio ? parseFloat(form.expense_ratio) / 100 : null,
        cost_basis: form.cost_basis ? parseFloat(form.cost_basis) : null,
      })
      setForm({ account_id: '', security_name: '', ticker: '', market_value: '', asset_class: 'us_large_cap', expense_ratio: '', cost_basis: '' })
      await load()
    } finally { setSaving(false) }
  }

  const remove = async id => { await axios.delete(`/api/holdings/${id}`); await load() }

  if (loading) return <div className="loading">Loading holdings...</div>

  return (
    <div>
      <form onSubmit={submit} className="card" style={{ marginBottom: 20, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <select className="input" value={form.account_id} onChange={e => setForm(f => ({ ...f, account_id: e.target.value }))} style={{ minWidth: 160 }}>
          <option value="">Account…</option>
          {accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <input className="input" placeholder="Security name" value={form.security_name} onChange={e => setForm(f => ({ ...f, security_name: e.target.value }))} style={{ minWidth: 160 }} />
        <input className="input" placeholder="Ticker (optional)" value={form.ticker} onChange={e => setForm(f => ({ ...f, ticker: e.target.value }))} style={{ maxWidth: 100 }} />
        <input className="input" type="number" placeholder="Market value" value={form.market_value} onChange={e => setForm(f => ({ ...f, market_value: e.target.value }))} style={{ maxWidth: 140 }} />
        <select className="input" value={form.asset_class} onChange={e => setForm(f => ({ ...f, asset_class: e.target.value }))} style={{ minWidth: 160 }}>
          {ASSET_CLASSES.map(c => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)}
        </select>
        <input className="input" type="number" placeholder="Expense ratio %" value={form.expense_ratio} onChange={e => setForm(f => ({ ...f, expense_ratio: e.target.value }))} style={{ maxWidth: 130 }} />
        <input className="input" type="number" placeholder="Cost basis" value={form.cost_basis} onChange={e => setForm(f => ({ ...f, cost_basis: e.target.value }))} style={{ maxWidth: 130 }} />
        <button className="btn-primary" disabled={saving} type="submit">Add holding</button>
      </form>

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
              <span>{h.security_name} {h.ticker ? `(${h.ticker})` : ''} — {h.asset_class.replace(/_/g, ' ')}</span>
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

function PolicyTab() {
  const [policy, setPolicy] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const load = () => axios.get('/api/investment-policy').then(r => {
    setPolicy(r.data.has_policy ? r.data.policy : {
      name: 'Household Policy', target_us_large_cap_pct: 0, target_us_mid_cap_pct: 0, target_us_small_cap_pct: 0,
      target_international_developed_pct: 0, target_emerging_markets_pct: 0, target_us_bonds_pct: 0,
      target_international_bonds_pct: 0, target_cash_pct: 0, target_real_estate_pct: 0, target_alternatives_pct: 0,
      drift_band_pct: 5, minimum_cash_reserve: 0, rebalance_cadence: 'annual', use_contributions_before_sales: true,
    })
  }).finally(() => setLoading(false))
  useEffect(() => { load() }, [])

  if (loading || !policy) return <div className="loading">Loading policy...</div>

  const total = POLICY_TARGET_FIELDS.reduce((sum, [field]) => sum + (parseFloat(policy[field]) || 0), 0)

  const save = async () => {
    setSaving(true)
    try { await axios.post('/api/investment-policy', policy); await load() } finally { setSaving(false) }
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
      <div style={{ marginTop: 10, fontSize: 13, color: total === 100 ? 'var(--green)' : 'var(--amber)' }}>
        Total: {total}% {total !== 100 && '— targets should sum to 100%'}
      </div>
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
      <button className="btn-primary" style={{ marginTop: 16 }} disabled={saving} onClick={save}>Save policy</button>
    </div>
  )
}

function OptionsTab({ accounts }) {
  const [accountId, setAccountId] = useState('')
  const [options, setOptions] = useState([])
  const [form, setForm] = useState({ option_name: '', ticker: '', asset_class: 'us_large_cap', available_for_new_contributions: true, available_for_exchange: true })
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
    await axios.post('/api/account-investment-options', { account_id: parseInt(accountId, 10), ...form, ticker: form.ticker || null })
    setForm({ option_name: '', ticker: '', asset_class: 'us_large_cap', available_for_new_contributions: true, available_for_exchange: true })
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
      {tab === 'policy' && <PolicyTab />}
      {tab === 'options' && <OptionsTab accounts={accounts} />}
    </div>
  )
}
