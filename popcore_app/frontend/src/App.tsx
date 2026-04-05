import { useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { ConfigProvider } from 'antd'

import ProtectedRoute from './auth/ProtectedRoute'
import { useHasRole, type Role } from './auth/useRole'
import AppLayout from './components/AppLayout'
import { setTokenGetter } from './api/client'
import { useAppStore } from './store'
import client from './api/client'

import DashboardPage  from './pages/Dashboard'
import ProductsPage   from './pages/Products'
import StockPage      from './pages/Stock'
import RestockPage    from './pages/Restock'
import SalesPage      from './pages/Sales'
import DayDetailPage  from './pages/Sales/DayDetail'
import UsersPage      from './pages/Users'
import SchedulePage   from './pages/Schedule'

function RoleRoute({ minRole, element }: { minRole: Role; element: React.ReactNode }) {
  return useHasRole(minRole) ? <>{element}</> : <Navigate to="/products" replace />
}

function AppInner() {
  const { getAccessTokenSilently, isAuthenticated } = useAuth0()
  const { setSeries, setProductTypes } = useAppStore()

  useEffect(() => {
    setTokenGetter(() =>
      getAccessTokenSilently({ authorizationParams: { audience: 'https://popcore/api' } })
    )
  }, [getAccessTokenSilently])

  useEffect(() => {
    if (!isAuthenticated) return
    client.get('/series').then(r => setSeries(r.data))
    client.get('/product_types').then(r => setProductTypes(r.data))
  }, [isAuthenticated, setSeries, setProductTypes])

  return (
    <AppLayout>
      <Routes>
        <Route path="/"              element={<RoleRoute minRole="staff"   element={<DashboardPage />} />} />
        <Route path="/products"      element={<ProductsPage />} />
        <Route path="/stock"         element={<RoleRoute minRole="staff"   element={<StockPage />} />} />
        <Route path="/restock"       element={<RoleRoute minRole="staff"   element={<RestockPage />} />} />
        <Route path="/sales"          element={<RoleRoute minRole="manager" element={<SalesPage />} />} />
        <Route path="/sales/day/:date" element={<RoleRoute minRole="manager" element={<DayDetailPage />} />} />
        <Route path="/users"          element={<RoleRoute minRole="admin"   element={<UsersPage />} />} />
        <Route path="/schedule"      element={<RoleRoute minRole="viewer"  element={<SchedulePage />} />} />
        <Route path="*"              element={<Navigate to="/" replace />} />
      </Routes>
    </AppLayout>
  )
}

export default function App() {
  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary:     '#6366F1',
          colorSuccess:     '#10B981',
          colorWarning:     '#F59E0B',
          colorError:       '#EF4444',
          colorInfo:        '#6366F1',
          borderRadius:     8,
          fontFamily:       '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          colorBgContainer: '#ffffff',
          colorBgLayout:    '#f0f2f5',
        },
        components: {
          Menu: {
            darkItemBg:            '#0D1B2A',
            darkSubMenuItemBg:     '#0D1B2A',
            darkItemHoverBg:       'rgba(99,102,241,0.15)',
            darkItemSelectedBg:    'rgba(99,102,241,0.2)',
            darkItemSelectedColor: '#818cf8',
            itemBorderRadius:      8,
          },
          Table: {
            headerBg:    '#f9fafb',
            headerColor: '#374151',
            rowHoverBg:  '#f5f3ff',
            borderColor: '#e5e7eb',
          },
          Card:   { paddingLG: 16 },
          Button: { borderRadius: 8 },
          Tag:    { borderRadius: 6 },
        },
      }}
    >
      <ProtectedRoute>
        <AppInner />
      </ProtectedRoute>
    </ConfigProvider>
  )
}
