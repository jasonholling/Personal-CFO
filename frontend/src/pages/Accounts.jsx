import TaskPanel from '../components/TaskPanel'
import { useState, useEffect } from 'react'
import axios from 'axios'
import QuickenImport from '../components/QuickenImport'
import { usePersonNames } from '../hooks/usePersonNames'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt = (n) => isPrivacyMode() ? MASK_CURRENCY : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n)

const ACCOUNT_TYPES = [
  { value: 'checking', label: 'Checking' },
  { value: 'savings', label: 'Savings' },
  { value: 'taxable', label: 'Taxable Brokerage' },
  { value: 'roth_ira', label: 'Roth IRA' },
  { value: 'ira', label: 'Traditional IRA' },
  { value: '401k', label: '401(k)' },
  { value: 'hsa', label: 'HSA' },
  { value: '529', label: '529 Education' },
  { value: 'custodial', label: 'Custodial (UGMA/UTMA)' },
  { value: 'real_estate', label: 'Real Estate (Equity)' },
  { value: 'business', label: 'Business Asset' },
  { value: 'insurance', label: 'Life Insurance CSV' },
  { value: 'mortgage', label: 'Mortgage (Liability)' },
  { value: 'credit_card', label: 'Credit Card (Liability)' },
  { value: 'student_loan', label: 'Student Loan (Liability)' },
  { value: 'car_loan', label: 'Car Loan (Liability)' },
  { value: 'personal_loan', label: 'Personal Loan (Liability)' },
  { value: 'other', label: 'Other' },
]

// account_type values that the Debt Payoff page's avalanche/snowball
// planner picks up — keep in sync with backend/debt_engine.py DEBT_TYPES.
const DEBT_TYPES = new Set(['mortgage', 'credit_card', 'student_loan', 'car_loan', 'personal_loan'])

// account_type values that Allocation & Fees (backend/allocation_engine.py
// INVESTMENT_TYPES) reads a stock/bond split and expense ratio from.
const INVESTMENT_TYPES = new Set(['401k', 'ira', 'roth_ira', 'taxable', 'hsa', 'custodial'])

// account.owner stays a fixed internal key (jason/justin/abby/cooper) —
// only the displayed label is personalized, via Settings.
const ownerOptions = (names) => [
  { value: 'jason', label: names.person1Name },
  { value: 'justin', label: names.person2Name },
  { value: 'joint', label: 'Joint' },
  { value: 'abby', label: names.kid1Name },
  { value: 'cooper', label: names.kid2Name },
  { value: 'trust', label: 'Trust' },
]

const TYPE_GROUPS = {
  'Cash': ['checking', 'savings'],
  'Investments': ['taxable', 'roth_ira', 'ira', '401k', 'hsa', 'custodial'],
  'Education': ['529'],
  'Real Estate & Business': ['real_estate', 'business'],
  'Other': ['insurance', 'other'],
  'Liabilities': ['mortgage', 'credit_card', 'student_loan', 'car_loan', 'personal_loan'],
}

const EMPTY = { name: '', account_type: 'taxable', owner: 'jason', institution: '', balance: '', notes: '', interest_rate: '', minimum_payment: '', term_months: '', stock_allocation_pct: '', expense_ratio: '', monthly_rental_income: '', monthly_rental_expenses: '' }

