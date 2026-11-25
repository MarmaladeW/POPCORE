import assert from 'node:assert/strict'
import test from 'node:test'
import { activeSection, bottomItems, checkoutSections, homeShortcuts, inventorySections, moreItems, railGroups, removedFromMenus, type NavAccess } from './navigation.ts'

const roles: Record<string, NavAccess> = {
  viewer: { staff: false, manager: false, admin: false, inventory: false },
  staff: { staff: true, manager: false, admin: false, inventory: true },
  manager: { staff: true, manager: true, admin: false, inventory: true },
  admin: { staff: true, manager: true, admin: true, inventory: true },
}
// Staff an admin has not picked for 库存查看 at the selected store.
const unpicked: NavAccess = { ...roles.staff, inventory: false }

// Every page each role could open from the menus before the redesign.
const before: Record<string, string[]> = {
  // 可疑人员 Watch list came after the redesign, for every role (owner, 2026-11-19).
  viewer: ['/', '/schedule', '/products', '/dex', '/watch-list'],
  staff: ['/', '/checkout', '/incoming', '/claw', '/summary', '/stock', '/restock', '/special-orders', '/today', '/trades', '/schedule', '/products', '/dex',
    // 网站订单 Web orders, 可疑人员 Watch list and 员工折扣 Staff discounts came after the redesign (owner, 2026-11-19).
    '/web-orders', '/watch-list', '/staff-discounts', '/activity',
    // 移货 Move stock (owner, 2026-11-24) is the one way to move goods; 入店 and 补货 left the menus for it.
    '/move'],
}
before.manager = [...before.staff, '/sales', '/reports', '/website']
before.admin = [...before.manager, '/users', '/settings']

// Special orders sit inside Checkout, so anyone who can open Checkout reaches them from there.
// The claw machine sits inside Inventory.
// 历史日报 (/sales) is a tab of 销售, whose menu entry opens 每日汇总 (/summary).
const viaCheckout = (keys: string[], manager = false) => [...new Set([...keys, ...(keys.includes('/checkout') ? ['/special-orders'] : []), ...(keys.includes('/stock') ? ['/claw'] : []), ...(manager ? ['/sales'] : [])])]
const rail = (access: NavAccess) => viaCheckout(railGroups(access).flatMap(group => group.items.map(entry => entry.key)), access.manager)
const phone = (access: NavAccess) => viaCheckout([...bottomItems(access, '/'), ...moreItems(access), ...homeShortcuts(access)].map(entry => entry.key), access.manager)

test('Trades and Today leave every menu, and 入店 and 补货 give way to 移货', () => {
  assert.deepEqual(removedFromMenus, ['/trades', '/today', '/incoming', '/restock'])
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
  assert.ok(!rail(roles.viewer).includes('/staff-discounts') && !phone(roles.viewer).includes('/staff-discounts'))
  assert.ok(!rail(roles.staff).includes('/sales'))
  assert.ok(!railGroups(roles.staff).flatMap(group => group.items).some(entry => entry.zh === '销售'))
  assert.ok(!rail(roles.manager).includes('/users'))
  assert.ok(!moreItems(roles.manager).some(entry => entry.key === '/settings'))
})

test('更多 lists 移货, 库存, 网站订单, 可疑人员, 产品, 图鉴, 销售, 员工折扣, 报表, 网站, 员工, 设置 in order, by role', () => {
  assert.deepEqual(moreItems(roles.admin).map(entry => entry.zh), ['移货', '库存', '网站订单', '动态', '可疑人员', '产品', '图鉴', '销售', '员工折扣', '报表', '网站', '员工', '设置'])
  // Staff find 员工折扣 after 网站订单; a manager finds it beside 销售.
  assert.deepEqual(moreItems(roles.staff).map(entry => entry.zh), ['移货', '库存', '网站订单', '员工折扣', '动态', '可疑人员', '产品', '图鉴'])
  assert.equal(moreItems(roles.admin).find(entry => entry.zh === '销售')?.key, '/summary')
  assert.deepEqual(moreItems(roles.viewer).map(entry => entry.key), ['/watch-list', '/products', '/dex'])
})

