import { useState } from 'react'
import Education from './Education'
import Kids from './Kids'
import GoalsFunding from './GoalsFunding'

// Consolidation (2026-09-14, at the user's request to reduce PLAN group
// density while keeping the existing planning -> doing -> settings-at-
// bottom flow) -- Education/Kids/Goals & Funding used to be 3 separate
// PLAN nav entries about the same subject (the kids' own money: 529s,
// custodial/Roth accounts, funding-gap goals). Folded into one page's
// tabs, same pattern as RetirementProjection.jsx/Compare.jsx: no
// calculation change, each page moved unchanged, only which parent
// renders it changed. The standalone 'education'/'kids'/'goals' page
// ids still exist in App.jsx's route map for existing onNavigate(...)
// deep links (e.g. Dashboard's education card), which still land on
// the right content -- they just render outside this tab chrome.
const TABS = [
  { id:'education', label:'Education' },
  { id:'kids',       label:'Kids' },
  { id:'goals',      label:'Goals & Funding' },
]

export default function Family({ onNavigate }) {
  const [tab, setTab] = useState('education')

  return (
    <div>
      <div style={{ marginBottom:20 }}>
        <h1 className="section-title">Family & Education</h1>
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

      {tab === 'education' && <Education onNavigate={onNavigate} hideTitle />}
      {tab === 'kids'      && <Kids hideTitle />}
      {tab === 'goals'     && <GoalsFunding onNavigate={onNavigate} hideTitle />}
    </div>
  )
}
