import { useCallback, useEffect, useMemo, useState } from 'react'
import { Dropdown, Grid } from 'antd'
import type { MenuProps } from 'antd'
import { Link, useLocation } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import dayjs from 'dayjs'

import { useHasRole, useRole } from '../auth/useRole'
import { activeSection, bottomItems, navItem, railGroups, type NavItem } from '../lib/navigation'
import Icon from './pc/Icon'
import { PageHeaderSlots, PhoneTopBar } from './pc/PageHeader'
import StoreChip from './pc/StoreChip'
import { CheckoutBusy, useCheckoutBusyListener } from './pc/useCheckoutBusy'
import './AppLayout.css'

const { useBreakpoint } = Grid

export const ROLE_LABELS: Record<string, string> = {
  viewer: '查看者 Viewer', staff: '店员 Staff', manager: '经理 Manager', admin: '管理员 Admin',
}

function RailLink({ item, active, collapsed }: { item: NavItem; active: boolean; collapsed: boolean }) {
  return <Link
    to={item.key} className={active ? 'pc-rail-item active' : 'pc-rail-item'}
    aria-current={active ? 'page' : undefined} title={collapsed ? `${item.zh} ${item.en}` : undefined}
  >
    <Icon name={item.icon} size={18} />
    {collapsed ? <span className="pc-sr">{item.zh} {item.en}</span>
      : <span className="pc-nav-text"><strong>{item.zh}</strong> <small>{item.en}</small></span>}
  </Link>
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const checkoutBusy = useCheckoutBusyListener()
  const screens = useBreakpoint()
  const mobile = !screens.md
  const location = useLocation()
  const { user, logout } = useAuth0()
  const role = useRole()
  const admin = useHasRole('admin')
  const manager = useHasRole('manager')
  const staff = useHasRole('staff')
  const access = useMemo(() => ({ staff, manager, admin }), [staff, manager, admin])
  const active = activeSection(location.pathname, manager)
  const onMore = location.pathname === '/more'
  // Schedule opens with the rail collapsed to give the calendar room (existing behaviour).
  const [userCollapsed, setUserCollapsed] = useState(false)
  const [scheduleExpanded, setScheduleExpanded] = useState(false)
  useEffect(() => setScheduleExpanded(false), [location.pathname])
  const collapsed = active === '/schedule' ? !scheduleExpanded : userCollapsed

  const [phoneSlot, setPhoneSlot] = useState<HTMLElement | null>(null)
  const [desktopSlot, setDesktopSlot] = useState<HTMLElement | null>(null)
  const [pageHeaders, setPageHeaders] = useState(0)
  const register = useCallback(() => {
    setPageHeaders(count => count + 1)
    return () => setPageHeaders(count => count - 1)
  }, [])
  const slots = useMemo(() => ({ phone: phoneSlot, desktop: desktopSlot, mobile, register }), [phoneSlot, desktopSlot, mobile, register])

  const signOut = () => logout({ logoutParams: { returnTo: window.location.origin } })
  const accountItems: MenuProps['items'] = [{ key: 'logout', label: '退出登录 Sign out', danger: true, onClick: signOut }]
  const userName = user?.nickname ?? user?.name ?? 'User'
  const fallback = navItem(active)

  return <CheckoutBusy.Provider value={checkoutBusy}><PageHeaderSlots.Provider value={slots}>
    <div className={`pc-shell pc-shell-operations${mobile ? ' pc-shell-phone' : ''}${collapsed ? ' pc-shell-collapsed' : ''}`}>
      {!mobile && <aside className="pc-rail">
        <div className="pc-rail-brand">{collapsed ? <strong>P</strong> : <><strong>POPCORE</strong><small>Store operations</small></>}</div>
        <nav className="pc-sidebar-menu" aria-label="Primary navigation">
          {railGroups(access).map(group => <section key={group.label} aria-label={group.label}>
            {!collapsed && <h2>{group.label}</h2>}
            {group.items.map(item => <RailLink key={item.key} item={item} active={active === item.key} collapsed={collapsed} />)}
          </section>)}
        </nav>
        <button type="button" className="pc-rail-collapse" aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'} onClick={() => active === '/schedule' ? setScheduleExpanded(value => !value) : setUserCollapsed(value => !value)}>
          <Icon name={collapsed ? 'right' : 'back'} size={14} />{!collapsed && <span>收起 Collapse</span>}
        </button>
      </aside>}

      <div className="pc-main">
        {mobile
          ? <div className="pc-phone-top" ref={setPhoneSlot}>
            {!pageHeaders && !onMore && <PhoneTopBar title={fallback?.zh ?? 'POPCORE'} sub={fallback?.en ?? 'Store operations'} />}
          </div>
          : <>
            <div className="pc-top">
              <span>
                {dayjs().format('dddd, MMMM D')} · <Dropdown menu={{ items: accountItems }} placement="bottomLeft" trigger={['click']}>
                  <button type="button" className="pc-account" aria-label="Account menu">{userName} · {ROLE_LABELS[role] || role}</button>
                </Dropdown>
              </span>
              <StoreChip />
            </div>
            <div ref={setDesktopSlot} />
          </>}
        <main className="pc-content">{children}</main>
      </div>

      {mobile && <nav className="pc-bottom-nav" aria-label="Primary navigation">
        {bottomItems(access, active).map(item => {
          const current = !onMore && active === item.key
          return <Link key={item.key} to={item.key} className={current ? 'active' : undefined} aria-current={current ? 'page' : undefined}>
            <Icon name={item.icon} size={20} /><small>{item.zh}</small><span className="pc-sr"> {item.en}</span>
          </Link>
        })}
        <Link to="/more" className={onMore ? 'active' : undefined} aria-current={onMore ? 'page' : undefined}>
          <Icon name="dots" size={20} /><small>更多</small><span className="pc-sr"> More</span>
        </Link>
      </nav>}
    </div>
  </PageHeaderSlots.Provider></CheckoutBusy.Provider>
}
