import { useState } from 'react'
import Retirement from './Retirement'
import TwoAgeScenario from './TwoAgeScenario'

// Milestone 3 (navigation consolidation, 2026-09-09): Side by Side and
// Sensitivity moved OUT of this page's own tabs into the new Compare.jsx
// page (Compare nav group) -- both are inherently comparison views (one
// retirement age vs. another), not a "build the plan" activity, and
// belong with Stress Test & What-If/Saved Scenarios instead of here. No
// calculation change: SideBySide.jsx/RetirementSensitivity.jsx moved
// unchanged, only which parent renders them changed. See
// CALCULATION_CONTRACT.md's Milestone 3 section for the full navigation
// map this was approved against.
const TABS = [
  { id:'overview',    label:'Overview' },
  // Two-Age Scenario is a deliberately separate, additive tool (backend/
  // docs/TWO_DIMENSIONAL_RETIREMENT_DESIGN.md section 7) -- Overview
  // still models a single household retirement date. Kept as its own
  // tab rather than folded in, so the two models stay visibly distinct
  // instead of quietly merged.
  { id:'twoage',      label:'Two-Age Scenario' },
]

export default function RetirementProjection({ onNavigate }) {
  const [tab, setTab] = useState('overview')

  return (
    <div>
      <div style={{ marginBottom:20 }}>
        <h1 className="section-title">Retirement Projection</h1>
      </div>

      {/* Tab switcher */}
      <div style={{ display:'flex', gap:4, marginBottom:24, borderBottom:'1px solid var(--border)', paddingBottom:0 }}>
        {TABS.map(t => (
          <button key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              background:'none', border:'none', padding:'10px 20px', cursor:'pointer',
              fontSize:13, fontWeight:600,
              color: tab===t.id ? 'var(--accent)' : 'var(--text2)',
              borderBottom: tab===t.id ? '2px solid var(--accent)' : '2px solid transparent',
              marginBottom:-1, transition:'all 0.15s',
            }}
          >{t.label}</button>
        ))}
      </div>

      {tab === 'overview'    && <Retirement onNavigate={onNavigate} />}
      {tab === 'twoage'      && <TwoAgeScenario />}
    </div>
  )
}