export default function Accounts() {
  const personNames = usePersonNames()
  const OWNERS = ownerOptions(personNames)
  const [accounts, setAccounts] = useState([])
  const [netWorth, setNetWorth] = useState(null)
  const [freshness, setFreshness] = useState(null)
  const [form, setForm] = useState(EMPTY)
  const [editing, setEditing] = useState(null)
  const [showForm, setShowForm] = useState(false)

  // Total Assets/Liabilities/Net Worth tiles below come from the same
  // /api/net-worth endpoint Dashboard.jsx and NetWorth.jsx use, rather than
  // a local sum of every account here — a local sum would include kids'
  // custodial/Roth/529 balances that /api/net-worth deliberately excludes,
  // so this page's tiles would otherwise disagree with those two pages for
  // the exact same accounts.
  const load = () => Promise.all([
    axios.get('/api/accounts').then(r => setAccounts(r.data)),
    axios.get('/api/net-worth').then(r => setNetWorth(r.data)),
    axios.get('/api/accounts/freshness').then(r => setFreshness(r.data)),
  ])
  useEffect(() => { load() }, [])

  const save = async () => {
    const data = {
      ...form,
      balance: parseFloat(form.balance) || 0,
      interest_rate: parseFloat(form.interest_rate) / 100 || 0,
      minimum_payment: parseFloat(form.minimum_payment) || 0,
      term_months: parseInt(form.term_months) || 0,
      stock_allocation_pct: form.stock_allocation_pct === '' ? null : parseFloat(form.stock_allocation_pct),
      expense_ratio: (parseFloat(form.expense_ratio) || 0) / 100,
      monthly_rental_income: parseFloat(form.monthly_rental_income) || 0,
      monthly_rental_expenses: parseFloat(form.monthly_rental_expenses) || 0,
    }
    if (editing !== null) {
      await axios.put(`/api/accounts/${editing}`, data)
    } else {
      await axios.post('/api/accounts', data)
    }
    setForm(EMPTY)
    setEditing(null)
    setShowForm(false)
    load()
  }

  const del = async (id) => {
    if (!confirm('Delete this account?')) return
    await axios.delete(`/api/accounts/${id}`)
    load()
  }

  const startEdit = (acc) => {
    setForm({
      ...acc,
      balance: acc.balance.toString(),
      interest_rate: acc.interest_rate ? (acc.interest_rate * 100).toString() : '',
      minimum_payment: acc.minimum_payment ? acc.minimum_payment.toString() : '',
      term_months: acc.term_months ? acc.term_months.toString() : '',
      stock_allocation_pct: acc.stock_allocation_pct == null ? '' : acc.stock_allocation_pct.toString(),
      expense_ratio: acc.expense_ratio ? (acc.expense_ratio * 100).toString() : '',
      monthly_rental_income: acc.monthly_rental_income ? acc.monthly_rental_income.toString() : '',
      monthly_rental_expenses: acc.monthly_rental_expenses ? acc.monthly_rental_expenses.toString() : '',
    })
    setEditing(acc.id)
    setShowForm(true)
  }

  const grouped = Object.entries(TYPE_GROUPS).map(([group, types]) => ({
    group,
    items: accounts.filter(a => types.includes(a.account_type))
  })).filter(g => g.items.length > 0)

  const total = netWorth?.total_assets ?? 0
  const liabilities = netWorth?.liabilities ?? 0

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 32 }}>
        <div>
          <h1 className="section-title">Accounts</h1>
          <p className="section-sub">All accounts across your financial picture</p>
        </div>
        <button className="btn-primary" onClick={() => { setForm(EMPTY); setEditing(null); setShowForm(!showForm) }}>
          {showForm ? 'Cancel' : '+ Add Account'}
        </button>
      </div>

      {freshness?.stale_count > 0 && <div className="card" style={{ marginBottom:20, borderLeft:'3px solid var(--amber)' }}>
        <div style={{ fontWeight:650, marginBottom:4 }}>Balance refresh needed</div>
        <div style={{ color:'var(--text2)', fontSize:13 }}>{freshness.stale_count} account{freshness.stale_count === 1 ? '' : 's'} ha{freshness.stale_count === 1 ? 's' : 've'} not been updated in over 35 days. Refresh through Quicken import or edit the balance before relying on projections.</div>
      </div>}

      <QuickenImport onImportComplete={load} />

      {showForm && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div style={{ fontWeight: 500, marginBottom: 16 }}>{editing !== null ? 'Edit Account' : 'New Account'}</div>
          <div className="grid-3" style={{ marginBottom: 12 }}>
            <div>
              <div className="label" style={{ marginBottom: 6 }}>Account Name</div>
              <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder={`e.g. ${personNames.person1Name} Roth IRA`} />
            </div>
            <div>
              <div className="label" style={{ marginBottom: 6 }}>Type</div>
              <select value={form.account_type} onChange={e => setForm({ ...form, account_type: e.target.value })}>
                {ACCOUNT_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <div className="label" style={{ marginBottom: 6 }}>Owner</div>
              <select value={form.owner} onChange={e => setForm({ ...form, owner: e.target.value })}>
                {OWNERS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <div className="label" style={{ marginBottom: 6 }}>Institution</div>
              <input value={form.institution} onChange={e => setForm({ ...form, institution: e.target.value })} placeholder="e.g. Schwab" />
            </div>
            <div>
              <div className="label" style={{ marginBottom: 6 }}>Current Balance ($)</div>
              <input type="number" value={form.balance} onChange={e => setForm({ ...form, balance: e.target.value })} placeholder="0" />
            </div>
            <div>
              <div className="label" style={{ marginBottom: 6 }}>Notes (optional)</div>
              <input value={form.notes || ''} onChange={e => setForm({ ...form, notes: e.target.value })} placeholder="Any notes" />
            </div>
            {DEBT_TYPES.has(form.account_type) && (
              <>
                <div>
                  <div className="label" style={{ marginBottom: 6 }}>Interest Rate (APR %)</div>
                  <input type="number" step="0.01" value={form.interest_rate} onChange={e => setForm({ ...form, interest_rate: e.target.value })} placeholder="e.g. 18.99" />
                </div>
                <div>
                  <div className="label" style={{ marginBottom: 6 }}>Minimum Payment ($/mo)</div>
                  <input type="number" value={form.minimum_payment} onChange={e => setForm({ ...form, minimum_payment: e.target.value })} placeholder="e.g. 150" />
                </div>
                <div>
                  <div className="label" style={{ marginBottom: 6 }}>Remaining Term (months, optional)</div>
                  <input type="number" value={form.term_months} onChange={e => setForm({ ...form, term_months: e.target.value })} placeholder="e.g. 48" />
                </div>
              </>
            )}
            {INVESTMENT_TYPES.has(form.account_type) && (
              <>
                <div>
                  <div className="label" style={{ marginBottom: 6 }}>Stock Allocation (%, optional)</div>
                  <input type="number" step="1" min="0" max="100" value={form.stock_allocation_pct} onChange={e => setForm({ ...form, stock_allocation_pct: e.target.value })} placeholder="e.g. 80 — used for rebalancing" />
                </div>
                <div>
                  <div className="label" style={{ marginBottom: 6 }}>Expense Ratio (%, optional)</div>
                  <input type="number" step="0.01" value={form.expense_ratio} onChange={e => setForm({ ...form, expense_ratio: e.target.value })} placeholder="e.g. 0.04 — used for fee audit" />
                </div>
              </>
            )}
            {form.account_type === 'real_estate' && (
              <>
                <div>
                  <div className="label" style={{ marginBottom: 6 }}>Monthly Rental Income ($, if a rental)</div>
                  <input type="number" value={form.monthly_rental_income} onChange={e => setForm({ ...form, monthly_rental_income: e.target.value })} placeholder="e.g. 2000" />
                </div>
                <div>
                  <div className="label" style={{ marginBottom: 6 }}>Monthly Rental Expenses ($, if a rental)</div>
                  <input type="number" value={form.monthly_rental_expenses} onChange={e => setForm({ ...form, monthly_rental_expenses: e.target.value })} placeholder="e.g. 500 — taxes, insurance, maintenance" />
                </div>
              </>
            )}
          </div>
          <button className="btn-primary" onClick={save}>Save Account</button>
        </div>
      )}

      {accounts.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '48px 24px' }}>
          <div style={{ color: 'var(--text2)', fontSize: 13 }}>No accounts yet. Add your first account above.</div>
        </div>
      ) : (
        <>
          <div className="grid-3" style={{ marginBottom: 24 }}>
            <div className="card">
              <div className="label">Total Assets</div>
              <div className="number-md" style={{ marginTop: 6 }}>{fmt(total)}</div>
            </div>
            <div className="card">
              <div className="label">Liabilities</div>
              <div className="number-md" style={{ marginTop: 6, color: 'var(--red)' }}>{fmt(liabilities)}</div>
            </div>
            <div className="card">
              <div className="label">Net Worth</div>
              <div className="number-md" style={{ marginTop: 6, color: 'var(--green)' }}>{fmt(total - liabilities)}</div>
            </div>
          </div>

          {grouped.map(({ group, items }) => (
            <div key={group} style={{ marginBottom: 24 }}>
              <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text3)', marginBottom: 8 }}>{group}</div>
              <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      {['Account', 'Type', 'Owner', 'Institution', 'Balance', ''].map(h => (
                        <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontSize: 11, fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text3)' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((acc, i) => (
                      <tr key={acc.id} style={{ borderBottom: i < items.length - 1 ? '1px solid var(--border)' : 'none' }}>
                        <td style={{ padding: '12px 16px', fontWeight: 500 }}>{acc.name}</td>
                        <td style={{ padding: '12px 16px', color: 'var(--text2)', fontSize: 13 }}>
                          {ACCOUNT_TYPES.find(t => t.value === acc.account_type)?.label || acc.account_type}
                        </td>
                        <td style={{ padding: '12px 16px' }}>
                          <span className="tag tag-blue">{OWNERS.find(o => o.value === acc.owner)?.label || acc.owner}</span>
                        </td>
                        <td style={{ padding: '12px 16px', color: 'var(--text2)', fontSize: 13 }}>{acc.institution}</td>
                        <td style={{ padding: '12px 16px', fontWeight: 600, color: DEBT_TYPES.has(acc.account_type) ? 'var(--red)' : 'var(--text)' }}>
                          {fmt(acc.balance)}
                        </td>
                        <td style={{ padding: '12px 16px' }}>
                          <div style={{ display: 'flex', gap: 4 }}>
                            <button className="btn-secondary" style={{ padding: '4px 10px', fontSize: 12 }} onClick={() => startEdit(acc)}>Edit</button>
                            <button className="btn-danger" onClick={() => del(acc.id)}>✕</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </>
      )}

      <TaskPanel section="investments" />
    </div>
  )
}