test('rails follow the Figma groups', () => {
  assert.deepEqual(railGroups(roles.admin).map(group => group.label), ['日常', '查看', '计划与管理'])
  assert.deepEqual(railGroups(roles.staff).map(group => group.label), ['日常', '计划'])
  assert.deepEqual(railGroups(roles.manager)[1].items.map(entry => entry.zh), ['销售', '员工折扣', '报表', '网站'])
  assert.deepEqual(railGroups(roles.manager)[0].items.map(entry => entry.zh), ['首页', '买单', '移货', '库存', '网站订单', '动态', '可疑人员'])
  assert.deepEqual(railGroups(roles.staff)[0].items.map(entry => entry.zh), ['首页', '买单', '移货', '汇总', '库存', '网站订单', '员工折扣', '动态', '可疑人员'])
})

test('the bottom bar promotes the open 更多 section before 排班', () => {
  assert.deepEqual(bottomItems(roles.staff, '/').map(entry => entry.zh), ['首页', '买单', '排班'])
  assert.deepEqual(bottomItems(roles.staff, '/stock').map(entry => entry.zh), ['首页', '买单', '库存', '排班'])
  assert.deepEqual(bottomItems(roles.viewer, '/').map(entry => entry.zh), ['首页', '排班'])
  assert.deepEqual(bottomItems(roles.manager, '/summary').map(entry => entry.zh), ['首页', '买单', '销售', '排班'])
  assert.deepEqual(bottomItems(roles.staff, '/summary').map(entry => entry.zh), ['首页', '买单', '排班'])
})

test('special orders and 换现金 open from Checkout, not the menus', () => {
  for (const access of Object.values(roles)) {
    const menus = [...railGroups(access).flatMap(group => group.items), ...moreItems(access)].map(entry => entry.key)
    assert.ok(!menus.includes('/special-orders'))
  }
  assert.deepEqual(checkoutSections, ['/sales/quick', '/special-orders', '/checkout/cash-exchange', '/checkout/history'])
  assert.equal(activeSection('/sales/quick', false), '/checkout')
  assert.equal(activeSection('/special-orders', false), '/checkout')
  assert.equal(activeSection('/checkout/cash-exchange', false), '/checkout')
})

test('晚盘 and the claw machine open from Inventory, not the menus', () => {
  for (const access of Object.values(roles)) {
    const menus = [...railGroups(access).flatMap(group => group.items), ...moreItems(access)].map(entry => entry.key)
    for (const key of inventorySections) assert.ok(!menus.includes(key))
  }
  assert.equal(activeSection('/stock/count', false), '/stock')
  assert.equal(activeSection('/stock/claw', false), '/stock')
  assert.equal(activeSection('/claw', false), '/stock')
})

test('nested pages light up their section', () => {
  assert.equal(activeSection('/goods/counts', false), '/stock')
  assert.equal(activeSection('/goods/receiving', false), '/move')
  assert.equal(activeSection('/restock', false), '/move')
  assert.equal(activeSection('/sales/day/2026-10-22', true), '/summary')
  assert.equal(activeSection('/sales', true), '/summary')
  assert.equal(activeSection('/sales/name-picks', true), '/summary')
  assert.equal(activeSection('/sales/matching', true), '/reports')
  assert.equal(activeSection('/sales/documents/4', true), '/summary')
  assert.equal(activeSection('/sales/documents/4', false), '/checkout')
  assert.equal(activeSection('/products/clover-matching', true), '/products')
  assert.equal(activeSection('/dex/12', false), '/dex')
  assert.equal(activeSection('/closing', true), '/summary')
})

