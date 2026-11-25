import assert from 'node:assert/strict'
import test from 'node:test'
import { hereState, safeReturn } from './returnTo.ts'

test('signing in goes back to the page inside POPCORE, never elsewhere', () => {
  assert.deepEqual(hereState({ pathname: '/web-orders', search: '?order=701' }), { returnTo: '/web-orders?order=701' })
  assert.equal(safeReturn('/web-orders?order=701'), '/web-orders?order=701')
  assert.equal(safeReturn('//evil.example'), '/')
  assert.equal(safeReturn('https://evil.example'), '/')
  assert.equal(safeReturn('/?code=abc&state=xyz'), '/')
  assert.equal(safeReturn(undefined), '/')
})
