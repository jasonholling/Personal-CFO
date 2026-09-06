import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt = value => isPrivacyMode() ? MASK_CURRENCY : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(value || 0)
const EMPTY = { name:'', cash_flow_type:'expense', category:'Housing', amount:'', essential:true, notes:'' }
const EXPENSE_CATEGORIES = ['Housing', 'Utilities', 'Food', 'Transportation', 'Insurance', 'Healthcare', 'Debt payments', 'Childcare & education', 'Giving', 'Lifestyle', 'Other']

export default function CashFlow({ onNavigate }) {
  const [items, setItems] = useState([])
  const [summary, setSummary] = useState(null)
  const [form, setForm] = useState(null)
  const [editing, setEditing] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => axios.get('/api/cash-flow').then(response => {
    setItems(response.data.items); setSummary(response.data.summary); setLoading(false)
  }).catch(() => { setError('Could not load your monthly plan.'); setLoading(false) })
  useEffect(() => { load() }, [])

  const save = async () => {
    if (!form.name.trim() || Number(form.amount) < 0 || form.amount === '') { setError('Enter a name and a monthly amount.'); return }
    const payload = { ...form, name:form.name.trim(), amount:Number(form.amount), essential:form.cash_flow_type === 'expense' && form.essential }
    try {
      if (editing) await axios.put(`/api/cash-flow/${editing}`, payload)
      else await axios.post('/api/cash-flow', payload)
      setForm(null); setEditing(null); setError(''); await load()
    } catch (e) { setError(e.response?.data?.detail || 'Could not save this item.') }
  }
  const remove = async id => { if (confirm('Remove this monthly item?')) { await axios.delete(`/api/cash-flow/${id}`); await load() } }
  const edit = item => { setForm({ ...item, amount:String(item.amount), essential:!!item.essential, notes:item.notes || '' }); setEditing(item.id); setError('') }
  const incomes = useMemo(() => items.filter(item => item.cash_flow_type === 'income'), [items])
  const expenses = useMemo(() => items.filter(item => item.cash_flow_type === 'expense'), [items])

  if (loading) return <div className="loading">Loading cash-flow plan...</div>
  const statusColor = summary?.status === 'surplus' ? 'var(--green)' : summary?.status === 'shortfall' ? 'var(--red)' : 'var(--amber)'
  return <div>
    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', flexWrap:'wrap', gap:16, marginBottom:28 }}>
      <div><h1 className="section-title">Monthly Cash Flow</h1><p className="section-sub">Give every recurring dollar a job before directing money to long-term goals.</p></div>
      <button className="btn-primary" onClick={() => { setForm({ ...EMPTY }); setEditing(null); setError('') }}>+ Add monthly item</button>
    </div>

    <div className="grid-4" style={{ marginBottom:24 }}>
      <div className="card"><div className="label">Take-home income</div><div className="number-lg" style={{ marginTop:8, color:'var(--green)' }}>{fmt(summary?.monthly_income)}</div><div style={{ color:'var(--text3)', fontSize:12, marginTop:3 }}>/ month</div></div>
      <div className="card"><div className="label">Monthly spending</div><div className="number-lg" style={{ marginTop:8 }}>{fmt(summary?.monthly_expenses)}</div><div style={{ color:'var(--text3)', fontSize:12, marginTop:3 }}>/ month</div></div>
      <div className="card"><div className="label">Essential spending</div><div className="number-lg" style={{ marginTop:8 }}>{fmt(summary?.monthly_essential_expenses)}</div><div style={{ color:'var(--text3)', fontSize:12, marginTop:3 }}>needs & commitments</div></div>
      <div className="card"><div className="label">Available to assign</div><div className="number-lg" style={{ marginTop:8, color:statusColor }}>{fmt(summary?.monthly_surplus)}</div><div style={{ color:'var(--text3)', fontSize:12, marginTop:3 }}>{fmt(summary?.annual_surplus)} / year</div></div>
    </div>

    <div className="card" style={{ marginBottom:24, borderLeft:`3px solid ${statusColor}` }}>
      <div style={{ fontWeight:650, marginBottom:5 }}>{summary?.status === 'surplus' ? 'Monthly surplus ready to assign' : summary?.status === 'shortfall' ? 'Monthly shortfall needs attention' : 'Cash-flow plan needs one more step'}</div>
      <div style={{ color:'var(--text2)', fontSize:13, lineHeight:1.5 }}>{summary?.guidance}</div>
      {summary?.status === 'surplus' && <button className="btn-secondary" onClick={() => onNavigate('annualplan')} style={{ marginTop:12 }}>Review annual priorities →</button>}
    </div>

    {form && <div className="card" style={{ marginBottom:24, background:'var(--bg2)' }}>
      <div style={{ fontWeight:650, marginBottom:16 }}>{editing ? 'Edit monthly item' : 'Add monthly item'}</div>
      <div className="grid-3" style={{ marginBottom:12 }}>
        <div><div className="label" style={{ marginBottom:6 }}>Name</div><input autoFocus value={form.name} onChange={e => setForm({ ...form, name:e.target.value })} placeholder="e.g. Net paycheck" /></div>
        <div><div className="label" style={{ marginBottom:6 }}>Type</div><select value={form.cash_flow_type} onChange={e => setForm({ ...form, cash_flow_type:e.target.value, essential:e.target.value === 'expense' ? form.essential : false })}><option value="income">Income</option><option value="expense">Expense</option></select></div>
        <div><div className="label" style={{ marginBottom:6 }}>Monthly amount</div><input type="number" min="0" value={form.amount} onChange={e => setForm({ ...form, amount:e.target.value })} placeholder="0" /></div>
        <div><div className="label" style={{ marginBottom:6 }}>Category</div>{form.cash_flow_type === 'expense' ? <select value={form.category} onChange={e => setForm({ ...form, category:e.target.value })}>{EXPENSE_CATEGORIES.map(category => <option key={category}>{category}</option>)}</select> : <input value={form.category} onChange={e => setForm({ ...form, category:e.target.value })} placeholder="Salary, rental, other" />}</div>
        <div style={{ display:'flex', alignItems:'end', paddingBottom:8 }}>{form.cash_flow_type === 'expense' && <label style={{ display:'flex', gap:8, alignItems:'center', fontSize:13, cursor:'pointer' }}><input type="checkbox" checked={form.essential} onChange={e => setForm({ ...form, essential:e.target.checked })} /> Essential or committed</label>}</div>
        <div><div className="label" style={{ marginBottom:6 }}>Note (optional)</div><input value={form.notes} onChange={e => setForm({ ...form, notes:e.target.value })} placeholder="What this covers" /></div>
      </div>
      {error && <div style={{ color:'var(--red)', fontSize:13, marginBottom:10 }}>{error}</div>}
      <div style={{ display:'flex', gap:8 }}><button className="btn-primary" onClick={save}>Save item</button><button className="btn-secondary" onClick={() => { setForm(null); setEditing(null); setError('') }}>Cancel</button></div>
    </div>}

    {!summary?.has_data && !form ? <div className="card" style={{ textAlign:'center', padding:'44px 24px' }}><div style={{ fontSize:28, marginBottom:8 }}>⊞</div><div style={{ fontWeight:650, marginBottom:6 }}>Start with recurring take-home pay and core bills</div><div style={{ color:'var(--text2)', fontSize:13 }}>Use monthly, after-tax amounts. Keep one-time purchases and account balances out of this view.</div></div> : <div className="grid-2">
      <CashFlowList title="Income" items={incomes} color="var(--green)" onEdit={edit} onRemove={remove} />
      <CashFlowList title="Expenses" items={expenses} color="var(--text)" onEdit={edit} onRemove={remove} />
    </div>}
  </div>
}

