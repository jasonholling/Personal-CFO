import { useEffect, useState } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt = value => isPrivacyMode() ? MASK_CURRENCY : (value == null ? '—' : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(value))

function GoalCard({ eyebrow, title, status, detail, amount, progress, color, action, onNavigate }) {
  return <div className="card" style={{ display:'flex', flexDirection:'column', minHeight:220 }}>
    <div className="label" style={{ color, marginBottom:7 }}>{eyebrow}</div>
    <div style={{ fontSize:17, fontWeight:650, marginBottom:7 }}>{title}</div>
    <div style={{ color:'var(--text2)', fontSize:13, lineHeight:1.45, minHeight:57 }}>{detail}</div>
    {progress != null && <><div className="progress-bar-track" style={{ height:8, margin:'16px 0 7px' }}><div className="progress-bar-fill" style={{ width:`${Math.max(0, Math.min(100, progress))}%`, background:color }} /></div><div style={{ color:'var(--text3)', fontSize:12 }}>{Math.round(progress)}% funded</div></>}
    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'end', gap:12, marginTop:'auto', paddingTop:14 }}>
      <div style={{ fontSize:18, fontWeight:700, color }}>{amount}</div>
      <button className="btn-secondary" onClick={() => onNavigate(action.destination)} style={{ fontSize:12 }}>{action.label} →</button>
    </div>
    {status && <div style={{ marginTop:10, color, fontSize:12, fontWeight:600 }}>{status}</div>}
  </div>
}

export default function GoalsFunding({ onNavigate }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    Promise.all([
      axios.get('/api/cfo-briefing'), axios.get('/api/projections/retirement'), axios.get('/api/projections/education'), axios.get('/api/debts/recommendation'),
    ]).then(([briefing, retirement, education, debt]) => setData({ briefing:briefing.data, retirement:retirement.data, education:education.data, debt:debt.data }))
      .catch(() => setError('Complete your planning inputs to see the funding map.'))
  }, [])
  if (error) return <div><h1 className="section-title">Goals & Funding</h1><div className="card" style={{ marginTop:24, textAlign:'center', padding:40 }}><div style={{ color:'var(--amber)', marginBottom:14 }}>⚠ {error}</div><button className="btn-primary" onClick={() => onNavigate('settings')}>Set planning inputs →</button></div></div>
  if (!data) return <div className="loading">Building goal-funding map...</div>

  const emergency = data.briefing.emergency_fund || {}
  const cashFlow = data.briefing.cash_flow || {}
  const age60 = data.retirement.scenarios?.find(item => item.label === 'age_60_early')
  const education = data.education.goals || []
  const debt = data.debt
  const emergencyColor = emergency.status === 'funded' ? 'var(--green)' : emergency.status === 'adequate' ? 'var(--amber)' : 'var(--red)'
  const retirementColor = age60?.on_track ? 'var(--green)' : 'var(--amber)'

  return <div>
    <div style={{ marginBottom:28 }}><h1 className="section-title">Goals & Funding</h1><p className="section-sub">See the household’s funding priorities together before assigning monthly surplus.</p></div>
    <div className="card" style={{ marginBottom:24, background:'var(--bg2)' }}>
      <div style={{ display:'flex', justifyContent:'space-between', gap:16, flexWrap:'wrap', alignItems:'center' }}>
        <div><div className="label" style={{ marginBottom:5 }}>MONTHLY CAPACITY</div><div style={{ fontSize:15, fontWeight:600 }}>{cashFlow.has_data ? (cashFlow.monthly_surplus >= 0 ? 'Surplus available to direct' : 'Shortfall to stabilize first') : 'Cash flow not set up yet'}</div></div>
        <div style={{ color:cashFlow.monthly_surplus >= 0 ? 'var(--green)' : 'var(--red)', fontWeight:700, fontSize:24 }}>{cashFlow.has_data ? fmt(cashFlow.monthly_surplus) : '—'}<span style={{ color:'var(--text3)', fontSize:12, fontWeight:400 }}> / mo</span></div>
        <button className="btn-primary" onClick={() => onNavigate('cashflow')}>{cashFlow.has_data ? 'Adjust cash flow' : 'Set up cash flow'} →</button>
      </div>
    </div>
    <div className="grid-2" style={{ marginBottom:16 }}>
      <GoalCard eyebrow="LIQUIDITY" title="Emergency reserve" color={emergencyColor} progress={emergency.has_data ? Math.min(100, (emergency.months_covered / (emergency.target_full_months || 6)) * 100) : 0} status={emergency.has_data ? `${emergency.months_covered} months liquid` : 'Spending input needed'} amount={emergency.has_data ? fmt(emergency.gap_to_full || 0) + ' to 6-mo target' : '—'} detail={emergency.has_data ? 'Protects you from drawing long-term investments or taking on debt when life happens.' : 'Set current monthly expenses to calculate your liquidity runway.'} action={{ destination:'settings', label:'Update inputs' }} onNavigate={onNavigate} />
      <GoalCard eyebrow="DEBT" title="Required debt payoff" color={debt.has_debt ? 'var(--amber)' : 'var(--green)'} status={debt.has_debt ? `${debt.strategy === 'avalanche' ? 'Avalanche' : 'Snowball'} plan selected` : 'No debt entered'} amount={debt.has_debt ? fmt(debt.total_balance) : '✓ Clear'} detail={debt.has_debt ? `${fmt(debt.total_minimum_payment)} in required monthly payments. Payoff strategy belongs ahead of optional investing when rates are high.` : 'Add any debts to compare payoff options against other funding goals.'} action={{ destination:'debt', label:'Review debt' }} onNavigate={onNavigate} />
      <GoalCard eyebrow="RETIREMENT" title="Age-60 retirement plan" color={retirementColor} progress={age60?.percent_funded} status={age60?.on_track ? 'On track under current assumptions' : 'Needs a funding decision'} amount={age60 ? `${age60.percent_funded}% funded` : '—'} detail={age60 ? `Projected portfolio: ${fmt(age60.portfolio_at_retirement)}. This uses the same assumptions as the retirement projection.` : 'Set retirement inputs to calculate this goal.'} action={{ destination:'retirement', label:'Test scenarios' }} onNavigate={onNavigate} />
      {education.map(goal => <GoalCard key={goal.child} eyebrow="EDUCATION" title={`${goal.child_name}'s education`} color={goal.funding_percent >= 100 ? 'var(--green)' : 'var(--amber)'} progress={goal.funding_percent} status={goal.funding_gap > 0 ? 'Funding gap to address' : 'Projected to cover four years'} amount={goal.funding_gap > 0 ? fmt(goal.monthly_savings_to_close_gap) + ' / mo needed' : `${goal.funding_percent}% funded`} detail={`College begins in ${goal.years_to_college} years. The plan uses the existing year-by-year 529 calculation.`} action={{ destination:'education', label:'View options' }} onNavigate={onNavigate} />)}
    </div>
    <div className="card"><div className="label" style={{ marginBottom:8 }}>HOW TO USE THIS MAP</div><div style={{ color:'var(--text2)', fontSize:13, lineHeight:1.55 }}>Treat this as a sequencing tool, not an automated recommendation: protect liquidity, make required debt payments, then choose how to direct the remaining monthly capacity among retirement, education, and other goals. Use each goal’s detailed page to test assumptions before changing contributions.</div></div>
  </div>
}
