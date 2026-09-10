import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import SavedScenarios from './SavedScenarios'
import { setSsTiming as setGlobalSsTiming } from '../utils/scenario'

// Milestone 1 (2026-09-09, "Complete, reproducible saved scenarios") --
// rendered-DOM coverage for the actual saved payload sent on save, the
// insert-only 409 conflict surfaced as an inline error (not a silent
// overwrite), the legacy badge on pre-migration rows, and Recalculate
// creating a distinct new row rather than mutating the original.

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container, root
const flush = async () => { await act(async () => { await Promise.resolve() }) }
const click = async text => {
  const button = [...container.querySelectorAll('button')].find(b => b.textContent.trim() === text)
  expect(button, text).toBeTruthy()
  await act(async () => { button.click() })
}
// React tracks <input> value via a property override, so a plain
// `el.value = x` assignment is invisible to its onChange — go through the
// native setter first, same workaround RTL's fireEvent uses internally.
const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
const typeInto = async (el, value) => {
  await act(async () => {
    nativeInputValueSetter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

const freshScenario = {
  id: 1, name: 'Retire at 60', retirement_age: 60, ss_timing: 'early',
  created_at: '2026-09-09T00:00:00', schema_version: 2, calculation_version: '1',
  is_legacy: false, revision_of: null, revision_number: 1,
  summary: { retirement_age: 60, percent_funded: 88, portfolio_at_retirement: 900000, projected_surplus: 50000, on_track: true },
  assumptions: { planning_inputs: { id: 1, jason_age: 45 }, ss_timing: 'early' },
}

const legacyScenario = {
  id: 2, name: 'Old Save', retirement_age: 62, ss_timing: 'early',
  created_at: '2026-01-01T00:00:00', schema_version: 1, calculation_version: null,
  is_legacy: true, revision_of: null, revision_number: 1,
  summary: { retirement_age: 62, percent_funded: 70, portfolio_at_retirement: 700000, projected_surplus: 10000, on_track: false },
  assumptions: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  setGlobalSsTiming('early')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  axios.get.mockResolvedValue({ data: [freshScenario, legacyScenario] })
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

describe('Saved Scenarios', () => {
  it('renders both a fresh and a legacy scenario, with only the legacy one badged', async () => {
    await act(async () => root.render(<SavedScenarios />))
    await flush()
    expect(container.textContent).toContain('Retire at 60')
    expect(container.textContent).toContain('Old Save')
    expect(container.textContent).toContain('LEGACY')
    // The badge text itself should not appear twice — only Old Save is legacy.
    expect(container.textContent.match(/LEGACY/g)).toHaveLength(1)
  })

  it('save sends the current name/age/ssTiming payload', async () => {
    axios.post.mockResolvedValue({ data: { id: 3 } })
    await act(async () => root.render(<SavedScenarios />))
    await flush()
    const nameInput = container.querySelector('input[placeholder="e.g. Retire at 58"]')
    await typeInto(nameInput, 'My New Plan')
    await click('Save scenario')
    await flush()
    expect(axios.post).toHaveBeenCalledWith('/api/saved-scenarios', {
      name: 'My New Plan', retirement_age: 60, ss_timing: 'early',
    })
  })

  it('a 409 conflict from saving over an existing name shows the backend message, not a silent failure', async () => {
    axios.post.mockRejectedValue({ response: { data: { detail: 'A saved scenario with this name already exists. Choose a different name, or use Recalculate on the existing one.' } } })
    await act(async () => root.render(<SavedScenarios />))
    await flush()
    const nameInput = container.querySelector('input[placeholder="e.g. Retire at 58"]')
    await typeInto(nameInput, 'Retire at 60')
    await click('Save scenario')
    await flush()
    expect(container.textContent).toContain('already exists')
  })

  it('Recalculate posts to the per-scenario recalculate endpoint and reloads the list', async () => {
    axios.post.mockResolvedValue({ data: { id: 4, revision_of: 1, revision_number: 2 } })
    await act(async () => root.render(<SavedScenarios />))
    await flush()
    await click('Recalculate with current data')
    await flush()
    expect(axios.post).toHaveBeenCalledWith('/api/saved-scenarios/1/recalculate')
    // load() is called again after recalculating — GET fires a second time.
    expect(axios.get).toHaveBeenCalledTimes(2)
  })

  it('checking two scenarios to compare shows their outcome and assumption diff', async () => {
    await act(async () => root.render(<SavedScenarios />))
    await flush()
    const checkboxes = [...container.querySelectorAll('input[type="checkbox"]')]
    expect(checkboxes).toHaveLength(2)
    await act(async () => { checkboxes[0].click() })
    await act(async () => { checkboxes[1].click() })
    await flush()
    expect(container.textContent).toContain('Comparing "Retire at 60" vs "Old Save"')
    expect(container.textContent).toContain('legacy saves')
  })

  it('compare shows changed scenario choices (retirement age) separately from household-data assumptions', async () => {
    await act(async () => root.render(<SavedScenarios />))
    await flush()
    const checkboxes = [...container.querySelectorAll('input[type="checkbox"]')]
    await act(async () => { checkboxes[0].click() })
    await act(async () => { checkboxes[1].click() })
    await flush()
    expect(container.textContent).toContain('Changed scenario choices')
    expect(container.textContent).toContain('Retirement age')
    expect(container.textContent).toContain('Changed household-data assumptions')
  })

  it('reopening (View saved inputs) shows the frozen snapshot, not current household data', async () => {
    await act(async () => root.render(<SavedScenarios />))
    await flush()
    const summary = [...container.querySelectorAll('summary')].find(s => s.textContent.trim() === 'View saved inputs')
    expect(summary).toBeTruthy()
    await act(async () => { summary.click() })
    await flush()
    expect(container.textContent).toContain("Jason's age at save: 45")
    expect(container.textContent).toContain('Frozen at save time')
  })
})
