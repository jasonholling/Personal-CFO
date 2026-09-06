import { useState } from 'react'
import axios from 'axios'

/**
 * First-run gate — shown once, before any /api route other than
 * /api/auth/status and /api/auth/setup will respond (see backend's
 * auth.setup_required()). Forces a deliberate choice instead of the app
 * silently opening unlocked on a fresh clone: either set a passphrase now
 * (Touch ID can be added afterward from the Lock screen / Settings), or
 * explicitly skip, which is recorded in backend/.auth_disabled.
 */
export default function AuthSetup({ onDone }) {
  const [passphrase, setPassphrase] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmSkip, setConfirmSkip] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    if (passphrase.length < 4) {
      setError('Use at least 4 characters.')
      return
    }
    if (passphrase !== confirm) {
      setError('Passphrases don’t match.')
      return
    }
    setBusy(true)
    try {
      await axios.post('/api/auth/setup', { passphrase })
      onDone(true)
    } catch {
      setError('Something went wrong — try again.')
    } finally {
      setBusy(false)
    }
  }

  const skip = async () => {
    setBusy(true)
    try {
      await axios.post('/api/auth/setup', { skip: true })
      onDone(false)
    } catch {
      setError('Something went wrong — try again.')
      setBusy(false)
    }
  }

  return (
    <div style={shellStyle}>
      <div className="card" style={cardStyle}>
        <div style={{ fontSize:32, marginBottom:12 }}>🔒</div>
        <div style={{ fontSize:16, fontWeight:600, marginBottom:8 }}>Set up a lock for Personal CFO</div>
        <p style={{ fontSize:13, color:'var(--text2)', marginBottom:20, lineHeight:1.6 }}>
          This isn't internet-facing security — the app only ever listens on
          127.0.0.1 — just a deterrent for anyone else who picks up this Mac
          or glances at a screen share. You can add Touch ID afterward.
        </p>

        {!confirmSkip ? (
          <>
            <form onSubmit={submit}>
              <input
                type="password"
                autoFocus
                value={passphrase}
                onChange={e => setPassphrase(e.target.value)}
                placeholder="Choose a passphrase"
                style={{ width:'100%', marginBottom:8, textAlign:'center' }}
              />
              <input
                type="password"
                value={confirm}
                onChange={e => setConfirm(e.target.value)}
                placeholder="Confirm passphrase"
                style={{ width:'100%', marginBottom:12, textAlign:'center' }}
              />
              <button className="btn-primary" type="submit" disabled={busy || !passphrase || !confirm} style={{ width:'100%', marginBottom:12 }}>
                {busy ? 'Setting up…' : 'Set passphrase & continue'}
              </button>
            </form>
            <button
              onClick={() => setConfirmSkip(true)}
              style={{ background:'none', border:'none', color:'var(--text2)', fontSize:12, cursor:'pointer', padding:0 }}
            >
              Skip — I'm the only one with access to this Mac
            </button>
          </>
        ) : (
          <>
            <p style={{ fontSize:13, color:'var(--text2)', marginBottom:16, lineHeight:1.6 }}>
              This leaves the app open to anyone who opens it on this Mac.
              You can turn on a passphrase later from Settings.
            </p>
            <button className="btn-secondary" onClick={skip} disabled={busy} style={{ width:'100%', marginBottom:8 }}>
              {busy ? 'Skipping…' : 'Confirm — skip for now'}
            </button>
            <button
              onClick={() => setConfirmSkip(false)}
              style={{ background:'none', border:'none', color:'var(--accent)', fontSize:12, cursor:'pointer', padding:0 }}
            >
              Never mind, set a passphrase
            </button>
          </>
        )}

        {error && <div style={{ marginTop:14, fontSize:12, color:'var(--red)' }}>{error}</div>}
      </div>
    </div>
  )
}

const shellStyle = {
  position:'fixed', inset:0, display:'flex', alignItems:'center', justifyContent:'center',
  background:'var(--bg)', zIndex:1000,
}
const cardStyle = { width:360, padding:'32px 28px', textAlign:'center' }
