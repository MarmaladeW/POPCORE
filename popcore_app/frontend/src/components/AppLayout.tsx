import { useState } from 'react'
import { Layout, Menu, Avatar, Dropdown, Badge, Tag, Drawer, Grid, Button } from 'antd'
import {
  DashboardOutlined,
  AppstoreOutlined,
  InboxOutlined,
  DollarOutlined,
  TagsOutlined,
  SyncOutlined,
  UserOutlined,
  BellOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MenuOutlined,
  ShopOutlined,
} from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { useRole, useHasRole } from '../auth/useRole'
import dayjs from 'dayjs'

const { Sider, Header, Content } = Layout
const { useBreakpoint } = Grid

const ROLE_COLORS: Record<string, string> = {
  viewer:  '#64748b',
  staff:   '#0ea5e9',
  manager: '#8b5cf6',
  admin:   '#ef4444',
}

const ROLE_LABELS: Record<string, string> = {
  viewer:  'Viewer',
  staff:   'Staff',
  manager: 'Manager',
  admin:   'Admin',
}

const SIDEBAR_BG = '#0D1B2A'
const SIDEBAR_W  = 220
const SIDEBAR_C  = 64

/** The logo + nav menu block, shared between Sider and Drawer */
function SidebarContent({
  collapsed,
  selectedKey,
  navItems,
  onNavigate,
  onCollapse,
  showCollapse,
}: {
  collapsed: boolean
  selectedKey: string
  navItems: { key: string; icon: React.ReactNode; label: string }[]
  onNavigate: (key: string) => void
  onCollapse?: () => void
  showCollapse: boolean
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Logo */}
      <div style={{
        padding:      collapsed ? '20px 14px' : '20px 20px',
        display:      'flex',
        alignItems:   'center',
        gap:          10,
        borderBottom: '1px solid rgba(255,255,255,0.08)',
        marginBottom: 8,
        flexShrink:   0,
      }}>
        <div style={{
          width:          36,
          height:         36,
          borderRadius:   10,
          background:     'linear-gradient(135deg, #6366F1, #8B5CF6)',
          display:        'flex',
          alignItems:     'center',
          justifyContent: 'center',
          fontWeight:     700,
          fontSize:       16,
          color:          '#fff',
          flexShrink:     0,
        }}>P</div>
        {!collapsed && (
          <div>
            <div style={{ color: '#fff', fontWeight: 700, fontSize: 15, lineHeight: 1.2 }}>POPCORE</div>
            <div style={{ color: 'rgba(255,255,255,0.45)', fontSize: 11 }}>Inventory System</div>
          </div>
        )}
      </div>

      {/* Nav menu */}
      <Menu
        theme="dark"
        mode="inline"
        selectedKeys={[selectedKey]}
        items={navItems}
        onClick={({ key }) => onNavigate(key)}
        style={{ background: SIDEBAR_BG, borderRight: 'none', flex: 1 }}
      />

      {/* Collapse button (desktop only) */}
      {showCollapse && (
        <div
          onClick={onCollapse}
          style={{
            padding:    '14px 20px',
            color:      'rgba(255,255,255,0.45)',
            cursor:     'pointer',
            display:    'flex',
            alignItems: 'center',
            gap:        8,
            fontSize:   13,
            borderTop:  '1px solid rgba(255,255,255,0.08)',
            flexShrink: 0,
          }}
        >
          {collapsed
            ? <MenuUnfoldOutlined />
            : <><MenuFoldOutlined /><span>Collapse</span></>
          }
        </div>
      )}
    </div>
  )
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const [collapsed,   setCollapsed]   = useState(false)
  const [drawerOpen,  setDrawerOpen]  = useState(false)
  const screens    = useBreakpoint()
  const isMobile   = !screens.md          // < 768px

  const navigate  = useNavigate()
  const location  = useLocation()
  const { user, logout } = useAuth0()
  const role    = useRole()
  const isAdmin = useHasRole('admin')

  const selectedKey = location.pathname === '/' ? '/' : '/' + location.pathname.split('/')[1]

  const navItems = [
    { key: '/',              icon: <DashboardOutlined />, label: 'Dashboard'     },
    { key: '/products',      icon: <AppstoreOutlined />,  label: 'Products'      },
    { key: '/stock',         icon: <InboxOutlined />,     label: 'Stock'         },
    { key: '/restock',       icon: <ShopOutlined />,      label: 'Restock'       },
    { key: '/sales',         icon: <DollarOutlined />,    label: 'Sales'         },
    { key: '/market-prices', icon: <TagsOutlined />,      label: 'Market Prices' },
    { key: '/scrape-log',    icon: <SyncOutlined />,      label: 'Scrape Log'    },
    ...(isAdmin ? [{ key: '/users', icon: <UserOutlined />, label: 'Users' }] : []),
  ]

  const userMenu = [
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: 'Sign Out',
      danger: true,
      onClick: () => logout({ logoutParams: { returnTo: window.location.origin } }),
    },
  ]

  function handleNavigate(key: string) {
    navigate(key)
    setDrawerOpen(false)   // close drawer after nav on mobile
  }

  const sideW = collapsed ? SIDEBAR_C : SIDEBAR_W

  return (
    <Layout style={{ minHeight: '100vh', background: '#f0f2f5' }}>

      {/* ── Desktop: fixed sidebar ── */}
      {!isMobile && (
        <Sider
          collapsible
          collapsed={collapsed}
          trigger={null}
          width={SIDEBAR_W}
          collapsedWidth={SIDEBAR_C}
          style={{
            background:    SIDEBAR_BG,
            position:      'fixed',
            left:          0,
            top:           0,
            bottom:        0,
            zIndex:        100,
            overflow:      'auto',
            boxShadow:     '2px 0 8px rgba(0,0,0,0.3)',
          }}
        >
          <SidebarContent
            collapsed={collapsed}
            selectedKey={selectedKey}
            navItems={navItems}
            onNavigate={handleNavigate}
            onCollapse={() => setCollapsed(!collapsed)}
            showCollapse
          />
        </Sider>
      )}

      {/* ── Mobile: drawer sidebar ── */}
      {isMobile && (
        <Drawer
          placement="left"
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          width={SIDEBAR_W}
          styles={{
            body:   { padding: 0, background: SIDEBAR_BG },
            header: { display: 'none' },
          }}
          style={{ background: SIDEBAR_BG }}
        >
          <SidebarContent
            collapsed={false}
            selectedKey={selectedKey}
            navItems={navItems}
            onNavigate={handleNavigate}
            showCollapse={false}
          />
        </Drawer>
      )}

      {/* ── Main layout ── */}
      <Layout style={{
        marginLeft:  isMobile ? 0 : sideW,
        transition:  'margin-left 0.2s',
        background:  '#f0f2f5',
        minHeight:   '100vh',
      }}>
        {/* Header */}
        <Header style={{
          background:     '#fff',
          padding:        isMobile ? '0 12px' : '0 24px',
          height:         64,
          display:        'flex',
          alignItems:     'center',
          justifyContent: 'space-between',
          boxShadow:      '0 1px 4px rgba(0,0,0,0.08)',
          position:       'sticky',
          top:            0,
          zIndex:         99,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {/* Hamburger (mobile only) */}
            {isMobile && (
              <Button
                type="text"
                icon={<MenuOutlined style={{ fontSize: 18 }} />}
                onClick={() => setDrawerOpen(true)}
                style={{ color: '#6b7280' }}
              />
            )}
            {/* Date (desktop only) */}
            {!isMobile && (
              <span style={{ color: '#6b7280', fontSize: 13 }}>
                {dayjs().format('dddd, MMMM D, YYYY')}
              </span>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: isMobile ? 10 : 16 }}>
            {/* Role badge */}
            <Tag
              style={{
                background:   `${ROLE_COLORS[role] ?? '#6b7280'}18`,
                color:        ROLE_COLORS[role] ?? '#6b7280',
                border:       `1px solid ${ROLE_COLORS[role] ?? '#6b7280'}40`,
                borderRadius: 6,
                fontWeight:   500,
                fontSize:     12,
                padding:      '2px 8px',
                margin:       0,
              }}
            >
              {ROLE_LABELS[role] ?? role}
            </Tag>

            {/* Bell (desktop only — saves space on mobile) */}
            {!isMobile && (
              <Badge count={0} showZero={false}>
                <BellOutlined style={{ fontSize: 18, color: '#6b7280', cursor: 'pointer' }} />
              </Badge>
            )}

            {/* User dropdown */}
            <Dropdown menu={{ items: userMenu }} placement="bottomRight" trigger={['click']}>
              <div style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}>
                <Avatar
                  size={32}
                  src={user?.picture}
                  icon={!user?.picture ? <UserOutlined /> : undefined}
                  style={{ background: '#6366F1' }}
                />
                {!isMobile && (
                  <div style={{ lineHeight: 1.3 }}>
                    <div style={{ fontSize: 13, fontWeight: 500, color: '#111827' }}>
                      {user?.nickname ?? user?.name ?? 'User'}
                    </div>
                    <div style={{ fontSize: 11, color: '#9ca3af' }}>
                      {ROLE_LABELS[role] ?? role}
                    </div>
                  </div>
                )}
              </div>
            </Dropdown>
          </div>
        </Header>

        {/* Page content */}
        <Content style={{ padding: isMobile ? 12 : 24 }}>
          {children}
        </Content>
      </Layout>
    </Layout>
  )
}
