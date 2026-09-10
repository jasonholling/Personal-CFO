import { useState } from 'react'
import SideBySide from './SideBySide'
import RetirementSensitivity from './RetirementSensitivity'

// Milestone 3 (navigation consolidation, 2026-09-09): Side by Side and
// Sensitivity used to be 2 of RetirementProjection.jsx's tabs -- moved
// here unchanged (no calculation change, only which parent renders
// them) because both are inherently comparison views (retirement age A
// vs. B, a continuous age slider) rather than "build the plan" — they
// belong in the Compare nav group alongside Stress Test & What-If and
// Saved Scenarios, not under Plan. See CALCULATION_CONTRACT.md's
// Milestone 3 section for the approved navigation map this implements.
const TABS = [
  { id:'sidebyside',  label:'Side by Side' },
  { id:'sensitivity', label:'Sensitivity' },
]

export default function Compare({ onNavigate }) {
  const [tab, setTab] = useState('sidebyside')

  return (
    <div>
      <div style={{ marginBottom:20 }}>
        <h1 className="section-title">Compare Scenarios</h1>
      </div>

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

      {tab === 'sidebyside'  && <SideBySide onNavigate={onNavigate} />}
      {tab === 'sensitivity' && <RetirementSensitivity onNavigate={onNavigate} />}
    </div>
  )
}
