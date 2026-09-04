import assert from 'node:assert/strict'
import test from 'node:test'

import {
  assignableEmployees,
  normalizeEmployeeColor,
  shiftModalEmployees,
} from './employeeScheduling.ts'
import * as employeeScheduling from './employeeScheduling.ts'
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

test('optimistic employee settings updates preserve the prior state for rollback', () => {
  const updateEmployeeSetting = (
    employeeScheduling as unknown as {
      updateEmployeeSetting?: <T>(
        entries: Record<string, T>,
        auth0Id: string,
        patch: Partial<T>,
      ) => Record<string, T>
    }
  ).updateEmployeeSetting
  assert.equal(typeof updateEmployeeSetting, 'function')

  const original = {
    'auth0|1': { color: '#3D74C4', isSchedulable: 1 },
  }
  const optimistic = updateEmployeeSetting!(original, 'auth0|1', {
    color: '#2E7FA3',
    isSchedulable: 0,
  })

  assert.deepEqual(original['auth0|1'], { color: '#3D74C4', isSchedulable: 1 })
  assert.deepEqual(optimistic['auth0|1'], { color: '#2E7FA3', isSchedulable: 0 })
})

test('shift creation surfaces the server eligibility explanation', () => {
  const scheduleApiErrorMessage = (
    employeeScheduling as unknown as {
      scheduleApiErrorMessage?: (error: unknown, fallback: string) => string
    }
  ).scheduleApiErrorMessage
  assert.equal(typeof scheduleApiErrorMessage, 'function')
  assert.equal(
    scheduleApiErrorMessage!(
      { response: { data: { error: 'Employee is disabled for shift assignment' } } },
      'Failed to save shift',
    ),
    'Employee is disabled for shift assignment',
  )
  assert.equal(scheduleApiErrorMessage!(new Error('network'), 'Failed'), 'Failed')
})

test('a rejected employee setting request restores the previous value', async () => {
  const persistEmployeeSetting = (
    employeeScheduling as unknown as {
      persistEmployeeSetting?: <T>(options: {
        optimistic: () => void
        persist: () => Promise<T>
        rollback: () => void
      }) => Promise<T>
    }
  ).persistEmployeeSetting
  assert.equal(typeof persistEmployeeSetting, 'function')

  let color = '#3D74C4'
  await assert.rejects(
    persistEmployeeSetting!({
      optimistic: () => { color = '#2E7FA3' },
      persist: async () => { throw new Error('save failed') },
      rollback: () => { color = '#3D74C4' },
    }),
    /save failed/,
  )
  assert.equal(color, '#3D74C4')
})

test('screen eyedropper colors are normalized before saving', async () => {
  const pickScreenColor = (
    employeeScheduling as unknown as {
      pickScreenColor?: (
        open: () => Promise<{ sRGBHex: string }>,
      ) => Promise<string>
    }
  ).pickScreenColor
  assert.equal(typeof pickScreenColor, 'function')

  const color = await pickScreenColor!(async () => ({ sRGBHex: '#1d58af' }))

  assert.equal(color, '#1D58AF')
})

test('cancelling the screen eyedropper is recognized as a quiet cancellation', () => {
  const isEyeDropperCancellation = (
    employeeScheduling as unknown as {
      isEyeDropperCancellation?: (error: unknown) => boolean
    }
  ).isEyeDropperCancellation
  assert.equal(typeof isEyeDropperCancellation, 'function')

  assert.equal(isEyeDropperCancellation!({ name: 'AbortError' }), true)
  assert.equal(isEyeDropperCancellation!(new Error('permission denied')), false)
})
