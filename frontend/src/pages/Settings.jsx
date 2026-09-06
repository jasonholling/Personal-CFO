import { useState, useEffect } from 'react'
import axios from 'axios'
import { usePrivacyMode } from '../hooks/usePrivacyMode'

const Section = ({ title, children }) => (
  <div className="card" style={{ marginBottom:20 }}>
    <div style={{ fontWeight:700, fontSize:14, marginBottom:16, paddingBottom:10, borderBottom:'1px solid var(--border)' }}>{title}</div>
    {children}
  </div>
)

const Row = ({ label, hint, children }) => (
  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'8px 0', borderBottom:'1px solid var(--border)' }}>
    <div>
      <div style={{ fontSize:13 }}>{label}</div>
      {hint && <div style={{ fontSize:11, color:'var(--text3)', marginTop:1 }}>{hint}</div>}
    </div>
    <div>{children}</div>
  </div>
)

const NumInput = ({ value, onChange, prefix='', suffix='', pct=false, style={} }) => (
  <div style={{ display:'flex', alignItems:'center', gap:4 }}>
    {prefix && <span style={{ fontSize:12, color:'var(--text3)' }}>{prefix}</span>}
    <input
      type="number"
      value={pct ? +(value*100).toFixed(2) : value}
      onChange={e => onChange(pct ? parseFloat(e.target.value)/100 : parseFloat(e.target.value)||0)}
      style={{ width:100, textAlign:'right', ...style }}
      step={pct ? 0.1 : 1}
    />
    {suffix && <span style={{ fontSize:12, color:'var(--text3)' }}>{suffix}</span>}
  </div>
)

const TextInput = ({ value, onChange, style={} }) => (
  <input type="text" value={value ?? ''} onChange={e => onChange(e.target.value)} style={{ width:160, textAlign:'right', ...style }} />
)

