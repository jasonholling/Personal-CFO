import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import BackupRestore from './BackupRestore'

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container, root
// PBKDF2 (310,000 iterations) + AES-GCM over >1MB do real, non-trivial
// work under crypto.subtle — a microtask-only flush isn't enough to wait
// for them to settle, unlike the pure-mock axios flushes elsewhere in this
// suite. A single fixed-delay sleep flaked in CI (real observed failure,
// 2026-09-07): the component's `status` state only ever updates once to a
// terminal value ('...downloaded.' or 'Export failed: ...'/'Restore
// failed: ...' — no intermediate "encrypting..." state exists to
// accidentally match early), so poll for that terminal text instead of
// guessing a fixed delay long enough for a >1MB PBKDF2+AES-GCM operation
// on a runner that might be slower or more loaded than this machine.
const flush = async () => {
  const start = Date.now()
  while (Date.now() - start < 10000) {
    if (/downloaded\.|failed/i.test(container.textContent)) return
    await act(async () => { await new Promise(r => setTimeout(r, 25)) })
  }
  // Fall through on timeout — the caller's own assertion produces a real
  // failure message rather than this helper silently doing nothing.
}
const click = async text => {
  const button = [...container.querySelectorAll('button')].find(b => b.textContent.trim() === text)
  expect(button, text).toBeTruthy()
  await act(async () => { button.click() })
}
const setValue = (input, value) => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value)
  input.dispatchEvent(new Event('change', { bubbles: true }))
}

beforeEach(() => {
  vi.clearAllMocks()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  if (!URL.createObjectURL) URL.createObjectURL = vi.fn(() => 'blob:mock')
  if (!URL.revokeObjectURL) URL.revokeObjectURL = vi.fn()
  // jsdom doesn't implement blob-URL navigation — stub the anchor click so
  // the download trigger doesn't spam "Not implemented: navigation" noise.
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

describe('Encrypted backup export on large payloads (external audit 2026-09-07, finding #16)', () => {
  it('does not throw RangeError encoding a >1MB encrypted payload, and reports success', async () => {
    // A payload well over the ~250,000-byte threshold the audit reproduced
    // the "Maximum call stack size exceeded" RangeError at — real
    // AES-256-GCM encryption of ~1.5MB of plaintext, run through the app's
    // actual encrypt path (crypto.subtle is not mocked here).
    const bigPlaintext = JSON.stringify({ tables: { snapshots: Array.from({ length: 20000 }, (_, i) => ({
      id: i, note: 'x'.repeat(60),
    })) } })
    expect(bigPlaintext.length).toBeGreaterThan(1_000_000)
    axios.get.mockResolvedValue({ data: bigPlaintext })

    await act(async () => root.render(<BackupRestore />))
    const passwordInput = container.querySelector('input[type=password]')
    await act(async () => setValue(passwordInput, 'correct horse battery staple'))

    await click('Download encrypted backup')
    await flush()

    expect(container.textContent).toContain('Encrypted backup downloaded.')
    expect(container.textContent).not.toMatch(/failed/i)
  })

  it('shows an error message instead of failing silently when export throws', async () => {
    axios.get.mockRejectedValue(new Error('network down'))
    await act(async () => root.render(<BackupRestore />))
    const passwordInput = container.querySelector('input[type=password]')
    await act(async () => setValue(passwordInput, 'a-password'))
    await click('Download encrypted backup')
    await flush()
    expect(container.textContent).toMatch(/export failed/i)
  })
})
