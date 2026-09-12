import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY, MASK_PERCENT } from '../utils/privacy'

// Portfolio Coach (codex/portfolio-coach-recommendations). Decision
// support only -- nothing here places a trade, connects to a
// brokerage, or claims guaranteed/fiduciary advice. Every card answers
// (per the controlling spec): what changed, what's recommended, why it
// matters, what happens if you do nothing, what to do, expected effect,
// what could make it wrong, and when to review it again.

const fmt = n => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n))
const pct = n => isPrivacyMode() ? MASK_PERCENT : (n == null ? '—' : `${n}%`)

const CATEGORY_LABELS = {
  missing_data: 'Data quality', concentration_or_liquidity_risk: 'Concentration / liquidity risk',
  policy_violation: 'Policy violation', high_cost_or_redundant: 'High cost / redundant',
  new_money: 'New money', tax_advantaged_rebalance: 'Tax-advantaged rebalance',
  taxable_rebalance: 'Taxable rebalance', minor_optimization: 'Minor optimization',
}
const CATEGORY_COLORS = {
  missing_data: 'var(--red)', concentration_or_liquidity_risk: 'var(--red)',
  policy_violation: 'var(--amber)', high_cost_or_redundant: 'var(--amber)',
  new_money: 'var(--accent)', tax_advantaged_rebalance: 'var(--accent)',
  taxable_rebalance: 'var(--amber)', minor_optimization: 'var(--muted)',
}

function ActionCard({ card, onDecide, busy }) {
  const p = card.payload
  const [notes, setNotes] = useState('')
  const [showDecide, setShowDecide] = useState(false)
  return (
    <div className="card" style={{ marginBottom: 12, borderLeft: `3px solid ${CATEGORY_COLORS[card.category] || 'var(--border)'}` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, color: CATEGORY_COLORS[card.category] }}>
            {CATEGORY_LABELS[card.category] || card.category}
          </div>
          <div style={{ fontWeight: 600, fontSize: 15, marginTop: 4 }}>{p.title}</div>
          <div style={{ color: 'var(--muted)', fontSize: 13, marginTop: 6 }}>{p.action_text}</div>
          {p.current_value != null && p.target_value != null && (
            <div style={{ fontSize: 12, marginTop: 8, color: 'var(--muted)' }}>
              Current: <strong>{typeof p.current_value === 'number' && p.current_value <= 100 ? pct(p.current_value) : fmt(p.current_value)}</strong>
              {' → '}Target: <strong>{typeof p.target_value === 'number' && p.target_value <= 100 ? pct(p.target_value) : fmt(p.target_value)}</strong>
            </div>
          )}
          {p.proposed_change && (
            <div style={{ fontSize: 12, marginTop: 6 }}><strong>Proposed:</strong> {p.proposed_change}</div>
          )}
          {p.expected_effect && (
            <div style={{ fontSize: 12, marginTop: 4, color: 'var(--muted)' }}><strong>Expected effect:</strong> {p.expected_effect}</div>
          )}
          <div style={{ fontSize: 12, marginTop: 4, color: 'var(--muted)' }}>
            <strong>If you do nothing:</strong> This condition stays as-is until holdings, the policy, or a related planning input changes.
          </div>
          {p.tax_impact && (
            <div style={{ fontSize: 12, marginTop: 6, color: 'var(--amber)' }}>
              ⚠ {p.tax_impact.has_cost_basis
                ? `Estimated taxable gain: ${fmt(p.tax_impact.estimated_gain)}`
                : 'Tax impact could not be estimated — cost basis is missing (never invented).'}
            </div>
          )}
          {p.assumptions?.length > 0 && (
            <div style={{ fontSize: 11, marginTop: 8, color: 'var(--muted)' }}>
              Assumptions: {p.assumptions.join(' ')}
            </div>
          )}
          <div style={{ fontSize: 11, marginTop: 4, color: 'var(--muted)' }}>
            Confidence: {p.confidence} · Status: {card.status}
            {card.decision_date ? ` · Last decision: ${card.decision_date}` : ''}
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 140 }}>
          {card.status !== 'accepted' && (
            <button className="btn-secondary" disabled={busy} onClick={() => onDecide(card.id, 'accepted', notes)}>Accept</button>
          )}
          {card.status === 'accepted' && (
            <button className="btn-primary" disabled={busy} onClick={() => onDecide(card.id, 'completed', notes)}>Mark complete</button>
          )}
          <button className="btn-secondary" disabled={busy} onClick={() => onDecide(card.id, 'deferred', notes)}>Defer</button>
          <button className="btn-secondary" disabled={busy} onClick={() => setShowDecide(v => !v)}>Reject…</button>
        </div>
      </div>
      {showDecide && (
        <div style={{ marginTop: 10, display: 'flex', gap: 8 }}>
          <input className="input" placeholder="Reason (optional)" value={notes} onChange={e => setNotes(e.target.value)} style={{ flex: 1 }} />
          <button className="btn-secondary" disabled={busy} onClick={() => { onDecide(card.id, 'rejected', notes); setShowDecide(false) }}>Confirm reject</button>
        </div>
      )}
    </div>
  )
}