export default function Settings() {
  const { privacyMode } = usePrivacyMode()
  const [form, setForm] = useState(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    axios.get('/api/planning-inputs').then(r => setForm(r.data))
  }, [])

  const set = (key, val) => setForm(f => ({ ...f, [key]: val }))

  const save = () => {
    axios.put('/api/planning-inputs', form)
      .then(() => { setSaved(true); setTimeout(() => setSaved(false), 2500) })
  }

  if (!form) return <div className="loading">Loading...</div>

  const pretax_pct = form.pretax_401k_pct ?? 0.75
  const roth_pct   = 1 - pretax_pct
  const p1 = form.person1_name || 'Person 1'
  const p2 = form.person2_name || 'Person 2'
  const k1 = form.kid1_name || 'Child 1'
  const k2 = form.kid2_name || 'Child 2'

  return (
    <div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:28 }}>
        <div>
          <h1 className="section-title">Planning Inputs</h1>
          <p className="section-sub">Update when your situation changes — drives all projections</p>
        </div>
        <button className="btn-primary" onClick={save} style={{ minWidth:100 }}>
          {saved ? '✓ Saved' : 'Save'}
        </button>
      </div>

      {privacyMode && (
        <div className="card" style={{ marginBottom:20, borderLeft:'3px solid var(--accent)', textAlign:'center', padding:'20px' }}>
          <div style={{ fontSize:22, marginBottom:8 }}>👁</div>
          <div style={{ fontWeight:600, marginBottom:4 }}>Privacy mode is on</div>
          <div style={{ fontSize:12, color:'var(--text2)' }}>
            This page shows editable fields with your real numbers, which can't be masked while still letting you edit them —
            it's blurred and locked instead. Turn off privacy mode (top-left eye icon) to view or edit Planning Inputs.
          </div>
        </div>
      )}

      <div style={privacyMode ? { filter:'blur(6px)', pointerEvents:'none', userSelect:'none' } : undefined}>
      <Section title="Names">
        <Row label="Person 1 Name" hint="Shown throughout the app instead of a generic label"><TextInput value={form.person1_name} onChange={v => set('person1_name', v)} /></Row>
        <Row label="Person 2 Name"><TextInput value={form.person2_name} onChange={v => set('person2_name', v)} /></Row>
        <Row label="Child 1 Name"><TextInput value={form.kid1_name} onChange={v => set('kid1_name', v)} /></Row>
        <Row label="Child 2 Name"><TextInput value={form.kid2_name} onChange={v => set('kid2_name', v)} /></Row>
      </Section>

      <Section title="Ages">
        <Row label={`${p1}'s Current Age`}><NumInput value={form.jason_age} onChange={v => set('jason_age', v)} /></Row>
        <Row label={`${p2}'s Current Age`}><NumInput value={form.justin_age} onChange={v => set('justin_age', v)} /></Row>
        <Row label={`${k1}'s Current Age`}><NumInput value={form.kid1_age ?? 0} onChange={v => set('kid1_age', v)} /></Row>
        <Row label={`${k2}'s Current Age`}><NumInput value={form.kid2_age ?? 0} onChange={v => set('kid2_age', v)} /></Row>
      </Section>

      <Section title="Return & Inflation Assumptions">
        <Row label="Inflation Rate"><NumInput value={form.inflation_rate} onChange={v => set('inflation_rate', v)} pct suffix="%" /></Row>
        <Row label="Pre-Retirement Return" hint="Expected portfolio growth until retirement"><NumInput value={form.expected_return_pre_retirement} onChange={v => set('expected_return_pre_retirement', v)} pct suffix="%" /></Row>
        <Row label="Post-Retirement Return" hint="Expected portfolio growth during retirement"><NumInput value={form.expected_return_post_retirement} onChange={v => set('expected_return_post_retirement', v)} pct suffix="%" /></Row>
      </Section>

      <Section title="Retirement Income Goal">
        <Row label="Annual Income Goal (today's $)" hint="What you want to spend per year in retirement"><NumInput value={form.retirement_income_today_dollars} onChange={v => set('retirement_income_today_dollars', v)} prefix="$" /></Row>
      </Section>

      <Section title="Current Spending">
        <Row label="Current Monthly Expenses" hint="Actual current spending — used for the Emergency Fund check, separate from your retirement income goal above"><NumInput value={form.current_monthly_expenses ?? 0} onChange={v => set('current_monthly_expenses', v)} prefix="$" suffix="/mo" /></Row>
      </Section>

      <Section title="Income">
        <Row label="W2 Base Salary" hint="Used for 401k contribution calculations"><NumInput value={form.w2_salary ?? 0} onChange={v => set('w2_salary', v)} prefix="$" /></Row>
      </Section>

      <Section title="401k">
        <Row label="Pre-Tax % of Total 401k Balance" hint={`Balance from Quicken × this % = pre-tax bucket. Roth = ${(roth_pct*100).toFixed(1)}%`}>
          <NumInput value={pretax_pct} onChange={v => set('pretax_401k_pct', Math.min(1, Math.max(0, v)))} pct suffix="%" />
        </Row>
        <Row label="Roth % (calculated)" hint="Auto = 100% minus pre-tax %">
          <span style={{ fontSize:13, fontWeight:600, color:'var(--accent)' }}>{(roth_pct*100).toFixed(1)}%</span>
        </Row>
        <Row label="Your Contribution Rate" hint="Employee % → goes to Roth 401k">
          <NumInput value={form.employee_401k_pct ?? 0.06} onChange={v => set('employee_401k_pct', v)} pct suffix="%" />
        </Row>
        <Row label="Employer Contribution Rate" hint="Match + non-elective → goes pre-tax">
          <NumInput value={form.employer_401k_pct ?? 0.03} onChange={v => set('employer_401k_pct', v)} pct suffix="%" />
        </Row>
        <Row label="Total Annual 401k" hint="Calculated from salary × rates">
          <span style={{ fontSize:13, color:'var(--text2)' }}>
            ~${(((form.employee_401k_pct ?? 0.06) + (form.employer_401k_pct ?? 0.03)) * (form.w2_salary ?? 0)).toLocaleString('en-US', {maximumFractionDigits:0})}/yr
          </span>
        </Row>
        <Row label="Annual HSA Contribution"><NumInput value={form.annual_hsa_contribution} onChange={v => set('annual_hsa_contribution', v)} prefix="$" /></Row>
        <Row label="Annual RSU Value (gross)" hint="0 if none this year"><NumInput value={form.annual_rsu_value} onChange={v => set('annual_rsu_value', v)} prefix="$" /></Row>
      </Section>

      <Section title="Social Security">
        <Row label={`${p1} SS at 62 (early)`}><NumInput value={form.jason_social_security} onChange={v => set('jason_social_security', v)} prefix="$" suffix="/yr" /></Row>
        <Row label={`${p1} SS at 67 (delayed)`}><NumInput value={form.jason_ss_delayed ?? 0} onChange={v => set('jason_ss_delayed', v)} prefix="$" suffix="/yr" /></Row>
        <Row label={`${p2} Spousal SS at 67`} hint={`50% of ${p1}'s FRA benefit`}>
          <NumInput value={form.justin_social_security ?? 0} onChange={v => set('justin_social_security', v)} prefix="$" suffix="/yr" />
        </Row>
      </Section>

      <Section title="Kids — 529 Contributions">
        <Row label={`${k1} 529 Monthly`}><NumInput value={form.abby_529_monthly ?? 0} onChange={v => set('abby_529_monthly', v)} prefix="$" suffix="/mo" /></Row>
        <Row label={`${k2} 529 Monthly`}><NumInput value={form.cooper_529_monthly ?? 0} onChange={v => set('cooper_529_monthly', v)} prefix="$" suffix="/mo" /></Row>
        <Row label="Annual Tuition &amp; Fees" hint="Current cost — update each fall · inflates at 5%/yr in projections"><NumInput value={form.unl_annual_cost ?? 0} onChange={v => set('unl_annual_cost', v)} prefix="$" suffix="/yr" /></Row>
      </Section>

      <Section title="Kids — Roth IRA & Custodial">
        <Row label="Kids Roth IRA Monthly" hint="Per kid — from your paycheck"><NumInput value={form.kids_roth_monthly ?? 0} onChange={v => set('kids_roth_monthly', v)} prefix="$" suffix="/mo each" /></Row>
        <Row label="Kids Custodial Monthly"><NumInput value={form.kids_custodial_monthly ?? 0} onChange={v => set('kids_custodial_monthly', v)} prefix="$" suffix="/mo each" /></Row>
      </Section>

      <Section title="Pension (100% Joint &amp; Survivor)">
        <Row label="Pension at Age 55" hint="Monthly × 12"><NumInput value={form.pension_55 ?? 0} onChange={v => set('pension_55', v)} prefix="$" suffix="/yr" /></Row>
        <Row label="Pension at Age 60" hint="Monthly × 12"><NumInput value={form.pension_60 ?? 0} onChange={v => set('pension_60', v)} prefix="$" suffix="/yr" /></Row>
        <Row label="Pension at Age 65" hint="Monthly × 12"><NumInput value={form.pension_65 ?? 0} onChange={v => set('pension_65', v)} prefix="$" suffix="/yr" /></Row>
      </Section>

      <Section title="Life Insurance Coverage">
        <p style={{ fontSize:12, color:'var(--text3)', marginTop:-8, marginBottom:12 }}>
          Detailed per-policy tracking (insurer, policy #, premium) lives on the Risk Management page. These totals feed the insurance-gap calculations here.
        </p>
        <Row label={`${p1} — Basic coverage`}><NumInput value={form.jason_life_basic ?? 0} onChange={v => set('jason_life_basic', v)} prefix="$" /></Row>
        <Row label={`${p1} — Supplemental coverage`}><NumInput value={form.jason_life_supplemental ?? 0} onChange={v => set('jason_life_supplemental', v)} prefix="$" /></Row>
        <Row label={`${p1} — Term coverage`}><NumInput value={form.jason_life_term ?? 0} onChange={v => set('jason_life_term', v)} prefix="$" /></Row>
        <Row label={`${p2} — Universal life`}><NumInput value={form.justin_life_ul ?? 0} onChange={v => set('justin_life_ul', v)} prefix="$" /></Row>
        <Row label={`${p2} — Whole life`}><NumInput value={form.justin_life_whole ?? 0} onChange={v => set('justin_life_whole', v)} prefix="$" /></Row>
        <Row label={`${p2} — Employer spousal`}><NumInput value={form.justin_life_conagra ?? 0} onChange={v => set('justin_life_conagra', v)} prefix="$" /></Row>
        <Row label={`${p2} — Term coverage`}><NumInput value={form.justin_life_term ?? 0} onChange={v => set('justin_life_term', v)} prefix="$" /></Row>
        <Row label={`Kids — Employer dependent (${k1} + ${k2} combined)`}><NumInput value={form.justin_life_kids ?? 0} onChange={v => set('justin_life_kids', v)} prefix="$" /></Row>
      </Section>

      <Section title="Disability &amp; Other Insurance">
        <Row label="Disability Monthly Benefit"><NumInput value={form.disability_monthly ?? 0} onChange={v => set('disability_monthly', v)} prefix="$" suffix="/mo" /></Row>
        <Row label="LTC Daily Benefit"><NumInput value={form.ltc_daily ?? 200} onChange={v => set('ltc_daily', v)} prefix="$" suffix="/day" /></Row>
        <Row label="LTC Maximum Benefit"><NumInput value={form.ltc_max ?? 0} onChange={v => set('ltc_max', v)} prefix="$" /></Row>
        <Row label="Home Insured Value" hint="Primary residence"><NumInput value={form.home_insured ?? 0} onChange={v => set('home_insured', v)} prefix="$" /></Row>
        <Row label="Umbrella Policy"><NumInput value={form.umbrella ?? 0} onChange={v => set('umbrella', v)} prefix="$" /></Row>
      </Section>

      <Section title="Planned Asset Sales">
        <Row label="Asset 1 — Name" hint="e.g. a property or rental you plan to sell">
          <TextInput value={form.asset1_label} onChange={v => set('asset1_label', v)} />
        </Row>
        <Row label={`${form.asset1_label || 'Asset 1'} Sale Age`} hint="Your age when it sells · 0 = not planned"><NumInput value={form.asset1_sale_age ?? 0} onChange={v => set('asset1_sale_age', v)} suffix="yrs" /></Row>
        <Row label={`${form.asset1_label || 'Asset 1'} Net Proceeds`} hint="Sale price minus mortgage payoff"><NumInput value={form.asset1_sale_net ?? 0} onChange={v => set('asset1_sale_net', v)} prefix="$" /></Row>
        <Row label={`${form.asset1_label || 'Asset 1'} Appreciation Rate`} hint="Annual appreciation until sale"><NumInput value={form.asset1_appreciation ?? 0.03} onChange={v => set('asset1_appreciation', v)} pct suffix="%" /></Row>
        <Row label="Asset 2 — Name" hint="e.g. a business you plan to sell">
          <TextInput value={form.asset2_label} onChange={v => set('asset2_label', v)} />
        </Row>
        <Row label={`${form.asset2_label || 'Asset 2'} Sale Age`} hint="Your age when it sells · 0 = not planned"><NumInput value={form.asset2_sale_age ?? 0} onChange={v => set('asset2_sale_age', v)} suffix="yrs" /></Row>
        <Row label={`${form.asset2_label || 'Asset 2'} Net Proceeds`} hint="Expected net proceeds"><NumInput value={form.asset2_sale_net ?? 0} onChange={v => set('asset2_sale_net', v)} prefix="$" /></Row>
      </Section>

      <Section title="Healthcare in Retirement">
        <Row label="Pre-Medicare Annual Cost" hint="ACA marketplace coverage · age 60-65 gap before Medicare">
          <NumInput value={form.healthcare_pre_medicare ?? 0} onChange={v => set('healthcare_pre_medicare', v)} prefix="$" suffix="/yr" />
        </Row>
        <Row label="Post-Medicare Annual Cost" hint="Medicare Part B/D + supplement · age 65+">
          <NumInput value={form.healthcare_post_medicare ?? 0} onChange={v => set('healthcare_post_medicare', v)} prefix="$" suffix="/yr" />
        </Row>
        <div style={{ padding:'10px 12px', background:'rgba(251,191,36,0.08)', borderRadius:8, marginTop:8, fontSize:12, color:'var(--amber)' }}>
          ⚠ Pre-Medicare gap (age 60-65): est. ${((form.healthcare_pre_medicare ?? 0)*5).toLocaleString()} total over 5 years. Projection engine auto-calculates gap per scenario (10 yrs at 55, 5 yrs at 60, 0 yrs at 65).
        </div>
      </Section>

      </div>

      <div style={{ textAlign:'right', marginTop:8 }}>
        <button className="btn-primary" onClick={save} style={{ minWidth:120 }}>
          {saved ? '✓ Saved' : 'Save All Changes'}
        </button>
      </div>
    </div>
  )
}
