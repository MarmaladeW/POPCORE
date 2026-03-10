import { useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { ConfigProvider } from 'antd'

import ProtectedRoute from './auth/ProtectedRoute'
import AppLayout from './components/AppLayout'
import { setTokenGetter } from './api/client'
import { useAppStore } from './store'
import client from './api/client'

import DashboardPage from './pages/Dashboard'
import ProductsPage  from './pages/Products'
import StockPage     from './pages/Stock'
import RestockPage   from './pages/Restock'
import SalesPage     from './pages/Sales'
import MarketPage    from './pages/Market'
import ScrapeLogPage from './pages/ScrapeLog'
import UsersPage     from './pages/Users'

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
        <Route path="/"              element={<DashboardPage />} />
        <Route path="/products"      element={<ProductsPage />} />
        <Route path="/stock"         element={<StockPage />} />
        <Route path="/restock"       element={<RestockPage />} />
        <Route path="/sales"         element={<SalesPage />} />
        <Route path="/market-prices" element={<MarketPage />} />
        <Route path="/scrape-log"    element={<ScrapeLogPage />} />
        <Route path="/users"         element={<UsersPage />} />
        <Route path="/market"        element={<Navigate to="/market-prices" replace />} />
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
