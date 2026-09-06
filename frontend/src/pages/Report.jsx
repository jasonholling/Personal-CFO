import { useState, useEffect } from 'react'
import axios from 'axios'
import {
  BarChart, Bar, AreaChart, Area, PieChart, Pie, Cell,
  XAxis, YAxis, Tooltip, Legend, ResponsiveContainer
} from 'recharts'
import { usePersonNames } from '../hooks/usePersonNames'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const NAVY = '#5C7CE0' // was #1B3A6B — nearly the same luminance as the dark card background, effectively invisible

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(n))
const fmtK = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : Math.abs(n)>=1000000 ? `$${(n/1000000).toFixed(2)}M` : `$${(n/1000).toFixed(0)}K`)

// ── Stoplight ─────────────────────────────────────────────────────────────────
const Stoplight = ({ status, large }) => {
  const map = { 'ON TRACK':'var(--green)', 'ATTENTION':'var(--amber)', 'NEEDS REVIEW':'var(--red)' }
  const color = map[status] || 'var(--text3)'
  return (
    <div style={{ display:'flex', alignItems:'center', gap:6 }}>
      <div style={{ width: large?12:8, height:large?12:8, borderRadius:'50%', background:color, boxShadow:`0 0 ${large?8:4}px ${color}` }} />
      <span style={{ fontSize: large?13:11, fontWeight:600, color, letterSpacing:'0.04em' }}>{status}</span>
    </div>
  )
}

// ── Section header ────────────────────────────────────────────────────────────
const SectionHead = ({ title, status, icon }) => (
  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:20, paddingBottom:12, borderBottom:'1px solid var(--border)' }}>
    <div style={{ display:'flex', alignItems:'center', gap:10 }}>
      <span style={{ fontSize:20, opacity:0.6 }}>{icon}</span>
      <h2 style={{ fontFamily:'var(--font-display)', fontSize:20, margin:0 }}>{title}</h2>
    </div>
    <Stoplight status={status} large />
  </div>
)

// ── Metric card ───────────────────────────────────────────────────────────────
const Metric = ({ label, value, sub, color='var(--accent)', size='normal' }) => (
  <div className="card" style={{ textAlign:'center', padding:'16px 12px' }}>
    <div style={{ fontSize:10, fontWeight:700, letterSpacing:'0.08em', textTransform:'uppercase', color:'var(--text3)', marginBottom:8 }}>{label}</div>
    <div style={{ fontSize: size==='large'?28:20, fontWeight:700, color, fontFamily:'var(--font-display)', lineHeight:1.1 }}>{value}</div>
    {sub && <div style={{ fontSize:10, color:'var(--text3)', marginTop:6, lineHeight:1.4 }}>{sub}</div>}
  </div>
)

// ── Data row ──────────────────────────────────────────────────────────────────
const Row = ({ label, value, bold, color='var(--text)', indent }) => (
  <div style={{ display:'flex', justifyContent:'space-between', padding:'7px 0', borderBottom:'1px solid var(--border)', fontSize:13 }}>
    <span style={{ color:'var(--text2)', fontWeight:bold?600:400, paddingLeft:indent?16:0 }}>{label}</span>
    <span style={{ fontWeight:bold?700:500, color }}>{value}</span>
  </div>
)

// ── Tooltip ───────────────────────────────────────────────────────────────────
const ChartTip = ({ active, payload, label }) => {
  if (!active||!payload?.length) return null
  return (
    <div style={{ background:'var(--bg3)', border:'1px solid var(--border2)', borderRadius:8, padding:'10px 14px', fontSize:11 }}>
      <div style={{ fontWeight:600, marginBottom:6, color:'var(--text)' }}>Age {label}</div>
      {payload.map(p => p.value > 0 && (
        <div key={p.name} style={{ color:p.color, marginBottom:2 }}>{p.name}: {fmt(p.value)}</div>
      ))}
    </div>
  )
}

// ── Progress bar ──────────────────────────────────────────────────────────────
const ProgressBar = ({ value, max, color='var(--accent)', label, sublabel }) => (
  <div style={{ marginBottom:12 }}>
    <div style={{ display:'flex', justifyContent:'space-between', fontSize:12, marginBottom:4 }}>
      <span style={{ color:'var(--text2)' }}>{label}</span>
      <span style={{ fontWeight:600, color }}>{sublabel}</span>
    </div>
    <div style={{ height:6, background:'var(--bg3)', borderRadius:3, overflow:'hidden' }}>
      <div style={{ height:'100%', width:`${Math.min(100,(value/max*100)).toFixed(1)}%`, background:color, borderRadius:3, transition:'width 0.6s ease' }} />
    </div>
  </div>
)

