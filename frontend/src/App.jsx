import { useState, useEffect } from 'react'
import axios from 'axios'
import Lock from './components/Lock'
import AuthSetup from './components/AuthSetup'
import Dashboard from './pages/Dashboard'
import Accounts from './pages/Accounts'
import RetirementProjection from './pages/RetirementProjection'
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
import { usePrivacyMode } from './hooks/usePrivacyMode'
import './App.css'

// RETIREMENT used to have 9 top-level tabs; 5 of them (Retirement, Side by
// Side, Age Sensitivity, What-If Builder, Retirement Sim) were all the same
// base projection viewed different ways. Consolidated into 2 tabbed pages
// (RetirementProjection, StressTestWhatIf) — the other 4 (Roth Conversion,
// Tax Planning, Retirement Tools, Allocation & Fees) are genuinely distinct
// financial questions, not just different views, so they stayed separate.
const NAV = [
  { group:'WEALTH' },
  { id:'dashboard',  label:'Dashboard',      icon:'◈' },
  { id:'accounts',   label:'Accounts',       icon:'⊞' },
  { id:'networth',   label:'Net Worth',      icon:'◬' },
  { id:'debt',       label:'Debt Payoff',    icon:'⊝' },
  { id:'cashflow',   label:'Monthly Cash Flow', icon:'≋' },
  { id:'goals',      label:'Goals & Funding', icon:'◇' },
  { id:'surplus',    label:'Assign Surplus', icon:'+' },
  { id:'annualplan', label:'Action Tracker', icon:'✓' },
  { id:'annualreview', label:'Annual Review Checklist', icon:'↻' },
  { group:'RETIREMENT' },
  { id:'retirement', label:'Retirement Projection', icon:'◎' },
  { id:'stresstest',  label:'Stress Test & What-If', icon:'⊘' },
  { id:'scenarios', label:'Saved Scenarios', icon:'◫' },
  { id:'lifeevents', label:'Life-Event Planning', icon:'◇' },
  { id:'operating', label:'CFO Operating System', icon:'◉' },
  { id:'roth',       label:'Roth Conversion', icon:'⟳' },
  { id:'tax',        label:'Tax Planning',   icon:'⊛' },
  { id:'rettools',   label:'Retirement Tools', icon:'⊚' },
  // 'allocation' (Concentration Risk) intentionally not in the nav —
  // with Asset Allocation/Rebalancing and Investment Fee Audit already
  // hidden as not worth showing without real per-account data, the one
  // remaining section (concentration risk) wasn't judged worthwhile
  // either. Route/import/page component all still present below, so
  // this is a one-line re-enable if that changes.
  { group:'EDUCATION & KIDS' },
  { id:'education',  label:'Education',      icon:'◇' },
  { id:'kids',       label:'Kids',           icon:'◉' },
  { group:'PROTECTION' },
  { id:'insurance',  label:'Insurance',      icon:'⊕' },
  { id:'risk',       label:'Risk Management',icon:'⊗' },
  { id:'protection', label:'Protection Scorecard',icon:'✓' },
  { group:'ESTATE & PLANNING' },
  { id:'estate',     label:'Estate Planning',icon:'⊙' },
  { id:'settings',   label:'Planning Inputs',icon:'≡' },
  { id:'backup', label:'Backup & Restore', icon:'⇩' },
]

export default function App() {
  const [page, setPage] = useState('dashboard')
  const { privacyMode, toggle: togglePrivacy } = usePrivacyMode()
  const [authState, setAuthState] = useState(null) // null = checking

  useEffect(() => {
    axios.get('/api/auth/status')
      .then(r => setAuthState(r.data))
      .catch(() => setAuthState({ auth_enabled: false, authenticated: true, webauthn_registered: false }))
  }, [])

  if (authState === null) return null // avoid a flash of the unlocked app while checking
  if (authState.setup_required) {
    return <AuthSetup onDone={enabled => setAuthState({ auth_enabled: enabled, authenticated: true, webauthn_registered: false, setup_required: false })} />
  }
  if (authState.auth_enabled && !authState.authenticated) {
    return <Lock webauthnRegistered={authState.webauthn_registered} onUnlock={() => setAuthState(s => ({ ...s, authenticated: true }))} />
  }

  const pages = {
    dashboard:Dashboard, accounts:Accounts, retirement:RetirementProjection, stresstest:StressTestWhatIf, scenarios:SavedScenarios, lifeevents:LifeEvents, operating:PlanOperatingSystem,
    education:Education, kids:Kids, insurance:Insurance,
    risk:Risk, estate:Estate, settings:Settings, backup:BackupRestore,
    tax:TaxPlanning, report:Report, networth:NetWorth, roth:RothConversion, debt:Debt,
    rettools:RetirementTools, allocation:Allocation, annualplan:AnnualPlan, annualreview:AnnualReview, cashflow:CashFlow, goals:GoalsFunding, surplus:SurplusPlan, protection:ProtectionScorecard,
  }
  const Page = pages[page]

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-logo" style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
          <div>
            <span className="logo-mark">CFO</span>
            <span className="logo-text">Personal</span>
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
            <div key={i} style={{ marginTop: i===0 ? 4 : 14, marginBottom:2 }}>
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
          <div className="sidebar-footer-text">All data stays local</div>
          <div className="sidebar-footer-sub">Your Mac only</div>
        </div>
      </aside>
      <main className="main-content">
        {/* key forces a remount on privacy-mode toggle so every page's
            local fmt()/fmtK() re-evaluate against the new flag — see
            utils/privacy.js for why they aren't hook-driven directly. */}
        <Page key={privacyMode ? 'private' : 'normal'} onNavigate={setPage} />
      </main>
    </div>
  )
}
