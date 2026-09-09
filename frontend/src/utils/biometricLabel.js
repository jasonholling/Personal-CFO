// The Lock/AuthSetup screens used to hardcode "Touch ID" everywhere, since
// this app only ran on Macs. The underlying call (navigator.credentials.
// create/get() against a WebAuthn platform authenticator -- see backend/
// auth.py) isn't Mac-specific at all: the same flow resolves to Windows
// Hello on Windows, or whatever platform authenticator a given OS/browser
// offers elsewhere. Only the LABEL needs to match the platform; the flow
// itself is identical everywhere. Falls back to a generic phrase rather
// than guessing wrong on an OS this hasn't been tested against.
export function biometricLabel() {
  const platform = (
    navigator.userAgentData?.platform || navigator.platform || navigator.userAgent || ''
  ).toLowerCase()
  if (platform.includes('mac')) return 'Touch ID'
  if (platform.includes('win')) return 'Windows Hello'
  return 'device unlock'
}
