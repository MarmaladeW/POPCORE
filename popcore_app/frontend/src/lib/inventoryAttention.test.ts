import assert from 'node:assert/strict'
import test from 'node:test'
import { inventoryAttention, needsAttention } from './inventoryAttention.ts'

const locations = [{id:1,store_id:1,code:'floor'},{id:2,store_id:1,code:'upstairs'},{id:3,store_id:2,code:'floor'},{id:4,store_id:2,code:'warehouse'}]
// By default the product has been held in store 1, on its floor too (the ledger's stocked_store_ids / floor_store_ids).
const product = (quantities:Array<number|null>, extras:Array<{location_id:number;disposition:string;quantity:number|null}>=[], held:{stocked_store_ids?:number[];floor_store_ids?:number[]}={}) => ({
  identity_status:'verified', stocked_store_ids:[1], floor_store_ids:[1], ...held,
  balances:[...quantities.map((quantity,index)=>({location_id:index+1,disposition:'saleable',quantity})),...extras]})

test('unknown scopes stay distinct from trusted out of stock',()=>{
  assert.deepEqual(inventoryAttention(product([0,0]),locations.slice(0,2)), ['out_of_stock'])
  assert.deepEqual(inventoryAttention(product([0,null]),locations.slice(0,2)), ['unverified'])
  assert.deepEqual(inventoryAttention({...product([0,0]),identity_status:'unverified'},locations.slice(0,2)), ['unverified'])
})
test('floor replenishment uses positive back stock in the same store only',()=>{
  assert.deepEqual(inventoryAttention(product([0,4]),locations.slice(0,2)), ['replenish'])
  assert.deepEqual(inventoryAttention(product([0,0,5,4],[],{floor_store_ids:[1,2]}),locations), [])
  assert.deepEqual(inventoryAttention(product([null,4]),locations.slice(0,2)), ['unverified'])
  assert.deepEqual(inventoryAttention(product([0,0,3]),[...locations.slice(0,2),{id:3,store_id:1,code:'claw'}]), [])
})
test('only what a store has held can run out, and only a floor that held it can be empty of it',()=>{
  const here=locations.slice(0,2)
  // Never carried here: its zero is not out of stock.
  assert.deepEqual(inventoryAttention(product([0,0],[],{stocked_store_ids:[],floor_store_ids:[]}),here), [])
  // Back stock the floor never held (a big figure upstairs, an old series kept as spares): not a refill.
  assert.deepEqual(inventoryAttention(product([0,4],[],{floor_store_ids:[]}),here), [])
  // Another store's floor history does not count here.
  assert.deepEqual(inventoryAttention(product([0,4],[],{floor_store_ids:[2]}),here), [])
  // Missing history (an older server) flags nothing rather than everything.
  assert.deepEqual(inventoryAttention({identity_status:'verified',balances:product([0,4]).balances},here), [])
})
test('hold and damage remain visible without treating transit or display as problems',()=>{
  assert.deepEqual(inventoryAttention(product([2,0],[{location_id:1,disposition:'hold',quantity:1}]),locations.slice(0,2)), ['condition'])
  assert.deepEqual(inventoryAttention(product([2,0],[{location_id:1,disposition:'transit',quantity:4},{location_id:1,disposition:'display',quantity:2}]),locations.slice(0,2)), [])
})
test('sealed sets and confirmed designs are never floor refills, and a box counts its whole sets',()=>{
  const here=locations.slice(0,2)
  assert.deepEqual(inventoryAttention({...product([0,4]),stock_form:'sealed_set'},here), [])
  assert.deepEqual(inventoryAttention({...product([0,0]),stock_form:'sealed_set'},here), [])
  assert.deepEqual(inventoryAttention({...product([0,4]),stock_form:'confirmed_design'},here), [])
  assert.deepEqual(inventoryAttention({...product([0,0]),stock_form:'confirmed_design'},here), ['out_of_stock'])
  assert.deepEqual(inventoryAttention({...product([0,0]),stock_form:'random_box'},here,product([0,2])), ['replenish'])
  assert.deepEqual(inventoryAttention({...product([3,0]),stock_form:'random_box'},here,product([0,2])), [])
  // Only the sets were ever held here, and they are gone too: the box is out of stock.
  assert.deepEqual(inventoryAttention({...product([0,0],[],{stocked_store_ids:[],floor_store_ids:[]}),stock_form:'random_box'},here,product([0,0])), ['out_of_stock'])
})
test('needs attention leaves products never reviewed to Unverified stock',()=>{
  const verified=product([0,null]), unreviewed={...product([0,0]),identity_status:'unverified'}
  assert.equal(needsAttention(verified,inventoryAttention(verified,locations.slice(0,2))), true)
  assert.equal(needsAttention(unreviewed,inventoryAttention(unreviewed,locations.slice(0,2))), false)
  assert.equal(needsAttention(product([3,0]),[]), false)
  assert.equal(needsAttention(unreviewed,['unverified','condition']), true)
})
