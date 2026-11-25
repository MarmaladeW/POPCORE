// Menu model for the desktop rail, the phone bottom bar and the 更多 page (Figma "Menus" page).
// Pages that left the menus keep their routes: /trades, /today.

// inventory: 库存查看 at the selected store (always true for managers and admins).
// operations: 门店操作权限 Store operations access at some store.
// A 查看者 Viewer is the store computer's account (owner, 2026-11-24: "specific for the store computer, where it is able to
// see the order we need to prepare in store, see the stock, dex, no need for shift, able to view products"): 网站订单 and
// 库存 at the stores an admin ticked as 店铺电脑 in Users, the watch list, Products and the Dex. No 排班, nothing to edit.
// checkout: 买单 at the selected store; false for staff until its Clover is connected (owner, 2026-11-20).
export type NavAccess = { staff: boolean; manager: boolean; admin: boolean; inventory: boolean; operations?: boolean; checkout?: boolean }

// 买单 for staff who take checkouts at the selected store.
const checkoutFor = (access: NavAccess): NavItem[] => access.checkout === false ? [] : [item.checkout]

// What the store computer looks at besides Products and the Dex. Its home is 网站订单, so 首页 shows only until a store
// is ticked for it.
const counterItems = (access: NavAccess): NavItem[] =>
  [...(access.operations ? [item.webOrders] : [item.home]), ...(access.inventory ? [item.stock] : [])]
export type NavIcon = 'home' | 'pay' | 'in' | 'box' | 'swap' | 'doc' | 'chart' | 'cal' | 'grid' | 'user' | 'gear' | 'gift' | 'count' | 'web' | 'eye' | 'clock'
export type NavItem = { key: string; zh: string; en: string; icon: NavIcon; note?: string }
export type NavGroup = { label: string; items: NavItem[] }

const item = {
  home: { key: '/', zh: '首页', en: 'Home', icon: 'home' },
  checkout: { key: '/checkout', zh: '买单', en: 'Checkout', icon: 'pay' },
  // One way to move goods (owner, 2026-11-24): it stands in for 入店, 补货, 入库, 调拨, the claw buttons and 损耗,
  // whose pages keep their routes and their records.
  move: { key: '/move', zh: '移货', en: 'Move stock', icon: 'swap' },
  summary: { key: '/summary', zh: '汇总', en: 'Summary', icon: 'doc' },
  stock: { key: '/stock', zh: '库存', en: 'Inventory', icon: 'box', note: '晚盘、娃娃机' },
  // Without 库存查看 the 库存 entry opens 晚盘 only (owner, 2026-11-01).
  eveningCount: { key: '/stock/count', zh: '晚盘', en: 'Evening count', icon: 'count' },
  // The shop computer's pickup and delivery orders from popcore.ca (owner, 2026-11-19).
  webOrders: { key: '/web-orders', zh: '网站订单', en: 'Web orders', icon: 'web' },
  // Photos and notes of people who stole from the stores (owner, 2026-11-19: "so everyone can recognize the people that
  // stole from the store ... also something showing in the store computer"): every role, the counter account included.
  watchList: { key: '/watch-list', zh: '可疑人员', en: 'Watch list', icon: 'eye' },
  // Who did what at the store, like the group chat (owner, 2026-11-20): every role that works there.
  activity: { key: '/activity', zh: '动态', en: 'Activity', icon: 'clock' },
  // 销售 opens its first tab, 每日汇总; 收工 and 历史日报 (/closing, /sales) are its other tabs.
  sales: { key: '/summary', zh: '销售', en: 'Sales', icon: 'doc', note: '汇总、收工、历史日报' },
  // 正式员工 and above have 4 员工折扣 a month (owner, 2026-11-19): staff see who has uses left, managers keep the record.
  staffDiscounts: { key: '/staff-discounts', zh: '员工折扣', en: 'Staff discounts', icon: 'user' },
  reports: { key: '/reports', zh: '报表', en: 'Insights', icon: 'chart', note: '经理' },
  // popcore.ca stock sync and web orders (owner, 2026-11-01).
  website: { key: '/website', zh: '网站', en: 'Website', icon: 'web', note: '经理' },
  schedule: { key: '/schedule', zh: '排班', en: 'Schedule', icon: 'cal' },
  products: { key: '/products', zh: '产品', en: 'Products', icon: 'grid', note: '系列与款式' },
  // The book of companies, IPs, series, products and designs (owner, 2026-11-12); any signed-in role reads it.
  dex: { key: '/dex', zh: '图鉴', en: 'Dex', icon: 'gift', note: '公司、IP、系列' },
  users: { key: '/users', zh: '员工', en: 'Users', icon: 'user', note: '管理员' },
  settings: { key: '/settings', zh: '设置', en: 'Settings', icon: 'gear', note: '管理员' },
} satisfies Record<string, NavItem>

