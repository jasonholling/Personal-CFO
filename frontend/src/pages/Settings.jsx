import { useState, useEffect } from 'react'
import axios from 'axios'
import { usePrivacyMode } from '../hooks/usePrivacyMode'
import { useKids } from '../hooks/useKids'
import ClaimAgeSlider from '../components/ClaimAgeSlider'

const MAX_KIDS = 5
const EMPTY_KID = { name: '', age: 0, monthly_529: 0 }

// Kids-variable-count (2026-09-09) — replaced the old fixed "Child 1
// Name"/"Child 2 Name" + "Kids — Ages & 529 Contributions" rows (always
// exactly 2 kids) with a real add/edit/remove list, 0-5 kids, backed by
// the new /api/kids CRUD (see db.py's kids table + main.py's Kid model).
// Deliberately its own small local edit-draft state rather than living
// inside Settings' single `form` object with the page's one Save button
// -- kids are a separate table/endpoint now, not a planning_inputs
// column, and each row commits independently (Save/Cancel per row,
// mirroring Risk.jsx's insurance-policy list editing pattern) rather
// than batching into the page-wide save.
function KidsSection() {
  const { kids, loading, refetch } = useKids()
  const [editingId, setEditingId] = useState(null)   // null | 'new' | a kid id
  const [draft, setDraft] = useState(EMPTY_KID)
  const [busy, setBusy] = useState(false)

  const startEdit = (kid) => { setEditingId(kid.id); setDraft({ name: kid.name, age: kid.age, monthly_529: kid.monthly_529 }) }
  const startAdd  = () => { setEditingId('new'); setDraft(EMPTY_KID) }
  const cancel    = () => { setEditingId(null); setDraft(EMPTY_KID) }

  const save = async () => {
    if (!draft.name.trim()) return
    setBusy(true)
    try {
      if (editingId === 'new') await axios.post('/api/kids', draft)
      else await axios.put(`/api/kids/${editingId}`, { ...draft, display_order: kids.find(k => k.id === editingId)?.display_order ?? 0 })
      await refetch()
      cancel()
    } finally {
      setBusy(false)
    }
  }

  const remove = async (kid) => {
    if (!window.confirm(
      `Remove ${kid.name}? Any accounts already owned by ${kid.name} keep their balance and stay excluded ` +
      `from your own net worth/retirement totals, but won't show up under Education/Kids projections anymore.`
    )) return
    await axios.delete(`/api/kids/${kid.id}`)
    await refetch()
  }

  if (loading) return null

  return (
    <>
      {kids.map(kid => (
        <div key={kid.id} style={{ padding:'8px 0', borderBottom:'1px solid var(--border)' }}>
          {editingId === kid.id ? (
            <div style={{ display:'flex', gap:8, alignItems:'center', flexWrap:'wrap' }}>
              <TextInput value={draft.name} onChange={v => setDraft(d => ({ ...d, name: v }))} style={{ width:120, textAlign:'left' }} />
              <NumInput value={draft.age} onChange={v => setDraft(d => ({ ...d, age: v }))} suffix="yrs old" />
              <NumInput value={draft.monthly_529} onChange={v => setDraft(d => ({ ...d, monthly_529: v }))} prefix="$" suffix="/mo 529" />
              <button className="btn-primary" onClick={save} disabled={busy || !draft.name.trim()}>Save</button>
              <button className="btn-secondary" onClick={cancel} disabled={busy}>Cancel</button>
            </div>
          ) : (
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
              <div style={{ fontSize:13 }}>{kid.name} <span style={{ color:'var(--text3)' }}>· age {kid.age} · ${kid.monthly_529}/mo 529</span></div>
              <div style={{ display:'flex', gap:6 }}>
                <button className="btn-secondary" onClick={() => startEdit(kid)}>Edit</button>
                <button className="btn-secondary" onClick={() => remove(kid)}>Remove</button>
              </div>
            </div>
          )}
        </div>
      ))}

      {editingId === 'new' && (
        <div style={{ padding:'8px 0', borderBottom:'1px solid var(--border)', display:'flex', gap:8, alignItems:'center', flexWrap:'wrap' }}>
          <TextInput value={draft.name} onChange={v => setDraft(d => ({ ...d, name: v }))} style={{ width:120, textAlign:'left' }} />
          <NumInput value={draft.age} onChange={v => setDraft(d => ({ ...d, age: v }))} suffix="yrs old" />
          <NumInput value={draft.monthly_529} onChange={v => setDraft(d => ({ ...d, monthly_529: v }))} prefix="$" suffix="/mo 529" />
          <button className="btn-primary" onClick={save} disabled={busy || !draft.name.trim()}>Add</button>
          <button className="btn-secondary" onClick={cancel} disabled={busy}>Cancel</button>
        </div>
      )}

      {kids.length === 0 && editingId !== 'new' && (
        <div style={{ fontSize:12, color:'var(--text3)', padding:'8px 0' }}>No kids added — that's fine, everything below (Education, Kids, Insurance college-funding) just shows nothing kid-related until you add one.</div>
      )}

      <div style={{ paddingTop:10 }}>
        {kids.length < MAX_KIDS ? (
          editingId !== 'new' && <button className="btn-secondary" onClick={startAdd}>+ Add Kid</button>
        ) : (
          <div style={{ fontSize:11, color:'var(--text3)' }}>Maximum of {MAX_KIDS} kids.</div>
        )}
      </div>
    </>
  )
}

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
  const { kids } = useKids()
  const [form, setForm] = useState(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    axios.get('/api/planning-inputs').then(r => setForm(r.data))
  }, [])

  const set = (key, val) => setForm(f => ({ ...f, [key]: val }))

  const save = () => {
    // Backlog item 6 (CALCULATION_CONTRACT.md section 13): a second salary
    // with retirement age left at the 0-fallback silently ties that
    // person's contribution window to Jason's own ret_age scenario,
    // which reads as "independent" but isn't. Require an explicit
    // confirmation at the moment of saving that combination, rather than
    // only a passive warning banner elsewhere on the page.
    if ((form.justin_w2_salary ?? 0) > 0 && (form.justin_ret_age ?? 0) === 0) {
      const p2 = form.person2_name || 'Person 2'
      const ok = window.confirm(
        `${p2}'s Retirement Age is still 0 (unset) even though ${p2}'s salary is filled in. ` +
        `That means ${p2}'s contributions will stop whenever the scenario you're viewing has the ` +
        `other person retiring — not at ${p2}'s own actual retirement date.\n\n` +
        `Save anyway with this fallback, or Cancel to go set ${p2}'s Retirement Age first?`
      )
      if (!ok) return
    }
    axios.put('/api/planning-inputs', form)
      .then(() => { setSaved(true); setTimeout(() => setSaved(false), 2500) })
  }

  if (!form) return <div className="loading">Loading...</div>

  const pretax_pct = form.pretax_401k_pct ?? 0.75
  const roth_pct   = 1 - pretax_pct
  const p1 = form.person1_name || 'Person 1'
  const p2 = form.person2_name || 'Person 2'
  const kidsLabel = kids.length ? kids.map(k => k.name).join(' + ') : 'kids'

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
      </Section>

      <Section title={`${p1} — Age, Income & Retirement`}>
        <Row label={`${p1}'s Current Age`}><NumInput value={form.jason_age} onChange={v => set('jason_age', v)} /></Row>
        <Row label="W2 Base Salary" hint="Used for 401k contribution calculations"><NumInput value={form.w2_salary ?? 0} onChange={v => set('w2_salary', v)} prefix="$" /></Row>
        <Row label={`${p1}'s Contribution Rate`} hint="Employee % → goes to Roth 401k">
          <NumInput value={form.employee_401k_pct ?? 0.06} onChange={v => set('employee_401k_pct', v)} pct suffix="%" />
        </Row>
        <Row label={`${p1}'s Employer Contribution Rate`} hint="Match + non-elective → goes pre-tax">
          <NumInput value={form.employer_401k_pct ?? 0.03} onChange={v => set('employer_401k_pct', v)} pct suffix="%" />
        </Row>
        <Row label={`${p1}'s Total Annual 401k`} hint="Calculated from salary × rates">
          <span style={{ fontSize:13, color:'var(--text2)' }}>
            ~${(((form.employee_401k_pct ?? 0.06) + (form.employer_401k_pct ?? 0.03)) * (form.w2_salary ?? 0)).toLocaleString('en-US', {maximumFractionDigits:0})}/yr
          </span>
        </Row>
        <Row label="Annual RSU Value (gross)" hint="0 if none this year"><NumInput value={form.annual_rsu_value} onChange={v => set('annual_rsu_value', v)} prefix="$" /></Row>
        <Row label="Annual Bonus" hint="As % of salary — scales with raises, unlike RSU above"><NumInput value={form.annual_bonus_pct ?? 0} onChange={v => set('annual_bonus_pct', v)} pct suffix="%" /></Row>
        {((form.annual_bonus_pct ?? 0) > 0 || (form.annual_rsu_value ?? 0) > 0) && (
          <div style={{ padding:'10px 12px', background:'rgba(251,191,36,0.08)', borderRadius:8, marginTop:8, fontSize:12, color:'var(--amber)' }}>
            ⚠ RSU/Bonus above are already assumed invested automatically each year toward retirement. If you've
            also assigned this same money on the Assign Surplus page (e.g. under "Taxable investing"), it will be
            counted twice — pick one place to describe it, not both.
          </div>
        )}
        <Row label={`${p1} SS at 62 (early)`}><NumInput value={form.jason_social_security} onChange={v => set('jason_social_security', v)} prefix="$" suffix="/yr" /></Row>
        <Row label={`${p1} SS at 67 (delayed)`}><NumInput value={form.jason_ss_delayed ?? 0} onChange={v => set('jason_ss_delayed', v)} prefix="$" suffix="/yr" /></Row>
        <Row label={`${p1} SS at 70`} hint="Real dollar figure from your SSA statement, not a formula guess — used as the anchor for the claim-age slider below">
          <NumInput value={form.jason_ss_70 ?? 0} onChange={v => set('jason_ss_70', v)} prefix="$" suffix="/yr" />
        </Row>
        <ClaimAgeSlider
          label={`${p1}'s Social Security claim age`}
          claimAge={form.jason_ss_claim_age}
          onChange={v => set('jason_ss_claim_age', v)}
          benefit62={form.jason_social_security}
          benefit67={form.jason_ss_delayed}
          benefit70={form.jason_ss_70}
          benefitType="worker"
        />
        <div style={{ padding:'10px 12px', background:'var(--bg3)', borderRadius:8, marginTop:8, fontSize:12, color:'var(--text2)' }}>
          {p1}'s own retirement age is chosen interactively as a scenario (55/60/65/etc.) on the Retirement
          Projection and Stress Test pages, not fixed here as a single planning assumption.
        </div>
      </Section>

      <Section title={`${p2} — Age, Income & Retirement (if working full-time)`}>
        <Row label={`${p2}'s Current Age`}><NumInput value={form.justin_age} onChange={v => set('justin_age', v)} /></Row>
        <div style={{ fontSize:11, color:'var(--text3)', margin:'12px 0' }}>
          Leave everything below at $0/unset if this household has one primary income — none of it changes any number.
        </div>
        <Row label={`${p2}'s W2 Base Salary`} hint="A separate, independent income and 401k — not combined with the salary above"><NumInput value={form.justin_w2_salary ?? 0} onChange={v => set('justin_w2_salary', v)} prefix="$" /></Row>
        <Row label={`${p2}'s Contribution Rate`} hint="Employee % → goes to Roth 401k"><NumInput value={form.justin_employee_401k_pct ?? 0.06} onChange={v => set('justin_employee_401k_pct', v)} pct suffix="%" /></Row>
        <Row label={`${p2}'s Employer Contribution Rate`} hint="Match + non-elective → goes pre-tax"><NumInput value={form.justin_employer_401k_pct ?? 0.03} onChange={v => set('justin_employer_401k_pct', v)} pct suffix="%" /></Row>
        <Row label={`${p2}'s Total Annual 401k`} hint="Calculated from salary × rates">
          <span style={{ fontSize:13, color:'var(--text2)' }}>
            ~${(((form.justin_employee_401k_pct ?? 0.06) + (form.justin_employer_401k_pct ?? 0.03)) * (form.justin_w2_salary ?? 0)).toLocaleString('en-US', {maximumFractionDigits:0})}/yr
          </span>
        </Row>
        <Row label={`${p2}'s Annual RSU Value (gross)`} hint="0 if none this year"><NumInput value={form.justin_annual_rsu_value ?? 0} onChange={v => set('justin_annual_rsu_value', v)} prefix="$" /></Row>
        <Row label={`${p2}'s Annual Bonus`} hint="As % of salary"><NumInput value={form.justin_annual_bonus_pct ?? 0} onChange={v => set('justin_annual_bonus_pct', v)} pct suffix="%" /></Row>
        {/* External audit review of commit 0c1a569, finding 6 (P2): the
            claim-age slider below always applies SSA's SPOUSAL-benefit
            reduction/credit schedule (different rates than a worker's
            own record -- CALCULATION_CONTRACT.md section 49, finding
            4) no matter what kind of figure is entered into these
            fields. The 67 field below still supports a flat,
            single-age entry for Justin's own independent worker
            benefit (no formula applied there), but the slider itself
            does not yet support a worker-type benefit -- restricting
            the wording here rather than silently computing the wrong
            reduction for a worker record.
            2026-09-09: reordered 67/62/70 -> 62/67/70 (age order) --
            the 67 field predates the other two and was never moved
            when they were added alongside it. */}
        <Row label={`${p2} Spousal SS at 62`} hint="Real dollar figure from your SSA statement — only meaningful for a spousal benefit (see note below); used as the anchor for the claim-age slider">
          <NumInput value={form.justin_ss_early ?? 0} onChange={v => set('justin_ss_early', v)} prefix="$" suffix="/yr" />
        </Row>
        <Row label={`${p2} Spousal SS at 67`} hint={`50% of ${p1}'s FRA benefit — or ${p2}'s own independent benefit at a flat single figure, if entered directly`}>
          <NumInput value={form.justin_social_security ?? 0} onChange={v => set('justin_social_security', v)} prefix="$" suffix="/yr" />
        </Row>
        <Row label={`${p2} Spousal SS at 70`} hint="Real dollar figure from your SSA statement — only meaningful for a spousal benefit (see note below)">
          <NumInput value={form.justin_ss_70 ?? 0} onChange={v => set('justin_ss_70', v)} prefix="$" suffix="/yr" />
        </Row>
        <ClaimAgeSlider
          label={`${p2}'s Social Security claim age`}
          claimAge={form.justin_ss_claim_age}
          onChange={v => set('justin_ss_claim_age', v)}
          benefit62={form.justin_ss_early}
          benefit67={form.justin_social_security}
          benefit70={form.justin_ss_70}
          benefitType="spousal"
          checkEarlyAnchor
          scopeNote={`This slider only supports ${p2} claiming a SPOUSAL benefit (50% of ${p1}'s FRA figure) — it always applies SSA's spousal reduction schedule, which differs from a worker's own record. If ${p2} has an independent work-record benefit instead, leave this off and use the Early/Delayed toggle on Retirement/Simulation pages, which applies your 67 figure above at face value with no formula.`}
        />
        <Row label={`${p2}'s Retirement Age`} hint={`0 = assume ${p2} retires the same year as ${p1} (the old default). Set a specific age for an independent retirement date — e.g. ${p2} keeps working/contributing past, or stops well before, whichever age you're viewing for ${p1}.`}>
          <NumInput value={form.justin_ret_age ?? 0} onChange={v => set('justin_ret_age', Math.max(0, Math.round(v)))} suffix="age" />
        </Row>
        {(form.justin_w2_salary ?? 0) > 0 && (form.justin_ret_age ?? 0) === 0 && (
          <div style={{ padding:'10px 12px', background:'rgba(251,191,36,0.08)', borderRadius:8, marginTop:8, fontSize:12, color:'var(--amber)' }}>
            ⚠ {p2}'s Retirement Age is 0 (unset) — {p2}'s contributions will stop whenever the scenario you're
            viewing has {p1} retiring, not at {p2}'s own real retirement date. Set an age above for an independent
            {' '}{p2} retirement timeline.
          </div>
        )}
        <div style={{ padding:'10px 12px', background:'var(--bg3)', borderRadius:8, marginTop:8, fontSize:12, color:'var(--text2)' }}>
          Note: this models {p2}'s own income and contributions building up before retirement. It does not yet
          model {p2} continuing to earn (offsetting spending) during years where {p1} has already retired but
          {' '}{p2} hasn't reached the age above — a "one spouse still working" phase isn't represented in the
          withdrawal-phase numbers.
        </div>
      </Section>

      <Section title="Return & Inflation Assumptions">
        <Row label="Inflation Rate"><NumInput value={form.inflation_rate} onChange={v => set('inflation_rate', v)} pct suffix="%" /></Row>
        <Row label="Pre-Retirement Return" hint="Expected portfolio growth until retirement"><NumInput value={form.expected_return_pre_retirement} onChange={v => set('expected_return_pre_retirement', v)} pct suffix="%" /></Row>
        <Row label="Post-Retirement Return" hint="Expected portfolio growth during retirement"><NumInput value={form.expected_return_post_retirement} onChange={v => set('expected_return_post_retirement', v)} pct suffix="%" /></Row>
        <Row label="State Income-Tax Rate" hint="Optional planning estimate applied to taxable retirement distributions; enter 0% if not using one"><NumInput value={form.state_income_tax_rate ?? 0} onChange={v => set('state_income_tax_rate', Math.min(0.25, Math.max(0, v)))} pct suffix="%" /></Row>
      </Section>

      <Section title="Retirement Income Goal">
        <Row label="Annual Income Goal (today's $)" hint="What you want to spend per year in retirement"><NumInput value={form.retirement_income_today_dollars} onChange={v => set('retirement_income_today_dollars', v)} prefix="$" /></Row>
        <Row label="Planning Horizon" hint="Age through which retirement income is projected; this is an assumption, not a longevity prediction"><NumInput value={form.retirement_end_age ?? 99} onChange={v => set('retirement_end_age', Math.min(110, Math.max(70, Math.round(v))))} suffix="age" /></Row>
      </Section>

      <Section title="Current Spending">
        <Row label="Current Monthly Expenses" hint="Actual current spending — used for the Emergency Fund check, separate from your retirement income goal above"><NumInput value={form.current_monthly_expenses ?? 0} onChange={v => set('current_monthly_expenses', v)} prefix="$" suffix="/mo" /></Row>
      </Section>

      <Section title="Household 401k Balance Split &amp; HSA">
        <Row label="Pre-Tax % of Total 401k Balance" hint={`Combined balance from Quicken (both spouses' 401ks) × this % = pre-tax bucket. Roth = ${(roth_pct*100).toFixed(1)}%`}>
          <NumInput value={pretax_pct} onChange={v => set('pretax_401k_pct', Math.min(1, Math.max(0, v)))} pct suffix="%" />
        </Row>
        <Row label="Roth % (calculated)" hint="Auto = 100% minus pre-tax %">
          <span style={{ fontSize:13, fontWeight:600, color:'var(--accent)' }}>{(roth_pct*100).toFixed(1)}%</span>
        </Row>
        <Row label="Annual HSA Contribution" hint="Household/family HSA, not split per person"><NumInput value={form.annual_hsa_contribution} onChange={v => set('annual_hsa_contribution', v)} prefix="$" /></Row>
      </Section>

      <Section title="Kids (0-5)" >
        <KidsSection />
        <Row label="Annual Tuition &amp; Fees" hint="Current cost — update each fall · inflates at 5%/yr in projections"><NumInput value={form.unl_annual_cost ?? 0} onChange={v => set('unl_annual_cost', v)} prefix="$" suffix="/yr" /></Row>
      </Section>

      <Section title="Kids — Roth IRA & Custodial">
        <Row label="Kids Roth IRA Monthly" hint="Per kid — from your paycheck"><NumInput value={form.kids_roth_monthly ?? 0} onChange={v => set('kids_roth_monthly', v)} prefix="$" suffix="/mo each" /></Row>
        <Row label="Kids Custodial Monthly"><NumInput value={form.kids_custodial_monthly ?? 0} onChange={v => set('kids_custodial_monthly', v)} prefix="$" suffix="/mo each" /></Row>
      </Section>

      <Section title="Pension (100% Joint &amp; Survivor)">
        <div style={{ fontSize:11, color:'var(--text3)', marginBottom:12 }}>
          One household pension (e.g. {p1}'s employer plan) with a joint-and-survivor election —
          not two independent pensions. Keyed to {p1}'s retirement age; {p2} continues receiving
          it after {p1}'s death. If {p2} has their own separate pension, there's no field for it
          yet — this is a known gap, not silently missing.
        </div>
        <Row label="Pension at Age 55" hint="Monthly × 12"><NumInput value={form.pension_55 ?? 0} onChange={v => set('pension_55', v)} prefix="$" suffix="/yr" /></Row>
        <Row label="Pension at Age 60" hint="Monthly × 12"><NumInput value={form.pension_60 ?? 0} onChange={v => set('pension_60', v)} prefix="$" suffix="/yr" /></Row>
        <Row label="Pension at Age 65" hint="Monthly × 12"><NumInput value={form.pension_65 ?? 0} onChange={v => set('pension_65', v)} prefix="$" suffix="/yr" /></Row>
      </Section>

      <Section title={`${p1} — Life Insurance`}>
        <p style={{ fontSize:12, color:'var(--text3)', marginTop:-8, marginBottom:12 }}>
          Detailed per-policy tracking (insurer, policy #, premium) lives on the Risk Management page. These totals feed the insurance-gap calculations here.
        </p>
        <Row label="Basic coverage"><NumInput value={form.jason_life_basic ?? 0} onChange={v => set('jason_life_basic', v)} prefix="$" /></Row>
        <Row label="Supplemental coverage"><NumInput value={form.jason_life_supplemental ?? 0} onChange={v => set('jason_life_supplemental', v)} prefix="$" /></Row>
        <Row label="Term coverage"><NumInput value={form.jason_life_term ?? 0} onChange={v => set('jason_life_term', v)} prefix="$" /></Row>
      </Section>

      <Section title={`${p2} — Life Insurance`}>
        <Row label="Universal life"><NumInput value={form.justin_life_ul ?? 0} onChange={v => set('justin_life_ul', v)} prefix="$" /></Row>
        <Row label="Whole life"><NumInput value={form.justin_life_whole ?? 0} onChange={v => set('justin_life_whole', v)} prefix="$" /></Row>
        <Row label="Employer-sponsored coverage"><NumInput value={form.person2_life_employer ?? 0} onChange={v => set('person2_life_employer', v)} prefix="$" /></Row>
        <Row label="Term coverage"><NumInput value={form.justin_life_term ?? 0} onChange={v => set('justin_life_term', v)} prefix="$" /></Row>
      </Section>

      <Section title="Kids — Life Insurance">
        <Row label={`Employer dependent (${kidsLabel} combined)`}><NumInput value={form.justin_life_kids ?? 0} onChange={v => set('justin_life_kids', v)} prefix="$" /></Row>
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
