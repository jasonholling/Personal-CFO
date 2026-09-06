import { useEffect, useState } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt = value => isPrivacyMode() ? MASK_CURRENCY : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(value || 0)
const TYPES = [
  ['career', 'Career change'], ['home', 'Home purchase or sale'], ['education', 'Education'], ['caregiving', 'Caregiving'], ['sabbatical', 'Sabbatical'], ['windfall', 'Windfall / inheritance'], ['other', 'Other'],
]
const empty = () => ({ name:'', event_type:'career', event_year:new Date().getFullYear()+1, one_time_cash_delta:'', monthly_cash_flow_delta:'', duration_months:0, notes:'' })

export default function LifeEvents() {
  const [data, setData] = useState(null)
  const [form, setForm] = useState(null)
  const [error, setError] = useState('')
  const load = () => axios.get('/api/life-events').then(r => setData(r.data)).catch(() => setError('Could not load life-event scenarios.'))
  useEffect(() => { load() }, [])
  const save = async () => {
    if (!form.name.trim()) { setError('Give this event a clear name.'); return }
    try {
      await axios.post('/api/life-events', { ...form, name:form.name.trim(), event_year:Number(form.event_year), one_time_cash_delta:Number(form.one_time_cash_delta || 0), monthly_cash_flow_delta:Number(form.monthly_cash_flow_delta || 0), duration_months:Number(form.duration_months || 0) })
      setForm(null); setError(''); load()
    } catch (e) { setError(e.response?.data?.detail || 'Could not save this event.') }
  }
  const remove = async id => { if (confirm('Remove this life-event scenario?')) { await axios.delete(`/api/life-events/${id}`); load() } }
  return <div>
    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:16, marginBottom:28 }}>
      <div><h1 className="section-title">Life-Event Planning</h1><p className="section-sub">Test major changes against the account-level plan before they become real.</p></div>
      <button className="btn-primary" onClick={() => { setForm(empty()); setError('') }}>+ Add life event</button>
    </div>
    <div className="card" style={{ marginBottom:20, borderLeft:'3px solid var(--accent)' }}>
      <div style={{ fontWeight:650, marginBottom:6 }}>Scenario overlay — it does not change your base plan</div>
      <div style={{ fontSize:13, color:'var(--text2)', lineHeight:1.5 }}>Enter the one-time cash change and recurring monthly effect at the account level. A positive number improves available capital; a negative number is a cost. No transactions, tax lots, or uploads are needed.</div>
      {data?.assumptions && <div style={{ marginTop:10, color:'var(--text3)', fontSize:12 }}>Impact is shown in projected retirement-year dollars using your {((data.assumptions.pre_retirement_return || 0)*100).toFixed(1)}% pre-retirement return through age {data.assumptions.retirement_age}.</div>}
    </div>
    {form && <div className="card" style={{ marginBottom:20 }}>
      <div style={{ fontWeight:650, marginBottom:16 }}>Add life event</div>
      <div className="grid-3" style={{ marginBottom:12 }}>
        <Field label="Event name"><input autoFocus value={form.name} onChange={e => setForm({...form,name:e.target.value})} placeholder="e.g. Move to a smaller home" /></Field>
        <Field label="Event type"><select value={form.event_type} onChange={e => setForm({...form,event_type:e.target.value})}>{TYPES.map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
        <Field label="Start year"><input type="number" value={form.event_year} onChange={e => setForm({...form,event_year:e.target.value})} /></Field>
        <Field label="One-time cash impact"><input type="number" value={form.one_time_cash_delta} onChange={e => setForm({...form,one_time_cash_delta:e.target.value})} placeholder="Positive = proceeds; negative = cost" /></Field>
        <Field label="Monthly cash-flow impact"><input type="number" value={form.monthly_cash_flow_delta} onChange={e => setForm({...form,monthly_cash_flow_delta:e.target.value})} placeholder="Positive = more cash flow" /></Field>
        <Field label="Duration (months)"><input type="number" min="0" value={form.duration_months} onChange={e => setForm({...form,duration_months:e.target.value})} /><div style={{fontSize:11,color:'var(--text3)',marginTop:4}}>0 = continues to retirement</div></Field>
        <Field label="Note (optional)"><input value={form.notes} onChange={e => setForm({...form,notes:e.target.value})} placeholder="What this estimate includes" /></Field>
      </div>
      {error && <div style={{ color:'var(--red)', fontSize:13, marginBottom:10 }}>{error}</div>}
      <button className="btn-primary" onClick={save}>Save scenario</button><button className="btn-secondary" onClick={() => setForm(null)} style={{marginLeft:8}}>Cancel</button>
    </div>}
    {!data ? <div className="loading">Loading life-event scenarios...</div> : data.events.length === 0 ? <div className="card" style={{ textAlign:'center', padding:'44px 24px' }}><div style={{fontWeight:650,marginBottom:6}}>No life-event scenarios yet</div><div style={{color:'var(--text2)',fontSize:13}}>Use this for a job change, home decision, sabbatical, caregiving period, or windfall—not day-to-day spending.</div></div> : <div className="grid-2">{data.events.map(event => <div className="card" key={event.id}><div style={{display:'flex',justifyContent:'space-between',gap:12}}><div><div style={{fontWeight:650}}>{event.name}</div><div style={{fontSize:12,color:'var(--text3)',marginTop:3}}>{TYPES.find(([key]) => key === event.event_type)?.[1] || 'Other'} · {event.event_year} · {event.years_until_event === 0 ? 'now' : `${event.years_until_event} years out`}</div></div><button onClick={() => remove(event.id)} style={{border:'none',background:'transparent',color:'var(--text3)',cursor:'pointer'}}>Remove</button></div><div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,marginTop:18}}><Metric label="One-time impact" value={fmt(event.one_time_cash_delta)} /><Metric label="Monthly impact" value={fmt(event.monthly_impact)} /></div><div style={{marginTop:16,paddingTop:14,borderTop:'1px solid var(--border)'}}><div className="label">Estimated retirement-capital effect</div><div style={{fontSize:23,fontWeight:700,color:event.retirement_impact >= 0 ? 'var(--green)' : 'var(--red)',marginTop:4}}>{fmt(event.retirement_impact)}</div><div style={{fontSize:11,color:'var(--text3)',marginTop:5}}>{event.assumption}</div></div>{event.notes && <div style={{fontSize:12,color:'var(--text2)',marginTop:12}}>{event.notes}</div>}</div>)}</div>}
  </div>
}
function Field({label,children}) { return <div><div className="label" style={{marginBottom:6}}>{label}</div>{children}</div> }
function Metric({label,value}) { return <div><div className="label">{label}</div><div style={{fontSize:16,fontWeight:650,marginTop:4}}>{value}</div></div> }
