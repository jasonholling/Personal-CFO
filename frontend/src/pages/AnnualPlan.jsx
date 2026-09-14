import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { Cell, Pie, PieChart } from 'recharts'
import { isPrivacyMode, MASK_PERCENT } from '../utils/privacy'

const SECTION_LABELS = {
  financial_independence: 'Retirement & Tax',
  investments: 'Investments & Cash',
  education: 'Education Funding',
  risk: 'Protection',
  estate: 'Estate & Household',
}

const SECTION_ORDER = ['financial_independence', 'investments', 'education', 'risk', 'estate']
const ALLOCATION_COLORS = ['#6ea8fe', '#56c7b5', '#f4b860', '#b894ee', '#f08080', '#79b8ff', '#c5d86d', '#f4a261', '#9aa7bd', '#d986ba']

function InvestmentPosture({ allocation, onNavigate }) {
  if (!allocation || allocation.has_holdings === false) {
    return <section className="card" style={{ marginBottom: 24 }}><div className="label">HOUSEHOLD INVESTMENT POSTURE</div><div style={{ marginTop: 8, color: 'var(--text2)', fontSize: 13 }}>Enter included holdings and a policy target to review current allocation against the household plan.</div><button className="btn-secondary" onClick={() => onNavigate('coach')} style={{ marginTop: 12 }}>Open Portfolio Coach →</button></section>
  }
  if (!allocation.has_policy || !allocation.comparison) {
    return <section className="card" style={{ marginBottom: 24 }}><div className="label">HOUSEHOLD INVESTMENT POSTURE</div><div style={{ marginTop: 8, color: 'var(--text2)', fontSize: 13 }}>Save a household policy target to compare your current allocation with the plan.</div><button className="btn-secondary" onClick={() => onNavigate('coach')} style={{ marginTop: 12 }}>Set policy in Portfolio Coach →</button></section>
  }
  const rows = Object.entries(allocation.comparison.by_class || [])
    .filter(([, value]) => Number(value.current_pct) || Number(value.target_pct))
    .map(([assetClass, value], index) => ({ assetClass, ...value, color: ALLOCATION_COLORS[index % ALLOCATION_COLORS.length] }))
  const gaps = [...rows].sort((a, b) => Math.abs(Number(b.deviation_pct) || 0) - Math.abs(Number(a.deviation_pct) || 0)).slice(0, 3)
  const displayPct = value => isPrivacyMode() ? MASK_PERCENT : `${Number(value || 0).toFixed(1)}%`
  const donut = key => rows.filter(row => Number(row[key]) > 0).map(row => ({ name: row.assetClass, value: Number(row[key]), color: row.color }))
  return <section className="card" style={{ marginBottom: 24 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      <div><div className="label">HOUSEHOLD INVESTMENT POSTURE</div><div style={{ marginTop: 5, fontSize: 16, fontWeight: 650 }}>Current allocation vs. policy target</div><div style={{ color: 'var(--text2)', fontSize: 12, marginTop: 4 }}>Included accounts and entered holdings · allocation, not performance · {allocation.included_account_count || 0} account{allocation.included_account_count === 1 ? '' : 's'} · as of {allocation.holdings_as_of || 'date unavailable'}</div></div>
      <button className="btn-secondary" onClick={() => onNavigate('coach')}>Review in Portfolio Coach →</button>
    </div>
    <div style={{ display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap', marginTop: 12 }}>
      <div style={{ display: 'flex', gap: 4 }}>
        {[['Current', 'current_pct'], ['Policy target', 'target_pct']].map(([label, key]) => <div key={key} style={{ textAlign: 'center' }}><PieChart width={142} height={142}><Pie data={donut(key)} dataKey="value" innerRadius={38} outerRadius={58} paddingAngle={1} stroke="none">{donut(key).map(row => <Cell key={row.name} fill={row.color} />)}</Pie><text x="71" y="68" textAnchor="middle" fill="var(--text)" fontSize="12">{label === 'Current' ? 'Now' : 'Target'}</text></PieChart><div style={{ color: 'var(--text2)', fontSize: 11 }}>{label}</div></div>)}
      </div>
      <div style={{ flex: 1, minWidth: 220 }}><div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>Largest differences</div>{gaps.map(row => <div key={row.assetClass} style={{ display: 'flex', gap: 8, alignItems: 'center', borderTop: '1px solid var(--border)', padding: '7px 0', fontSize: 12 }}><span style={{ width: 8, height: 8, borderRadius: 4, background: row.color }} /><span style={{ flex: 1, textTransform: 'capitalize' }}>{row.assetClass.replace(/_/g, ' ')}</span><span>{displayPct(row.current_pct)} → {displayPct(row.target_pct)}</span><strong style={{ color: row.within_drift_band ? 'var(--green)' : 'var(--amber)' }}>{Number(row.deviation_pct) > 0 ? '+' : ''}{displayPct(row.deviation_pct)}</strong></div>)}</div>
    </div>
  </section>
}

export default function AnnualPlan({ onNavigate }) {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [showComplete, setShowComplete] = useState(false)
  const [allocation, setAllocation] = useState(null)

  const load = () => Promise.all([
    axios.get('/api/tasks').catch(() => ({ data: [] })),
    axios.get('/api/portfolio/allocation').catch(() => ({ data: null })),
  ])
    .then(([tasksResponse, allocationResponse]) => { setTasks(tasksResponse.data); setAllocation(allocationResponse.data) })
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

      <InvestmentPosture allocation={allocation} onNavigate={onNavigate} />

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
