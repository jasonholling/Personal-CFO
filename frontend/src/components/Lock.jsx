import { useState, useEffect, useRef } from 'react'
import axios from 'axios'

/**
 * Passphrase/Touch ID lock screen — see backend/auth.py for the full
 * design rationale. This isn't internet-facing security, just a "someone
 * else picks up this Mac" deterrent. Rendered by App.jsx in place of the
 * app shell until /api/auth/status reports authenticated:true.
 */
export default function Lock({ webauthnRegistered, onUnlock }) {
  const [mode, setMode] = useState(webauthnRegistered ? 'touchid' : 'passphrase')
  const [passphrase, setPassphrase] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [offerSetup, setOfferSetup] = useState(false)
  // React 18 StrictMode intentionally double-invokes mount effects in dev
  // to surface exactly this kind of bug — without this guard, the effect
  // below fired navigator.credentials.get() twice on every app start,
  // triggering two back-to-back Touch ID prompts. The ref survives
  // StrictMode's simulated mount/unmount/remount (same component instance),
  // so it reliably blocks the second call.
  const autoPromptedRef = useRef(false)

  useEffect(() => {
    if (mode === 'touchid' && !autoPromptedRef.current) {
      autoPromptedRef.current = true
      tryTouchId()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const tryTouchId = async () => {
    setError('')
    setBusy(true)
    try {
      const { data: optionsJSON } = await axios.get('/api/auth/webauthn/login-options')
      const options = PublicKeyCredential.parseRequestOptionsFromJSON(optionsJSON)
      const credential = await navigator.credentials.get({ publicKey: options })
      await axios.post('/api/auth/webauthn/login-verify', { credential: credential.toJSON() })
      onUnlock()
    } catch (e) {
      setError('Touch ID didn’t work — try again, or use your passphrase.')
    } finally {
      setBusy(false)
    }
  }

  const submitPassphrase = async (e) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await axios.post('/api/auth/login', { passphrase })
      if (!webauthnRegistered) setOfferSetup(true)
      else onUnlock()
    } catch {
      setError('Incorrect passphrase.')
    } finally {
      setBusy(false)
    }
  }

  const setUpTouchId = async () => {
    setError('')
    setBusy(true)
    try {
      const { data: optionsJSON } = await axios.get('/api/auth/webauthn/register-options')
      const options = PublicKeyCredential.parseCreationOptionsFromJSON(optionsJSON)
      const credential = await navigator.credentials.create({ publicKey: options })
      await axios.post('/api/auth/webauthn/register-verify', { credential: credential.toJSON() })
      onUnlock()
    } catch {
      // Registration is optional — failing here shouldn't block getting
      // into the app, since the passphrase login above already succeeded.
      onUnlock()
    } finally {
      setBusy(false)
    }
  }

  if (offerSetup) {
    return (
      <div style={shellStyle}>
        <div className="card" style={cardStyle}>
          <div style={{ fontSize:32, marginBottom:12 }}>👆</div>
          <div style={{ fontSize:16, fontWeight:600, marginBottom:8 }}>Enable Touch ID?</div>
          <p style={{ fontSize:13, color:'var(--text2)', marginBottom:20, lineHeight:1.6 }}>
            Unlock faster next time without typing your passphrase.
          </p>
          <button className="btn-primary" onClick={setUpTouchId} disabled={busy} style={{ width:'100%', marginBottom:8 }}>
            {busy ? 'Setting up…' : 'Enable Touch ID'}
          </button>
          <button className="btn-secondary" onClick={onUnlock} style={{ width:'100%' }}>Skip for now</button>
        </div>
      </div>
    )
  }

  return (
    <div style={shellStyle}>
      <div className="card" style={cardStyle}>
        <div style={{ fontSize:32, marginBottom:12 }}>🔒</div>
        <div style={{ fontSize:16, fontWeight:600, marginBottom:20 }}>Personal CFO is locked</div>

        {mode === 'touchid' ? (
          <>
            <button className="btn-primary" onClick={tryTouchId} disabled={busy} style={{ width:'100%', marginBottom:12 }}>
              {busy ? 'Waiting for Touch ID…' : '👆 Unlock with Touch ID'}
            </button>
            <button
              onClick={() => { setMode('passphrase'); setError('') }}
              style={{ background:'none', border:'none', color:'var(--accent)', fontSize:12, cursor:'pointer', padding:0 }}
            >
              Use passphrase instead
            </button>
          </>
        ) : (
          <form onSubmit={submitPassphrase}>
            <input
              type="password"
              autoFocus
              value={passphrase}
              onChange={e => setPassphrase(e.target.value)}
              placeholder="Passphrase"
              style={{ width:'100%', marginBottom:12, textAlign:'center' }}
            />
            <button className="btn-primary" type="submit" disabled={busy || !passphrase} style={{ width:'100%', marginBottom:12 }}>
              {busy ? 'Checking…' : 'Unlock'}
            </button>
            {webauthnRegistered && (
              <button
                type="button"
                onClick={() => { setMode('touchid'); setError('') }}
                style={{ background:'none', border:'none', color:'var(--accent)', fontSize:12, cursor:'pointer', padding:0 }}
              >
                Use Touch ID instead
              </button>
            )}
          </form>
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
// className="card" (index.css) supplies the real background/border/radius
// used everywhere else in the app — this just narrows the width and
// centers the text for a modal-sized card.
const cardStyle = { width:320, padding:'32px 28px', textAlign:'center' }
