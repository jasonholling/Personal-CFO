import { useState, useEffect } from 'react'
import axios from 'axios'
import Lock from './components/Lock'
import AuthSetup from './components/AuthSetup'
import WelcomeModal from './components/WelcomeModal'
import Dashboard from './pages/Dashboard'
import Accounts from './pages/Accounts'
import RetirementProjection from './pages/RetirementProjection'
import Compare from './pages/Compare'
import StressTestWhatIf from './pages/StressTestWhatIf'
import Education from './pages/Education'
import Kids from './pages/Kids'
import Risk from './pages/Risk'
import Estate from './pages/Estate'
import Insurance from './pages/Insurance'
import Settings from './pages/Settings'
import TaxPlanning from './pages/TaxPlanning'
import Report from './pages/Report'
import NetWorth from './pages/NetWorth'
import RothConversion from './pages/RothConversion'
import Debt from './pages/Debt'
import RetirementTools from './pages/RetirementTools'
import Allocation from './pages/Allocation'
import AnnualPlan from './pages/AnnualPlan'
import CashFlow from './pages/CashFlow'
import GoalsFunding from './pages/GoalsFunding'
import SurplusPlan from './pages/SurplusPlan'
import ProtectionScorecard from './pages/ProtectionScorecard'
import AnnualReview from './pages/AnnualReview'
import SavedScenarios from './pages/SavedScenarios'
import BackupRestore from './pages/BackupRestore'
import LifeEvents from './pages/LifeEvents'
import PlanOperatingSystem from './pages/PlanOperatingSystem'
import PortfolioCoach from './pages/PortfolioCoach'
import PortfolioSetup from './pages/PortfolioSetup'
import { usePrivacyMode } from './hooks/usePrivacyMode'
import './App.css'

// Milestone 3 (navigation consolidation, 2026-09-09): restructured from
// topic-based groups (WEALTH/RETIREMENT/EDUCATION & KIDS/PROTECTION/
// ESTATE & PLANNING) into activity-based groups (Household/Plan/
// Compare/Protect/Review), approved via a navigation-map proposal
// before any code changed (see CALCULATION_CONTRACT.md's Milestone 3
// section for the full map and the reasoning per page). Two concrete
// changes fell out of drafting that map, not just relabeling:
//   - Side by Side and Sensitivity moved out of RetirementProjection's
//     own tabs into a new Compare.jsx page (both are comparison views,
//     not plan-building) -- no calculation change, same components.
//   - PlanOperatingSystem.jsx's estate-document tracker (a second,
//     disconnected copy of what Estate.jsx already tracked, under
//     different keys) was removed in favor of Estate Planning being
//     the single canonical place -- see that file's own comment.
// Dashboard and Annual Report stay pinned outside the 5 groups (an
// entry point and a report aren't themselves one of the 5 activities).
const NAV = [
  { id:'dashboard',  label:'Dashboard',      icon:'◈' },
  { group:'HOUSEHOLD' },
  { id:'accounts',   label:'Accounts',       icon:'⊞' },
  { id:'networth',   label:'Net Worth',      icon:'◬' },
  { id:'debt',       label:'Debt Payoff',    icon:'⊝' },
  { id:'cashflow',   label:'Monthly Cash Flow', icon:'≋' },
  { id:'settings',   label:'Planning Inputs',icon:'≡' },
  { id:'backup', label:'Backup & Restore', icon:'⇩' },
  { group:'PLAN' },
  { id:'retirement', label:'Retirement Projection', icon:'◎' },
  { id:'roth',       label:'Roth Conversion', icon:'⟳' },
  { id:'tax',        label:'Tax Planning',   icon:'⊛' },
  { id:'rettools',   label:'Retirement Tools', icon:'⊚' },
  { id:'education',  label:'Education',      icon:'◇' },
  { id:'kids',       label:'Kids',           icon:'◉' },
  { id:'goals',      label:'Goals & Funding', icon:'◇' },
  { id:'surplus',    label:'Assign Surplus', icon:'+' },
  { id:'portfoliosetup', label:'Portfolio Setup', icon:'⊞' },
  { id:'coach',      label:'Portfolio Coach', icon:'◈' },
  { id:'lifeevents', label:'Life-Event Planning', icon:'◇' },
  // 'allocation' (Concentration Risk) intentionally not in the nav —
  // with Asset Allocation/Rebalancing and Investment Fee Audit already
  // hidden as not worth showing without real per-account data, the one
  // remaining section (concentration risk) wasn't judged worthwhile
  // either. Route/import/page component all still present below, so
  // this is a one-line re-enable if that changes.
  { group:'COMPARE' },
  { id:'compare',    label:'Compare Scenarios', icon:'◫' },
  { id:'stresstest',  label:'Stress Test & What-If', icon:'⊘' },
  { id:'scenarios', label:'Saved Scenarios', icon:'◫' },
  { group:'PROTECT' },
  { id:'insurance',  label:'Insurance',      icon:'⊕' },
  { id:'risk',       label:'Risk Management',icon:'⊗' },
  { id:'protection', label:'Protection Scorecard',icon:'✓' },
  { id:'estate',     label:'Estate Planning',icon:'⊙' },
  { group:'REVIEW' },
  { id:'annualplan', label:'Action Tracker', icon:'✓' },
  { id:'annualreview', label:'Annual Review Checklist', icon:'↻' },
  { id:'operating', label:'Review & Decision Rules', icon:'◉' },
]

