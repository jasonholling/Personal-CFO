import { useState, useEffect } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt  = (n) => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:0 }).format(n))
const fmtMonths = (n) => n == null ? 'beyond 50 years' : n < 12 ? `${n} mo` : `${Math.floor(n/12)}yr ${n%12}mo`

const GREEN  = '#34d399'
const AMBER  = '#fbbf24'
const RED    = '#f87171'
const ACCENT = '#4f9cf9'

const DEBT_TYPES = [
  { value: 'credit_card',   label: 'Credit Card' },
  { value: 'student_loan',  label: 'Student Loan' },
  { value: 'car_loan',      label: 'Car Loan' },
  { value: 'personal_loan', label: 'Personal Loan' },
  { value: 'mortgage',      label: 'Mortgage' },
]

const EMPTY_DEBT = { name: '', account_type: 'credit_card', owner: 'joint', institution: '', balance: '', interest_rate: '', minimum_payment: '', term_months: '', notes: '' }

export default function Debt() {
  const [debts, setDebts]           = useState([])
  const [rec, setRec]               = useState(null)
  const [extra, setExtra]           = useState(200)
  const [loading, setLoading]       = useState(true)
  const [showAll, setShowAll]       = useState(false)
  const [form, setForm]             = useState(null) // null = form hidden
  const [editing, setEditing]       = useState(null)
  const [refi, setRefi]             = useState({ balance:'', current_rate:'', new_rate:'', term_years:30, closing_costs:'' })
  const [refiResult, setRefiResult] = useState(null)

  const load = () => {
    setLoading(true)
    Promise.all([
      axios.get('/api/accounts'),
      axios.get(`/api/debts/recommendation?extra_monthly=${extra || 0}`),
    ]).then(([accountsRes, recRes]) => {
      const debtTypeValues = new Set(DEBT_TYPES.map(t => t.value))
      setDebts(accountsRes.data.filter(a => debtTypeValues.has(a.account_type)))
      setRec(recRes.data)
      setLoading(false)
      // Pre-fill the refinance calculator from an existing mortgage account
      // instead of starting blank — still editable, since you might be
      // pricing a refi on a different balance than what's on the books.
      const mortgage = accountsRes.data.find(a => a.account_type === 'mortgage')
      if (mortgage) {
        setRefi(r => ({
          ...r,
          balance: mortgage.balance ? mortgage.balance.toString() : r.balance,
          current_rate: mortgage.interest_rate ? (mortgage.interest_rate * 100).toString() : r.current_rate,
        }))
      }
    }).catch(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const saveDebt = async () => {
    const payload = {
      ...form,
      balance: parseFloat(form.balance) || 0,
      interest_rate: (parseFloat(form.interest_rate) || 0) / 100,
      minimum_payment: parseFloat(form.minimum_payment) || 0,
      term_months: parseInt(form.term_months) || 0,
    }
    if (editing !== null) await axios.put(`/api/accounts/${editing}`, payload)
    else await axios.post('/api/accounts', payload)
    setForm(null)
    setEditing(null)
    load()
  }

  const editDebt = (d) => {
    setForm({
      ...d,
      balance: d.balance.toString(),
      interest_rate: d.interest_rate ? (d.interest_rate * 100).toString() : '',
      minimum_payment: d.minimum_payment ? d.minimum_payment.toString() : '',
      term_months: d.term_months ? d.term_months.toString() : '',
    })
    setEditing(d.id)
  }

  const deleteDebt = async (id) => {
    if (!confirm('Delete this debt?')) return
    await axios.delete(`/api/accounts/${id}`)
    load()
  }

  const runRefiCalc = () => {
    axios.post('/api/debts/refinance-analysis', {
      balance: parseFloat(refi.balance) || 0,
      current_rate: (parseFloat(refi.current_rate) || 0) / 100,
      new_rate: (parseFloat(refi.new_rate) || 0) / 100,
      term_years: parseInt(refi.term_years) || 30,
      closing_costs: parseFloat(refi.closing_costs) || 0,
    }).then(r => setRefiResult(r.data))
  }

  const suggestPayment = async () => {
    if (!form.balance || !form.interest_rate || !form.term_months) return
    const r = await axios.post('/api/debts/suggest-minimum-payment', {
      balance: parseFloat(form.balance) || 0,
      interest_rate: (parseFloat(form.interest_rate) || 0) / 100,
      term_months: parseInt(form.term_months) || 0,
    })
    setForm(f => ({ ...f, minimum_payment: r.data.suggested_minimum_payment.toString() }))
  }

  if (loading) return <div className="loading">Building your payoff plan...</div>

  return (
    <div>
      <div style={{ marginBottom:28 }}>
        <h1 className="section-title">Debt Payoff</h1>
        <p className="section-sub">Enter your real debts and get a specific plan to be debt-free</p>
      </div>

      {/* Debt list + add/edit form */}
      <div className="card" style={{ marginBottom:24 }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:16 }}>
          <div className="label">Your Debts</div>
          <button className="btn-secondary" onClick={() => { setForm(EMPTY_DEBT); setEditing(null) }}>+ Add Debt</button>
        </div>

        {form && (
          <div className="card" style={{ marginBottom:16, background:'var(--bg2)' }}>
            <div className="grid-3" style={{ marginBottom:10 }}>
              <div>
                <div className="label" style={{ marginBottom:6 }}>Name</div>
                <input value={form.name} onChange={e => setForm({ ...form, name:e.target.value })} placeholder="e.g. Chase Sapphire" />
              </div>
              <div>
                <div className="label" style={{ marginBottom:6 }}>Type</div>
                <select value={form.account_type} onChange={e => setForm({ ...form, account_type:e.target.value })}>
                  {DEBT_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </div>
              <div>
                <div className="label" style={{ marginBottom:6 }}>Institution</div>
                <input value={form.institution} onChange={e => setForm({ ...form, institution:e.target.value })} placeholder="e.g. Chase" />
              </div>
              <div>
                <div className="label" style={{ marginBottom:6 }}>Current Balance ($)</div>
                <input type="number" value={form.balance} onChange={e => setForm({ ...form, balance:e.target.value })} placeholder="5000" />
              </div>
              <div>
                <div className="label" style={{ marginBottom:6 }}>Interest Rate (APR %)</div>
                <input type="number" step="0.01" value={form.interest_rate} onChange={e => setForm({ ...form, interest_rate:e.target.value })} placeholder="18.99" />
              </div>
              <div>
                <div className="label" style={{ marginBottom:6 }}>Remaining Term (months, optional)</div>
                <input type="number" value={form.term_months} onChange={e => setForm({ ...form, term_months:e.target.value })} placeholder="e.g. 48" />
              </div>
              <div>
                <div className="label" style={{ marginBottom:6 }}>
                  Minimum Payment ($/mo)
                  {form.balance && form.interest_rate && form.term_months && (
                    <button onClick={suggestPayment} style={{ marginLeft:8, fontSize:11, color:'var(--accent)', background:'none', border:'none', cursor:'pointer', padding:0 }}>
                      calculate from term
                    </button>
                  )}
                </div>
                <input type="number" value={form.minimum_payment} onChange={e => setForm({ ...form, minimum_payment:e.target.value })} placeholder="150" />
              </div>
              <div style={{ gridColumn:'span 2' }}>
                <div className="label" style={{ marginBottom:6 }}>Notes (optional)</div>
                <input value={form.notes || ''} onChange={e => setForm({ ...form, notes:e.target.value })} placeholder="Any notes" />
              </div>
            </div>
            <div style={{ display:'flex', gap:8 }}>
              <button className="btn-primary" onClick={saveDebt}>Save Debt</button>
              <button className="btn-secondary" onClick={() => { setForm(null); setEditing(null) }}>Cancel</button>
            </div>
          </div>
        )}

        {debts.length === 0 && !form && (
          <div style={{ fontSize:13, color:'var(--text2)', padding:'16px 0' }}>
            No debts entered yet. Add a credit card, student loan, car loan, personal loan, or mortgage above with its real balance, rate, and minimum payment to get a plan.
          </div>
        )}

        {debts.length > 0 && (
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%', borderCollapse:'collapse' }}>
              <thead>
                <tr style={{ borderBottom:'1px solid var(--border)' }}>
                  {['Name','Type','Balance','APR','Min. Payment','Term',''].map(h => (
                    <th key={h} style={{ padding:'8px 12px', textAlign:'left', fontSize:11, fontWeight:600, letterSpacing:'0.05em', textTransform:'uppercase', color:'var(--text3)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {debts.map(d => (
                  <tr key={d.id} style={{ borderBottom:'1px solid var(--border)' }}>
                    <td style={{ padding:'10px 12px', fontSize:13, fontWeight:500 }}>{d.name}</td>
                    <td style={{ padding:'10px 12px', fontSize:12, color:'var(--text2)' }}>{DEBT_TYPES.find(t=>t.value===d.account_type)?.label || d.account_type}</td>
                    <td style={{ padding:'10px 12px', fontSize:13 }}>{fmt(d.balance)}</td>
                    <td style={{ padding:'10px 12px', fontSize:13 }}>{d.interest_rate ? `${(d.interest_rate*100).toFixed(2)}%` : '—'}</td>
                    <td style={{ padding:'10px 12px', fontSize:13 }}>{d.minimum_payment ? `${fmt(d.minimum_payment)}/mo` : '—'}</td>
                    <td style={{ padding:'10px 12px', fontSize:13 }}>{d.term_months ? `${d.term_months} mo` : '—'}</td>
                    <td style={{ padding:'10px 12px', textAlign:'right' }}>
                      <button className="btn-secondary" style={{ marginRight:6 }} onClick={() => editDebt(d)}>Edit</button>
                      <button className="btn-secondary" onClick={() => deleteDebt(d.id)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {rec?.has_debt && (
        <>
          {/* Negative amortization warning */}
          {rec.negative_amortization_debts.length > 0 && (
            <div className="card" style={{ marginBottom:24, borderLeft:`3px solid ${RED}` }}>
              <div style={{ fontWeight:600, color:RED, marginBottom:6 }}>⚠ Minimum payment doesn't cover interest</div>
              {rec.negative_amortization_debts.map(d => (
                <div key={d.id} style={{ fontSize:13, color:'var(--text2)' }}>
                  <strong>{d.name}</strong>: paying {fmt(d.minimum_payment)}/mo but accruing ~{fmt(d.monthly_interest)}/mo in interest — this balance will grow, not shrink, at that payment level.
                </div>
              ))}
            </div>
          )}

          {/* Extra payment input */}
          <div className="card" style={{ marginBottom:24 }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
              <div className="label">Extra Monthly Payment (beyond minimums)</div>
              <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                <span style={{ fontSize:12, color:'var(--text3)' }}>$</span>
                <input type="number" value={extra} onChange={e => setExtra(e.target.value)} style={{ width:100, textAlign:'right' }} />
                <button className="btn-primary" onClick={load}>Update Plan</button>
              </div>
            </div>
          </div>

          {/* The recommendation — the actual verdict */}
          <div className="card" style={{ marginBottom:24, borderTop:`3px solid ${ACCENT}` }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:16 }}>
              <div>
                <div className="label">Recommended Strategy</div>
                <div style={{ fontFamily:'var(--font-display)', fontSize:26, marginTop:6, textTransform:'capitalize' }}>{rec.strategy}</div>
              </div>
              <div style={{ textAlign:'right' }}>
                <div className="label">Debt-Free By</div>
                <div style={{ fontSize:22, fontWeight:700, color:GREEN, marginTop:6 }}>{rec.debt_free_date || fmtMonths(rec.months_to_debt_free)}</div>
              </div>
            </div>

            <p style={{ fontSize:13, color:'var(--text2)', lineHeight:1.6, marginBottom:16 }}>{rec.reason}</p>

            <div className="grid-3" style={{ marginBottom:16 }}>
              <div>
                <div className="label">Total Debt</div>
                <div style={{ fontSize:18, fontWeight:600, marginTop:4 }}>{fmt(rec.total_balance)}</div>
              </div>
              <div>
                <div className="label">Total Interest You'll Pay</div>
                <div style={{ fontSize:18, fontWeight:600, marginTop:4 }}>{fmt(rec.total_interest_paid)}</div>
              </div>
              <div>
                <div className="label">Monthly Commitment</div>
                <div style={{ fontSize:18, fontWeight:600, marginTop:4 }}>{fmt(rec.total_minimum_payment + (rec.extra_monthly || 0))}/mo</div>
              </div>
            </div>
            {rec.surplus_debt_payoff_monthly > 0 && (
              <div style={{ fontSize:11, color:'var(--text3)', marginTop:-8, marginBottom:16 }}>
                Includes {fmt(rec.surplus_debt_payoff_monthly)}/mo from the "High-interest debt payoff" goal on the Surplus Plan page, on top of the {fmt(rec.manual_extra_monthly || 0)}/mo entered above.
              </div>
            )}

            {rec.focus_first && (
              <div style={{ padding:'12px 16px', background:'rgba(79,156,249,0.08)', borderRadius:8, fontSize:13 }}>
                <strong>Next action:</strong> put every extra dollar toward <strong>{rec.focus_first.name}</strong> until it's gone (~{fmtMonths(rec.focus_first.payoff_month)} from now), while paying minimums on everything else.
              </div>
            )}
          </div>

          {/* Life-event debt paydowns — distinct from the manual extra_monthly input above */}
          {rec.life_event_debt_payments?.length > 0 && (
            <div className="card" style={{ marginBottom:24, borderLeft:`3px solid ${GREEN}` }}>
              <div className="label" style={{ marginBottom:12 }}>From Life Events</div>
              {rec.life_event_debt_payments.map(p => (
                <div key={p.account_id} style={{ fontSize:13, color:'var(--text2)', padding:'8px 0', borderBottom:'1px solid var(--border)' }}>
                  A one-time payment from a life event accelerates <strong>{p.name}</strong>:{' '}
                  {p.new_payoff_month != null ? `paid off in ${fmtMonths(p.new_payoff_month)}` : 'still beyond 50 years'}
                  {p.original_payoff_month != null && p.months_saved != null && ` (${p.months_saved} month${p.months_saved === 1 ? '' : 's'} sooner than without it)`}
                  {p.interest_saved > 0 && `, saving ~${fmt(p.interest_saved)} in interest`}.
                </div>
              ))}
              <div style={{ fontSize:11, color:'var(--text3)', marginTop:8 }}>Set up on the Life Events page — separate from the manual extra monthly payment above.</div>
            </div>
          )}

          {/* Full payoff order */}
          <div className="card" style={{ marginBottom:24 }}>
            <div className="label" style={{ marginBottom:16 }}>Payoff Order</div>
            {rec.payoff_order.map((d, i) => (
              <div key={d.id} style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'10px 0', borderBottom:i < rec.payoff_order.length-1 ? '1px solid var(--border)' : 'none' }}>
                <div style={{ display:'flex', alignItems:'center', gap:10 }}>
                  <span className="tag tag-blue">{i+1}</span>
                  <span style={{ fontSize:13 }}>{d.name}</span>
                </div>
                <span style={{ fontSize:12, color:'var(--text2)' }}>paid off in {fmtMonths(d.payoff_month)}</span>
              </div>
            ))}
          </div>

          {/* Transparency: show the comparison that led to the recommendation */}
          <div className="card" style={{ marginBottom:24 }}>
            <button onClick={() => setShowAll(!showAll)} style={{ background:'none', border:'none', color:'var(--accent)', fontSize:12, cursor:'pointer', padding:0, marginBottom: showAll ? 16 : 0 }}>
              {showAll ? '▾' : '▸'} How this compares to the other strategy
            </button>
            {showAll && (
              <div className="grid-2">
                <div>
                  <div className="label" style={{ marginBottom:8, color: rec.strategy==='avalanche' ? ACCENT : 'var(--text2)' }}>Avalanche (highest rate first)</div>
                  <div style={{ fontSize:13 }}>{fmtMonths(rec.avalanche_summary.months_to_debt_free)} · {fmt(rec.avalanche_summary.total_interest_paid)} interest</div>
                </div>
                <div>
                  <div className="label" style={{ marginBottom:8, color: rec.strategy==='snowball' ? ACCENT : 'var(--text2)' }}>Snowball (smallest balance first)</div>
                  <div style={{ fontSize:13 }}>{fmtMonths(rec.snowball_summary.months_to_debt_free)} · {fmt(rec.snowball_summary.total_interest_paid)} interest</div>
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {/* Refinance break-even — a different question from the payoff plan above */}
      <div className="card" style={{ marginBottom:24 }}>
        <div className="label" style={{ marginBottom:16 }}>Should I Refinance a Loan?</div>
        <div className="grid-4" style={{ marginBottom:12 }}>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Loan Balance ($)</div>
            <input type="number" value={refi.balance} onChange={e => setRefi({ ...refi, balance:e.target.value })} placeholder="300000" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Current Rate (%)</div>
            <input type="number" step="0.01" value={refi.current_rate} onChange={e => setRefi({ ...refi, current_rate:e.target.value })} placeholder="7.0" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>New Rate Offer (%)</div>
            <input type="number" step="0.01" value={refi.new_rate} onChange={e => setRefi({ ...refi, new_rate:e.target.value })} placeholder="5.5" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Term (years)</div>
            <input type="number" value={refi.term_years} onChange={e => setRefi({ ...refi, term_years:e.target.value })} placeholder="30" />
          </div>
          <div>
            <div className="label" style={{ marginBottom:6 }}>Closing Costs ($)</div>
            <input type="number" value={refi.closing_costs} onChange={e => setRefi({ ...refi, closing_costs:e.target.value })} placeholder="5000" />
          </div>
        </div>
        <button className="btn-primary" onClick={runRefiCalc}>Calculate</button>
        {refiResult && (
          <div className="grid-3" style={{ marginTop:16 }}>
            <div>
              <div className="label">Monthly Savings</div>
              <div style={{ fontSize:14, marginTop:4 }}>{fmt(refiResult.monthly_savings)}/mo</div>
            </div>
            <div>
              <div className="label">Break-Even</div>
              <div style={{ fontSize:14, marginTop:4 }}>{fmtMonths(refiResult.breakeven_months)}</div>
            </div>
            <div>
              <div className="label">Worth It?</div>
              <div style={{ fontSize:14, marginTop:4, fontWeight:600, color: refiResult.worth_it ? GREEN : AMBER }}>
                {refiResult.worth_it ? `✓ Saves ${fmt(refiResult.total_savings_over_term)} over the loan term` : '⚠ Breaks even too late relative to the loan term'}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
