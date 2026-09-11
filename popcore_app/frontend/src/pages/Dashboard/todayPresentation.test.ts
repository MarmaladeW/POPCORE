import assert from 'node:assert/strict'
import test from 'node:test'
import { selectShiftHighlights, torontoDate } from './todayPresentation.ts'

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
