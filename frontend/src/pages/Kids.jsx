import { useState, useEffect } from 'react'
import axios from 'axios'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, LineChart, Line, Legend } from 'recharts'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(n)
const fmtK = (n) => isPrivacyMode() ? MASK_CURRENCY : (n >= 1000000 ? `$${(n/1000000).toFixed(2)}M` : `$${(n/1000).toFixed(0)}K`)

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, padding:'10px 14px', fontSize:12 }}>
      <div style={{ fontWeight:600, marginBottom:6 }}>Age {label}</div>
      {payload.map(p => <div key={p.name} style={{ color:p.color, marginBottom:2 }}>{p.name}: {fmt(p.value)}</div>)}
    </div>
  )
}

// Timeline row: Account | Today | At 18 | At 22/24 | At 60
const TimelineRow = ({ label, current, at18, mid, midLabel, at60, contrib, color, note }) => (
  <div style={{ display:'grid', gridTemplateColumns:'160px 1fr 1fr 1fr 1fr', gap:0, borderBottom:'1px solid var(--border)', alignItems:'center' }}>
    <div style={{ padding:'14px 16px' }}>
      <div style={{ fontWeight:600, fontSize:13 }}>{label}</div>
      {contrib && <div style={{ fontSize:11, color:'var(--text3)', marginTop:2 }}>{contrib}/mo</div>}
      {note && <div style={{ fontSize:10, color:'var(--text3)', marginTop:2 }}>{note}</div>}
    </div>
    {[
      { val: current, label: 'Today' },
      { val: at18,    label: 'Age 18' },
      { val: mid,     label: midLabel },
      { val: at60,    label: 'Age 60' },
    ].map((col, i) => (
      <div key={i} style={{ padding:'14px 16px', textAlign:'right' }}>
        <div style={{ fontSize:15, fontWeight:700, color: col.val > 0 ? (i === 3 ? 'var(--green)' : color) : 'var(--text3)' }}>
          {col.val != null ? (col.val === 0 ? '—' : fmtK(col.val)) : '—'}
        </div>
        <div style={{ fontSize:10, color:'var(--text3)', marginTop:2 }}>{col.label}</div>
      </div>
    ))}
  </div>
)