// ── Donut chart ───────────────────────────────────────────────────────────────
const Donut = ({ pct, color, size=90 }) => (
  <div style={{ position:'relative', width:size, height:size, flexShrink:0 }}>
    <PieChart width={size} height={size}>
      <Pie data={[{v:pct},{v:100-pct}]} cx={size/2-2} cy={size/2-2}
        innerRadius={size*0.3} outerRadius={size*0.44}
        startAngle={90} endAngle={-270} dataKey="v">
        <Cell fill={color} />
        <Cell fill="var(--bg3)" />
      </Pie>
    </PieChart>
    <div style={{ position:'absolute', top:'50%', left:'50%', transform:'translate(-50%,-50%)', fontSize:size*0.18, fontWeight:700, color }}>{pct}%</div>
  </div>
)

export default function Report() {
  const { person1Name, person2Name } = usePersonNames()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      axios.get('/api/net-worth'),
      axios.get('/api/projections/retirement'),
      axios.get('/api/projections/education'),
      axios.get('/api/projections/kids'),
      axios.get('/api/projections/insurance'),
    ]).then(([nw, ret, edu, kids, ins]) => {
      const scenario = ret.data.scenarios?.find(s => s.label==='age_60_early')
      setData({ nw:nw.data, ret:scenario, edu:edu.data.goals, kids:kids.data.kids, ins:ins.data })
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="loading">Generating report...</div>
  if (!data)   return <div className="loading">Error loading report</div>

  const { nw, ret, edu, kids, ins } = data
  const jason_ins  = ins?.jason
  const justin_ins = ins?.justin

  const sections = [
    { name:'Investments',            icon:'⊞', status: nw?.investment>0?'ON TRACK':'ATTENTION' },
    { name:'Financial Independence', icon:'◎', status: ret?.on_track?'ON TRACK':'NEEDS REVIEW' },
    { name:'Education',              icon:'◇', status: edu?.every(g=>g.funding_percent>=85)?'ON TRACK':'ATTENTION' },
    { name:'Risk Management',        icon:'⊕', status: (jason_ins?.on_track&&justin_ins?.on_track)?'ON TRACK':'ATTENTION' },
    { name:'Estate Planning',        icon:'⊙', status: 'ATTENTION' },
  ]

  const retChart = ret?.yearly_detail?.filter((_,i)=>i%2===0).map(y=>({
    age: y.jason_age,
    'Pension':        y.pension,
    'Social Security':y.social_security,
    'RMDs':           y.rmd_reinvested>0?y.rmd_reinvested:0,
    'Withdrawals':    y.withdrawal,
  })) || []

  const burnChart = ret?.yearly_detail?.filter((_,i)=>i%2===0).map(y=>({
    age:    y.jason_age,
    taxable:y.taxable_balance,
    pretax: y.pretax_balance,
    roth:   y.roth_balance,
  })) || []

  const investCats = [
    { label:'Investment Accounts', key:'investment',  color:'#4f9cf9' },
    { label:'Real Estate',         key:'real_estate', color:'#fbbf24' },
    { label:'Cash & Savings',      key:'savings',     color:'#34d399' },
    { label:'Business',            key:'business',    color:'#f97316' },
    { label:'Other',               key:'other',       color:'#8b8fa8' },
  ].filter(c=>(nw[c.key]||0)>0)

  return (
    <div>
      {/* ── Cover ── */}
      <div style={{ marginBottom:32 }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:8 }}>
          <div>
            <h1 className="section-title">Annual Financial Dashboard</h1>
            <p className="section-sub">{person1Name} &amp; {person2Name} &nbsp;·&nbsp; {new Date().toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'})}</p>
          </div>
        </div>

        {/* Status grid */}
        <div style={{ display:'grid', gridTemplateColumns:'repeat(5,1fr)', gap:10, marginTop:20 }}>
          {sections.map(s => {
            const colorMap = { 'ON TRACK':'var(--green)', 'ATTENTION':'var(--amber)', 'NEEDS REVIEW':'var(--red)' }
            const color = colorMap[s.status]
            return (
              <div key={s.name} className="card" style={{ padding:'14px 16px', borderLeft:`3px solid ${color}` }}>
                <div style={{ display:'flex', justify:'space-between', alignItems:'center', gap:6, marginBottom:8 }}>
                  <span style={{ fontSize:16 }}>{s.icon}</span>
                </div>
                <div style={{ fontSize:12, color:'var(--text2)', marginBottom:6, lineHeight:1.3 }}>{s.name}</div>
                <Stoplight status={s.status} />
              </div>
            )
          })}
        </div>
      </div>

      {/* ── Investments ── */}
      <div style={{ marginBottom:36 }}>
        <SectionHead title="Investments" status={sections[0].status} icon="⊞" />
        <div className="grid-4" style={{ marginBottom:16 }}>
          <Metric label="Net Worth"         value={fmtK(nw?.net_worth)}     color="var(--green)" size="large" />
          <Metric label="Investment Accounts" value={fmtK(nw?.investment)}  color="var(--accent)" />
          <Metric label="Real Estate"        value={fmtK(nw?.real_estate)}  color="var(--amber)" />
          <Metric label="Total Assets"       value={fmtK(nw?.total_assets)} color="var(--text)" />
        </div>
        <div className="card">
          <div className="label" style={{ marginBottom:16 }}>Asset Allocation</div>
          {investCats.map(c => (
            <ProgressBar key={c.key} label={c.label} value={nw[c.key]||0} max={nw.total_assets}
              color={c.color} sublabel={`${fmt(nw[c.key]||0)} (${((nw[c.key]||0)/nw.total_assets*100).toFixed(0)}%)`} />
          ))}
          <div style={{ display:'flex', justifyContent:'space-between', paddingTop:10, borderTop:'1px solid var(--border)', fontSize:13 }}>
            <span style={{ color:'var(--text3)' }}>Total Liabilities</span>
            <span style={{ color:'var(--red)', fontWeight:600 }}>{fmt(nw?.liabilities)}</span>
          </div>
        </div>
      </div>

      {/* ── Financial Independence ── */}
      <div style={{ marginBottom:36 }}>
        <SectionHead title="Financial Independence" status={sections[1].status} icon="◎" />
        <div className="grid-4" style={{ marginBottom:16 }}>
          <Metric label="Portfolio at 60"    value={fmtK(ret?.portfolio_at_retirement)} color="var(--accent)" sub="Retire age 60 · SS at 62" />
          <Metric label="Pension / yr"       value={fmt(ret?.pension_annual)}           color="var(--green)"  sub={`${fmt(ret?.pension_annual ? ret.pension_annual/12 : 0)}/mo · 100% J&S`} />
          <Metric label="Combined SS / yr"   value={fmt((ret?.jason_ss_annual||0)+(ret?.justin_ss_annual||0))} color="var(--green)" sub={`${person1Name} at 62 + ${person2Name} spousal at ${ret?.justin_ss_start_age ?? 67}`} />
          <Metric label="Projected Surplus"  value={fmtK(ret?.projected_surplus)}      color={ret?.projected_surplus>=0?'var(--green)':'var(--red)'} sub={ret?.on_track?'✓ On Track':'⚠ Shortfall'} />
        </div>
        {/* Healthcare callout */}
        <div style={{ padding:'12px 16px', background:'rgba(79,156,249,0.06)', border:'1px solid rgba(79,156,249,0.15)', borderRadius:8, marginBottom:16, fontSize:12 }}>
          <div style={{ fontWeight:600, marginBottom:6, color:'var(--text)' }}>🏥 Healthcare Costs Modeled in Projection</div>
          <div style={{ display:'grid', gridTemplateColumns:'1fr auto', gap:'3px 20px', color:'var(--text2)' }}>
            <span>Pre-Medicare (age 60–65) · ACA marketplace · 5-year gap</span>
            <span style={{ fontWeight:600, color:'var(--accent)', textAlign:'right' }}>{fmt(ret?.healthcare_pre_annual)}/yr · {fmt(ret?.healthcare_gap_total)} total</span>
            <span>Post-Medicare (age 65+) · Medicare + supplement</span>
            <span style={{ fontWeight:600, color:'var(--accent)', textAlign:'right' }}>{fmt(ret?.healthcare_post_annual)}/yr</span>
          </div>
          <div style={{ fontSize:10, color:'var(--text3)', marginTop:6 }}>Included in income need · adjust in Planning Inputs → Healthcare</div>
        </div>

        <div className="grid-2" style={{ marginBottom:16 }}>
          <div className="card">
            <div className="label" style={{ marginBottom:16 }}>Income Sources in Retirement</div>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={retChart} margin={{top:0,right:0,bottom:0,left:0}}>
                <XAxis dataKey="age" tick={{fill:'var(--text3)',fontSize:10}} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={fmtK} tick={{fill:'var(--text3)',fontSize:10}} axisLine={false} tickLine={false} width={50} />
                <Tooltip content={<ChartTip />} />
                <Legend wrapperStyle={{fontSize:10}} />
                <Bar dataKey="Pension"         stackId="a" fill={NAVY} />
                <Bar dataKey="Social Security"  stackId="a" fill="#2E7D8C" />
                <Bar dataKey="RMDs"             stackId="a" fill="#C9A84C" />
                <Bar dataKey="Withdrawals"      stackId="a" fill="#C45C1A" />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="card">
            <div className="label" style={{ marginBottom:16 }}>Portfolio Burndown by Bucket</div>
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={burnChart} margin={{top:0,right:0,bottom:0,left:0}}>
                <defs>
                  {[['t','#fbbf24'],['p','#4f9cf9'],['r','#34d399']].map(([id,color])=>(
                    <linearGradient key={id} id={`g${id}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor={color} stopOpacity={0.4}/>
                      <stop offset="95%" stopColor={color} stopOpacity={0.05}/>
                    </linearGradient>
                  ))}
                </defs>
                <XAxis dataKey="age" tick={{fill:'var(--text3)',fontSize:10}} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={fmtK} tick={{fill:'var(--text3)',fontSize:10}} axisLine={false} tickLine={false} width={50} />
                <Tooltip content={<ChartTip />} />
                <Legend wrapperStyle={{fontSize:10}} />
                <Area type="monotone" dataKey="taxable" name="Taxable"  stackId="a" stroke="#fbbf24" fill="url(#gt)" />
                <Area type="monotone" dataKey="pretax"  name="Pre-Tax"  stackId="a" stroke="#4f9cf9" fill="url(#gp)" />
                <Area type="monotone" dataKey="roth"    name="Roth"     stackId="a" stroke="#34d399" fill="url(#gr)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="card">
          <div className="label" style={{ marginBottom:12 }}>Year-by-Year Cash Flow</div>
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%', borderCollapse:'collapse', fontSize:11 }}>
              <thead>
                <tr>
                  {[person1Name,person2Name,'Year','Income Need','Pension','SS','Withdrawal','Balance'].map(h=>(
                    <th key={h} style={{ padding:'6px 8px', textAlign:'right', color:'var(--text3)', fontWeight:600, fontSize:10, textTransform:'uppercase', borderBottom:'2px solid var(--border)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ret?.yearly_detail?.filter((_,i)=>i%2===0).map((y,i)=>(
                  <tr key={y.jason_age} style={{ background:i%2===0?'transparent':'var(--bg3)' }}>
                    <td style={{ padding:'5px 8px', textAlign:'right' }}>{y.jason_age}</td>
                    <td style={{ padding:'5px 8px', textAlign:'right' }}>{y.justin_age}</td>
                    <td style={{ padding:'5px 8px', textAlign:'right', color:'var(--text3)' }}>{y.year}</td>
                    <td style={{ padding:'5px 8px', textAlign:'right' }}>{fmt(y.income_need)}</td>
                    <td style={{ padding:'5px 8px', textAlign:'right', color:NAVY }}>{fmt(y.pension)}</td>
                    <td style={{ padding:'5px 8px', textAlign:'right', color:'#2E7D8C' }}>{fmt(y.social_security)}</td>
                    <td style={{ padding:'5px 8px', textAlign:'right', color:'var(--amber)' }}>{fmt(y.withdrawal)}</td>
                    <td style={{ padding:'5px 8px', textAlign:'right', fontWeight:600, color:y.portfolio_balance>0?'var(--green)':'var(--red)' }}>{fmt(y.portfolio_balance)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* ── Education ── */}
      <div style={{ marginBottom:36 }}>
        <SectionHead title="Education Planning" status={sections[2].status} icon="◇" />
        <div className="grid-2" style={{ marginBottom:16 }}>
          {edu?.map(g => {
            const pct = g.funding_percent
            // funding_percent/funding_gap are computed from the real
            // drawdown simulation (does the account actually run dry
            // during college), not a snapshot against an arbitrary
            // target — 100% means no real shortfall, full stop. Keeping
            // this in sync with Education.jsx's own framing. gap>0 and
            // depleted_during_college are always equal by construction
            // (see run_education_projection), so this is just two states.
            const color = g.depleted_during_college ? 'var(--red)' : 'var(--green)'
            return (
              <div key={g.child} className="card">
                <div style={{ display:'flex', alignItems:'center', gap:16, marginBottom:16 }}>
                  <Donut pct={pct} color={color} size={80} />
                  <div>
                    <div style={{ fontFamily:'var(--font-display)', fontSize:20, marginBottom:4 }}>{g.child_name}</div>
                    <div style={{ fontSize:12, color:'var(--text3)' }}>{g.years_to_college} years to college · UNL</div>
                    <div style={{ fontSize:11, color, fontWeight:600, marginTop:4 }}>
                      {g.depleted_during_college ? '⚠ Projected to run out during college' : '✓ No real shortfall projected'}
                    </div>
                  </div>
                </div>
                <ProgressBar label="529 Today" value={g.current_529_balance} max={g.projected_total_cost}
                  color="var(--text3)" sublabel={fmt(g.current_529_balance)} />
                <ProgressBar label="Projected at 18" value={g.projected_529_at_college} max={g.projected_total_cost}
                  color={color} sublabel={`${fmt(g.projected_529_at_college)} of ${fmt(g.projected_total_cost)}`} />
                <div style={{ fontSize:11, color:'var(--text3)', marginTop:8 }}>
                  {fmt(g.monthly_contribution)}/mo contribution
                  {g.funding_gap>0 && <span style={{ color:'var(--amber)', marginLeft:8 }}>· {fmt(g.monthly_savings_to_close_gap)}/mo to close gap</span>}
                </div>
              </div>
            )
          })}
        </div>
        {kids?.length>0 && (
          <div className="card">
            <div className="label" style={{ marginBottom:12 }}>Kids Roth IRA Projections</div>
            <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:0, fontSize:11 }}>
              <div style={{ padding:'6px 8px', color:'var(--text3)', fontWeight:600, fontSize:10, textTransform:'uppercase', borderBottom:'2px solid var(--border)' }}>Child</div>
              {['Roth Today','At 18','Roth at 60'].map(h=>(
                <div key={h} style={{ padding:'6px 8px', textAlign:'right', color:'var(--text3)', fontWeight:600, fontSize:10, textTransform:'uppercase', borderBottom:'2px solid var(--border)' }}>{h}</div>
              ))}
              {kids.map((k,i)=>(
                <>
                  <div key={k.child+'n'} style={{ padding:'8px 8px', fontWeight:600, background:i%2?'var(--bg3)':'transparent', borderBottom:'1px solid var(--border)' }}>{k.child_name}</div>
                  <div key={k.child+'c'} style={{ padding:'8px 8px', textAlign:'right', background:i%2?'var(--bg3)':'transparent', borderBottom:'1px solid var(--border)' }}>{fmt(k.roth.current)}</div>
                  <div key={k.child+'18'} style={{ padding:'8px 8px', textAlign:'right', background:i%2?'var(--bg3)':'transparent', borderBottom:'1px solid var(--border)' }}>{fmt(k.roth.at_18)}</div>
                  <div key={k.child+'60'} style={{ padding:'8px 8px', textAlign:'right', fontWeight:700, color:'var(--green)', background:i%2?'var(--bg3)':'transparent', borderBottom:'1px solid var(--border)' }}>{fmt(k.roth.at_60)}</div>
                </>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── Risk Management ── */}
      <div style={{ marginBottom:36 }}>
        <SectionHead title="Risk Management" status={sections[3].status} icon="⊕" />
        <div className="grid-2" style={{ marginBottom:16 }}>
          {[
            { who:person1Name, d:jason_ins,  note:`Debt + college + ${person2Name} income replacement` },
            { who:person2Name, d:justin_ins, note:`Debt + college only — ${person1Name} keeps earning` },
          ].map(({who,d,note}) => {
            if (!d) return null
            const pct  = Math.min(100, Math.round(d.current_coverage/d.total_need*100))
            const color = d.on_track?'var(--green)':'var(--red)'
            return (
              <div key={who} className="card">
                <div style={{ display:'flex', alignItems:'center', gap:16, marginBottom:16 }}>
                  <Donut pct={pct} color={color} size={80} />
                  <div>
                    <div style={{ fontFamily:'var(--font-display)', fontSize:16, marginBottom:4 }}>Event of {who}'s Death</div>
                    <div style={{ fontSize:11, color:'var(--text3)', marginBottom:4 }}>{note}</div>
                    <Stoplight status={d.on_track?'ON TRACK':'ATTENTION'} />
                  </div>
                </div>
                <Row label="Total Need"       value={fmt(d.total_need)}       bold />
                <Row label="Current Coverage" value={fmt(d.current_coverage)} bold />
                <Row label={d.surplus_gap>=0?'Surplus':'Gap'}
                  value={fmt(Math.abs(d.surplus_gap))}
                  color={d.surplus_gap>=0?'var(--green)':'var(--red)'} bold />
              </div>
            )
          })}
        </div>
        <div className="grid-2">
          <div className="card">
            <div className="label" style={{ marginBottom:12 }}>Disability</div>
            <Row label="Group Policy" value={isPrivacyMode() ? `${MASK_CURRENCY}/mo` : `$${ins?.disability?.monthly_benefit?.toLocaleString()}/mo`} />
            <Row label="To Age"               value={ins?.disability?.to_age} />
            <Row label="Funded By"            value="Employer paid" />
          </div>
          <div className="card">
            <div className="label" style={{ marginBottom:12 }}>Long Term Care &amp; Property</div>
            <Row label="LTC Daily Benefit"  value={isPrivacyMode() ? MASK_CURRENCY : `$${ins?.ltc?.daily_benefit}/day`} />
            <Row label="LTC Maximum"         value={fmt(ins?.ltc?.max_benefit)} />
            <Row label="Umbrella Policy"     value={fmt(ins?.property?.umbrella)}
              color={ins?.property?.umbrella_adequate ? 'var(--green)' : 'var(--amber)'} bold />
            <Row label="Recommended (≈ net worth)" value={fmt(ins?.property?.recommended_umbrella)} indent />
            {ins?.property && !ins.property.umbrella_adequate && (
              <Row label="Gap" value={fmt(ins.property.umbrella_gap)} color="var(--amber)" indent />
            )}
          </div>
        </div>
      </div>

      {/* ── Estate ── */}
      <div style={{ marginBottom:36 }}>
        <SectionHead title="Estate Planning" status={sections[4].status} icon="⊙" />
        <div style={{ padding:'12px 16px', background:'rgba(251,191,36,0.08)', border:'1px solid rgba(251,191,36,0.2)', borderRadius:8, marginBottom:16, fontSize:13, color:'var(--amber)', fontWeight:500 }}>
          ⚠ Review document dates on the Estate Planning page periodically.
        </div>
        <div className="grid-2">
          <div className="card">
            <div className="label" style={{ marginBottom:12 }}>Documents</div>
            {['Joint Revocable Living Trust',`Wills (${person1Name} & ${person2Name})`,'Financial POA','Health Care POA'].map(d=>(
              <Row key={d} label={d} value="See Estate Planning" />
            ))}
          </div>
          <div className="card">
            <div className="label" style={{ marginBottom:12 }}>Action Items</div>
            {[
              'Review and update estate documents',
              'Verify beneficiary designations',
              'Confirm credit freeze — all 3 bureaus',
              'Verify accounts titled in trust',
            ].map(item=>(
              <div key={item} style={{ padding:'7px 0', borderBottom:'1px solid var(--border)', fontSize:12, color:'var(--text2)', display:'flex', alignItems:'flex-start', gap:8 }}>
                <span style={{ color:'var(--amber)', flexShrink:0 }}>→</span>
                {item}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div style={{ fontSize:10, color:'var(--text3)', fontStyle:'italic', textAlign:'center', paddingTop:16, borderTop:'1px solid var(--border)' }}>
        Projected values are for planning purposes only and are not a promise of future performance. Personal CFO · {new Date().toLocaleDateString()}
      </div>
    </div>
  )
}
