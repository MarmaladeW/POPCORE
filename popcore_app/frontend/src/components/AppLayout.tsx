import { useEffect, useRef, useState } from 'react'
import { Avatar, Button, Drawer, Dropdown, Grid, Layout, Menu, Tag } from 'antd'
import type { MenuProps } from 'antd'
import {
  AppstoreOutlined, BarChartOutlined, CalendarOutlined, CheckCircleOutlined,
  HomeOutlined, CameraOutlined, FileSearchOutlined, FileTextOutlined, GiftOutlined, HistoryOutlined, DashboardOutlined, DollarOutlined, EllipsisOutlined, InboxOutlined,
  LogoutOutlined, MenuFoldOutlined, MenuUnfoldOutlined, SettingOutlined,
  ShopOutlined, SwapOutlined, UserOutlined,
} from '@ant-design/icons'
import { Link, useLocation } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import dayjs from 'dayjs'

import { useHasRole, useRole } from '../auth/useRole'
import { ALL_STORES, useAppStore } from '../store'

const { Content, Header, Sider } = Layout
const { useBreakpoint } = Grid
const SIDEBAR_W = 220
const SIDEBAR_C = 64

const ROLE_COLORS: Record<string, string> = {
  viewer: '#64748b', staff: '#0284c7', manager: '#7c3aed', admin: '#dc2626',
}
const ROLE_LABELS: Record<string, string> = {
  viewer: 'Viewer', staff: 'Staff', manager: 'Manager', admin: 'Admin',
}

type NavItem = { key: string; icon: React.ReactNode; label: string }

function Brand({ collapsed = false }: { collapsed?: boolean }) {
  return <div className="pc-brand">
    <span className="pc-brand-mark" aria-hidden="true">P</span>
    {!collapsed && <span><strong>POPCORE</strong><small>Store operations</small></span>}
  </div>
}

function StoreSelect({ mobile = false, disabled = false }: { mobile?: boolean; disabled?: boolean }) {
  const { stores, selectedStore, setSelectedStore } = useAppStore()
  if (!stores.length || !selectedStore) return null
  return <select
    disabled={disabled}
    aria-label="Operations store"
    className={mobile ? 'pc-store-select pc-store-select-mobile' : 'pc-store-select'}
    value={selectedStore.code}
    onChange={event => {
      const code = event.target.value
      if (code === 'ALL') setSelectedStore(ALL_STORES)
      else {
        const store = stores.find(item => item.code === code)
        if (store) setSelectedStore(store)
      }
    }}
  >
    <option value="ALL">All stores</option>
    {stores.map(store => <option key={store.code} value={store.code}>
      {mobile ? store.code : (store.name || store.code)}
    </option>)}
  </select>
}