export default function PortfolioCoach() {
  const [data, setData] = useState(null)
  const [allocation, setAllocation] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [contribAmount, setContribAmount] = useState('')
  const [contribResult, setContribResult] = useState(null)
  const [filterCategory, setFilterCategory] = useState('all')

  const load = (pending = 0) => {
    setLoading(true)
    Promise.all([
      axios.get('/api/recommendations', { params: { pending_contribution: pending || 0 } }),
      axios.get('/api/portfolio/allocation').catch(() => ({ data: { has_holdings: false } })),
    ]).then(([rec, alloc]) => {
      setData(rec.data)
      setAllocation(alloc.data)
    }).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const decide = async (id, status, notes) => {
    setBusy(true)
    try {
      await axios.post(`/api/recommendations/${id}/decide`, { status, notes: notes || null })
      load()
    } finally {
      setBusy(false)
    }
  }

  const runContribution = () => {
    const amount = parseFloat(contribAmount)
    if (!amount || amount <= 0) return
    setBusy(true)
    axios.post('/api/portfolio/contribution-destination', { amount })
      .then(r => setContribResult(r.data))
      .finally(() => setBusy(false))
  }

  const cards = data?.recommendations || []
  const visible = useMemo(
    () => filterCategory === 'all' ? cards : cards.filter(c => c.category === filterCategory),
    [cards, filterCategory],
  )
  const categoriesPresent = useMemo(() => [...new Set(cards.map(c => c.category))], [cards])

  if (loading && !data) return <div className="loading">Analyzing your portfolio...</div>

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <h1 className="section-title">Portfolio Coach</h1>
        <p className="section-sub">
          Explainable, decision-support-only recommendations from your holdings, investment policy, and account
          investment options. Nothing here places a trade or connects to a brokerage — every action is yours to take.
        </p>
      </div>

      {!data?.has_policy && (
        <div className="card" style={{ marginBottom: 20, borderLeft: '3px solid var(--red)' }}>
          <strong>No investment policy saved yet.</strong> The Coach won't manufacture a target allocation from your
          age or a generic model — set a target allocation, drift band, and rebalance preferences on the Investment
          Policy page before drift, new-money, or rebalance recommendations can be generated.
        </div>
      )}

      {allocation?.has_holdings && (
        <div className="grid-4" style={{ marginBottom: 24 }}>
          <div className="card">
            <div className="label">Household investable assets</div>
            <div className="number-lg" style={{ marginTop: 8 }}>{fmt(allocation.current_allocation?.total)}</div>
          </div>
          <div className="card">
            <div className="label">Unclassified holdings</div>
            <div className="number-lg" style={{ marginTop: 8, color: allocation.unclassified_flags?.length ? 'var(--amber)' : 'var(--green)' }}>
              {allocation.unclassified_flags?.length || 0}
            </div>
          </div>
          <div className="card">
            <div className="label">Concentration flags</div>
            <div className="number-lg" style={{ marginTop: 8, color: allocation.concentration_flags?.length ? 'var(--amber)' : 'var(--green)' }}>
              {allocation.concentration_flags?.length || 0}
            </div>
          </div>
          <div className="card">
            <div className="label">Open recommendations</div>
            <div className="number-lg" style={{ marginTop: 8, color: cards.length ? 'var(--amber)' : 'var(--green)' }}>{cards.length}</div>
          </div>
        </div>
      )}

      {allocation?.has_policy && allocation?.comparison && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="label" style={{ marginBottom: 10 }}>Current vs. target allocation</div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--muted)' }}>
                  <th style={{ padding: '4px 8px' }}>Asset class</th>
                  <th style={{ padding: '4px 8px' }}>Current</th>
                  <th style={{ padding: '4px 8px' }}>Target</th>
                  <th style={{ padding: '4px 8px' }}>Drift</th>
                  <th style={{ padding: '4px 8px' }}>Within band</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(allocation.comparison.by_class || {}).map(([assetClass, d]) => (
                  <tr key={assetClass} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '4px 8px' }}>{assetClass.replace(/_/g, ' ')}</td>
                    <td style={{ padding: '4px 8px' }}>{pct(d.current_pct)}</td>
                    <td style={{ padding: '4px 8px' }}>{pct(d.target_pct)}</td>
                    <td style={{ padding: '4px 8px', color: d.within_drift_band ? 'var(--muted)' : 'var(--amber)' }}>
                      {d.deviation_pct > 0 ? '+' : ''}{isPrivacyMode() ? MASK_PERCENT : d.deviation_pct}%
                    </td>
                    <td style={{ padding: '4px 8px' }}>{d.within_drift_band ? '✓' : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card" style={{ marginBottom: 24 }}>
        <div className="label" style={{ marginBottom: 10 }}>Where should new money go?</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input className="input" type="number" placeholder="Amount to invest" value={contribAmount}
                 onChange={e => setContribAmount(e.target.value)} style={{ maxWidth: 200 }} />
          <button className="btn-primary" disabled={busy} onClick={runContribution}>Get contribution recommendation</button>
        </div>
        {contribResult && (
          <div style={{ marginTop: 14 }}>
            {(contribResult.actions || []).length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13 }}>No underweight asset classes to fund — allocation is already at or above target everywhere.</div>}
            {(contribResult.actions || []).map((a, i) => (
              <div key={i} style={{ fontSize: 13, padding: '6px 0', borderTop: i ? '1px solid var(--border)' : 'none' }}>
                <strong>{fmt(a.amount)}</strong> → {a.asset_class?.replace(/_/g, ' ')} — {a.reason}
              </div>
            ))}
          </div>
        )}
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <h2 style={{ fontSize: 16, fontWeight: 600 }}>What should I do next?</h2>
        <select className="input" value={filterCategory} onChange={e => setFilterCategory(e.target.value)} style={{ maxWidth: 260 }}>
          <option value="all">All categories ({cards.length})</option>
          {categoriesPresent.map(c => (
            <option key={c} value={c}>{CATEGORY_LABELS[c] || c} ({cards.filter(x => x.category === c).length})</option>
          ))}
        </select>
      </div>

      {visible.length === 0 && (
        <div className="card" style={{ color: 'var(--muted)' }}>
          No open recommendations right now — your portfolio matches your policy within its drift band, or there's
          nothing new to review.
        </div>
      )}
      {visible.map(card => (
        <ActionCard key={card.id} card={card} onDecide={decide} busy={busy} />
      ))}
    </div>
  )
}
