import assert from 'node:assert/strict'
import test from 'node:test'
import type { DexListingCandidate, DexListingRow } from '../api/dex'
import { codeHint, listingFacts, waitText } from './dexListings.ts'

const box: DexListingCandidate = {
  product_id: 1, popcore_id: 'PM0100', kind: 'blind_box', name: 'Echo Series Figures', book_ref: 10, popcore_type: '盲盒',
  price_cents: 1599, set_id: 'PM0100-SET', boxes_per_set: 12, set_price_cents: 19188, stock: 20, set_stock: 2,
  missing: ['PM0100', 'PM0100-SET'], action: 'new', state: 'ready', entry: null, again: false,
}

test('a blind box lists its options, both prices and its stock', () => {
  assert.equal(listingFacts(box), 'Single Box + Whole Set · $15.99 · 整端 set $191.88 (12 boxes) · 库存 stock 20 + 2 sets')
  assert.equal(listingFacts({ ...box, action: 'complete', missing: ['PM0100-SET'] }),
    '补 Adds PM0100-SET · $15.99 · 整端 set $191.88 (12 boxes) · 库存 stock 20 + 2 sets')
  assert.equal(listingFacts({ ...box, set_id: null, boxes_per_set: null, set_price_cents: null, set_stock: null }),
    'Single Box · $15.99 · 库存 stock 20')
})

test('a design with no manager price says the website keeps its own, never the box price', () => {
  const design = { ...box, kind: 'confirmed_design' as const, price_cents: null, set_id: undefined, set_price_cents: undefined, set_stock: null, stock: 5 }
  assert.equal(listingFacts(design), 'no design price (the website keeps its own) · 库存 stock 5')
  assert.equal(listingFacts({ ...design, price_cents: 2899 }), '$28.99 · 库存 stock 5')
})

test('every website code has a plain hint, and an unknown code still reads', () => {
  for (const code of ['item_malformed', 'book_unknown', 'id_in_use', 'listing_not_variable', 'options_unusual', 'listing_failed']) {
    assert.notEqual(codeHint(code), 'popcore.ca did not take it.', code)
  }
  assert.equal(codeHint('something_new'), 'popcore.ca did not take it.')
  assert.equal(codeHint(null), 'popcore.ca did not take it.')
})

test('an approved item says what it waits for', () => {
  const row = { waits: null } as DexListingRow
  assert.match(waitText(row, false), /Dex sync is on/)
  assert.match(waitText(row, true), /next pass/)
  assert.match(waitText({ ...row, waits: 'book' }, true), /book entry/)
  assert.match(waitText({ ...row, waits: 'not_in_book' }, true), /No longer in the book/)
})
