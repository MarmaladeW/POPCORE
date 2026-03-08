import { useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'

import ProtectedRoute from './auth/ProtectedRoute'
import AppLayout from './components/AppLayout'
import { setTokenGetter } from './api/client'
import { useAppStore } from './store'
import client from './api/client'

import ProductsPage from './pages/Products'
import StockPage    from './pages/Stock'
import SalesPage    from './pages/Sales'
import MarketPage   from './pages/Market'
import UsersPage    from './pages/Users'

dayjs.locale('zh-cn')

function AppInner() {
  const { getAccessTokenSilently, isAuthenticated } = useAuth0()
  const { setSeries, setProductTypes } = useAppStore()

  // Wire up the Axios interceptor once authenticated
  useEffect(() => {
    setTokenGetter(() =>
      getAccessTokenSilently({ authorizationParams: { audience: 'https://popcore/api' } })
    )
  }, [getAccessTokenSilently])

  // Pre-load series and product types once logged in
  useEffect(() => {
    if (!isAuthenticated) return
    client.get('/series').then(r => setSeries(r.data))
    client.get('/product_types').then(r => setProductTypes(r.data))
  }, [isAuthenticated, setSeries, setProductTypes])

  return (
    <AppLayout>
      <Routes>
        <Route path="/"         element={<Navigate to="/products" replace />} />
        <Route path="/products" element={<ProductsPage />} />
        <Route path="/stock"    element={<StockPage />} />
        <Route path="/sales"    element={<SalesPage />} />
        <Route path="/market"   element={<MarketPage />} />
        <Route path="/users"    element={<UsersPage />} />
        <Route path="*"         element={<Navigate to="/products" replace />} />
      </Routes>
    </AppLayout>
  )
}

export default function App() {
  return (
    <ConfigProvider locale={zhCN}>
      <ProtectedRoute>
        <AppInner />
      </ProtectedRoute>
    </ConfigProvider>
  )
}
