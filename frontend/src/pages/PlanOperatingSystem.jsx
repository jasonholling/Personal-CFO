import { useEffect, useState } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY } from '../utils/privacy'

const fmt = n => isPrivacyMode() ? MASK_CURRENCY : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n || 0)

const PRESETS = {
  Conservative: { expected_return_pre_retirement: .05, expected_return_post_retirement: .035, inflation_rate: .03, retirement_end_age: 100 },
  Base:         { expected_return_pre_retirement: .07, expected_return_post_retirement: .05,  inflation_rate: .025, retirement_end_age: 99 },
  Optimistic:   { expected_return_pre_retirement: .08, expected_return_post_retirement: .06,  inflation_rate: .02,  retirement_end_age: 95 },
}

// Milestone 3 (navigation consolidation, 2026-09-09): this page used to
// ALSO track estate-document readiness (a DOCS array of its own —
// "Will", "Revocable trust", etc.) via /api/estate-documents, entirely
// independently of Estate.jsx's own document tracker on the SAME
// endpoint -- different document_type keys, different status
// vocabulary, never reconciled, so a household using both pages saw
// two disconnected checklists both claiming to answer "is our estate
// plan current." That section is removed here; Estate Planning is now
// the single canonical place for document + beneficiary tracking (it
// had the richer UI already -- tax exposure, beneficiaries, action
// items). This page keeps its own distinct content (assumption
// presets/review history, financial runway, planning calendar) and
// links out to Estate Planning instead of duplicating it.
export default function PlanOperatingSystem({ onNavigate }) {
  const [inputs, setInputs] = useState(null)
  const [reviews, setReviews] = useState([])
  const [runway, setRunway] = useState(null)
  const [saved, setSaved] = useState('')

  const load = () => Promise.all([
    axios.get('/api/planning-inputs'),
    axios.get('/api/assumption-reviews'),
    axios.get('/api/financial-runway'),
  ]).then(([a, b, c]) => { setInputs(a.data); setReviews(b.data); setRunway(c.data) })

  useEffect(() => { load() }, [])

  if (!inputs) return <div className="loading">Loading plan controls...</div>

  const apply = async (name) => {
    const next = { ...inputs, ...PRESETS[name] }
    await axios.put('/api/planning-inputs', next)
    await axios.post('/api/assumption-reviews', { label: `${name} assumptions`, assumptions: PRESETS[name] })
    setInputs(next)
    setSaved(`${name} assumptions saved`)
    load()
  }

  return (
    <div>
      <div style={{ marginBottom: 28 }}>
        <h1 className="section-title">Review &amp; Decision Rules</h1>
        <p className="section-sub">Set the decision rules, review your plan's assumptions, and turn annual reviews into calendar actions.</p>
      </div>

      {saved && <div style={{ color: 'var(--green)', fontSize: 13, marginBottom: 14 }}>✓ {saved}</div>}

      <div className="grid-3" style={{ marginBottom: 24 }}>
        {Object.entries(PRESETS).map(([name, p]) => (
          <button className="card" key={name} onClick={() => apply(name)} style={{ cursor: 'pointer', color: 'var(--text)', textAlign: 'left', border: '1px solid var(--border)' }}>
            <div style={{ fontWeight: 650 }}>{name}</div>
            <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6, marginTop: 7 }}>
              Pre-retirement {(p.expected_return_pre_retirement * 100).toFixed(1)}% · retirement {(p.expected_return_post_retirement * 100).toFixed(1)}% · inflation {(p.inflation_rate * 100).toFixed(1)}% · through age {p.retirement_end_age}
            </div>
            <div style={{ color: 'var(--accent)', fontSize: 12, marginTop: 10 }}>Apply and record review →</div>
          </button>
        ))}
      </div>

      <div className="grid-2" style={{ marginBottom: 24 }}>
        <section className="card">
          <div className="label">Financial independence runway</div>
          <div style={{ fontSize: 26, fontWeight: 700, marginTop: 8, color: runway?.retirement?.percent_funded >= 100 ? 'var(--green)' : 'var(--amber)' }}>
            {runway?.retirement?.percent_funded ?? '—'}% funded at age 60
          </div>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 10, lineHeight: 1.7 }}>
            Net worth {fmt(runway?.net_worth)} · monthly cash flow {fmt(runway?.cash_flow?.monthly_surplus)} · emergency reserve {runway?.emergency?.has_data ? `${runway.emergency.months_covered} months` : 'needs current spending input'}.
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>A dashboard for decision-making, not a guarantee of investment outcomes.</div>
        </section>
        <section className="card">
          <div className="label">Planning calendar</div>
          <div style={{ fontWeight: 650, marginTop: 8 }}>Keep annual reviews in your calendar</div>
          <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.55, margin: '8px 0 14px' }}>
            Downloads a standard calendar file containing your open planning tasks. Import it into Google Calendar once; no financial data or account details are sent to Google.
          </div>
          <a className="btn-primary" href="/api/calendar/export" download="personal-cfo-planning-calendar.ics">Download calendar file</a>
        </section>
      </div>

      <section className="card" style={{ marginBottom: 24 }}>
        <div className="label" style={{ marginBottom: 12 }}>Assumption review history</div>
        {reviews.length ? reviews.map(r => (
          <div key={r.id} style={{ borderTop: '1px solid var(--border)', padding: '10px 0', display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
            <span>{r.label}</span>
            <span style={{ color: 'var(--text3)' }}>{new Date(r.created_at + 'Z').toLocaleDateString()}</span>
          </div>
        )) : (
          <div style={{ fontSize: 13, color: 'var(--text2)' }}>Apply a preset to create your first recorded review.</div>
        )}
      </section>

      <section className="card">
        <div className="label" style={{ marginBottom: 6 }}>Estate documents &amp; beneficiaries</div>
        <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 14, lineHeight: 1.6 }}>
          Document status, beneficiary designations, and estate tax exposure now live in one place — this page used to
          track a separate, disconnected copy of the same checklist.
        </div>
        <button className="btn-secondary" onClick={() => onNavigate?.('estate')}>Go to Estate Planning →</button>
      </section>
    </div>
  )
}
