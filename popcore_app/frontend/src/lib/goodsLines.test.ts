import assert from 'node:assert/strict'
import test from 'node:test'
import {addReceiptLine, addTransferLine, receiptLineBody, unitTotals, unreceivable} from './goodsLines.ts'

test('adding a listed product to a receipt raises its saleable quantity instead of adding a line', () => {
  let lines = addReceiptLine([], 7, 'piece', 1)
  lines = addReceiptLine(lines, 9, 'set', 2)
  lines = addReceiptLine(lines.map(line => line.product_id === 7 ? {...line, damaged_quantity: 1} : line), 7, 'piece', 3)
  assert.deepEqual(lines.map(line => [line.line_no, line.product_id, line.saleable_quantity, line.damaged_quantity]), [[1, 7, 4, 1], [2, 9, 2, 0]])
})

test('a new receipt line follows the highest line number, even after a removal', () => {
  const lines = addReceiptLine(addReceiptLine(addReceiptLine([], 1, 'piece', 1), 2, 'piece', 1), 3, 'box', 1).filter(line => line.line_no !== 2)
  assert.deepEqual(addReceiptLine(lines, 4, 'piece', 1).map(line => line.line_no), [1, 3, 4])
})

test('receipt lines are sent in the API shape', () => {
  const [line] = addReceiptLine([], 5, 'box', 2)
  assert.deepEqual(receiptLineBody(line), {product_id: 5, unit: 'box', expected_quantity: null, saleable_quantity: 2, damaged_quantity: 0, hold_quantity: 0, discrepancy_note: null})
})

test('adding a listed product to a transfer raises its requested quantity', () => {
  const lines = addTransferLine(addTransferLine(addTransferLine([], 1, 'piece', 1), 2, 'set', 1), 1, 'piece', 1)
  assert.deepEqual(lines, [{product_id: 1, unit: 'piece', requested_quantity: 2}, {product_id: 2, unit: 'set', requested_quantity: 1}])
})

test('unverified products and products without a stock unit cannot be received', () => {
  assert.equal(unreceivable({identity_status: 'verified', stock_unit: 'piece'}), '')
  assert.equal(unreceivable({stock_unit: 'piece'}), '')
  assert.match(unreceivable({identity_status: 'unverified', stock_unit: 'piece'}), /identity not verified/)
  assert.match(unreceivable({identity_status: 'verified', stock_unit: null}), /no stock unit/)
})

test('totals are given per unit and skip empty units', () => {
  assert.equal(unitTotals([{unit: 'piece', quantity: 2}, {unit: 'set', quantity: 1}, {unit: 'piece', quantity: 1}, {unit: 'box', quantity: 0}]), '+3 pieces · +1 set')
  assert.equal(unitTotals([{unit: 'box', quantity: 2}], '-'), '-2 boxes')
  assert.equal(unitTotals([]), '')
})
