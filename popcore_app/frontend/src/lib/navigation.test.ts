import assert from 'node:assert/strict'
import test from 'node:test'
import { activeSection, bottomItems, checkoutSections, homeShortcuts, moreItems, railGroups, removedFromMenus, type NavAccess } from './navigation.ts'

const roles: Record<string, NavAccess> = {
  viewer: { staff: false, manager: false, admin: false },
  staff: { staff: true, manager: false, admin: false },
  manager: { staff: true, manager: true, admin: false },
  admin: { staff: true, manager: true, admin: true },
}

// Every page each role could open from the menus before the redesign.
const before: Record<string, string[]> = {
  viewer: ['/', '/schedule', '/products'],
  staff: ['/', '/checkout', '/incoming', '/claw', '/summary', '/stock', '/restock', '/special-orders', '/today', '/trades', '/schedule', '/products'],
}
before.manager = [...before.staff, '/sales', '/reports']
before.admin = [...before.manager, '/users', '/settings']

// Special orders sit inside Checkout, so anyone who can open Checkout reaches them from there.
const viaCheckout = (keys: string[]) => keys.includes('/checkout') ? [...keys, '/special-orders'] : keys
const rail = (access: NavAccess) => viaCheckout(railGroups(access).flatMap(group => group.items.map(entry => entry.key)))
const phone = (access: NavAccess) => viaCheckout([...bottomItems(access, '/'), ...moreItems(access), ...homeShortcuts(access)].map(entry => entry.key))

test('Trades and Today leave every menu', () => {
  assert.deepEqual(removedFromMenus, ['/trades', '/today'])
  for (const access of Object.values(roles)) {
    for (const key of removedFromMenus) {
      assert.ok(!rail(access).includes(key), `${key} still in rail`)
      assert.ok(!phone(access).includes(key), `${key} still on phone`)
    }
  }
})

test('every other page stays reachable from the rail, and on phone from the menus or Home tiles', () => {
  for (const [role, access] of Object.entries(roles)) {
    const kept = before[role].filter(key => !removedFromMenus.includes(key))
    assert.deepEqual([...rail(access)].sort(), [...kept].sort(), `${role} rail`)
    assert.deepEqual([...new Set(phone(access))].sort(), [...kept].sort(), `${role} phone`)
  }
})

test('menus never offer a page above the role', () => {
  assert.ok(!rail(roles.viewer).includes('/checkout'))
  assert.ok(!rail(roles.staff).includes('/sales'))
  assert.ok(!rail(roles.manager).includes('/users'))
  assert.ok(!moreItems(roles.manager).some(entry => entry.key === '/settings'))
})

test('更多 lists 库存, 补货, 产品, 销售, 报表, 员工, 设置 in order, by role', () => {
  const order = ['/stock', '/restock', '/products', '/sales', '/reports', '/users', '/settings']
  const listed = moreItems(roles.admin).map(entry => entry.key).filter(key => order.includes(key))
  assert.deepEqual(listed, order)
  assert.deepEqual(moreItems(roles.viewer).map(entry => entry.key), ['/products'])
})

test('rails follow the Figma groups', () => {
  assert.deepEqual(railGroups(roles.admin).map(group => group.label), ['日常', '查看', '计划与管理'])
  assert.deepEqual(railGroups(roles.staff).map(group => group.label), ['日常', '计划'])
  assert.deepEqual(railGroups(roles.manager)[1].items.map(entry => entry.zh), ['销售', '报表'])
})

test('the bottom bar promotes the open 更多 section before 排班', () => {
  assert.deepEqual(bottomItems(roles.staff, '/').map(entry => entry.zh), ['首页', '买单', '排班'])
  assert.deepEqual(bottomItems(roles.staff, '/stock').map(entry => entry.zh), ['首页', '买单', '库存', '排班'])
  assert.deepEqual(bottomItems(roles.viewer, '/').map(entry => entry.zh), ['首页', '排班'])
})

test('special orders and 换现金 open from Checkout, not the menus', () => {
  for (const access of Object.values(roles)) {
    const menus = [...railGroups(access).flatMap(group => group.items), ...moreItems(access)].map(entry => entry.key)
    assert.ok(!menus.includes('/special-orders'))
  }
  assert.deepEqual(checkoutSections, ['/special-orders', '/checkout/cash-exchange', '/checkout/history'])
  assert.equal(activeSection('/special-orders', false), '/checkout')
  assert.equal(activeSection('/checkout/cash-exchange', false), '/checkout')
})

test('nested pages light up their section', () => {
  assert.equal(activeSection('/goods/counts', false), '/stock')
  assert.equal(activeSection('/goods/receiving', false), '/incoming')
  assert.equal(activeSection('/sales/day/2026-10-22', true), '/sales')
  assert.equal(activeSection('/sales/documents/4', false), '/checkout')
  assert.equal(activeSection('/products/clover-matching', true), '/products')
  assert.equal(activeSection('/closing', true), '/summary')
})
