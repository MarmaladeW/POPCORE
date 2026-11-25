import assert from 'node:assert/strict'
import test from 'node:test'
import type { CountSection, SectionCountView } from '../../api/countSections'
import {
  draftFrom, draftFromProposal, dueLabel, groupsLine, lastCounted, marksLine, moveGroup, nextCard, onlyRoles, orderedCards,
  orderSections, sectionOfGroup, setTakesNew, takesNew,
} from './sectionsView.ts'

const section = (id: number, extra: Partial<CountSection> = {}): CountSection => ({
  id, name: `S${id}`, sort_order: id, lines: 10, kinds: ['blind_box'], groups: [], takes_new: [], uneven: false,
  last_count: null, open_count: null, ...extra,
})
const counted = (day: string) => ({ id: 1, finished_at: `${day} 20:00:00`, business_date: day, counter_name: 'Amy' })

test('the section counted longest ago comes first, never counted before all', () => {
  const order = orderSections([section(1, { last_count: counted('2026-11-10') }), section(2, { last_count: counted('2026-11-01') }),
    section(3)]).map(s => s.id)
  assert.deepEqual(order, [3, 2, 1])
  assert.equal(lastCounted(section(4)), '还没点过 · Not counted yet')
  assert.equal(lastCounted(section(5, { last_count: counted('2026-11-03') })), '上次 11/3 · Amy')
})

test('due reads as days left, today, or overdue', () => {
  const next = (due: string) => ({ section_id: 1, name: 'S1', last_counted: null, due_on: due, overdue: false })
  assert.equal(dueLabel(next('2026-11-24'), '2026-11-20'), '11/24 到期 · Due in 4 days')
  assert.equal(dueLabel(next('2026-11-21'), '2026-11-20'), '11/21 到期 · Due in 1 day')
  assert.equal(dueLabel(next('2026-11-20'), '2026-11-20'), '今天到期 · Due today')
  assert.equal(dueLabel(next('2026-11-18'), '2026-11-20'), '逾期 2 天 · Overdue 2 days')
})

test('a section is described by its biggest groups', () => {
  const groups = ['THE MONSTERS', 'MOLLY', 'THE MONSTERS', 'DIMOO', 'HIRONO', 'CRYBABY'].map(name =>
    ({ kind: 'blind_box' as const, group_key: name, name, lines: 1, new: false }))
  assert.equal(groupsLine(section(1, { groups })), 'THE MONSTERS · MOLLY · DIMOO · HIRONO +1')
})

test('next goes to the next card not yet counted, then back to the ones before', () => {
  const card = (id: number, done: boolean) => ({ product_id: id, title: `P${id}`, sku: null, image: null, lines: 1,
    counted: done ? 1 : 0, added: false, roles: null })
  const view = { places: [{ id: 1, code: 'floor', name: 'Floor', cards: [card(10, false), card(11, true), card(12, false)] },
    { id: 2, code: 'upstairs', name: 'Upstairs', cards: [card(10, false)] }] } as unknown as SectionCountView
  assert.equal(nextCard(view, 1, 10)?.card.product_id, 12)
  assert.deepEqual([nextCard(view, 1, 12)?.place.id, nextCard(view, 1, 12)?.card.product_id], [2, 10])
  assert.deepEqual([nextCard(view, 2, 10)?.place.id, nextCard(view, 2, 10)?.card.product_id], [1, 10])
  assert.equal(nextCard(view, null, null)?.card.product_id, 10)
})

test('待盘点 marked cards come first, from any place, and Next goes through them before the rest', () => {
  const mark = (name: string, reason: string) => ({ product_id: 99, name, reason, counted: false })
  const card = (id: number, marks: ReturnType<typeof mark>[] = []) =>
    ({ product_id: id, title: `Card ${id}`, sku: null, image: null, lines: 1, counted: 0, added: false, roles: null, marks })
  const view = { places: [
    { id: 1, code: 'floor', name: 'Floor', cards: [card(10), card(11, [mark('Eevee', '日报 11/20 楼下不够 × 1')])] },
    { id: 2, code: 'upstairs', name: 'Upstairs', cards: [card(20, [mark('Card 20', 'Shelf looked short')]), card(21)] },
  ] } as unknown as SectionCountView
  assert.deepEqual(orderedCards(view).map(entry => [entry.place.id, entry.card.product_id]), [[1, 11], [2, 20], [1, 10], [2, 21]])
  assert.equal(nextCard(view, null, null)?.card.product_id, 11)
  assert.equal(nextCard(view, 1, 11)?.card.product_id, 20)
  assert.equal(nextCard(view, 2, 20)?.card.product_id, 10)
  // The marked item is named when it is not the card itself (a design of a blind box).
  assert.equal(marksLine(view.places[0].cards[1]), 'Eevee: 日报 11/20 楼下不够 × 1')
  assert.equal(marksLine(view.places[1].cards[0]), 'Shelf looked short')
})

test('a card shows only the items its section enters', () => {
  const items = [{ role: 'box' as const, id: 1 }, { role: 'set' as const, id: 2 }, { role: 'design' as const, id: 3 }]
  assert.deepEqual(onlyRoles(items, ['box', 'set']).map(item => item.id), [1, 2])
  assert.deepEqual(onlyRoles(items, ['design']).map(item => item.id), [3])
  assert.deepEqual(onlyRoles(items, null).map(item => item.id), [1, 2, 3])
})

test('the editor keeps a kind held in one section as its "*" alone, and moves a group with its own part', () => {
  const group = (kind: 'blind_box' | 'design', key: string) => ({ kind, group_key: key, name: key, lines: 5, new: false })
  const sections = [
    section(1, { groups: [group('blind_box', 'dex:1'), group('design', 'dex:1')], takes_new: ['blind_box', 'design'] }),
    section(2, { groups: [group('blind_box', 'dex:2')], takes_new: ['display', 'other'] }),
  ]
  const draft = draftFrom(sections)
  // Blind boxes sit in two sections, so their groups are named; designs only in one, so just '*'.
  assert.deepEqual(draft[0].parts, [{ kind: 'blind_box', group_key: 'dex:1' }, { kind: 'blind_box', group_key: '*' },
    { kind: 'design', group_key: '*' }])
  assert.deepEqual(draft[1].parts, [{ kind: 'blind_box', group_key: 'dex:2' }, { kind: 'display', group_key: '*' },
    { kind: 'other', group_key: '*' }])
  const moved = moveGroup(draft, { kind: 'design', group_key: 'dex:1' }, 1)
  assert.equal(sectionOfGroup(moved, { kind: 'design', group_key: 'dex:1' }), 1)
  assert.equal(sectionOfGroup(moved, { kind: 'design', group_key: 'dex:9' }), 0)
  const star = setTakesNew(moved, 'design', 1)
  assert.equal(takesNew(star, 'design'), 1)
  assert.equal(star.flatMap(s => s.parts).filter(p => p.kind === 'design' && p.group_key === '*').length, 1)
})

test('a fresh proposal keeps the ids of sections with the same name', () => {
  const proposal = { store: { id: 1, code: 'DT', name: 'DT' }, lines: 3, sections: [
    { name: 'S1', parts: [], lines: 2, groups: [] }, { name: 'New one', parts: [], lines: 1, groups: [] }] }
  assert.deepEqual(draftFromProposal(proposal, [section(1), section(2)]).map(s => s.id), [1, undefined])
})