function CashFlowList({ title, items, color, onEdit, onRemove }) {
  return <div className="card"><div style={{ display:'flex', justifyContent:'space-between', marginBottom:12 }}><div style={{ fontWeight:650 }}>{title}</div><div style={{ color:'var(--text3)', fontSize:12 }}>{items.length} items</div></div>{items.length ? items.map(item => <div key={item.id} style={{ display:'flex', gap:10, alignItems:'center', padding:'11px 0', borderTop:'1px solid var(--border)' }}><div style={{ flex:1 }}><div style={{ fontSize:13, fontWeight:550 }}>{item.name}</div><div style={{ color:'var(--text3)', fontSize:12, marginTop:2 }}>{item.category}{item.essential ? ' · essential' : ''}</div></div><div style={{ fontWeight:650, color }}>{fmt(item.amount)}</div><button onClick={() => onEdit(item)} title="Edit" style={{ background:'transparent', border:'none', color:'var(--accent)', cursor:'pointer' }}>Edit</button><button onClick={() => onRemove(item.id)} title="Remove" style={{ background:'transparent', border:'none', color:'var(--text3)', cursor:'pointer' }}>×</button></div>) : <div style={{ color:'var(--text3)', fontSize:13, padding:'16px 0' }}>No monthly {title.toLowerCase()} entered.</div>}</div>
}
