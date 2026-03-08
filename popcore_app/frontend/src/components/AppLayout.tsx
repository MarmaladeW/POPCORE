import { Layout, Menu, Button, Avatar, Dropdown, Typography } from 'antd'
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
      <Sider width={180} theme="dark" collapsible breakpoint="lg">
        <div style={{ padding: '16px 24px', color: '#fff', fontWeight: 700, fontSize: 18 }}>
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
        <Content style={{ margin: 24, background: '#fff', borderRadius: 8, padding: 24, overflow: 'auto' }}>
          {children}
        </Content>
      </Layout>
    </Layout>
  )
}
