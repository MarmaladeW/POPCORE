import assert from 'node:assert/strict'
import test from 'node:test'

import {
  assignableEmployees,
  normalizeEmployeeColor,
  shiftModalEmployees,
} from './employeeScheduling.ts'
import type { Employee } from './scheduleApi.ts'


function employee(id: number, isSchedulable?: number): Employee {
  return {
    id,
    auth0_id: `auth0|${id}`,
    name: `Employee ${id}`,
    email: `employee${id}@example.com`,
    is_active: 1,
    is_schedulable: isSchedulable as number,
    created_at: '2026-09-01T00:00:00',
  }
}

test('only explicitly disabled employees are excluded from new assignments', () => {
  assert.deepEqual(
    assignableEmployees([
      employee(1, 1),
      employee(2, 0),
      employee(3, undefined),
    ]).map((entry) => entry.id),
    [1, 3],
  )
})

test('editing keeps a disabled current employee visible', () => {
  assert.deepEqual(
    shiftModalEmployees([employee(1, 1), employee(2, 0)], 2).map((entry) => entry.id),
    [1, 2],
  )
})

test('creating excludes a disabled employee from the shift modal', () => {
  assert.deepEqual(
    shiftModalEmployees([employee(1, 1), employee(2, 0)]).map((entry) => entry.id),
    [1],
  )
})

test('picker colors are normalized to an opaque uppercase hex value', () => {
  assert.equal(normalizeEmployeeColor('#3d74c4'), '#3D74C4')
})
