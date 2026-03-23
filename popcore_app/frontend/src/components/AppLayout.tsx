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
  ShopOutlined,
  EllipsisOutlined,
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
  const [collapsed,      setCollapsed]      = useState(false)
  const [moreDrawerOpen, setMoreDrawerOpen] = useState(false)
  const screens    = useBreakpoint()
  const isMobile   = !screens.md          // < 768px

  const navigate  = useNavigate()
  const location  = useLocation()
  const { user, logout } = useAuth0()
  const role    = useRole()
  const isAdmin   = useHasRole('admin')
  const isManager = useHasRole('manager')
  const isStaff   = useHasRole('staff')

  const selectedKey = location.pathname === '/' ? '/' : '/' + location.pathname.split('/')[1]

  // All nav items (for desktop sidebar)
  const navItems = [
    ...(isStaff   ? [{ key: '/',              icon: <DashboardOutlined />, label: 'Dashboard'     }] : []),
    { key: '/products',      icon: <AppstoreOutlined />,  label: 'Products'      },
    ...(isStaff   ? [{ key: '/stock',         icon: <InboxOutlined />,     label: 'Stock'         }] : []),
    ...(isStaff   ? [{ key: '/restock',       icon: <ShopOutlined />,      label: 'Restock'       }] : []),
    ...(isManager ? [{ key: '/sales',         icon: <DollarOutlined />,    label: 'Sales'         }] : []),
    ...(isManager ? [{ key: '/market-prices', icon: <TagsOutlined />,      label: 'Market Prices' }] : []),
    ...(isManager ? [{ key: '/scrape-log',    icon: <SyncOutlined />,      label: 'Scrape Log'    }] : []),
    ...(isAdmin   ? [{ key: '/users',         icon: <UserOutlined />,      label: 'Users'         }] : []),
  ]

  // Mobile bottom tab bar: the 4 most-used pages (role-gated)
  const bottomTabs = [
    ...(isStaff   ? [{ key: '/',        icon: <DashboardOutlined />, label: 'Dashboard' }] : []),
    { key: '/products',  icon: <AppstoreOutlined />,  label: 'Products'  },
    ...(isStaff   ? [{ key: '/stock',   icon: <InboxOutlined />,     label: 'Stock'     }] : []),
    ...(isManager ? [{ key: '/sales',   icon: <DollarOutlined />,    label: 'Sales'     }] : []),
  ]

  // "More" drawer extra nav items
  const moreNavItems = [
    ...(isStaff   ? [{ key: '/restock',       icon: <ShopOutlined />,   label: 'Restock'       }] : []),
    ...(isManager ? [{ key: '/market-prices', icon: <TagsOutlined />,   label: 'Market Prices' }] : []),
    ...(isManager ? [{ key: '/scrape-log',    icon: <SyncOutlined />,   label: 'Scrape Log'    }] : []),
    ...(isAdmin   ? [{ key: '/users',         icon: <UserOutlined />,   label: 'Users'         }] : []),
  ]

  function handleNavigate(key: string) {
    navigate(key)
    setMoreDrawerOpen(false)
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

      {/* ── Main layout ── */}
      <Layout style={{
        marginLeft:  isMobile ? 0 : sideW,
        transition:  'margin-left 0.2s',
        background:  '#f0f2f5',
        minHeight:   '100vh',
      }}>

        {/* ── Mobile: dark top bar ── */}
        {isMobile ? (
          <Header style={{
            background:     SIDEBAR_BG,
            padding:        '0 16px',
            height:         54,
            display:        'flex',
            alignItems:     'center',
            justifyContent: 'space-between',
            position:       'sticky',
            top:            0,
            zIndex:         99,
            boxShadow:      '0 2px 8px rgba(0,0,0,0.3)',
            flexShrink:     0,
          }}>
            {/* Logo */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{
                width:          32,
                height:         32,
                borderRadius:   8,
                background:     'linear-gradient(135deg, #6366F1, #8B5CF6)',
                display:        'flex',
                alignItems:     'center',
                justifyContent: 'center',
                fontWeight:     700,
                fontSize:       15,
                color:          '#fff',
              }}>P</div>
              <div style={{ color: '#fff', fontWeight: 700, fontSize: 15 }}>POPCORE</div>
            </div>

            {/* Right: bell + avatar */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
              <Badge count={0} showZero={false}>
                <BellOutlined aria-label="Notifications" style={{ fontSize: 18, color: 'rgba(255,255,255,0.7)', cursor: 'pointer' }} />
              </Badge>
              <Dropdown
                menu={{
                  items: [{
                    key: 'logout',
                    icon: <LogoutOutlined />,
                    label: 'Sign Out',
                    danger: true,
                    onClick: () => logout({ logoutParams: { returnTo: window.location.origin } }),
                  }],
                }}
                placement="bottomRight"
                trigger={['click']}
              >
                <Avatar
                  size={32}
                  src={user?.picture}
                  icon={!user?.picture ? <UserOutlined /> : undefined}
                  style={{ background: '#6366F1', cursor: 'pointer' }}
                />
              </Dropdown>
            </div>
          </Header>
        ) : (
          /* ── Desktop: white header bar ── */
          <Header style={{
            background:     '#fff',
            padding:        '0 24px',
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
              <span style={{ color: '#6b7280', fontSize: 13 }}>
                {dayjs().format('dddd, MMMM D, YYYY')}
              </span>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
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

              <Badge count={0} showZero={false}>
                <BellOutlined aria-label="Notifications" style={{ fontSize: 18, color: '#6b7280', cursor: 'pointer' }} />
              </Badge>

              {/* User dropdown */}
              <Dropdown
                menu={{
                  items: [{
                    key: 'logout',
                    icon: <LogoutOutlined />,
                    label: 'Sign Out',
                    danger: true,
                    onClick: () => logout({ logoutParams: { returnTo: window.location.origin } }),
                  }],
                }}
                placement="bottomRight"
                trigger={['click']}
              >
                <div style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Avatar
                    size={32}
                    src={user?.picture}
                    icon={!user?.picture ? <UserOutlined /> : undefined}
                    style={{ background: '#6366F1' }}
                  />
                  <div style={{ lineHeight: 1.3 }}>
                    <div style={{ fontSize: 13, fontWeight: 500, color: '#111827' }}>
                      {user?.nickname ?? user?.name ?? 'User'}
                    </div>
                    <div style={{ fontSize: 11, color: '#9ca3af' }}>
                      {ROLE_LABELS[role] ?? role}
                    </div>
                  </div>
                </div>
              </Dropdown>
            </div>
          </Header>
        )}

        {/* Page content */}
        <Content style={{
          padding:       isMobile ? 12 : 24,
          paddingBottom: isMobile ? 72 : 24,
        }}>
          {children}
        </Content>
      </Layout>

      {/* ── Mobile: fixed bottom tab bar ── */}
      {isMobile && (
        <div style={{
          position:   'fixed',
          bottom:     0,
          left:       0,
          right:      0,
          zIndex:     1000,
          background: SIDEBAR_BG,
          display:    'flex',
          boxShadow:  '0 -2px 10px rgba(0,0,0,0.3)',
          paddingBottom: 'env(safe-area-inset-bottom)',
        }}>
          {bottomTabs.map(tab => {
            const isActive = selectedKey === tab.key
            return (
              <div
                key={tab.key}
                onClick={() => handleNavigate(tab.key)}
                style={{
                  flex:           1,
                  display:        'flex',
                  flexDirection:  'column',
                  alignItems:     'center',
                  justifyContent: 'center',
                  padding:        '8px 4px',
                  cursor:         'pointer',
                  color:          isActive ? '#6366F1' : 'rgba(255,255,255,0.45)',
                  transition:     'color 0.15s',
                  minHeight:      54,
                  position:       'relative',
                }}
              >
                <div style={{ fontSize: 20 }}>{tab.icon}</div>
                <div style={{ fontSize: 10, marginTop: 2, fontWeight: isActive ? 600 : 400 }}>{tab.label}</div>
                {isActive && (
                  <div style={{
                    position:     'absolute',
                    top:          0,
                    width:        32,
                    height:       2,
                    background:   '#6366F1',
                    borderRadius: '0 0 2px 2px',
                  }} />
                )}
              </div>
            )
          })}

          {/* More tab */}
          <div
            onClick={() => setMoreDrawerOpen(true)}
            style={{
              flex:           1,
              display:        'flex',
              flexDirection:  'column',
              alignItems:     'center',
              justifyContent: 'center',
              padding:        '8px 4px',
              cursor:         'pointer',
              color:          'rgba(255,255,255,0.45)',
              minHeight:      54,
            }}
          >
            <div style={{ fontSize: 20 }}><EllipsisOutlined /></div>
            <div style={{ fontSize: 10, marginTop: 2 }}>More</div>
          </div>
        </div>
      )}

      {/* ── Mobile: "More" bottom drawer ── */}
      {isMobile && (
        <Drawer
          placement="bottom"
          open={moreDrawerOpen}
          onClose={() => setMoreDrawerOpen(false)}
          height="auto"
          title={null}
          styles={{
            body:   { padding: '8px 0 16px' },
            header: { display: 'none' },
            wrapper: { maxHeight: '70vh' },
          }}
        >
          {/* Pull indicator */}
          <div style={{ display: 'flex', justifyContent: 'center', padding: '8px 0 12px' }}>
            <div style={{ width: 36, height: 4, borderRadius: 2, background: '#e5e7eb' }} />
          </div>

          {/* Extra nav items */}
          {moreNavItems.length > 0 && (
            <div style={{ borderBottom: '1px solid #f3f4f6', paddingBottom: 8, marginBottom: 8 }}>
              {moreNavItems.map(item => (
                <div
                  key={item.key}
                  onClick={() => handleNavigate(item.key)}
                  style={{
                    display:     'flex',
                    alignItems:  'center',
                    gap:         12,
                    padding:     '14px 24px',
                    cursor:      'pointer',
                    color:       selectedKey === item.key ? '#6366F1' : '#374151',
                    background:  selectedKey === item.key ? '#eef2ff' : 'transparent',
                    fontSize:    15,
                    fontWeight:  selectedKey === item.key ? 600 : 400,
                  }}
                >
                  <span style={{ fontSize: 18 }}>{item.icon}</span>
                  <span>{item.label}</span>
                </div>
              ))}
            </div>
          )}

          {/* User info + role */}
          <div style={{ padding: '12px 24px', display: 'flex', alignItems: 'center', gap: 12 }}>
            <Avatar
              size={40}
              src={user?.picture}
              icon={!user?.picture ? <UserOutlined /> : undefined}
              style={{ background: '#6366F1', flexShrink: 0 }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: '#111827' }}>
                {user?.nickname ?? user?.name ?? 'User'}
              </div>
              <Tag
                style={{
                  background:   `${ROLE_COLORS[role] ?? '#6b7280'}18`,
                  color:        ROLE_COLORS[role] ?? '#6b7280',
                  border:       `1px solid ${ROLE_COLORS[role] ?? '#6b7280'}40`,
                  borderRadius: 6,
                  fontSize:     11,
                  padding:      '1px 6px',
                  margin:       '4px 0 0',
                }}
              >
                {ROLE_LABELS[role] ?? role}
              </Tag>
            </div>
            <Button
              danger
              type="text"
              icon={<LogoutOutlined />}
              onClick={() => logout({ logoutParams: { returnTo: window.location.origin } })}
              style={{ flexShrink: 0 }}
            >
              Sign Out
            </Button>
          </div>
        </Drawer>
      )}
    </Layout>
  )
}