test('without 库存查看, 库存 becomes 晚盘 and 入店 leaves the menus', () => {
  assert.deepEqual(railGroups(unpicked)[0].items.map(entry => entry.zh), ['首页', '买单', '移货', '汇总', '晚盘', '网站订单', '员工折扣', '动态', '可疑人员'])
  assert.deepEqual(moreItems(unpicked).map(entry => entry.key), ['/move', '/stock/count', '/web-orders', '/staff-discounts', '/activity', '/watch-list', '/products', '/dex'])
  assert.deepEqual(homeShortcuts(unpicked).map(entry => entry.key), ['/checkout', '/move', '/stock/count', '/summary'])
  const everywhere = [...rail(unpicked), ...phone(unpicked)]
  for (const key of ['/stock', '/incoming', '/claw']) assert.ok(!everywhere.includes(key), key)
  assert.equal(activeSection('/stock/count', false, false), '/stock/count')
  assert.equal(activeSection('/goods/counts', false, false), '/stock/count')
  assert.deepEqual(bottomItems(unpicked, '/stock/count').map(entry => entry.zh), ['首页', '买单', '晚盘', '排班'])
})

test('a viewer counter account gets 网站订单 and 库存 to look at, beside Products and the Dex', () => {
  // Owner, 2026-11-19: the store computer's account. 查看者 Viewer, with store operations access (and 库存查看).
  const counter: NavAccess = { ...roles.viewer, operations: true, inventory: true }
  assert.deepEqual(railGroups(counter)[0].items.map(entry => entry.zh), ['首页', '网站订单', '库存', '动态', '可疑人员'])
  assert.deepEqual(moreItems(counter).map(entry => entry.key), ['/web-orders', '/stock', '/activity', '/watch-list', '/products', '/dex'])
  assert.deepEqual(railGroups({ ...counter, inventory: false })[0].items.map(entry => entry.key), ['/', '/web-orders', '/activity', '/watch-list'])
  for (const key of ['/checkout', '/move', '/summary', '/stock/count']) assert.ok(!rail(counter).includes(key), key)
  // A viewer without store operations access sees neither.
  assert.deepEqual(railGroups(roles.viewer)[0].items.map(entry => entry.key), ['/', '/watch-list'])
})

test('every role finds 可疑人员 Watch list in the rail and in 更多, the counter account included', () => {
  // Owner, 2026-11-19: "so everyone can recognize the people that stole from the store ... also something showing in the
  // store computer". Managers and up edit it on the same page.
  const counter: NavAccess = { ...roles.viewer, operations: true, inventory: true }
  for (const access of [...Object.values(roles), unpicked, counter]) {
    assert.ok(railGroups(access)[0].items.some(entry => entry.key === '/watch-list'))
    assert.ok(moreItems(access).some(entry => entry.key === '/watch-list'))
  }
  assert.equal(activeSection('/watch-list', false), '/watch-list')
  assert.deepEqual(bottomItems(roles.viewer, '/watch-list').map(entry => entry.zh), ['首页', '可疑人员', '排班'])
})

test('staff see no 买单 where checkout waits for Clover (owner, 2026-11-20)', () => {
  const waiting: NavAccess = { ...roles.staff, checkout: false }
  const keys = [...railGroups(waiting).flatMap(group => group.items), ...bottomItems(waiting, '/'), ...homeShortcuts(waiting)].map(entry => entry.key)
  assert.ok(!keys.includes('/checkout'))
  assert.deepEqual(bottomItems(waiting, '/').map(entry => entry.zh), ['首页', '排班'])
  assert.deepEqual(homeShortcuts({ ...unpicked, checkout: false }).map(entry => entry.zh), ['移货', '晚盘', '汇总'])
  assert.deepEqual(railGroups({ ...unpicked, checkout: false })[0].items.map(entry => entry.zh), ['首页', '移货', '汇总', '晚盘', '网站订单', '员工折扣', '动态', '可疑人员'])
  // Managers and admins keep it, and an open store shows it as before.
  assert.ok(railGroups({ ...roles.manager, checkout: false })[0].items.some(entry => entry.key === '/checkout'))
  assert.deepEqual(bottomItems({ ...roles.staff, checkout: true }, '/').map(entry => entry.zh), ['首页', '买单', '排班'])
})
