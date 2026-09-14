import { useState } from 'react'
import Estate from './Estate'
import Insurance from './Insurance'
import Risk from './Risk'
import ProtectionScorecard from './ProtectionScorecard'

// Consolidation (2026-09-14, at the user's request to reduce PLAN group
// density while keeping the existing planning -> doing -> settings-at-
// bottom flow) -- Estate Planning/Insurance/Risk Management/Protection
// Scorecard used to be 4 separate PLAN nav entries all about the same
// subject (what happens if something goes wrong: death, disability,
// liability, lawsuit). Folded into one page's tabs, same pattern as
// RetirementProjection.jsx/Compare.jsx: no calculation change, each
// page moved unchanged, only which parent renders it changed. The
// standalone 'estate'/'insurance'/'risk'/'protection' page ids still
// exist in App.jsx's route map for existing onNavigate(...) deep links
// (e.g. the Scorecard's own "Review coverage"/"Review estate plan"
// buttons), which still land on the right content -- they just render
// outside this tab chrome.
const TABS = [
  { id:'estate',     label:'Estate Planning' },
  { id:'insurance',  label:'Insurance' },
  { id:'risk',       label:'Risk Management' },
  { id:'scorecard',  label:'Scorecard' },
]

export default function Protection({ onNavigate }) {
  const [tab, setTab] = useState('scorecard')

  return (
    <div>
      <div style={{ marginBottom:20 }}>
        <h1 className="section-title">Protection & Estate</h1>
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

      {tab === 'scorecard'  && <ProtectionScorecard onNavigate={onNavigate} hideTitle />}
      {tab === 'estate'     && <Estate hideTitle />}
      {tab === 'insurance'  && <Insurance onNavigate={onNavigate} hideTitle />}
      {tab === 'risk'       && <Risk hideTitle />}
    </div>
  )
}
