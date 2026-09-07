/* One-time first-run guidance: "here's where to start." Auto-shows when
 * the household has zero accounts and hasn't dismissed it before (tracked
 * client-side in localStorage — this is pure UI guidance, not data, so it
 * doesn't need a backend flag). Also reachable anytime via the sidebar's
 * "Getting Started" link, regardless of account count or prior dismissal. */
const STEPS = [
  { icon: '⊞', title: 'Add your accounts', desc: 'Checking, investments, real estate, and debt — everything the plan is built on.', page: 'accounts' },
  { icon: '≡', title: 'Set your planning assumptions', desc: 'Ages, income, retirement age, Social Security timing — the inputs every projection uses.', page: 'settings' },
  { icon: '≋', title: 'Add your monthly expenses', desc: "Recurring income and spending, so the plan knows what's actually available each month.", page: 'cashflow' },
  { icon: '◈', title: 'Review your CFO Briefing', desc: "Once the basics are in, the Dashboard ranks exactly what to do next — automatically.", page: 'dashboard' },
]

export default function WelcomeModal({ onNavigate, onClose }) {
  const dismiss = () => {
    localStorage.setItem('pcfo_welcome_dismissed', '1')
    onClose()
  }
  const go = (page) => {
    localStorage.setItem('pcfo_welcome_dismissed', '1')
    onNavigate(page)
    onClose()
  }

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, padding: 20 }}
      onClick={dismiss}
    >
      <div className="card" style={{ maxWidth: 480, width: '100%', position: 'relative' }} onClick={e => e.stopPropagation()}>
        <button
          onClick={dismiss}
          title="Close"
          style={{ position: 'absolute', top: 14, right: 14, background: 'transparent', color: 'var(--text3)', fontSize: 18, width: 28, height: 28, borderRadius: 8, lineHeight: 1 }}
        >
          ×
        </button>
        <div style={{ fontSize: 26, marginBottom: 8, color: 'var(--accent)' }}>◈</div>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 400, color: 'var(--text)', marginBottom: 6 }}>
          Welcome to Personal CFO
        </h2>
        <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 22 }}>
          Here's where to start — four steps, in order:
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 24 }}>
          {STEPS.map((s, i) => (
            <button
              key={s.page}
              onClick={() => go(s.page)}
              style={{
                display: 'flex', gap: 14, textAlign: 'left', alignItems: 'flex-start',
                background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '12px 14px',
              }}
            >
              <div style={{
                width: 24, height: 24, borderRadius: 7, background: 'var(--bg2)', color: 'var(--accent)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, flexShrink: 0, marginTop: 1,
              }}>
                {i + 1}
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text)', marginBottom: 2 }}>{s.title}</div>
                <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.5 }}>{s.desc}</div>
              </div>
              <div style={{ color: 'var(--text3)', fontSize: 14, alignSelf: 'center', flexShrink: 0 }}>→</div>
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button className="btn-secondary" onClick={dismiss}>Got it — I'll explore</button>
        </div>
      </div>
    </div>
  )
}