export default function App() {
  const [page, setPage] = useState('dashboard')
  const { privacyMode, toggle: togglePrivacy } = usePrivacyMode()
  const [authState, setAuthState] = useState(null) // null = checking
  const [showWelcome, setShowWelcome] = useState(false)

  useEffect(() => {
    axios.get('/api/auth/status')
      .then(r => setAuthState(r.data))
      .catch(() => setAuthState({ auth_enabled: false, authenticated: true, webauthn_registered: false }))
  }, [])

  // First-run guidance: auto-show once the app is actually usable (past
  // auth) if the household has zero accounts and hasn't dismissed it
  // before. Also reachable anytime via the sidebar's "Getting Started" link.
  useEffect(() => {
    if (!authState || authState.setup_required || (authState.auth_enabled && !authState.authenticated)) return
    axios.get('/api/accounts').then(r => {
      if (r.data.length === 0 && !localStorage.getItem('pcfo_welcome_dismissed')) setShowWelcome(true)
    }).catch(() => {})
  }, [authState])

  if (authState === null) return null // avoid a flash of the unlocked app while checking
  if (authState.setup_required) {
    return <AuthSetup onDone={enabled => setAuthState({ auth_enabled: enabled, authenticated: true, webauthn_registered: false, setup_required: false })} />
  }
  if (authState.auth_enabled && !authState.authenticated) {
    return <Lock webauthnRegistered={authState.webauthn_registered} onUnlock={() => setAuthState(s => ({ ...s, authenticated: true }))} />
  }

  const pages = {
    dashboard:Dashboard, accounts:Accounts, retirement:RetirementProjection, compare:Compare, stresstest:StressTestWhatIf, scenarios:SavedScenarios, lifeevents:LifeEvents, operating:PlanOperatingSystem,
    education:Education, kids:Kids, insurance:Insurance,
    risk:Risk, estate:Estate, settings:Settings, backup:BackupRestore,
    tax:TaxPlanning, report:Report, networth:NetWorth, roth:RothConversion, debt:Debt,
    rettools:RetirementTools, allocation:Allocation, annualplan:AnnualPlan, annualreview:AnnualReview, cashflow:CashFlow, goals:GoalsFunding, surplus:SurplusPlan, protection:ProtectionScorecard, coach:PortfolioCoach, portfoliosetup:PortfolioSetup,
  }
  const Page = pages[page]

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-logo" style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
          <div className="logo-lockup">
            <svg className="logo-icon" width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden="true">
              <rect width="28" height="28" rx="8" fill="url(#logoBg)" />
              <path d="M6 18.5L11.5 12.5L15.5 16L22 8.5" stroke="white" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M22 13V8.5H17.5" stroke="white" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" />
              <defs>
                <linearGradient id="logoBg" x1="0" y1="28" x2="28" y2="0" gradientUnits="userSpaceOnUse">
                  <stop style={{ stopColor: 'var(--accent)' }} />
                  <stop offset="1" style={{ stopColor: 'var(--accent2)' }} />
                </linearGradient>
              </defs>
            </svg>
            <div className="logo-wordmark">
              <span className="logo-text">Personal</span>
              <span className="logo-mark">CFO</span>
            </div>
          </div>
          <div style={{ display:'flex', gap:6, flexShrink:0 }}>
            {authState?.auth_enabled && (
              <button
                onClick={() => axios.post('/api/auth/logout').finally(() => setAuthState(s => ({ ...s, authenticated: false })))}
                title="Lock the app now"
                style={{
                  display:'flex', alignItems:'center', justifyContent:'center',
                  width:30, height:30, borderRadius:8, cursor:'pointer',
                  border:'1px solid var(--border)', background:'transparent', color:'var(--text2)', fontSize:15,
                }}
              >
                🔒
              </button>
            )}
            <button
              onClick={togglePrivacy}
              title={privacyMode ? 'Privacy mode on — click to show real numbers' : 'Click to mask all numbers before sharing your screen'}
              style={{
                display:'flex', alignItems:'center', justifyContent:'center',
                width:30, height:30, borderRadius:8, cursor:'pointer',
                border: privacyMode ? '1px solid var(--accent)' : '1px solid var(--border)',
                background: privacyMode ? 'var(--accent)' : 'transparent',
                color: privacyMode ? '#fff' : 'var(--text2)',
                fontSize:15, flexShrink:0,
              }}
            >
              {privacyMode ? '🙈' : '👁'}
            </button>
          </div>
        </div>
        {privacyMode && (
          <div style={{ margin:'0 16px 10px', padding:'6px 10px', borderRadius:6, background:'rgba(79,156,249,0.12)', color:'var(--accent)', fontSize:11, fontWeight:600, textAlign:'center' }}>
            Privacy mode — numbers hidden
          </div>
        )}
        <nav className="sidebar-nav">
          {NAV.map((n, i) => n.group ? (
            // Dashboard now sits at index 0 (pinned above the 5 groups,
            // not itself one of them), so the FIRST group header is
            // index 1, not 0 -- tighter top margin there, same as
            // before Dashboard was pulled out of the group list.
            <div key={i} style={{ marginTop: i===1 ? 4 : 14, marginBottom:2 }}>
              <div style={{ fontSize:9, fontWeight:700, letterSpacing:'0.1em', color:'var(--text3)', padding:'0 16px', textTransform:'uppercase' }}>{n.group}</div>
              <div style={{ height:1, background:'var(--border)', margin:'4px 12px 2px' }} />
            </div>
          ) : (
            <button key={n.id} className={`nav-item ${page===n.id?'active':''}`} onClick={() => setPage(n.id)}>
              <span className="nav-icon">{n.icon}</span>
              <span className="nav-label">{n.label}</span>
            </button>
          ))}
          <div style={{ marginTop:'auto', paddingTop:4 }}>
            <div style={{ fontSize:9, fontWeight:700, letterSpacing:'0.1em', color:'var(--text3)', padding:'0 16px', textTransform:'uppercase' }}>REPORTS</div>
            <div style={{ height:1, background:'var(--border)', margin:'4px 12px 2px' }} />
            <button className={'nav-item ' + (page==='report'?'active':'')} onClick={() => setPage('report')} style={{ width:'100%' }}>
              <span className="nav-icon">⊛</span>
              <span className="nav-label">Annual Report</span>
            </button>
          </div>
        </nav>
        <div className="sidebar-footer">
          <button
            onClick={() => setShowWelcome(true)}
            className="sidebar-footer-text"
            style={{ background:'transparent', border:'none', padding:0, textAlign:'left', cursor:'pointer', color:'var(--text2)' }}
            onMouseEnter={e => e.currentTarget.style.color='var(--accent)'}
            onMouseLeave={e => e.currentTarget.style.color='var(--text2)'}
          >
            Getting Started
          </button>
          <div className="sidebar-footer-sub">All data stays local, your Mac only</div>
        </div>
      </aside>
      {showWelcome && <WelcomeModal onNavigate={setPage} onClose={() => setShowWelcome(false)} />}
      <main className="main-content">
        {/* key forces a remount on privacy-mode toggle so every page's
            local fmt()/fmtK() re-evaluate against the new flag — see
            utils/privacy.js for why they aren't hook-driven directly. */}
        <Page key={privacyMode ? 'private' : 'normal'} onNavigate={setPage} />
      </main>
    </div>
  )
}
