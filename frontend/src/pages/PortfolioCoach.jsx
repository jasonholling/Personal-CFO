import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { isPrivacyMode, MASK_CURRENCY, MASK_PERCENT, MASK_NUMBER, maskDigitsInText } from '../utils/privacy'

// Portfolio Coach (codex/portfolio-coach-recommendations). Decision
// support only -- nothing here places a trade, connects to a
// brokerage, or claims guaranteed/fiduciary advice. Every card answers
// (per the controlling spec): what changed, what's recommended, why it
// matters, what happens if you do nothing, what to do, expected effect,
// what could make it wrong, and when to review it again.

const fmt = n => isPrivacyMode() ? MASK_CURRENCY : (n == null ? '—' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n))
const pct = n => isPrivacyMode() ? MASK_PERCENT : (n == null ? '—' : `${n}%`)

// External review finding #11: a value's display unit is now always
// explicit on the payload (coach_engine.py's `value_unit`) -- never
// guessed from magnitude, which used to render a $50 cash balance as
// "50%" just because it happened to be <= 100.
const formatByUnit = (value, unit) => {
  if (value == null) return '—'
  switch (unit) {
    case 'percent': return pct(value)
    case 'currency': return fmt(value)
    case 'age': return isPrivacyMode() ? MASK_NUMBER : `age ${value}`
    case 'count': return isPrivacyMode() ? MASK_NUMBER : String(value)
    case 'text': return isPrivacyMode() ? maskDigitsInText(String(value)) : String(value)
    default: return typeof value === 'number' ? fmt(value) : String(value)
  }
}

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

