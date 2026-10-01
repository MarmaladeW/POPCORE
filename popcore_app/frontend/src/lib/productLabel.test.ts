import assert from 'node:assert/strict'
import test from 'node:test'
import {productLabel} from './productLabel.ts'

test('labels distinguish confirmed designs and unopened stock in the same series', () => {
  assert.equal(productLabel({id:1,sku:'A',jizhanming:'Generic',series_name:'Moon Garden',design_name:'Star Keeper',stock_form:'confirmed_design'}), 'Moon Garden · Star Keeper · Confirmed')
  assert.equal(productLabel({id:2,sku:'B',jizhanming:'Moon Garden',stock_form:'random_box'}), 'Moon Garden · Blind box')
  assert.equal(productLabel({id:3,sku:'C',jizhanming:'',name_cn_en:'Ordinary item'}), 'Ordinary item')
})
