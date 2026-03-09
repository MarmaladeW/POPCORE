import { Layout, Menu, Avatar, Dropdown, Typography } from 'antd'
import {
  AppstoreOutlined,
  InboxOutlined,
  BarChartOutlined,
  ShopOutlined,
  UserOutlined,
  LogoutOutlined,
} from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { useRole, useHasRole } from '../auth/useRole'

const { Header, Sider, Content } = Layout
const { Text } = Typography

const ROLE_LABEL: Record<string, string> = {
  admin:   '管理员',
  manager: '经理',
  staff:   '店员',
  viewer:  '查看',
}

const globalCSS = `
  /* Page fade-in */
  @keyframes pageIn {
    from { opacity: 0; transform: translateY(6px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  .page-fade { animation: pageIn 0.2s ease; }

  /* Product cards */
  .product-card {
    border-radius: 12px;
    border: 1px solid #f0f0f0;
    padding: 16px;
    background: #fff;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    transition: box-shadow 0.2s, transform 0.15s;
    position: relative;
  }
  .product-card:hover {
    box-shadow: 0 6px 20px rgba(0,0,0,0.13);
    transform: translateY(-2px);
  }

  /* Primary button hover scale */
  .ant-btn-primary:not(:disabled):hover { transform: scale(1.02); }

  /* Series tag chips */
  .series-chip {
    cursor: pointer;
    user-select: none;
    transition: all 0.15s;
    border-radius: 20px !important;
    margin: 0 !important;
  }
  .series-chip:hover { opacity: 0.82; }

  /* Zebra striping for tables */
  .ant-table-row-alt > td.ant-table-cell {
    background: rgba(0,0,0,0.018) !important;
  }

  /* Highlight search match */
  mark.search-hl { background: #fef08a; padding: 0; border-radius: 2px; }

  /* Sidebar menu transitions */
  .ant-menu-item { transition: background 0.15s, color 0.15s !important; }
`

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const navigate  = useNavigate()
  const location  = useLocation()
  const { user, logout } = useAuth0()
  const role      = useRole()
  const isAdmin   = useHasRole('admin')

  const menuItems = [
    { key: '/products', icon: <AppstoreOutlined />, label: '产品' },
    { key: '/stock',    icon: <InboxOutlined />,    label: '库存' },
    { key: '/sales',    icon: <BarChartOutlined />, label: '销售' },
    { key: '/market',   icon: <ShopOutlined />,     label: '市场' },
    ...(isAdmin ? [{ key: '/users', icon: <UserOutlined />, label: '用户' }] : []),
  ]

  const userMenuItems = [
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      onClick: () => logout({ logoutParams: { returnTo: window.location.origin } }),
    },
  ]

  const selectedKey = '/' + location.pathname.split('/')[1]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <style>{globalCSS}</style>
      <Sider width={180} theme="dark" collapsible breakpoint="lg">
        <div style={{
          padding: '16px 24px',
          fontWeight: 800,
          fontSize: 18,
          background: 'linear-gradient(135deg, #818cf8, #c4b5fd)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          backgroundClip: 'text',
          letterSpacing: 1,
        }}>
          POPCORE
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{
          background: '#fff',
          padding: '0 24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'flex-end',
          gap: 12,
          boxShadow: '0 1px 4px rgba(0,0,0,.08)',
        }}>
          <Text type="secondary" style={{ fontSize: 12 }}>{ROLE_LABEL[role] ?? role}</Text>
          <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
            <div style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}>
              <Avatar size="small" icon={<UserOutlined />} />
              <Text>{user?.nickname ?? user?.name ?? '用户'}</Text>
            </div>
          </Dropdown>
        </Header>
        <Content
          className="page-fade"
          style={{ margin: 24, background: '#fff', borderRadius: 8, padding: 24, overflow: 'auto' }}
        >
          {children}
        </Content>
      </Layout>
    </Layout>
  )
}
