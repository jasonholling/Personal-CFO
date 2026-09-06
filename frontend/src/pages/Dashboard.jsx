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
  const { person1Name, person2Name, kid1Name, kid2Name } = usePersonNames()
  const [nw, setNw] = useState(null)
  const [snapshots, setSnapshots] = useState([])
  const [snapping, setSnapping] = useState(false)
  const [note, setNote] = useState('')
  const [emergencyFund, setEmergencyFund] = useState(null)

  useEffect(() => {
    axios.get('/api/net-worth').then(r => setNw(r.data)).catch(() => {})
    axios.get('/api/snapshots').then(r => setSnapshots(r.data)).catch(() => {})
    axios.get('/api/emergency-fund').then(r => setEmergencyFund(r.data)).catch(() => {})
  }, [])

  const takeSnapshot = async () => {
    setSnapping(true)
    try {
      await axios.post('/api/snapshot', { note: note || 'Monthly update' })
      const r = await axios.get('/api/snapshots')
      setSnapshots(r.data)
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
              <div style={{ color: 'var(--text2)', fontSize: 13 }}>Track {kid1Name} & {kid2Name} funding →</div>
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