function ActionCard({ card, onDecide, busy, onNavigate }) {
  const p = card.payload
  const [notes, setNotes] = useState('')
  const [showDecide, setShowDecide] = useState(false)
  const [reviewDate, setReviewDate] = useState('')
  // Contextual link: a data-quality card about an account's own type
  // being unresolved should jump straight to where that gets fixed
  // (Accounts), not just describe the problem.
  const isAccountTypeIssue = card.category === 'missing_data' && p.recommendation_key?.startsWith('missing_account_type')
  return (
    <div className="card" style={{ marginBottom: 12, borderLeft: `3px solid ${CATEGORY_COLORS[card.category] || 'var(--border)'}` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, color: CATEGORY_COLORS[card.category] }}>
            {CATEGORY_LABELS[card.category] || card.category}
          </div>
          <div style={{ fontWeight: 600, fontSize: 15, marginTop: 4 }}>{p.title}</div>
          <div style={{ color: 'var(--muted)', fontSize: 13, marginTop: 6 }}>{p.action_text}</div>
          {(p.current_value != null || p.target_value != null) && (
            <div style={{ fontSize: 12, marginTop: 8, color: 'var(--muted)' }}>
              {p.current_value != null && <>Current: <strong>{formatByUnit(p.current_value, p.value_unit)}</strong></>}
              {p.current_value != null && p.target_value != null && ' → '}
              {p.target_value != null && <>Target: <strong>{formatByUnit(p.target_value, p.value_unit)}</strong></>}
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
            {card.review_date ? ` · Review again: ${card.review_date}` : ''}
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 140 }}>
          {isAccountTypeIssue && (
            <button className="btn-primary" onClick={() => onNavigate?.('accounts')}>Review account type →</button>
          )}
          {card.status !== 'accepted' && (
            <button className="btn-secondary" disabled={busy} onClick={() => onDecide(card.id, 'accepted', notes)}>Accept</button>
          )}
          {card.status === 'accepted' && (
            <button className="btn-primary" disabled={busy} onClick={() => onDecide(card.id, 'completed', notes)}>Mark complete</button>
          )}
          <input aria-label="Review date" className="input" type="date" value={reviewDate} onChange={e => setReviewDate(e.target.value)} />
          <button className="btn-secondary" disabled={busy || !reviewDate} onClick={() => onDecide(card.id, 'deferred', notes, reviewDate)}>Defer until date</button>
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

export default function PortfolioCoach({ onNavigate }) {
  const [data, setData] = useState(null)
  const [allocation, setAllocation] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [contribAmount, setContribAmount] = useState('')
  const [contribResult, setContribResult] = useState(null)
  const [rebalanceResult, setRebalanceResult] = useState(null)
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

  const decide = async (id, status, notes, reviewDate = null) => {
    setBusy(true)
    try {
      await axios.post(`/api/recommendations/${id}/decide`, { status, notes: notes || null, review_date: reviewDate })
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

  const runRebalance = () => {
    setBusy(true)
    setRebalanceResult(null)
    axios.post('/api/portfolio/rebalance', { amount: parseFloat(contribAmount) || 0 })
      .then(r => setRebalanceResult(r.data))
      .finally(() => setBusy(false))
  }

  const [planningResult, setPlanningResult] = useState(null)
  const runPlanningComparison = () => {
    if (!allocation?.comparison?.by_class) return
    const proposed_allocation = Object.fromEntries(
      Object.entries(allocation.comparison.by_class).map(([c, d]) => [c, d.target_pct]),
    )
    setBusy(true)
    axios.post('/api/portfolio/planning-comparison', { proposed_allocation })
      .then(r => setPlanningResult(r.data))
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
        <div className="card" style={{ marginBottom: 20, borderLeft: '3px solid var(--red)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
          <div>
            <strong>No investment policy saved yet.</strong> The Coach won't manufacture a target allocation from your
            age or a generic model — set a target allocation, drift band, and rebalance preferences before drift,
            new-money, or rebalance recommendations can be generated.
          </div>
          <button className="btn-primary" onClick={() => onNavigate?.('portfoliosetup')}>Set investment policy →</button>
        </div>
      )}

      {allocation && allocation.has_holdings === false && (
        <div className="card" style={{ marginBottom: 20, borderLeft: '3px solid var(--amber)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
          <div><strong>No holdings entered yet.</strong> The Coach needs your account holdings before it can evaluate allocation, drift, or rebalancing.</div>
          <button className="btn-primary" onClick={() => onNavigate?.('portfoliosetup')}>Add holdings →</button>
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

      {allocation?.has_policy && allocation?.comparison && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="label" style={{ marginBottom: 10 }}>What would this policy's target mix do to my retirement plan?</div>
          <p style={{ fontSize: 12, color: 'var(--muted)', marginTop: -4, marginBottom: 10 }}>
            Compares your saved retirement assumptions against the blended expected return and volatility of your
            policy's target mix, using the same retirement/Monte Carlo/SWR engines the rest of the app uses.
          </p>
          <button className="btn-primary" disabled={busy} onClick={runPlanningComparison}>Compare planning outcomes</button>
          {planningResult && (
            <div style={{ overflowX: 'auto', marginTop: 14 }}>
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: 'var(--muted)' }}>
                    <th style={{ padding: '4px 8px' }}></th>
                    <th style={{ padding: '4px 8px' }}>Saved assumptions</th>
                    <th style={{ padding: '4px 8px' }}>Policy target mix</th>
                  </tr>
                </thead>
                <tbody>
                  <tr style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '4px 8px' }}>Monte Carlo success rate</td>
                    <td style={{ padding: '4px 8px' }}>{planningResult.baseline.monte_carlo_success_rate}%</td>
                    <td style={{ padding: '4px 8px' }}>{planningResult.proposed.monte_carlo_success_rate}%</td>
                  </tr>
                  <tr style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '4px 8px' }}>Safe withdrawal (annual)</td>
                    <td style={{ padding: '4px 8px' }}>{fmt(planningResult.baseline.safe_withdrawal_annual)}</td>
                    <td style={{ padding: '4px 8px' }}>{fmt(planningResult.proposed.safe_withdrawal_annual)}</td>
                  </tr>
                  <tr style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '4px 8px' }}>SWR cushion</td>
                    <td style={{ padding: '4px 8px' }}>{pct(planningResult.baseline.swr_cushion_pct)}</td>
                    <td style={{ padding: '4px 8px' }}>{pct(planningResult.proposed.swr_cushion_pct)}</td>
                  </tr>
                  <tr style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '4px 8px' }}>Monte Carlo median ending balance</td>
                    <td style={{ padding: '4px 8px' }}>{fmt(planningResult.baseline.monte_carlo_median_final_balance)}</td>
                    <td style={{ padding: '4px 8px' }}>{fmt(planningResult.proposed.monte_carlo_median_final_balance)}</td>
                  </tr>
                </tbody>
              </table>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 8 }}>
                Assumes the target mix's blended expected return ({(planningResult.proposed_blended_expected_return * 100).toFixed(2)}%)
                and annualized volatility ({planningResult.proposed_portfolio_volatility == null ? 'unavailable' : `${(planningResult.proposed_portfolio_volatility * 100).toFixed(2)}%`})
                are held statically through both pre- and post-retirement phases — not a post-retirement glide path.
              </div>
            </div>
          )}
        </div>
      )}

      <div className="card" style={{ marginBottom: 24 }}>
        <div className="label" style={{ marginBottom: 10 }}>Where should new money go?</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input className="input" type="number" placeholder="Amount to invest" value={contribAmount}
                 onChange={e => { setContribAmount(e.target.value); setContribResult(null); setRebalanceResult(null) }} style={{ maxWidth: 200 }} />
          <button className="btn-primary" disabled={busy} onClick={runContribution}>Get contribution recommendation</button>
        </div>
        {contribResult && (
          <div style={{ marginTop: 14 }}>
            {(contribResult.actions || []).length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13 }}>No underweight asset classes to fund — allocation is already at or above target everywhere.</div>}
            {(contribResult.actions || []).map((a, i) => (
              <div key={i} style={{ fontSize: 13, padding: '6px 0', borderTop: i ? '1px solid var(--border)' : 'none' }}>
                <strong>{fmt(a.amount)}</strong> → {a.asset_class?.replace(/_/g, ' ')}
                {a.destination ? (
                  <> — <strong>{a.destination.option_name}</strong>{a.destination.ticker ? ` (${a.destination.ticker})` : ''} in {a.destination.account_name}</>
                ) : a.asset_class && (
                  <span style={{ color: 'var(--amber)' }}> — no eligible investment option recorded for this asset class in any account yet</span>
                )}
                <div style={{ color: 'var(--muted)', fontSize: 12 }}>{a.reason}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card" style={{ marginBottom: 24 }}>
        <div className="label" style={{ marginBottom: 10 }}>Build a rebalance checklist</div>
        <p style={{ fontSize: 12, color: 'var(--muted)' }}>Uses new money first when your policy requests it, keeps exchanges inside the funding account, and flags taxable sales.</p>
        <button className="btn-primary" disabled={busy || !allocation?.has_policy} onClick={runRebalance}>Generate rebalance checklist</button>
        {rebalanceResult && (
          <div style={{ marginTop: 12 }}>
            {(rebalanceResult.rebalance_actions || []).length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13 }}>No rebalance trades are needed or no eligible same-account destination is recorded.</div>}
            {(rebalanceResult.rebalance_actions || []).map((a, i) => (
              <div key={i} style={{ borderTop: i ? '1px solid var(--border)' : 'none', padding: '7px 0', fontSize: 13 }}>
                <strong>{a.action === 'buy' ? 'Buy' : a.action === 'sell' ? 'Sell' : 'Invest'} {fmt(a.amount)}</strong>
                {' '}{a.holding_name || a.asset_class?.replace(/_/g, ' ')}
                {a.destination && <> in <strong>{a.destination.account_name}</strong> using <strong>{a.destination.option_name}</strong>{a.destination.ticker ? ` (${a.destination.ticker})` : ''}</>}
                {a.tax_warning && <div style={{ color: 'var(--amber)', fontSize: 12 }}>⚠ {a.tax_warning.message}</div>}
                <div style={{ color: 'var(--muted)', fontSize: 12 }}>{a.reason}</div>
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
        <ActionCard key={card.id} card={card} onDecide={decide} busy={busy} onNavigate={onNavigate} />
      ))}
    </div>
  )
}
