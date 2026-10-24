import assert from 'node:assert/strict'
import test from 'node:test'
import { mergeWork, selectShiftHighlights, torontoDate } from './todayPresentation.ts'

const shift=(id:number,date:string,start_time:string)=>({id,date,start_time,end_time:'18:00',store_code:'MK',employee_id:1,assigned_by:'manager',notes:'',created_at:'',updated_at:''})
test('keeps all today shifts then the next later assignment in stable order',()=>{
  const r=selectShiftHighlights([shift(3,'2026-09-08','12:00'),shift(2,'2026-09-08','09:00'),shift(9,'2026-11-20','10:00'),shift(8,'2026-09-12','11:00')],'2026-09-08')
  assert.deepEqual(r.today.map(s=>s.id),[2,3]); assert.deepEqual(r.next.map(s=>s.id),[8])
})
test('ties use start time and id and malformed rows are reported',()=>{
  const r=selectShiftHighlights([shift(4,'2026-09-10','10:00'),shift(2,'2026-09-10','10:00'),shift(1,'bad','10:00')],'2026-09-08')
  assert.deepEqual(r.next.map(s=>s.id),[2,4]); assert.equal(r.invalidCount,1)
})
test('empty assignments stay distinct from invalid data',()=>{assert.deepEqual(selectShiftHighlights([],'2026-09-08'),{today:[],next:[],invalidCount:0})})
test('Toronto date is independent of browser timezone at midnight and DST',()=>{
  assert.equal(torontoDate(new Date('2026-03-08T04:30:00Z')),'2026-03-07')
  assert.equal(torontoDate(new Date('2026-11-01T05:30:00Z')),'2026-11-01')
})

test('我的工作 and 门店工作 merge into one 待处理 list, oldest first', () => {
  const row = (type: string, source_id: number, updated_at: string | null) => ({ type, source_id, updated_at, status: 'draft', store_id: 1, version: 1, link: '/' })
  const merged = mergeWork({
    my_work: { total: 2, rows: [row('sale', 4, '2026-10-24T15:00'), row('checkout', 9, null)] },
    operations: { total: 3, rows: [row('count', 2, '2026-10-23T09:00'), row('receipt', 7, '2026-10-24T10:00')] },
    financial: { total: 1, rows: [row('financial_sale', 1, '2026-10-24T08:00')] },
  })
  assert.deepEqual(Object.keys(merged), ['tasks', 'financial'])
  assert.equal(merged.tasks.total, 5)
  assert.deepEqual(merged.tasks.rows.map(r => `${r.type}#${r.source_id}`), ['count#2', 'receipt#7', 'sale#4', 'checkout#9'])
  assert.deepEqual(Object.keys(mergeWork({ catalog: { total: 0, rows: [] } })), ['catalog'])
})
