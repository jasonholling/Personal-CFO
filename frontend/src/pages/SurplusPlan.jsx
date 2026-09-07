import { useEffect, useState } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'
const fmt=n=>isPrivacyMode()?MASK_CURRENCY:new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(n||0)
// "Education funding" used to be one shared goal (split by no defined
// formula between the two kids). Replaced 2026-09-06 with two genuinely
// separate per-kid goals — "abby's will go to abby, cooper will go to
// cooper" — so surplus can be directed to a specific kid's 529.
//
// The stored `goal` string is a STABLE key ("Education funding - Abby" /
// "Education funding - Cooper"), not the kid's configurable display name
// (kid1_name/kid2_name in Settings) — a rename in Settings must never
// orphan an existing surplus_allocations row. We DO fetch planning-inputs
// here (same "second fetch alongside the page's main data" pattern as
// LifeEvents.jsx fetching /api/accounts) so the row LABEL shown to the
// user reflects the real configured kid name, while the key saved to the
// database stays fixed regardless of what Settings says.
const GOAL_EDUCATION_ABBY = 'Education funding - Abby'
const GOAL_EDUCATION_COOPER = 'Education funding - Cooper'
const GOALS=['Emergency reserve','High-interest debt payoff','Retirement contributions',GOAL_EDUCATION_ABBY,GOAL_EDUCATION_COOPER,'Tax reserve','Taxable investing','Other goal']
export default function SurplusPlan(){
  const[d,setD]=useState(null);const[values,setValues]=useState({});const[names,setNames]=useState({kid1_name:'Abby',kid2_name:'Cooper'})
  const load=()=>{
    axios.get('/api/surplus-allocations').then(r=>{setD(r.data);setValues(Object.fromEntries(r.data.allocations.map(x=>[x.goal,x.monthly_amount])))})
    axios.get('/api/planning-inputs').then(r=>{if(r.data) setNames({kid1_name:r.data.kid1_name||'Abby',kid2_name:r.data.kid2_name||'Cooper'})}).catch(()=>{})
  }
  useEffect(()=>{load()},[])
  // Saving one row used to call load(), which refetches /api/surplus-allocations
  // and replaces the ENTIRE `values` map with server data — wiping out any
  // unsaved edits typed into other rows in the meantime (external audit
  // 2026-09-07, finding #14). Instead, only merge this row's own server-
  // confirmed value plus the response's assigned/unassigned/monthly_surplus
  // totals into local state; every other row's in-progress edit survives.
  const save=async g=>{
    const r=await axios.put(`/api/surplus-allocations/${encodeURIComponent(g)}`,{goal:g,monthly_amount:Number(values[g]||0)})
    setValues(v=>({...v,[g]:r.data.monthly_amount}))
    const totals=await axios.get('/api/surplus-allocations')
    setD(totals.data)
  }
  const label=g=>g===GOAL_EDUCATION_ABBY?`Education funding — ${names.kid1_name}`:g===GOAL_EDUCATION_COOPER?`Education funding — ${names.kid2_name}`:g
  if(!d)return <div className="loading">Loading surplus plan...</div>
  return <div><div style={{marginBottom:28}}><h1 className="section-title">Assign Your Surplus</h1><p className="section-sub">Turn monthly capacity into deliberate funding across your highest-priority goals.</p></div><div className="grid-3" style={{marginBottom:24}}><Metric label="Monthly surplus" value={fmt(d.monthly_surplus)}/><Metric label="Assigned" value={fmt(d.assigned)}/><Metric label="Still unassigned" value={fmt(d.unassigned)} color={d.unassigned<0?'var(--red)':d.unassigned===0?'var(--green)':'var(--amber)'}/></div><div className="card"><div style={{fontWeight:650,marginBottom:12}}>Monthly funding instructions</div>{GOALS.map(g=><div key={g} style={{display:'flex',gap:12,alignItems:'center',padding:'12px 0',borderTop:'1px solid var(--border)'}}><div style={{flex:1,fontSize:13}}>{label(g)}</div><input aria-label={`${label(g)} monthly allocation`} type="number" min="0" value={values[g]??''} onChange={e=>setValues(v=>({...v,[g]:e.target.value}))} style={{width:120,textAlign:'right'}}/><button className="btn-secondary" onClick={()=>save(g)}>Save</button></div>)}</div>{d.unassigned<0&&<div style={{color:'var(--red)',fontSize:13,marginTop:14}}>Assignments exceed the available monthly surplus. Reduce one or more amounts before treating this plan as funded.</div>}</div>
}
function Metric({label,value,color}){return <div className="card"><div className="label">{label}</div><div className="number-lg" style={{marginTop:8,color}}>{value}</div></div>}
