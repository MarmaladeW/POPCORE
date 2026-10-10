import assert from 'node:assert/strict'
import test from 'node:test'

import {
  actualFinishChip,
  compactEmployeeLabel,
  finishesNextDay,
  manualChecklistPresentation,
  mobileShiftAccessibleLabel,
  shiftColorPresentation,
  shiftKindLabel,
  shiftTimeRange,
} from './schedulePresentation.ts'

test('shift types are readable on mobile', () => {
  assert.equal(shiftKindLabel('full'), 'Full day')
  assert.equal(shiftKindLabel('first'), 'Half day (AM)')
  assert.equal(shiftKindLabel('second'), 'Half day (PM)')
  assert.equal(shiftKindLabel('custom'), 'Custom')
})

test('shift time ranges keep the plan and add a recorded finish', () => {
  assert.equal(shiftTimeRange('12:00', '20:00'), '12:00–20:00')
  assert.equal(shiftTimeRange('12:00', '20:00', null), '12:00–20:00')
  assert.equal(shiftTimeRange('12:00', '20:00', '20:25'), '12:00–20:00 · finished 20:25')
  assert.equal(shiftTimeRange('12:00', '22:00', '00:45'), '12:00–22:00 · finished 00:45 (next day)')
})

test('calendar blocks mark a recorded actual finish', () => {
  assert.equal(actualFinishChip('12:00', null), '')
  assert.equal(actualFinishChip('12:00', '21:25'), '✓ 21:25')
  assert.equal(actualFinishChip('12:00', '00:45'), '✓ 00:45 +1')
  assert.equal(
    mobileShiftAccessibleLabel({ employeeName: 'Celia', startTime: '12:00', endTime: '22:00', actualEndTime: '21:25', position: '', isTrainee: false }),
    'Celia · 12:00–22:00 · finished 21:25',
  )
})

test('a finish up to 01:00 counts as the next day', () => {
  assert.equal(finishesNextDay('12:00', '00:00'), true)
  assert.equal(finishesNextDay('12:00', '01:00'), true)
  assert.equal(finishesNextDay('12:00', '01:01'), false)
  assert.equal(finishesNextDay('12:00', '21:25'), false)
  assert.equal(finishesNextDay('00:30', '00:45'), false)
})

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
