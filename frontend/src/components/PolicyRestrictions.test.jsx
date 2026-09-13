import React, { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import axios from 'axios'
import PolicyRestrictions from './PolicyRestrictions'

vi.mock('axios', () => ({ default: { get: vi.fn() } }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let root, node, result
const accounts = [{ id: 1, name: 'IRA' }]
function Harness({ initial = {} }) {
  const [policy, setPolicy] = useState(initial)
  result = policy
  return <PolicyRestrictions accounts={accounts} policy={policy} onChange={setPolicy} />
}
beforeEach(() => {
  node = document.createElement('div'); document.body.appendChild(node); root = createRoot(node)
  axios.get.mockResolvedValue({ data: [{ id: 7, account_id: 1, security_name: 'Index', ticker: 'TEST' }] })
})
afterEach(async () => { await act(async () => root.unmount()); node.remove() })
const select = async (label, value) => act(async () => {
  const input = node.querySelector(`[aria-label="${label}"]`)
  input.value = value; input.dispatchEvent(new Event('change', { bubbles: true }))
})
it('edits exclusions and protects a holding while preserving existing broad exceptions', async () => {
  await act(async () => root.render(<Harness initial={{ employer_stock_exceptions: [{ ticker: 'OTHER', reason: 'Restricted shares' }] }} />))
  await act(async () => node.querySelector('input[type="checkbox"]').click())
  expect(result.excluded_holdings).toEqual([7])
  await select('Holding to protect', '7')
  await select('Exception type', 'legacy_holding_exceptions')
  await act(async () => [...node.querySelectorAll('button')].find(b => b.textContent === 'Protect holding').click())
  expect(result.legacy_holding_exceptions).toEqual([{ holding_id: 7 }])
  expect(result.employer_stock_exceptions).toEqual([{ ticker: 'OTHER', reason: 'Restricted shares' }])
  expect(node.textContent).toContain('OTHER across accounts')
  await act(async () => node.querySelector('[aria-label="Remove Legacy holding exception 1"]').click())
  expect(result.legacy_holding_exceptions).toEqual([])
})
it('supports block, allow-none, allow-selected, and clearing restrictions', async () => {
  await act(async () => root.render(<Harness />))
  await select('Constraint mode for IRA', 'deny')
  await act(async () => node.querySelector('[aria-label="Block us bonds in IRA"]').click())
  expect(result.account_constraints[0].excluded_asset_classes).toEqual(['us_bonds'])
  await select('Constraint mode for IRA', 'allow')
  expect(node.textContent).toContain('No classes allowed')
  expect(result.account_constraints[0].allowed_asset_classes).toEqual([])
  await act(async () => node.querySelector('[aria-label="Allow us large cap in IRA"]').click())
  expect(result.account_constraints[0].allowed_asset_classes).toEqual(['us_large_cap'])
  await select('Constraint mode for IRA', 'any')
  expect(result.account_constraints[0].allowed_asset_classes).toBeNull()
  expect(result.account_constraints[0].excluded_asset_classes).toEqual([])
})
it('loads saved constraints and preserves policy on holdings lookup failure', async () => {
  axios.get.mockRejectedValue(new Error('offline'))
  const initial = { excluded_holdings: [99], account_constraints: [{ account_id: 1, allowed_asset_classes: ['cash'], notes: 'Reserve' }] }
  await act(async () => root.render(<Harness initial={initial} />))
  expect(node.querySelector('[role="alert"]').textContent).toContain('preserved')
  expect(node.querySelector('[aria-label="Allow cash in IRA"]').checked).toBe(true)
  expect(result).toEqual(initial)
})
