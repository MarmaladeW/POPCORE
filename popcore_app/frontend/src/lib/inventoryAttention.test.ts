import assert from 'node:assert/strict'
import test from 'node:test'
import { inventoryAttention } from './inventoryAttention.ts'

const locations = [{id:1,store_id:1,code:'floor'},{id:2,store_id:1,code:'upstairs'},{id:3,store_id:2,code:'floor'},{id:4,store_id:2,code:'warehouse'}]
const product = (quantities:Array<number|null>, extras:Array<{location_id:number;disposition:string;quantity:number|null}>=[]) => ({identity_status:'verified',balances:[...quantities.map((quantity,index)=>({location_id:index+1,disposition:'saleable',quantity})),...extras]})

test('unknown scopes stay distinct from trusted out of stock',()=>{
  assert.deepEqual(inventoryAttention(product([0,0]),locations.slice(0,2)), ['out_of_stock'])
  assert.deepEqual(inventoryAttention(product([0,null]),locations.slice(0,2)), ['unverified'])
  assert.deepEqual(inventoryAttention({...product([0,0]),identity_status:'unverified'},locations.slice(0,2)), ['unverified'])
})
test('floor replenishment uses positive back stock in the same store only',()=>{
  assert.deepEqual(inventoryAttention(product([0,4]),locations.slice(0,2)), ['replenish'])
  assert.deepEqual(inventoryAttention(product([0,0,5,4]),locations), [])
  assert.deepEqual(inventoryAttention(product([null,4]),locations.slice(0,2)), ['unverified'])
})
test('hold and damage remain visible without treating transit or display as problems',()=>{
  assert.deepEqual(inventoryAttention(product([2,0],[{location_id:1,disposition:'hold',quantity:1}]),locations.slice(0,2)), ['condition'])
  assert.deepEqual(inventoryAttention(product([2,0],[{location_id:1,disposition:'transit',quantity:4},{location_id:1,disposition:'display',quantity:2}]),locations.slice(0,2)), [])
})
