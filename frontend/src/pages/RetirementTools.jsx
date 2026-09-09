import { useState, useEffect } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY, MASK_PERCENT } from '../utils/privacy'

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(n))
const fpct = (n) => isPrivacyMode() ? MASK_PERCENT : (n == null ? '—' : `${(n*100).toFixed(0)}%`)

const GREEN  = '#34d399'
const AMBER  = '#fbbf24'
const RED    = '#f87171'
const ACCENT = '#4f9cf9'

export default function RetirementTools() {
  const [rmd, setRmd]           = useState(null)
  const [loading, setLoading]   = useState(true)

  const [lump, setLump]         = useState({ monthly_pension:'', lump_sum:'', current_age:'', pension_start_age:'', life_expectancy_age:90, discount_rate:6 })
  const [lumpResult, setLumpResult] = useState(null)

  const [roth, setRoth]         = useState({ magi:'', existing_traditional_ira_balance:'', existing_traditional_ira_basis:'', planned_contribution:7500 })
  const [rothResult, setRothResult] = useState(null)

  const [qcd, setQcd]           = useState({ age:'', ira_balance:'', rmd_amount:'', desired_qcd_amount:'', other_taxable_income:'' })
  const [qcdResult, setQcdResult] = useState(null)

  const [hsa, setHsa]           = useState({ oop_expense_this_year:'', years_to_delay:10, growth_rate:7 })
  const [hsaResult, setHsaResult] = useState(null)

  // Pre-fill from data you've already entered in Settings/Accounts, rather
  // than asking you to retype your own age or IRA balance. Every field
  // stays editable — these are just starting points for what-if scenarios.
  useEffect(() => {
    Promise.all([
      axios.get('/api/retirement-tools/rmd-planning').catch(() => ({ data: { has_pretax_balance:false } })),
      axios.get('/api/planning-inputs').catch(() => null),
      axios.get('/api/accounts').catch(() => ({ data: [] })),
    ]).then(([rmdRes, inputsRes, accountsRes]) => {
      setRmd(rmdRes.data)

      const jasonAge = inputsRes?.data?.jason_age
      if (jasonAge) setLump(l => ({ ...l, current_age: jasonAge.toString() }))
      if (jasonAge) setQcd(q => ({ ...q, age: jasonAge.toString() }))

      const existingIraBalance = accountsRes.data
        .filter(a => a.account_type === 'ira' && !a.owner?.startsWith('kid_'))
        .reduce((s, a) => s + a.balance, 0)
      if (existingIraBalance > 0) {
        setRoth(r => ({ ...r, existing_traditional_ira_balance: existingIraBalance.toString() }))
        setQcd(q => ({ ...q, ira_balance: existingIraBalance.toString() }))
      }
      if (rmdRes.data?.has_pretax_balance && rmdRes.data.first_rmd_amount) {
        setQcd(q => ({ ...q, rmd_amount: rmdRes.data.first_rmd_amount.toString() }))
      }
    }).finally(() => setLoading(false))
  }, [])

  const runLumpSum = () => {
    axios.post('/api/retirement-tools/pension-vs-lump-sum', {
      monthly_pension: parseFloat(lump.monthly_pension) || 0,
      lump_sum: parseFloat(lump.lump_sum) || 0,
      current_age: parseInt(lump.current_age) || 0,
      pension_start_age: parseInt(lump.pension_start_age) || 0,
      life_expectancy_age: parseInt(lump.life_expectancy_age) || 90,
      discount_rate: (parseFloat(lump.discount_rate) || 6) / 100,
    }).then(r => setLumpResult(r.data))
  }

  const runRoth = () => {
    axios.post('/api/retirement-tools/backdoor-roth', {
      magi: parseFloat(roth.magi) || 0,
      existing_traditional_ira_balance: parseFloat(roth.existing_traditional_ira_balance) || 0,
      existing_traditional_ira_basis: parseFloat(roth.existing_traditional_ira_basis) || 0,
      planned_contribution: parseFloat(roth.planned_contribution) || 7500,
    }).then(r => setRothResult(r.data))
  }

  const runQcd = () => {
    axios.post('/api/retirement-tools/qcd', {
      age: parseFloat(qcd.age) || 0,
      ira_balance: parseFloat(qcd.ira_balance) || 0,
      rmd_amount: parseFloat(qcd.rmd_amount) || 0,
      desired_qcd_amount: parseFloat(qcd.desired_qcd_amount) || 0,
      other_taxable_income: parseFloat(qcd.other_taxable_income) || 0,
    }).then(r => setQcdResult(r.data))
  }

  const runHsa = () => {
    axios.post('/api/retirement-tools/hsa-strategy', {
      oop_expense_this_year: parseFloat(hsa.oop_expense_this_year) || 0,
      years_to_delay: parseInt(hsa.years_to_delay) || 10,
      growth_rate: (parseFloat(hsa.growth_rate) || 7) / 100,
    }).then(r => setHsaResult(r.data))
  }

  if (loading) return <div className="loading">Loading retirement tools...</div>

  return (
    <div>
      <div style={{ marginBottom:28 }}>
        <h1 className="section-title">Retirement Tools</h1>
        <p className="section-sub">RMD planning, pension decisions, Roth strategy, charitable giving, and HSA optimization</p>
      </div>

      {/* RMD Planner */}
      <div className="card" style={{ marginBottom:24, borderTop:`3px solid ${ACCENT}` }}>
        <div className="label" style={{ marginBottom:16 }}>Required Minimum Distribution Planner</div>
        {!rmd?.has_pretax_balance ? (
          <div style={{ fontSize:13, color:'var(--text2)' }}>
            No pre-tax 401k/IRA balance found in Accounts — add one to see your projected RMD schedule and whether it will push you into a higher bracket.
          </div>
        ) : (
          <>
            <div className="grid-3" style={{ marginBottom:16 }}>
              <div>
                <div className="label">Pre-Tax Balance Today</div>
                <div style={{ fontSize:18, fontWeight:600, marginTop:4 }}>{fmt(rmd.pretax_balance_today)}</div>
              </div>
              <div>
                <div className="label">Projected Balance at {rmd.first_rmd_age}</div>
                <div style={{ fontSize:18, fontWeight:600, marginTop:4 }}>{fmt(rmd.projected_balance_at_start_age)}</div>
              </div>
              <div>
                <div className="label">First RMD (age {rmd.first_rmd_age})</div>
                <div style={{ fontSize:18, fontWeight:600, marginTop:4 }}>{fmt(rmd.first_rmd_amount)}</div>
              </div>
            </div>
            <div style={{
              padding:'12px 16px', borderRadius:8, fontSize:13, lineHeight:1.6, marginBottom:16,
              background: rmd.bracket_jump ? 'rgba(251,191,36,0.08)' : 'rgba(52,211,153,0.08)',
            }}>
              {rmd.bracket_jump && <span style={{ color:AMBER, fontWeight:600 }}>⚠ Bracket jump: </span>}
              {rmd.recommendation}
            </div>
            <div className="grid-2" style={{ marginBottom:12 }}>
              <div>
                <div className="label">Bracket Before RMDs</div>
                <div style={{ fontSize:14, marginTop:4 }}>{fpct(rmd.pre_rmd_bracket)}</div>
              </div>
              <div>
                <div className="label">Bracket at First RMD</div>
                <div style={{ fontSize:14, marginTop:4, color: rmd.bracket_jump ? RED : GREEN, fontWeight:600 }}>{fpct(rmd.first_rmd_bracket)}</div>
              </div>
            </div>
            <div className="label" style={{ marginBottom:8 }}>Lifetime RMD Total (through age {73 + (rmd.schedule?.length ? rmd.schedule.length*2 - 2 : 0)})</div>
            <div style={{ fontSize:16, fontWeight:600, marginBottom:16 }}>{fmt(rmd.lifetime_rmd_total)}</div>
            {rmd.schedule?.length > 0 && (
              <div style={{ overflowX:'auto' }}>
                <table style={{ width:'100%', borderCollapse:'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom:'1px solid var(--border)' }}>
                      {['Age','Starting Balance','RMD Amount','Bracket'].map(h => (
                        <th key={h} style={{ padding:'8px 12px', textAlign:'left', fontSize:11, fontWeight:600, letterSpacing:'0.05em', textTransform:'uppercase', color:'var(--text3)' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rmd.schedule.map(s => (
                      <tr key={s.age} style={{ borderBottom:'1px solid var(--border)' }}>
                        <td style={{ padding:'8px 12px', fontSize:13 }}>{s.age}</td>
                        <td style={{ padding:'8px 12px', fontSize:13 }}>{fmt(s.starting_balance)}</td>
                        <td style={{ padding:'8px 12px', fontSize:13 }}>{fmt(s.rmd_amount)}</td>
                        <td style={{ padding:'8px 12px', fontSize:13 }}>{fpct(s.marginal_rate)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>

      {/* Pension vs Lump Sum */}
      <div className="card" style={{ marginBottom:24 }}>
        <div className="label" style={{ marginBottom:16 }}>Pension vs. Lump Sum Decision</div>
        <div className="grid-3" style={{ marginBottom:12 }}>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Monthly Pension Offer ($)</div>
            <input type="number" value={lump.monthly_pension} onChange={e => setLump({ ...lump, monthly_pension:e.target.value })} placeholder="3000" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Lump Sum Offer ($)</div>
            <input type="number" value={lump.lump_sum} onChange={e => setLump({ ...lump, lump_sum:e.target.value })} placeholder="400000" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Current Age</div>
            <input type="number" value={lump.current_age} onChange={e => setLump({ ...lump, current_age:e.target.value })} placeholder="55" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Pension Start Age</div>
            <input type="number" value={lump.pension_start_age} onChange={e => setLump({ ...lump, pension_start_age:e.target.value })} placeholder="65" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Life Expectancy Age</div>
            <input type="number" value={lump.life_expectancy_age} onChange={e => setLump({ ...lump, life_expectancy_age:e.target.value })} placeholder="90" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Discount Rate (%)</div>
            <input type="number" step="0.1" value={lump.discount_rate} onChange={e => setLump({ ...lump, discount_rate:e.target.value })} placeholder="6" />
          </div>
        </div>
        <button className="btn-primary" onClick={runLumpSum}>Calculate</button>
        {lumpResult && (
          <div style={{ marginTop:16 }}>
            <div style={{
              padding:'12px 16px', borderRadius:8, fontSize:13, lineHeight:1.6, marginBottom:16,
              background: 'rgba(79,156,249,0.08)',
            }}>
              <strong style={{ color: ACCENT, textTransform:'capitalize' }}>{lumpResult.favors === 'lump_sum' ? 'Take the lump sum' : 'Take the pension'}</strong> — {lumpResult.recommendation}
            </div>
            <div className="grid-3">
              <div>
                <div className="label">PV of Pension Stream</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>{fmt(lumpResult.present_value_of_pension)}</div>
              </div>
              <div>
                <div className="label">Lump Sum Offer</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>{fmt(lumpResult.lump_sum_offer)}</div>
              </div>
              <div>
                <div className="label">Implied Discount Rate</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>{lumpResult.implied_discount_rate_pct == null ? '—' : (isPrivacyMode() ? MASK_PERCENT : `${lumpResult.implied_discount_rate_pct.toFixed(1)}%`)}</div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Backdoor Roth Eligibility */}
      <div className="card" style={{ marginBottom:24 }}>
        <div className="label" style={{ marginBottom:16 }}>Backdoor Roth Eligibility Checker</div>
        <div className="grid-3" style={{ marginBottom:12 }}>
          <div>
            <div className="label" style={{ marginBottom:6 }}>MAGI ($)</div>
            <input type="number" value={roth.magi} onChange={e => setRoth({ ...roth, magi:e.target.value })} placeholder="300000" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Existing Traditional IRA Balance ($)</div>
            <input type="number" value={roth.existing_traditional_ira_balance} onChange={e => setRoth({ ...roth, existing_traditional_ira_balance:e.target.value })} placeholder="0" />
            <div style={{ fontSize:11, color:'var(--text3)', marginTop:4 }}>Pre-filled from your Traditional IRA balance(s) in Accounts — adjust if planning a future contribution first.</div>
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Existing Basis (after-tax contributions, $)</div>
            <input type="number" value={roth.existing_traditional_ira_basis} onChange={e => setRoth({ ...roth, existing_traditional_ira_basis:e.target.value })} placeholder="0" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Planned Contribution ($)</div>
            <input type="number" value={roth.planned_contribution} onChange={e => setRoth({ ...roth, planned_contribution:e.target.value })} placeholder="7500" />
          </div>
        </div>
        <button className="btn-primary" onClick={runRoth}>Check Eligibility</button>
        {rothResult && (
          <div style={{ marginTop:16 }}>
            <div style={{
              padding:'12px 16px', borderRadius:8, fontSize:13, lineHeight:1.6, marginBottom:16,
              background: rothResult.needs_backdoor ? 'rgba(251,191,36,0.08)' : 'rgba(52,211,153,0.08)',
            }}>
              {rothResult.recommendation}
            </div>
            <div className="grid-3">
              <div>
                <div className="label">Direct Roth Eligible?</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4, color: rothResult.direct_roth_eligible ? GREEN : RED }}>{rothResult.direct_roth_eligible ? 'Yes' : 'No'}</div>
              </div>
              <div>
                <div className="label">Pro-Rata Rule Applies?</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4, color: rothResult.pro_rata_applies ? AMBER : GREEN }}>{rothResult.pro_rata_applies ? 'Yes' : 'No'}</div>
              </div>
              <div>
                <div className="label">Taxable Portion of Conversion</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>{fmt(rothResult.taxable_amount_of_conversion)}</div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Qualified Charitable Distribution Planner */}
      <div className="card" style={{ marginBottom:24 }}>
        <div className="label" style={{ marginBottom:16 }}>Qualified Charitable Distribution (QCD) Planner</div>
        <p style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>
          Once you're 70½+, donating straight from a Traditional IRA keeps the money off your tax return entirely — better than an itemized/DAF deduction, and it counts toward your RMD.
        </p>
        <div className="grid-3" style={{ marginBottom:12 }}>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Your Age</div>
            <input type="number" value={qcd.age} onChange={e => setQcd({ ...qcd, age:e.target.value })} placeholder="72" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Traditional IRA Balance ($)</div>
            <input type="number" value={qcd.ira_balance} onChange={e => setQcd({ ...qcd, ira_balance:e.target.value })} placeholder="0" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>This Year's RMD ($, 0 if none yet)</div>
            <input type="number" value={qcd.rmd_amount} onChange={e => setQcd({ ...qcd, rmd_amount:e.target.value })} placeholder="0" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Desired Donation Amount ($)</div>
            <input type="number" value={qcd.desired_qcd_amount} onChange={e => setQcd({ ...qcd, desired_qcd_amount:e.target.value })} placeholder="10000" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Other Taxable Income This Year ($)</div>
            <input type="number" value={qcd.other_taxable_income} onChange={e => setQcd({ ...qcd, other_taxable_income:e.target.value })} placeholder="0" />
          </div>
        </div>
        <button className="btn-primary" onClick={runQcd}>Calculate</button>
        {qcdResult && (
          <div style={{ marginTop:16 }}>
            {!qcdResult.eligible ? (
              <div style={{ padding:'12px 16px', borderRadius:8, fontSize:13, background:'rgba(251,191,36,0.08)', color:AMBER }}>
                {qcdResult.recommendation}
              </div>
            ) : (
              <>
                <div style={{ padding:'12px 16px', borderRadius:8, fontSize:13, lineHeight:1.6, marginBottom:16, background:'rgba(52,211,153,0.08)' }}>
                  {qcdResult.recommendation}
                </div>
                <div className="grid-3">
                  <div>
                    <div className="label">QCD Amount</div>
                    <div style={{ fontSize:16, fontWeight:600, marginTop:4, color:ACCENT }}>{fmt(qcdResult.qcd_amount)}</div>
                  </div>
                  <div>
                    <div className="label">RMD Satisfied by QCD</div>
                    <div style={{ fontSize:16, fontWeight:600, marginTop:4 }}>{fmt(qcdResult.rmd_satisfied_by_qcd)}</div>
                  </div>
                  <div>
                    <div className="label">Estimated Tax Savings</div>
                    <div style={{ fontSize:16, fontWeight:600, marginTop:4, color:GREEN }}>{fmt(qcdResult.estimated_tax_savings)}</div>
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* HSA Stealth-IRA Strategy */}
      <div className="card" style={{ marginBottom:24 }}>
        <div className="label" style={{ marginBottom:16 }}>HSA "Stealth IRA" Strategy</div>
        <p style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>
          Pay a medical expense out of pocket and keep the receipt — you can reimburse yourself from the HSA any time later, with no deadline, letting the money compound tax-free until then.
        </p>
        <div className="grid-3" style={{ marginBottom:12 }}>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Out-of-Pocket Expense This Year ($)</div>
            <input type="number" value={hsa.oop_expense_this_year} onChange={e => setHsa({ ...hsa, oop_expense_this_year:e.target.value })} placeholder="2000" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Years Before You'd Reimburse</div>
            <input type="number" value={hsa.years_to_delay} onChange={e => setHsa({ ...hsa, years_to_delay:e.target.value })} placeholder="10" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Expected Growth Rate (%)</div>
            <input type="number" step="0.1" value={hsa.growth_rate} onChange={e => setHsa({ ...hsa, growth_rate:e.target.value })} placeholder="7" />
          </div>
        </div>
        <button className="btn-primary" onClick={runHsa}>Calculate</button>
        {hsaResult?.has_expense && (
          <div style={{ marginTop:16 }}>
            <div style={{ padding:'12px 16px', borderRadius:8, fontSize:13, lineHeight:1.6, marginBottom:16, background:'rgba(79,156,249,0.08)' }}>
              {hsaResult.recommendation}
            </div>
            <div className="grid-2">
              <div>
                <div className="label">Value if Reimbursed Later</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4, color:ACCENT }}>{fmt(hsaResult.future_value_if_delayed)}</div>
              </div>
              <div>
                <div className="label">Extra Value from Delaying</div>
                <div style={{ fontSize:16, fontWeight:600, marginTop:4, color:GREEN }}>{fmt(hsaResult.extra_value_from_delaying)}</div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
