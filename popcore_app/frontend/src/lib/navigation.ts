// Menu model for the desktop rail, the phone bottom bar and the 更多 page (Figma "Menus" page).
// Pages that left the menus keep their routes: /trades, /today.

export type NavAccess = { staff: boolean; manager: boolean; admin: boolean }
export type NavIcon = 'home' | 'pay' | 'in' | 'box' | 'swap' | 'doc' | 'chart' | 'cal' | 'grid' | 'user' | 'gear' | 'gift' | 'count'
export type NavItem = { key: string; zh: string; en: string; icon: NavIcon; note?: string }
export type NavGroup = { label: string; items: NavItem[] }

const item = {
  home: { key: '/', zh: '首页', en: 'Home', icon: 'home' },
  checkout: { key: '/checkout', zh: '买单', en: 'Checkout', icon: 'pay' },
  incoming: { key: '/incoming', zh: '入店', en: 'Receive goods', icon: 'in' },
  summary: { key: '/summary', zh: '汇总', en: 'Summary', icon: 'doc' },
  stock: { key: '/stock', zh: '库存', en: 'Inventory', icon: 'box', note: '晚盘、娃娃机' },
  restock: { key: '/restock', zh: '补货', en: 'Restock', icon: 'swap' },
  // 销售 opens its first tab, 每日汇总; 收工 and 历史日报 (/closing, /sales) are its other tabs.
  sales: { key: '/summary', zh: '销售', en: 'Sales', icon: 'doc', note: '汇总、收工、历史日报' },
  reports: { key: '/reports', zh: '报表', en: 'Insights', icon: 'chart', note: '经理' },
  schedule: { key: '/schedule', zh: '排班', en: 'Schedule', icon: 'cal' },
  products: { key: '/products', zh: '产品', en: 'Products', icon: 'grid', note: '系列与款式' },
  users: { key: '/users', zh: '员工', en: 'Users', icon: 'user', note: '管理员' },
  settings: { key: '/settings', zh: '设置', en: 'Settings', icon: 'gear', note: '管理员' },
} satisfies Record<string, NavItem>

export const removedFromMenus = ['/trades', '/today']

export function railGroups(access: NavAccess): NavGroup[] {
  const daily: NavItem[] = [item.home]
  if (access.staff && access.manager) daily.push(item.checkout, item.incoming, item.stock, item.restock)
  else if (access.staff) daily.push(item.checkout, item.incoming, item.summary, item.stock, item.restock)
  const planning: NavItem[] = [item.schedule, item.products, ...(access.admin ? [item.users, item.settings] : [])]
  return [
    { label: '日常', items: daily },
    ...(access.manager ? [{ label: '查看', items: [item.sales, item.reports] }] : []),
    { label: access.manager ? '计划与管理' : '计划', items: planning },
  ]
}

export function moreItems(access: NavAccess): NavItem[] {
  return [
    ...(access.staff ? [item.stock, item.restock] : []),
    item.products,
    ...(access.manager ? [item.sales, item.reports] : []),
    ...(access.admin ? [item.users, item.settings] : []),
  ]
}

// Phone bottom bar: 首页 · 买单 · 排班 · 更多. When the open page lives under 更多, it shows as
// its own tab before 排班 (Figma "Phone bottom bar · 5 tabs (库存)").
export function bottomItems(access: NavAccess, active: string): NavItem[] {
  const promoted = moreItems(access).find(entry => entry.key === active)
  return [item.home, ...(access.staff ? [item.checkout] : []), ...(promoted ? [promoted] : []), item.schedule]
}

// Pages reached from inside Inventory: 晚盘 and the claw machine location.
export const inventorySections = ['/stock/count', '/stock/claw', '/claw']

// Pages reached from inside Checkout (a row on phone, a tab on desktop).
export const checkoutSections = ['/special-orders', '/checkout/cash-exchange', '/checkout/history']

export function activeSection(pathname: string, manager: boolean): string {
  if (pathname === '/checkout/history' || pathname === '/sales/entry' || pathname === '/special-orders' || pathname === '/checkout/cash-exchange') return '/checkout'
  if (pathname === '/closing' || pathname === '/sales' || pathname.startsWith('/sales/day/') || pathname === '/sales/matching') return '/summary'
  if (pathname === '/incoming/display') return '/incoming'
  if (pathname.startsWith('/goods/receiving') || pathname.startsWith('/goods/transfers')) return '/incoming'
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

// Home tiles (Figma Home): 买单 large, then 入店 / 库存 / 汇总. On phone these are the way to 入店 and 汇总.
export function homeShortcuts(access: NavAccess): NavItem[] {
  return access.staff ? [item.checkout, item.incoming, item.stock, item.summary] : []
}