function KidView({ kid }) {
  const { child, child_name, current_age, '529':s529, roth, custodial, bonds, timeline, roth_to_60 } = kid

  const totalAt18 = s529.at_18 + roth.at_18 + custodial.at_18 + (bonds?.at_18 || 0)
  // 529 isn't included here — by 60 it's been spent on college (any
  // leftover up to $35k is already inside roth.at_60 via the rollover,
  // see roth['529_rollover']), so adding s529 again would double-count it.
  const totalAt60 = roth.at_60 + custodial.at_60 + (bonds?.at_60 || 0)

  return (
    <div>
      <div style={{ marginBottom:24 }}>
        <div style={{ fontFamily:'var(--font-display)', fontSize:26, marginBottom:4 }}>{child_name}</div>
        <div style={{ color:'var(--text2)', fontSize:13 }}>Age {current_age} · All accounts projected at current contributions and 7% growth</div>
      </div>

      {/* Account timeline table */}
      <div className="card" style={{ padding:0, overflow:'hidden', marginBottom:24 }}>
        {/* Header */}
        <div style={{ display:'grid', gridTemplateColumns:'160px 1fr 1fr 1fr 1fr', gap:0, background:'var(--bg3)', borderBottom:'1px solid var(--border)' }}>
          {['Account', 'Today', 'Age 18', 'Age 22/24', 'Age 60'].map(h => (
            <div key={h} style={{ padding:'10px 16px', fontSize:11, fontWeight:600, letterSpacing:'0.06em', textTransform:'uppercase', color:'var(--text3)', textAlign: h === 'Account' ? 'left' : 'right' }}>{h}</div>
          ))}
        </div>

        <TimelineRow
          label="529 Education"
          contrib={`${fmt(s529.monthly_contribution)}`}
          note="Stops at college"
          current={s529.current}
          at18={s529.at_18}
          mid={s529.at_22}
          midLabel="After college"
          at60={null}
          color="var(--accent)"
        />
        <TimelineRow
          label="Roth IRA"
          contrib={fmt(roth.monthly_contribution)}
          note="Your contribution to 18"
          current={roth.current}
          at18={roth.at_18}
          mid={roth.at_22}
          midLabel={`Age 22${roth['529_rollover'] > 0 ? ` +${fmt(roth['529_rollover'])} rollover` : ''}`}
          at60={roth.at_60}
          color="var(--accent)"
        />
        <TimelineRow
          label="Custodial"
          contrib={`${fmt(custodial.monthly_contribution)}`}
          note="Contributions stop at 18 — not spent"
          current={custodial.current}
          at18={custodial.at_18}
          mid={custodial.at_24}
          midLabel="Age 24"
          at60={custodial.at_60}
          color="var(--amber)"
        />
        {bonds?.current > 0 && (
          <TimelineRow
            label="Savings Bonds"
            note="Not spent — grows at 4%, capped at 30-yr maturity"
            current={bonds.current}
            at18={bonds.at_18}
            mid={null}
            midLabel="—"
            at60={bonds.at_60}
            color="var(--text2)"
          />
        )}

        {/* Total row */}
        <div style={{ display:'grid', gridTemplateColumns:'160px 1fr 1fr 1fr 1fr', gap:0, background:'var(--bg3)', borderTop:'2px solid var(--border2)' }}>
          <div style={{ padding:'14px 16px', fontWeight:700, fontSize:13 }}>Total</div>
          {[
            { val: s529.current + roth.current + custodial.current + (bonds?.current||0), label:'Today' },
            { val: totalAt18, label:'Age 18' },
            { val: null, label:'—' },
            { val: totalAt60, label:'Total at 60' },
          ].map((col, i) => (
            <div key={i} style={{ padding:'14px 16px', textAlign:'right' }}>
              <div style={{ fontSize:15, fontWeight:700, color: i===3?'var(--green)':'var(--text)' }}>
                {col.val != null ? fmtK(col.val) : '—'}
              </div>
              <div style={{ fontSize:10, color:'var(--text3)', marginTop:2 }}>{col.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Charts */}
      <div className="grid-2">
        <div className="card">
          <div className="label" style={{ marginBottom:16 }}>All Accounts Through Age 24</div>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={timeline}>
              <defs>
                <linearGradient id={`g529_${child}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#4f9cf9" stopOpacity={0.3}/><stop offset="95%" stopColor="#4f9cf9" stopOpacity={0}/>
                </linearGradient>
                <linearGradient id={`groth_${child}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#34d399" stopOpacity={0.3}/><stop offset="95%" stopColor="#34d399" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <XAxis dataKey="age" tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={56} />
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={{ fontSize:11 }} />
              <Area type="monotone" dataKey="529"       name="529"       stroke="#4f9cf9" strokeWidth={2} fill={`url(#g529_${child})`} />
              <Area type="monotone" dataKey="roth"      name="Roth IRA"  stroke="#34d399" strokeWidth={2} fill={`url(#groth_${child})`} />
              <Area type="monotone" dataKey="custodial" name="Custodial" stroke="#fbbf24" strokeWidth={2} fill="none" strokeDasharray="4 2" />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <div className="label" style={{ marginBottom:16 }}>Roth IRA Growth to Age 60</div>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={roth_to_60}>
              <XAxis dataKey="age" tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={fmtK} tick={{ fill:'var(--text3)', fontSize:11 }} axisLine={false} tickLine={false} width={56} />
              <Tooltip contentStyle={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, fontSize:12 }} formatter={v => [fmt(v), 'Roth IRA']} />
              <Line type="monotone" dataKey="balance" stroke="#34d399" strokeWidth={2} dot={{ fill:'#34d399', r:3 }} />
            </LineChart>
          </ResponsiveContainer>
          <div style={{ fontSize:11, color:'var(--text3)', marginTop:8 }}>
            {fmt(roth.monthly_contribution)}/mo until 18 · 529 rollover at 22 · 7% growth to 60
          </div>
        </div>
      </div>
    </div>
  )
}

export default function Kids() {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [activeKid, setActiveKid] = useState(0)

  useEffect(() => {
    axios.get('/api/projections/kids')
      .then(r => { setData(r.data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="loading">Projecting futures...</div>

  const kids = data?.kids || []
  if (!kids.length) return (
    <div>
      <h1 className="section-title">Kids</h1>
      <div style={{ padding:'20px', background:'rgba(251,191,36,0.08)', border:'1px solid rgba(251,191,36,0.2)', borderRadius:8, color:'var(--amber)', fontSize:13 }}>
        ⚠ No kids account data found. Import your Quicken Net Worth statement to populate accounts.
      </div>
    </div>
  )

  const hasAccounts = kids.some(k => k['529'].current > 0 || k.roth.current > 0 || k.custodial.current > 0)

  return (
    <div>
      <div style={{ marginBottom:28 }}>
        <h1 className="section-title">Kids</h1>
        <p className="section-sub">All accounts projected at 7% — excluded from your retirement numbers</p>
      </div>
      {!hasAccounts && (
        <div style={{ padding:'12px 16px', background:'rgba(251,191,36,0.08)', border:'1px solid rgba(251,191,36,0.2)', borderRadius:8, marginBottom:20, fontSize:13, color:'var(--amber)' }}>
          ⚠ No account balances found — import from Quicken or add accounts manually. Projections below show contribution growth only from a $0 starting balance.
        </div>
      )}

      <div style={{ display:'flex', gap:8, marginBottom:28 }}>
        {kids.map((k, i) => (
          <button key={k.child}
            className={activeKid===i ? 'btn-primary' : 'btn-secondary'}
            onClick={() => setActiveKid(i)}
          >
            {k.child_name}
          </button>
        ))}
      </div>

      {kids[activeKid] && <KidView kid={kids[activeKid]} />}
    </div>
  )
}
