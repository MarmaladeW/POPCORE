import assert from 'node:assert/strict'
import test from 'node:test'
import {productLabel, productNames} from './productLabel.ts'

test('labels distinguish confirmed designs and unopened stock in the same series', () => {
  assert.equal(productLabel({id:1,sku:'A',jizhanming:'Generic',series_name:'Moon Garden',design_name:'Star Keeper',stock_form:'confirmed_design'}), 'Moon Garden · Star Keeper · Confirmed')
  assert.equal(productLabel({id:2,sku:'B',jizhanming:'Moon Garden',stock_form:'random_box'}), 'Moon Garden · Blind box')
  assert.equal(productLabel({id:3,sku:'C',jizhanming:'',name_cn_en:'Ordinary item'}), 'Ordinary item')
})

test('labels show the shop name together with the product name', () => {
  assert.equal(productLabel({id:4,sku:'D',jizhanming:'小野暗场毛绒',name_cn_en:'Hirono Shelter Plush',stock_form:'random_box'}), '小野暗场毛绒 · Hirono Shelter Plush · Blind box')
  assert.equal(productNames({id:5,sku:'E',jizhanming:'Same',name_cn_en:'Same'}), 'Same')
  assert.equal(productNames({id:6,sku:'F',jizhanming:' ',name_cn_en:''}), 'F')
})
