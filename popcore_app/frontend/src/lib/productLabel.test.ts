import assert from 'node:assert/strict'
import test from 'node:test'
import {pickParts, pickText, productLabel, productNames} from './productLabel.ts'

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

test('a pick leads with the design and the SKU, so designs of one series are told apart', () => {
  // DT, 2026-11-21: the box showed "Sonny Angel Easter Series 20…" for both designs.
  const lily = {id:7,sku:'SA0123-05',jizhanming:'SA复活节2018 Easter Lily',name_cn_en:'Sonny Angel Easter Series 2018 Easter Lily',
    design_name:'Easter Lily',stock_form:'confirmed_design'}
  const bunny = {...lily, id:8, sku:'SA0123-02', jizhanming:'SA复活节2018 Easter Bunny', name_cn_en:'Sonny Angel Easter Series 2018 Easter Bunny', design_name:'Easter Bunny'}
  assert.equal(pickText(lily), 'Easter Lily · SA0123-05 · SA复活节2018 Easter Lily · Sonny Angel Easter Series 2018 Easter Lily')
  assert.notEqual(pickText(lily).slice(0, 20), pickText(bunny).slice(0, 20))
  assert.deepEqual(pickParts(bunny), {design:'Easter Bunny', sku:'SA0123-02', names:'SA复活节2018 Easter Bunny · Sonny Angel Easter Series 2018 Easter Bunny'})
  // Not a design: the SKU leads; nothing is said twice.
  assert.equal(pickText({id:9,sku:'PM0564',jizhanming:'无畏契约',name_cn_en:'VALORANT Blind Box'}), 'PM0564 · 无畏契约 · VALORANT Blind Box')
  assert.equal(pickText({id:10,sku:'G',jizhanming:'',name_cn_en:''}), 'G')
})
