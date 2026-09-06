import { useState, useEffect } from 'react'
import axios from 'axios'
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, ReferenceLine
} from 'recharts'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(n)
const fmtK = (n) => isPrivacyMode() ? MASK_CURRENCY : (Math.abs(n)>=1000000?`$${(n/1000000).toFixed(2)}M`:`$${(n/1000).toFixed(0)}K`)

const ChartTip = ({ active, payload, label }) => {
  if (!active||!payload?.length) return null
  return (
    <div style={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, padding:'10px 14px', fontSize:12 }}>
      <div style={{ fontWeight:600, marginBottom:6 }}>{label}</div>
      {payload.map(p => (
        <div key={p.name} style={{ color:p.color, marginBottom:2 }}>{p.name}: {fmtK(p.value)}</div>
      ))}
    </div>
  )
}

export default function NetWorth() {
  const [snapshots, setSnapshots] = useState([])
  const [current,   setCurrent]   = useState(null)
  const [loading,   setLoading]   = useState(true)
  const [note,      setNote]      = useState('')
  const [saving,    setSaving]    = useState(false)
  const [saved,     setSaved]     = useState(false)
  const [rental,    setRental]    = useState(null)

  const load = () => {
    Promise.all([
      axios.get('/api/snapshots'),
      axios.get('/api/net-worth'),
      axios.get('/api/rental/analysis').catch(() => ({ data: { has_data: false } })),
    ]).then(([snaps, nw, rentalRes]) => {
      setSnapshots(snaps.data.reverse()) // chronological
      setCurrent(nw.data)
      setRental(rentalRes.data)
      setLoading(false)
    })
  }

  useEffect(() => { load() }, [])

  const takeSnapshot = () => {
    setSaving(true)
    axios.post('/api/snapshot', { note }).then(() => {
      setSaved(true)
      setNote('')
      setTimeout(() => setSaved(false), 2000)
      load()
    }).finally(() => setSaving(false))
  }

  if (loading) return <div className="loading">Loading net worth history...</div>

  // Chart data
  const chartData = snapshots.map(s => ({
    date:       new Date(s.snapshot_date).toLocaleDateString('en-US',{month:'short',year:'2-digit'}),
    'Net Worth':    s.net_worth,
    'Total Assets': s.total_assets,
    'Liabilities':  s.liabilities,
  }))

  // Growth metrics
  const oldest = snapshots[0]
  const newest = snapshots[snapshots.length-1]
  const growthAmt  = oldest && newest ? newest.net_worth - oldest.net_worth : 0
  const growthPct  = oldest && oldest.net_worth > 0 ? (growthAmt / oldest.net_worth * 100) : 0
  const monthCount = snapshots.length > 1 ? snapshots.length - 1 : 1

  // Category breakdown from current
  const cats = current ? [
    { label:'Investments',  value: current.investment,  color:'#4f9cf9' },
    { label:'Real Estate',  value: current.real_estate, color:'#fbbf24' },
    { label:'Cash',         value: current.savings,     color:'#34d399' },
    { label:'Business',     value: current.business,    color:'#f97316' },
    { label:'Education',    value: current.education,   color:'#a78bfa' },
    { label:'DAF',          value: current.daf||0,      color:'#6ee7b7' },
    { label:'Other',        value: current.other,       color:'#8b8fa8' },
  ].filter(c => c.value > 0) : []

  return (
    <div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:28 }}>
        <div>
          <h1 className="section-title">Net Worth</h1>
          <p className="section-sub">Track your financial progress over time</p>
        </div>
        <div style={{ display:'flex', gap:8, alignItems:'center' }}>
          <input type="text" value={note} onChange={e => setNote(e.target.value)}
            placeholder="Optional note..." style={{ width:180, fontSize:12 }} />
          <button className="btn-primary" onClick={takeSnapshot} disabled={saving}>
            {saved ? '✓ Saved' : saving ? 'Saving...' : '+ Snapshot'}
          </button>
        </div>
      </div>

      {/* Current summary */}
      <div className="grid-4" style={{ marginBottom:24 }}>
        <div className="card" style={{ textAlign:'center' }}>
          <div className="label" style={{ marginBottom:8 }}>Net Worth Today</div>
          <div style={{ fontSize:28, fontWeight:700, color:'var(--green)', fontFamily:'var(--font-display)' }}>{fmtK(current?.net_worth)}</div>
        </div>
        <div className="card" style={{ textAlign:'center' }}>
          <div className="label" style={{ marginBottom:8 }}>Total Assets</div>
          <div style={{ fontSize:24, fontWeight:700, color:'var(--accent)', fontFamily:'var(--font-display)' }}>{fmtK(current?.total_assets)}</div>
        </div>
        <div className="card" style={{ textAlign:'center' }}>
          <div className="label" style={{ marginBottom:8 }}>Liabilities</div>
          <div style={{ fontSize:24, fontWeight:700, color:'var(--red)', fontFamily:'var(--font-display)' }}>{fmtK(current?.liabilities)}</div>
        </div>
        <div className="card" style={{ textAlign:'center' }}>
          <div className="label" style={{ marginBottom:8 }}>Growth ({snapshots.length} snapshots)</div>
          <div style={{ fontSize:24, fontWeight:700, color: growthAmt>=0?'var(--green)':'var(--red)', fontFamily:'var(--font-display)' }}>
            {growthAmt>=0?'+':''}{fmtK(growthAmt)}
          </div>
          <div style={{ fontSize:11, color:'var(--text3)', marginTop:4 }}>
            {Math.abs(growthPct) > 999 ? `${growthPct>0?'>':'<'}999%` : `${growthPct>=0?'+':''}${growthPct.toFixed(1)}%`} since first snapshot
          </div>
        </div>
      </div>

      {snapshots.length > 1 ? (
        <>
          {/* Net worth trend chart */}
          <div className="card" style={{ marginBottom:24 }}>
            <div className="label" style={{ marginBottom:4 }}>Net Worth Over Time</div>
            <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>Each point = a Quicken import or manual snapshot</div>
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={chartData} margin={{top:0,right:0,bottom:0,left:10}}>
                <defs>
                  <linearGradient id="gAssets" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#4f9cf9" stopOpacity={0.2}/>
                    <stop offset="95%" stopColor="#4f9cf9" stopOpacity={0}/>
                  </linearGradient>
                  <linearGradient id="gNW" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#34d399" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#34d399" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <XAxis dataKey="date" tick={{fill:'var(--text3)',fontSize:11}} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={fmtK} tick={{fill:'var(--text3)',fontSize:11}} axisLine={false} tickLine={false} width={60} />
                <Tooltip content={<ChartTip />} />
                <Legend wrapperStyle={{fontSize:11}} />
                <Area type="monotone" dataKey="Total Assets" stroke="#4f9cf9" strokeWidth={1.5} fill="url(#gAssets)" />
                <Area type="monotone" dataKey="Net Worth"    stroke="#34d399" strokeWidth={2.5} fill="url(#gNW)" />
                <Area type="monotone" dataKey="Liabilities"  stroke="#f87171" strokeWidth={1.5} fill="none" strokeDasharray="4 2" />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* Period growth bar chart */}
          {chartData.length > 2 && (
            <div className="card" style={{ marginBottom:24 }}>
              <div className="label" style={{ marginBottom:16 }}>Net Worth by Snapshot</div>
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={chartData} margin={{top:0,right:0,bottom:0,left:10}}>
                  <XAxis dataKey="date" tick={{fill:'var(--text3)',fontSize:10}} axisLine={false} tickLine={false} />
                  <YAxis tickFormatter={fmtK} tick={{fill:'var(--text3)',fontSize:10}} axisLine={false} tickLine={false} width={60} />
                  <Tooltip content={<ChartTip />} />
                  <Bar dataKey="Net Worth" fill="var(--accent)" radius={[3,3,0,0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      ) : (
        <div className="card" style={{ textAlign:'center', padding:'40px 24px', marginBottom:24 }}>
          <div style={{ fontSize:24, marginBottom:12 }}>📊</div>
          <div style={{ fontWeight:600, marginBottom:8 }}>No history yet</div>
          <div style={{ fontSize:13, color:'var(--text2)', marginBottom:20 }}>
            Click "+ Snapshot" to save today's net worth. Import Quicken regularly to build your trend line.
          </div>
        </div>
      )}

      {/* Current breakdown */}
      <div className="grid-2">
        <div className="card">
          <div className="label" style={{ marginBottom:16 }}>Asset Breakdown Today</div>
          {cats.map(c => (
            <div key={c.label} style={{ marginBottom:10 }}>
              <div style={{ display:'flex', justifyContent:'space-between', fontSize:12, marginBottom:3 }}>
                <span style={{ color:'var(--text2)' }}>{c.label}</span>
                <span style={{ fontWeight:600 }}>{fmt(c.value)} <span style={{ color:'var(--text3)', fontWeight:400 }}>({(c.value/(current?.total_assets||1)*100).toFixed(0)}%)</span></span>
              </div>
              <div style={{ height:6, background:'var(--bg3)', borderRadius:3 }}>
                <div style={{ height:'100%', width:`${(c.value/(current?.total_assets||1)*100).toFixed(1)}%`, background:c.color, borderRadius:3 }} />
              </div>
            </div>
          ))}
          <div style={{ display:'flex', justifyContent:'space-between', paddingTop:10, borderTop:'1px solid var(--border)', fontSize:13, marginTop:4 }}>
            <span style={{ color:'var(--text3)' }}>Liabilities</span>
            <span style={{ color:'var(--red)', fontWeight:600 }}>{fmt(current?.liabilities)}</span>
          </div>
        </div>

        {/* Snapshot history table */}
        <div className="card">
          <div className="label" style={{ marginBottom:12 }}>Snapshot History</div>
          {snapshots.length === 0 ? (
            <div style={{ color:'var(--text3)', fontSize:13, textAlign:'center', padding:'20px 0' }}>No snapshots yet</div>
          ) : (
            <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
              <thead><tr style={{ borderBottom:'2px solid var(--border)' }}>
                {['Date','Assets','Liabilities','Net Worth'].map(h=>(
                  <th key={h} style={{ padding:'5px 6px', textAlign:'right', fontSize:10, color:'var(--text3)', fontWeight:600, textTransform:'uppercase' }}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {[...snapshots].reverse().slice(0, 12).map((s,i) => {
                  const prev = [...snapshots].reverse()[i+1]
                  const delta = prev ? s.net_worth - prev.net_worth : 0
                  return (
                    <tr key={s.id} style={{ borderBottom:'1px solid var(--border)', background:i%2===0?'transparent':'var(--bg3)' }}>
                      <td style={{ padding:'6px 6px', textAlign:'right', color:'var(--text3)' }}>
                        {new Date(s.snapshot_date).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'2-digit'})}
                        {s.note && <div style={{ fontSize:9, color:'var(--text3)' }}>{s.note}</div>}
                      </td>
                      <td style={{ padding:'6px 6px', textAlign:'right' }}>{fmtK(s.total_assets)}</td>
                      <td style={{ padding:'6px 6px', textAlign:'right', color:'var(--red)' }}>{fmtK(s.liabilities)}</td>
                      <td style={{ padding:'6px 6px', textAlign:'right', fontWeight:600 }}>
                        {fmtK(s.net_worth)}
                        {delta !== 0 && (
                          <div style={{ fontSize:9, color:delta>0?'var(--green)':'var(--red)' }}>
                            {delta>0?'+':''}{fmtK(delta)}
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {rental?.has_data && (
        <div className="card" style={{ marginTop:24 }}>
          <div className="label" style={{ marginBottom:16 }}>Rental Property Analysis — {rental.property_name}</div>
          <div style={{
            padding:'12px 16px', borderRadius:8, fontSize:13, lineHeight:1.6, marginBottom:16,
            background: rental.beats_alternative ? 'rgba(52,211,153,0.08)' : 'rgba(251,191,36,0.08)',
          }}>
            {rental.recommendation}
          </div>
          <div className="grid-4">
            <div>
              <div className="label">Annual Cash Flow (pre-debt-service)</div>
              <div style={{ fontSize:16, fontWeight:600, marginTop:4, color: rental.annual_cash_flow >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmt(rental.annual_cash_flow)}</div>
            </div>
            <div>
              <div className="label">Cap Rate</div>
              <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>{(rental.cap_rate*100).toFixed(1)}%</div>
            </div>
            <div>
              <div className="label">Cash-on-Cash Return</div>
              <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>{(rental.cash_on_cash_return*100).toFixed(1)}%</div>
            </div>
            <div>
              <div className="label">Equity</div>
              <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>{fmt(rental.equity)}</div>
            </div>
          </div>
          {rental.annual_debt_service > 0 && (
            <div style={{ marginTop:16, paddingTop:16, borderTop:'1px solid var(--border)' }} className="grid-4">
              <div>
                <div className="label">Annual Debt Service</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4, color:'var(--red)' }}>−{fmt(rental.annual_debt_service)}</div>
              </div>
              <div>
                <div className="label">Levered Cash Flow</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4, color: rental.levered_cash_flow >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmt(rental.levered_cash_flow)}</div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
