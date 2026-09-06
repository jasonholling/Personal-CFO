import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'

const SECTION_LABELS = {
  financial_independence: 'Retirement & Tax',
  investments: 'Investments & Cash',
  education: 'Education Funding',
  risk: 'Protection',
  estate: 'Estate & Household',
}

const SECTION_ORDER = ['financial_independence', 'investments', 'education', 'risk', 'estate']

export default function AnnualPlan({ onNavigate }) {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [showComplete, setShowComplete] = useState(false)

  const load = () => axios.get('/api/tasks')
    .then(r => setTasks(r.data))
    .finally(() => setLoading(false))

  useEffect(() => { load() }, [])

  const sync = async () => {
    setSyncing(true)
    try { await axios.post('/api/tasks/sync'); await load() } finally { setSyncing(false) }
  }

  const toggle = async task => {
    await axios.patch(`/api/tasks/${task.id}`, { completed: !task.completed })
    await load()
  }

  const visible = useMemo(() => tasks.filter(task => showComplete || !task.completed), [tasks, showComplete])
  const grouped = SECTION_ORDER.map(section => ({
    section,
    tasks: visible.filter(task => task.section === section),
  })).filter(group => group.tasks.length)
  const openCount = tasks.filter(task => !task.completed).length

  return (
    <div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:16, marginBottom:28, flexWrap:'wrap' }}>
        <div>
          <h1 className="section-title">Action Tracker</h1>
          <p className="section-sub">Your recurring household financial operating calendar and calculation-driven follow-ups.</p>
        </div>
        <div style={{ display:'flex', gap:8, alignItems:'center' }}>
          <button className="btn-secondary" onClick={() => setShowComplete(value => !value)}>{showComplete ? 'Hide completed' : 'Show completed'}</button>
          <button className="btn-primary" onClick={sync} disabled={syncing}>{syncing ? 'Syncing...' : '↻ Refresh plan'}</button>
        </div>
      </div>

      <div className="grid-4" style={{ marginBottom:24 }}>
        <div className="card"><div className="label">Open actions</div><div className="number-lg" style={{ marginTop:8, color:openCount ? 'var(--amber)' : 'var(--green)' }}>{openCount}</div></div>
        <div className="card"><div className="label">Recurring reviews</div><div className="number-lg" style={{ marginTop:8 }}>{tasks.filter(task => task.recurrence === 'annual' && !task.completed).length}</div></div>
        <div className="card"><div className="label">Calculation follow-ups</div><div className="number-lg" style={{ marginTop:8 }}>{tasks.filter(task => task.task_type === 'calculated' && !task.completed).length}</div></div>
        <button className="card" onClick={() => onNavigate('dashboard')} style={{ cursor:'pointer', textAlign:'left' }}><div className="label">CFO briefing</div><div style={{ marginTop:10, color:'var(--accent)', fontSize:13, fontWeight:600 }}>View priorities →</div></button>
      </div>

      {loading ? <div className="loading">Loading annual plan...</div> : grouped.length === 0 ? (
        <div className="card" style={{ textAlign:'center', padding:'44px 24px' }}>
          <div style={{ fontSize:28, marginBottom:10 }}>✓</div>
          <div style={{ fontWeight:600, marginBottom:8 }}>Your plan is clear</div>
          <div style={{ color:'var(--text2)', fontSize:13, marginBottom:18 }}>Refresh the plan to create annual reviews and data-driven follow-ups.</div>
          <button className="btn-primary" onClick={sync} disabled={syncing}>Create annual plan</button>
        </div>
      ) : grouped.map(group => (
        <section key={group.section} className="card" style={{ marginBottom:16 }}>
          <div style={{ display:'flex', justifyContent:'space-between', marginBottom:8 }}>
            <div style={{ fontSize:15, fontWeight:650 }}>{SECTION_LABELS[group.section] || group.section}</div>
            <div style={{ color:'var(--text3)', fontSize:12 }}>{group.tasks.filter(task => !task.completed).length} open</div>
          </div>
          {group.tasks.map(task => (
            <div key={task.id} style={{ display:'flex', gap:12, padding:'12px 0', borderTop:'1px solid var(--border)', opacity:task.completed ? 0.55 : 1 }}>
              <button onClick={() => toggle(task)} aria-label={`Mark ${task.title} ${task.completed ? 'incomplete' : 'complete'}`} style={{ width:20, height:20, flexShrink:0, marginTop:1, borderRadius:5, background:task.completed ? 'var(--green)' : 'transparent', border:`1.5px solid ${task.completed ? 'var(--green)' : 'var(--border2)'}`, color:'#fff', cursor:'pointer' }}>{task.completed ? '✓' : ''}</button>
              <div style={{ flex:1 }}>
                <div style={{ display:'flex', gap:8, alignItems:'center', flexWrap:'wrap' }}>
                  <span style={{ fontSize:13, fontWeight:550, textDecoration:task.completed ? 'line-through' : 'none' }}>{task.title}</span>
                  {task.task_type === 'calculated' && <span style={{ color:'var(--amber)', fontSize:10, fontWeight:700 }}>CALCULATED</span>}
                  {task.recurrence === 'annual' && <span style={{ color:'var(--accent)', fontSize:10, fontWeight:700 }}>ANNUAL</span>}
                </div>
                {task.description && <div style={{ color:'var(--text2)', fontSize:12, lineHeight:1.45, marginTop:3 }}>{task.description}</div>}
              </div>
            </div>
          ))}
        </section>
      ))}
    </div>
  )
}