export const removedFromMenus = ['/trades', '/today', '/incoming', '/restock']

export function railGroups(access: NavAccess): NavGroup[] {
  if (!access.staff) return [
    { label: '日常', items: [...counterItems(access), item.watchList] },
    { label: '查看', items: [item.products, item.dex] },
  ]
  const daily: NavItem[] = [item.home]
  if (access.manager) daily.push(item.checkout, item.move, item.stock, item.webOrders)
  else if (access.inventory) daily.push(...checkoutFor(access), item.move, item.summary, item.stock, item.webOrders, item.staffDiscounts)
  else daily.push(...checkoutFor(access), item.move, item.summary, item.eveningCount, item.webOrders, item.staffDiscounts)
  // 动态: who did what at the store, for everyone who works there.
  daily.push(item.activity, item.watchList)
  const planning: NavItem[] = [item.schedule, item.products, item.dex, ...(access.admin ? [item.users, item.settings] : [])]
  return [
    { label: '日常', items: daily },
    ...(access.manager ? [{ label: '查看', items: [item.sales, item.staffDiscounts, item.reports, item.website] }] : []),
    { label: access.manager ? '计划与管理' : '计划', items: planning },
  ]
}

export function moreItems(access: NavAccess): NavItem[] {
  return [
    ...(access.staff ? [item.move, access.inventory ? item.stock : item.eveningCount, item.webOrders, ...(access.manager ? [] : [item.staffDiscounts]), item.activity]
      : counterItems(access).filter(entry => entry !== item.home)),
    item.watchList,
    item.products,
    item.dex,
    ...(access.manager ? [item.sales, item.staffDiscounts, item.reports, item.website] : []),
    ...(access.admin ? [item.users, item.settings] : []),
  ]
}

// Phone bottom bar: 首页 · 买单 · 排班 · 更多. When the open page lives under 更多, it shows as
// its own tab before 排班 (Figma "Phone bottom bar · 5 tabs (库存)"). The store computer: 网站订单 · 库存 · 产品 · 更多.
export function bottomItems(access: NavAccess, active: string): NavItem[] {
  if (!access.staff) {
    const tabs = [...counterItems(access), item.products]
    const promoted = moreItems(access).find(entry => entry.key === active && !tabs.includes(entry))
    return [...tabs.slice(0, -1), ...(promoted ? [promoted] : []), item.products]
  }
  const promoted = moreItems(access).find(entry => entry.key === active)
  return [item.home, ...checkoutFor(access), ...(promoted ? [promoted] : []), item.schedule]
}

// Pages reached from inside Inventory: 晚盘 and the claw machine location.
export const inventorySections = ['/stock/count', '/stock/claw', '/claw']

// Pages reached from inside Checkout (a row on phone, a tab on desktop).
export const checkoutSections = ['/sales/quick', '/special-orders', '/checkout/cash-exchange', '/checkout/history']

export function activeSection(pathname: string, manager: boolean, inventory = true): string {
  if (!inventory && (pathname === '/stock/count' || pathname === '/goods/counts')) return '/stock/count'
  if (pathname === '/checkout/history' || pathname === '/sales/entry' || pathname === '/sales/quick' || pathname === '/special-orders' || pathname === '/checkout/cash-exchange') return '/checkout'
  if (pathname === '/closing' || pathname === '/sales' || pathname.startsWith('/sales/day/') || pathname === '/sales/report-stock' || pathname === '/sales/name-picks') return '/summary'
  if (pathname === '/sales/matching') return '/reports'
  if (pathname === '/incoming' || pathname === '/incoming/display' || pathname === '/restock') return '/move'
  if (pathname.startsWith('/goods/receiving') || pathname.startsWith('/goods/transfers')) return '/move'
  if (pathname.startsWith('/goods/')) return '/stock'
  if (pathname.startsWith('/trades/cases/')) return '/trades'
  if (pathname.startsWith('/sales/documents/') || pathname.startsWith('/sales/payments/')) {
    return manager ? '/summary' : '/checkout'
  }
  if (pathname.startsWith('/products/')) return '/products'
  if (pathname === '/stock/products' || pathname === '/claw' || pathname.startsWith('/stock/')) return '/stock'
  return pathname === '/' ? '/' : `/${pathname.split('/')[1]}`
}

export function navItem(key: string): NavItem | undefined {
  return Object.values(item).find(entry => entry.key === key)
}

// Home tiles (Figma Home): 记一笔 large, then 移货 / 库存 / 汇总. On phone these are the way to 移货 and 汇总.
export function homeShortcuts(access: NavAccess): NavItem[] {
  if (!access.staff) return []
  return access.inventory ? [...checkoutFor(access), item.move, item.stock, item.summary]
    : [...checkoutFor(access), item.move, item.eveningCount, item.summary]
}
