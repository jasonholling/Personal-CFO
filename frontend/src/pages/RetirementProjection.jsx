import { useState } from 'react'
import Retirement from './Retirement'
import SideBySide from './SideBySide'
import RetirementSensitivity from './RetirementSensitivity'

// Combines Retirement + Side by Side + Age Sensitivity under one nav entry —
// these were 3 separate top-level tabs, but they're all the same base
// retirement projection viewed 3 different ways (single-scenario detail,
// side-by-side comparison, continuous age slider), with no extra
// user-adjustable assumptions beyond retirement age / SS timing (shared via
// useScenario). Part of the RETIREMENT nav consolidation alongside
// Stress Test & What-If.
const TABS = [
  { id:'overview',    label:'Overview' },
  { id:'sidebyside',  label:'Side by Side' },
  { id:'sensitivity', label:'Sensitivity' },
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
      {tab === 'sidebyside'  && <SideBySide onNavigate={onNavigate} />}
      {tab === 'sensitivity' && <RetirementSensitivity onNavigate={onNavigate} />}
    </div>
  )
}
