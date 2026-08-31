import assert from 'node:assert/strict'
import test from 'node:test'

import {
  compactEmployeeLabel,
  manualChecklistPresentation,
  mobileMonthEventLimit,
  mobileShiftAccessibleLabel,
  shiftColorPresentation,
} from './schedulePresentation.ts'

test('mobile employee labels stay recognizable inside narrow month cells', () => {
  assert.equal(compactEmployeeLabel('YC'), 'YC')
  assert.equal(compactEmployeeLabel('jessi'), 'JES')
  assert.equal(compactEmployeeLabel('Belle Chan'), 'BC')
  assert.equal(compactEmployeeLabel(''), '?')
})

test('mobile shift labels retain the complete assignment for assistive technology', () => {
  assert.equal(
    mobileShiftAccessibleLabel({
      employeeName: 'Celia',
      startTime: '12:00',
      endTime: '22:00',
      position: 'Cashier',
      isTrainee: false,
    }),
    'Celia · 12:00–22:00 · Cashier',
  )

  assert.equal(
    mobileShiftAccessibleLabel({
      employeeName: 'Trainee 1',
      startTime: '12:00',
      endTime: '17:00',
      position: '',
      isTrainee: true,
    }),
    'Trainee 1 · 12:00–17:00 · Trainee',
  )
})

test('mobile month view shows three assignments before using an overflow count', () => {
  assert.equal(mobileMonthEventLimit(true, 'dayGridMonth'), 3)
  assert.equal(mobileMonthEventLimit(false, 'dayGridMonth'), 4)
  assert.equal(mobileMonthEventLimit(true, 'timeGridWeek'), 4)
})

test('checklist completion is controlled only by the saved manual tick', () => {
  assert.deepEqual(manualChecklistPresentation(false), {
    state: 'unchecked',
    statusLabel: 'Unchecked',
  })
  assert.deepEqual(manualChecklistPresentation(true), {
    state: 'checked',
    statusLabel: 'Checked',
  })
})

test('trainee status never replaces the employee color', () => {
  assert.deepEqual(shiftColorPresentation('#3D74C4', 'full'), {
    backgroundColor: '#3D74C4',
    borderColor: '#3D74C4',
    display: 'block',
  })
  assert.deepEqual(shiftColorPresentation('#3D74C4', 'custom'), {
    backgroundColor: '#3D74C4',
    borderColor: '#3D74C4',
    display: 'block',
  })
})
