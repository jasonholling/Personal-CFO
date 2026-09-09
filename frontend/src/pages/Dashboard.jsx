import { useState, useEffect } from 'react'
import axios from 'axios'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { usePersonNames } from '../hooks/usePersonNames'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n))
const fmtK = (n) => isPrivacyMode() ? MASK_CURRENCY : (n >= 1000000 ? `$${(n/1000000).toFixed(2)}M` : `$${(n/1000).toFixed(0)}K`)

const CATEGORY_LABELS = {
  investment: 'Investments',
  savings: 'Cash & Savings',
  education: '529 Education',
  real_estate: 'Real Estate',
  business: 'Business Assets',
  daf:      'Donor Advised Fund',
  insurance: 'Insurance CV',
  other: 'Other Assets',
}

const CATEGORY_COLORS = {
  investment: '#4f9cf9',
  savings: '#34d399',
  education: '#a78bfa',
  real_estate: '#fbbf24',
  business: '#f97316',
  insurance: '#22d3ee',
  other: '#8b92a8',
}

export default function Dashboard({ onNavigate }) {
  const { person1Name, person2Name } = usePersonNames()
  const [nw, setNw] = useState(null)
  const [snapshots, setSnapshots] = useState([])
  const [snapping, setSnapping] = useState(false)
  const [note, setNote] = useState('')
  const [emergencyFund, setEmergencyFund] = useState(null)
  const [briefing, setBriefing] = useState(null)
  const [confidence, setConfidence] = useState(null)

  useEffect(() => {
    axios.get('/api/net-worth').then(r => setNw(r.data)).catch(() => {})
    axios.get('/api/snapshots').then(r => setSnapshots(r.data)).catch(() => {})
    axios.get('/api/emergency-fund').then(r => setEmergencyFund(r.data)).catch(() => {})
    axios.get('/api/cfo-briefing').then(r => setBriefing(r.data)).catch(() => {})
    axios.get('/api/plan-confidence').then(r => setConfidence(r.data)).catch(() => {})
  }, [])

  const takeSnapshot = async () => {
    setSnapping(true)
    try {
      await axios.post('/api/snapshot', { note: note || 'Monthly update' })
      const r = await axios.get('/api/snapshots')
      setSnapshots(r.data)
      const updatedBriefing = await axios.get('/api/cfo-briefing')
      setBriefing(updatedBriefing.data)
      setNote('')
    } finally {
      setSnapping(false)
    }
  }

  const categories = nw ? Object.entries(CATEGORY_LABELS)
    .map(([key, label]) => ({ key, label, value: nw[key] || 0 }))
    .filter(c => c.value > 0)
    .sort((a, b) => b.value - a.value) : []

  const chartData = [...snapshots].reverse().map(s => ({
    date: new Date(s.snapshot_date).toLocaleDateString('en-US', { month: 'short', year: '2-digit' }),
    netWorth: s.net_worth,
    assets: s.total_assets,
  }))

  const generateReport = () => onNavigate('report')

  return (
    <div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom: 32 }}>
        <div>
          <h1 className="section-title">Good morning, {person1Name} & {person2Name}</h1>
          <p className="section-sub">Here's where things stand today</p>
        </div>
        <button className="btn-primary" onClick={generateReport} style={{ display:'flex', alignItems:'center', gap:8 }}>
          ↓ Generate Annual Report
        </button>
      </div>

      {!nw ? (
        <div className="card" style={{ textAlign: 'center', padding: '48px 24px' }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>◈</div>
          <div style={{ color: 'var(--text)', fontSize: 16, marginBottom: 8 }}>No accounts yet</div>
          <div style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 20 }}>Add your accounts to see your net worth dashboard</div>
          <button className="btn-primary" onClick={() => onNavigate('accounts')}>Add Accounts →</button>
        </div>
      ) : (
        <>
          {briefing && (
            <div className="card" style={{ marginBottom: 24, border: '1px solid rgba(79,156,249,0.3)', background: 'linear-gradient(135deg, rgba(79,156,249,0.10), var(--bg2) 62%)' }}>
              <div style={{ display:'flex', justifyContent:'space-between', gap:16, alignItems:'flex-start', marginBottom: briefing.priorities?.length ? 16 : 0 }}>
                <div>
                  <div className="label" style={{ color:'var(--accent)', marginBottom:6 }}>CFO BRIEFING</div>
                  <div style={{ fontSize:18, fontWeight:650 }}>Your highest-value next moves</div>
                  <div style={{ color:'var(--text2)', fontSize:12, marginTop:4 }}>
                    {briefing.data_health?.planning_ready ? 'Based on your current household inputs and projections.' : 'Complete the missing inputs below to turn this into a fully personalized plan.'}
                  </div>
                </div>
                <div style={{ textAlign:'right', color:'var(--text2)', fontSize:11, whiteSpace:'nowrap' }}>
                  {briefing.data_health?.open_task_count || 0} open actions
                  <div style={{ marginTop:3 }}>{briefing.data_health?.snapshot_age_days == null ? 'No snapshot yet' : `Snapshot ${briefing.data_health.snapshot_age_days}d ago`}</div>
                </div>
              </div>
              {briefing.priorities?.length > 0 ? briefing.priorities.map((item, index) => (
                <button key={`${item.title}-${index}`} onClick={() => onNavigate(item.destination)} style={{ width:'100%', display:'flex', alignItems:'center', gap:12, textAlign:'left', padding:'11px 0', background:'transparent', border:'none', borderTop: index ? '1px solid var(--border)' : 'none', color:'var(--text)', cursor:'pointer' }}>
                  <span style={{ width:22, height:22, flexShrink:0, borderRadius:11, background:'rgba(79,156,249,0.16)', color:'var(--accent)', display:'grid', placeItems:'center', fontSize:11, fontWeight:700 }}>{index + 1}</span>
                  <span style={{ flex:1 }}>
                    <span style={{ display:'block', fontSize:13, fontWeight:600 }}>{item.title}</span>
                    <span style={{ display:'block', color:'var(--text2)', fontSize:12, marginTop:2, lineHeight:1.4 }}>{item.detail}</span>
                  </span>
                  {item.amount != null && <span style={{ color:'var(--amber)', fontWeight:650, fontSize:13, whiteSpace:'nowrap' }}>{fmt(item.amount)}</span>}
                  <span style={{ color:'var(--text3)' }}>→</span>
                </button>
              )) : (
                <div style={{ color:'var(--green)', fontSize:13 }}>✓ No material gaps surfaced by the current assumptions. Keep the annual plan and monthly snapshot current.</div>
              )}
            </div>
          )}
          {confidence && (
            <div className="card" style={{ marginBottom:24, padding:'16px', border:`1px solid ${confidence.status === 'ready' ? 'rgba(52,211,153,0.30)' : 'rgba(251,191,36,0.30)'}`, background:'var(--bg2)' }}>
              <div style={{ display:'flex', justifyContent:'space-between', gap:12, alignItems:'center' }}>
                <div><div className="label" style={{ marginBottom:4 }}>PLAN SETUP</div><div style={{ fontSize:16, fontWeight:650 }}>{confidence.status === 'ready' ? 'Your core plan inputs are ready' : `${confidence.checks.filter(check => check.status === 'attention').length} items needed to complete your plan`}</div></div>
              </div>
              <div style={{ display:'flex', flexWrap:'wrap', gap:10, marginTop:14 }}>{confidence.checks.map(check => {
                const destination = check.key === 'balances' ? 'accounts' : check.key === 'cash_flow' ? 'cashflow' : 'settings'
                return check.status === 'attention' ? <button key={check.key} className="btn-secondary" onClick={() => onNavigate(destination)} style={{ textAlign:'left', padding:'9px 12px' }}><span style={{ color:'var(--amber)', fontWeight:700 }}>! </span><span style={{ fontWeight:650 }}>{check.label}</span><span style={{ display:'block', fontSize:11, color:'var(--text2)', marginTop:3 }}>{check.detail} →</span></button> : <span key={check.key} style={{ fontSize:12, padding:'8px 10px', borderRadius:6, background:check.status === 'ready' ? 'rgba(52,211,153,0.10)' : 'rgba(79,156,249,0.10)', color:check.status === 'ready' ? 'var(--green)' : 'var(--accent)' }}>{check.status === 'ready' ? '✓' : 'i'} {check.label}</span>
              })}</div>
            </div>
          )}
          {emergencyFund?.has_data && emergencyFund.status === 'underfunded' && (
            <div
              style={{ padding: '12px 16px', background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.2)', borderRadius: 8, marginBottom: 24, fontSize: 13, color: 'var(--amber)', fontWeight: 500, cursor: 'pointer' }}
              onClick={() => onNavigate('settings')}
            >
              ⚠ Emergency fund: only {emergencyFund.months_covered} months of expenses liquid — {fmt(emergencyFund.gap_to_min)} more would reach the 3-month floor →
            </div>
          )}

          {/* Top KPIs */}
          <div className="grid-4" style={{ marginBottom: 24 }}>
            <div className="card">
              <div className="label">Net Worth</div>
              <div className="number-lg" style={{ color: nw.net_worth >= 0 ? 'var(--green)' : 'var(--red)', marginTop: 8 }}>
                {fmtK(nw.net_worth)}
              </div>
            </div>
            <div className="card">
              <div className="label">Total Assets</div>
              <div className="number-lg" style={{ marginTop: 8 }}>{fmtK(nw.total_assets)}</div>
            </div>
            <div className="card">
              <div className="label">Investments</div>
              <div className="number-lg" style={{ color: 'var(--accent)', marginTop: 8 }}>{fmtK(nw.investment || 0)}</div>
            </div>
            <div className="card">
              <div className="label">Liabilities</div>
              <div className="number-lg" style={{ color: 'var(--red)', marginTop: 8 }}>{fmtK(nw.liabilities || 0)}</div>
            </div>
          </div>

          <div className="grid-2" style={{ marginBottom: 24 }}>
            {/* Asset breakdown */}
            <div className="card">
              <div className="label" style={{ marginBottom: 16 }}>Asset Breakdown</div>
              {categories.map(c => (
                <div key={c.key} style={{ marginBottom: 14 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 13, color: 'var(--text2)' }}>{c.label}</span>
                    <span style={{ fontSize: 13, fontWeight: 500 }}>{fmt(c.value)}</span>
                  </div>
                  <div className="progress-bar-track">
                    <div className="progress-bar-fill" style={{
                      width: `${(c.value / nw.total_assets * 100).toFixed(1)}%`,
                      background: CATEGORY_COLORS[c.key]
                    }} />
                  </div>
                </div>
              ))}
            </div>

            {/* Net worth history or snapshot prompt */}
            <div className="card">
              <div className="label" style={{ marginBottom: 16 }}>Net Worth History</div>
              {chartData.length >= 2 ? (
                <ResponsiveContainer width="100%" height={200}>
                  <AreaChart data={chartData}>
                    <defs>
                      <linearGradient id="nwGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#4f9cf9" stopOpacity={0.3}/>
                        <stop offset="95%" stopColor="#4f9cf9" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="date" tick={{ fill: 'var(--text3)', fontSize: 11 }} axisLine={false} tickLine={false} />
                    <YAxis tickFormatter={fmtK} tick={{ fill: 'var(--text3)', fontSize: 11 }} axisLine={false} tickLine={false} width={60} />
                    <Tooltip
                      contentStyle={{ background: 'var(--bg3)', border: '1px solid var(--border2)', borderRadius: 8, fontSize: 12 }}
                      formatter={(v) => [fmt(v), 'Net Worth']}
                    />
                    <Area type="monotone" dataKey="netWorth" stroke="#4f9cf9" strokeWidth={2} fill="url(#nwGrad)" />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 16 }}>
                  Take monthly snapshots to track your progress over time.
                </div>
              )}
              <div className="divider" />
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input
                  value={note}
                  onChange={e => setNote(e.target.value)}
                  placeholder="Snapshot note (optional)"
                  style={{ flex: 1 }}
                />
                <button className="btn-primary" onClick={takeSnapshot} disabled={snapping}>
                  {snapping ? '...' : 'Save snapshot'}
                </button>
              </div>
            </div>
          </div>

          {/* Quick links */}
          <div className="grid-4">
            <div className="card" style={{ cursor: 'pointer' }} onClick={() => onNavigate('retirement')}>
              <div className="label" style={{ marginBottom: 8 }}>Retirement Projection</div>
              <div style={{ color: 'var(--text2)', fontSize: 13 }}>Run age 60 & 65 scenarios →</div>
            </div>
            <div className="card" style={{ cursor: 'pointer' }} onClick={() => onNavigate('education')}>
              <div className="label" style={{ marginBottom: 8 }}>529 Education</div>
              <div style={{ color: 'var(--text2)', fontSize: 13 }}>Track college funding →</div>
            </div>
            <div className="card" style={{ cursor: 'pointer' }} onClick={() => onNavigate('settings')}>
              <div className="label" style={{ marginBottom: 8 }}>Planning Inputs</div>
              <div style={{ color: 'var(--text2)', fontSize: 13 }}>Update SS, pension, contributions →</div>
            </div>
            <div className="card" style={{ cursor: 'pointer' }} onClick={() => onNavigate('settings')}>
              <div className="label" style={{ marginBottom: 8 }}>Emergency Fund</div>
              {emergencyFund?.has_data ? (
                <div style={{ fontSize: 13 }}>
                  <span style={{ color: emergencyFund.status === 'funded' ? 'var(--green)' : emergencyFund.status === 'adequate' ? 'var(--accent)' : 'var(--amber)', fontWeight: 600 }}>
                    {emergencyFund.months_covered} months
                  </span>
                  <span style={{ color: 'var(--text2)' }}> liquid ({emergencyFund.target_min_months}-{emergencyFund.target_full_months}mo target)</span>
                </div>
              ) : (
                <div style={{ color: 'var(--text2)', fontSize: 13 }}>Set current monthly expenses →</div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
