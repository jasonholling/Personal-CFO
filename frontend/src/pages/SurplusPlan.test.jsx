import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import SurplusPlan from './SurplusPlan'

vi.mock('axios', () => ({ default: { get: vi.fn(), put: vi.fn() } }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container, root
const flush = async () => { await act(async () => { await Promise.resolve() }) }

const allocationsResponse = {
  allocations: [
    { goal: 'Emergency reserve', monthly_amount: 200, notes: null },
    { goal: 'High-interest debt payoff', monthly_amount: 0, notes: null },
  ],
  monthly_surplus: 2000, assigned: 200, unassigned: 1800,
}

const rowInput = goal => {
  const label = [...container.querySelectorAll('div')].find(d => d.textContent === goal)
  return label.parentElement.querySelector('input[type=number]')
}
const setValue = (input, value) => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, String(value))
  input.dispatchEvent(new Event('change', { bubbles: true }))
}
const saveButtonFor = goal => {
  const label = [...container.querySelectorAll('div')].find(d => d.textContent === goal)
  return [...label.parentElement.querySelectorAll('button')].find(b => b.textContent === 'Save')
}

beforeEach(() => {
  vi.clearAllMocks()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  axios.get.mockImplementation(url => Promise.resolve({
    data: url === '/api/planning-inputs' ? { kid1_name: 'Abby', kid2_name: 'Cooper' } : allocationsResponse,
  }))
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

describe('SurplusPlan row saves (external audit 2026-09-07, finding #14)', () => {
  it('does not wipe an unsaved edit in another row when a different row is saved', async () => {
    axios.put.mockResolvedValue({ data: { goal: 'Emergency reserve', monthly_amount: 500, notes: null } })
    await act(async () => root.render(<SurplusPlan />))
    await flush()

    // Edit row B (High-interest debt payoff) but don't save it yet.
    const rowB = rowInput('High-interest debt payoff')
    await act(async () => setValue(rowB, 350))
    expect(rowB.value).toBe('350')

    // Edit and save row A (Emergency reserve).
    const rowA = rowInput('Emergency reserve')
    await act(async () => setValue(rowA, 500))
    await act(async () => { saveButtonFor('Emergency reserve').click() })
    await flush()

    // Saving row A must NOT have refetched and replaced row B's unsaved edit.
    expect(rowInput('High-interest debt payoff').value).toBe('350')
    expect(rowInput('Emergency reserve').value).toBe('500')
  })

  it('still refreshes the assigned/unassigned totals after a save', async () => {
    axios.put.mockResolvedValue({ data: { goal: 'Emergency reserve', monthly_amount: 500, notes: null } })
    axios.get.mockImplementation(url => Promise.resolve({
      data: url === '/api/planning-inputs' ? { kid1_name: 'Abby', kid2_name: 'Cooper' }
        : { ...allocationsResponse, assigned: 500, unassigned: 1500 },
    }))
    await act(async () => root.render(<SurplusPlan />))
    await flush()
    await act(async () => { saveButtonFor('Emergency reserve').click() })
    await flush()
    expect(container.textContent).toContain('$1,500')
  })
})