function currentNav(pathname: string, manager: boolean) {
  if (pathname === '/checkout/history') return '/checkout/history'
  if (pathname.startsWith('/goods/')) return '/stock'
  if (pathname.startsWith('/trades/cases/')) return '/trades'
  if (pathname.startsWith('/sales/documents/') || pathname.startsWith('/sales/payments/')) {
    return manager ? '/sales' : '/sales/entry'
  }
  if (pathname.startsWith('/sales/day/')) return '/sales'
  if (pathname === '/sales/entry') return '/sales/entry'
  return pathname === '/' ? '/' : `/${pathname.split('/')[1]}`
}

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  return <Link to={item.key} aria-current={active ? 'page' : undefined}>{item.label}</Link>
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const [checkoutBusy, setCheckoutBusy] = useState(false)
  useEffect(() => {
    const update = (event: Event) => setCheckoutBusy(Boolean((event as CustomEvent).detail))
    window.addEventListener('popcore:checkout-busy', update)
    return () => window.removeEventListener('popcore:checkout-busy', update)
  }, [])
  const [userCollapsed, setCollapsed] = useState(false)
  const [scheduleExpanded, setScheduleExpanded] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const moreButton = useRef<HTMLButtonElement>(null)
  const screens = useBreakpoint()
  const mobile = !screens.md
  const location = useLocation()
  const { user, logout } = useAuth0()
  const role = useRole()
  const admin = useHasRole('admin')
  const manager = useHasRole('manager')
  const staff = useHasRole('staff')
  const active = currentNav(location.pathname, manager)
  const collapsed = active === '/schedule' ? !scheduleExpanded : userCollapsed
  useEffect(() => setScheduleExpanded(false), [location.pathname])

  useEffect(() => setMoreOpen(false), [location.pathname])

  const daily: NavItem[] = [
    { key: '/', icon: <HomeOutlined />, label: 'Home' },
    ...(staff ? [
      { key: '/checkout', icon: <CameraOutlined />, label: '买单 / Checkout' },
      { key: '/special-orders', icon: <FileSearchOutlined />, label: 'Special orders' },
      { key: '/incoming', icon: <InboxOutlined />, label: '入店 / Receive goods' },
      { key: '/claw', icon: <GiftOutlined />, label: '娃娃机 / Claw machine' },
      { key: '/summary', icon: <FileTextOutlined />, label: '汇总 / Summary' },
      { key: '/checkout/history', icon: <HistoryOutlined />, label: 'Order history' },
      { key: '/today', icon: <DashboardOutlined />, label: 'Store tasks' },
      { key: '/stock', icon: <InboxOutlined />, label: 'Inventory' },
      { key: '/restock', icon: <ShopOutlined />, label: 'Restock' },
      { key: '/sales/entry', icon: <DollarOutlined />, label: 'Enter sale' },
      { key: '/closing', icon: <CheckCircleOutlined />, label: 'Closing' },
      { key: '/trades', icon: <SwapOutlined />, label: 'Trades' },
    ] : []),
  ]
  const review: NavItem[] = manager ? [
    { key: '/sales', icon: <DollarOutlined />, label: 'Sales' },
    { key: '/reports', icon: <BarChartOutlined />, label: 'Reports' },
  ] : []
  const planning: NavItem[] = [
    { key: '/schedule', icon: <CalendarOutlined />, label: 'Schedule' },
    { key: '/products', icon: <AppstoreOutlined />, label: 'Products' },
    ...(admin ? [
      { key: '/users', icon: <UserOutlined />, label: 'Users' },
      { key: '/settings', icon: <SettingOutlined />, label: 'Settings' },
    ] : []),
  ]
  const groupedMenuItems: MenuProps['items'] = [
    { type: 'group', label: collapsed ? undefined : 'Daily work', children: daily.map(item => ({
      key: item.key, icon: item.icon, label: <NavLink item={item} active={active === item.key} />,
    })) },
    ...(review.length ? [{ type: 'group' as const, label: collapsed ? undefined : 'Review', children: review.map(item => ({
      key: item.key, icon: item.icon, label: <NavLink item={item} active={active === item.key} />,
    })) }] : []),
    { type: 'group', label: collapsed ? undefined : 'Planning & admin', children: planning.map(item => ({
      key: item.key, icon: item.icon, label: <NavLink item={item} active={active === item.key} />,
    })) },
  ]
  const bottom: NavItem[] = [
    daily[0],
    ...(staff ? [daily[1]] : []),
    planning[0],
  ]
  const bottomKeys = new Set(bottom.map(item => item.key))
  const more = [...daily, ...review, ...planning].filter(item => !bottomKeys.has(item.key))
  const shellClass = 'pc-shell pc-shell-operations'

  const accountItems: MenuProps['items'] = [{
    key: 'logout', icon: <LogoutOutlined />, label: 'Sign out', danger: true,
    onClick: () => logout({ logoutParams: { returnTo: window.location.origin } }),
  }]

  return <Layout className={shellClass}>
    {!mobile && <Sider
      className="pc-sidebar" width={SIDEBAR_W} collapsedWidth={SIDEBAR_C}
      collapsed={collapsed} trigger={null}
    >
      <Brand collapsed={collapsed} />
      <Menu className="pc-sidebar-menu" theme="light" mode="inline" selectedKeys={[active]} items={groupedMenuItems} />
      <Button
        className="pc-collapse" type="text"
        aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
        onClick={() => active === '/schedule' ? setScheduleExpanded(value => !value) : setCollapsed(value => !value)}
      >{collapsed ? null : 'Collapse'}</Button>
    </Sider>}

    <Layout className="pc-main" style={{ marginLeft: mobile ? 0 : (collapsed ? SIDEBAR_C : SIDEBAR_W) }}>
      <Header className="pc-header">
        {mobile ? <Brand /> : <span className="pc-date">{dayjs().format('dddd, MMMM D, YYYY')}</span>}
        <div className="pc-header-actions">
          <StoreSelect mobile={mobile} disabled={checkoutBusy} />
          {!mobile && <Tag className="pc-role" style={{ color: ROLE_COLORS[role], borderColor: `${ROLE_COLORS[role]}55` }}>
            {ROLE_LABELS[role] || role}
          </Tag>}
          <Dropdown menu={{ items: accountItems }} placement="bottomRight" trigger={['click']}>
            <Button type="text" className="pc-account" aria-label="Account menu">
              <Avatar size={32} src={user?.picture} icon={!user?.picture ? <UserOutlined /> : undefined} />
              {!mobile && <span><strong>{user?.nickname ?? user?.name ?? 'User'}</strong><small>{ROLE_LABELS[role] || role}</small></span>}
            </Button>
          </Dropdown>
        </div>
      </Header>
      <Content className="pc-content">{children}</Content>
    </Layout>

    {mobile && <nav className="pc-bottom-nav" aria-label="Primary navigation">
      {bottom.map(item => <Link
        key={item.key} to={item.key} className={active === item.key ? 'active' : undefined}
        aria-current={active === item.key ? 'page' : undefined}
      ><span aria-hidden="true">{item.icon}</span><small>{item.label}</small></Link>)}
      <button ref={moreButton} type="button" className="pc-more-button" aria-label="More" onClick={() => setMoreOpen(true)}>
        <EllipsisOutlined /><small>More</small>
      </button>
    </nav>}

    {mobile && <Drawer
      title="More" placement="bottom" open={moreOpen} height="auto"
      onClose={() => {
        setMoreOpen(false)
        window.setTimeout(() => moreButton.current?.focus(), 350)
      }}
      className="pc-more-drawer"
    >
      <nav aria-label="More navigation">
        {more.map(item => <Link
          key={item.key} to={item.key} aria-current={active === item.key ? 'page' : undefined}
          onClick={() => setMoreOpen(false)}
        ><span aria-hidden="true">{item.icon}</span>{item.label}</Link>)}
      </nav>
      <div className="pc-more-account">
        <Avatar size={40} src={user?.picture} icon={!user?.picture ? <UserOutlined /> : undefined} />
        <span><strong>{user?.nickname ?? user?.name ?? 'User'}</strong><small>{ROLE_LABELS[role] || role}</small></span>
        <Button danger type="text" icon={<LogoutOutlined />} onClick={() => logout({ logoutParams: { returnTo: window.location.origin } })}>Sign out</Button>
      </div>
    </Drawer>}
  </Layout>
}
