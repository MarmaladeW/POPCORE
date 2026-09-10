import { lazy, Suspense, useEffect, useState } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { Button, ConfigProvider, Modal, Result } from 'antd'

import ProtectedRoute from './auth/ProtectedRoute'
import { useHasRole, type Role } from './auth/useRole'
import AppLayout from './components/AppLayout'
import ErrorBoundary from './components/ErrorBoundary'
import { resetAuthWarnings, setTokenGetter } from './api/client'
import { useAppStore, ALL_STORES } from './store'
import type { Store } from './store'
import client from './api/client'

import SchedulePage    from './pages/Schedule'
const DashboardPage=lazy(()=>import('./pages/Dashboard'))
const ProductsPage=lazy(()=>import('./pages/Products'))
const StockPage=lazy(()=>import('./pages/Stock'))
const GoodsPage=lazy(()=>import('./pages/Stock/Goods'))
const RestockPage=lazy(()=>import('./pages/Restock'))
const SalesPage=lazy(()=>import('./pages/Sales'))
const DayDetailPage=lazy(()=>import('./pages/Sales/DayDetail'))
const SaleEntryPage=lazy(()=>import('./pages/Sales/Entry'))
const SaleDocumentPage=lazy(()=>import('./pages/Sales/SaleDocument'))
const PaymentEvidencePage=lazy(()=>import('./pages/Sales/PaymentEvidence'))
const ClosingPage=lazy(()=>import('./pages/Closing'))
const UsersPage=lazy(()=>import('./pages/Users'))
const SettingsPage=lazy(()=>import('./pages/Settings'))
const NotFound=lazy(()=>import('./pages/NotFound'))
const Unauthorized=lazy(()=>import('./pages/Unauthorized'))
const TradesPage=lazy(()=>import('./pages/Trades'))
const TradeCasePage=lazy(()=>import('./pages/Trades/TradeCase'))
const ReportsPage=lazy(()=>import('./pages/Reports'))

function RoleRoute({ minRole, element }: { minRole: Role; element: React.ReactNode }) {
  return useHasRole(minRole) ? <>{element}</> : <Unauthorized />
}

function AppInner() {
  const { getAccessTokenSilently, isAuthenticated, loginWithRedirect, user } = useAuth0()
  const { setSeries, setProductTypes, setStores, setSelectedStore, selectedStore } = useAppStore()
  const [bootstrapError, setBootstrapError] = useState(false)
  const [bootstrapAttempt, setBootstrapAttempt] = useState(0)

  useEffect(() => {
    setTokenGetter(() =>
      getAccessTokenSilently({ authorizationParams: { audience: import.meta.env.VITE_AUTH0_AUDIENCE as string } })
    )
    const reauthenticate = () => {
      loginWithRedirect().catch(() => {
        Modal.error({
          title: 'Unable to start sign-in',
          content: 'Check the Auth0 configuration or connection, then retry.',
          okText: 'Retry',
          onOk: reauthenticate,
        })
      })
    }
    window.addEventListener('popcore:reauthenticate', reauthenticate)
    return () => {
      window.removeEventListener('popcore:reauthenticate', reauthenticate)
      setTokenGetter(null)
    }
  }, [getAccessTokenSilently, loginWithRedirect])

  useEffect(() => {
    resetAuthWarnings()
    setSeries([])
    setProductTypes([])
    setStores([])
    setBootstrapError(false)
  }, [user?.sub, setProductTypes, setSeries, setStores])

  useEffect(() => {
    if (!isAuthenticated) return
    let current = true
    setBootstrapError(false)
    Promise.all([
      client.get('/series'),
      client.get('/product_types'),
      client.get('/stores'),
    ]).then(([seriesResponse, typesResponse, storesResponse]) => {
      if (!current) return
      setSeries(seriesResponse.data)
      setProductTypes(typesResponse.data)
      const stores: Store[] = storesResponse.data
      setStores(stores)
      if (stores.length === 0) return
      // Keep persisted selection if still valid (including ALL), else default to ALL
      const valid = selectedStore && (
        selectedStore.code === 'ALL' ||
        stores.find(s => s.code === selectedStore.code)
      )
      if (!valid) setSelectedStore(ALL_STORES)
    }).catch(() => {
      if (current) setBootstrapError(true)
    })
    return () => { current = false }
  }, [bootstrapAttempt, isAuthenticated, user?.sub]) // eslint-disable-line react-hooks/exhaustive-deps

  if (bootstrapError) {
    return (
      <AppLayout key={user?.sub}>
        <Result
          status="error"
          title="Unable to load store setup"
          subTitle="Check your connection, then retry."
          extra={<Button type="primary" onClick={() => setBootstrapAttempt(n => n + 1)}>Retry</Button>}
        />
      </AppLayout>
    )
  }

  return (
    <AppLayout key={user?.sub}>
      <ErrorBoundary>
        <Suspense fallback={<div role="status" aria-live="polite">Loading page…</div>}><Routes>
          <Route path="/"               element={<RoleRoute minRole="viewer"  element={<DashboardPage />} />} />
          <Route path="/products"       element={<ProductsPage />} />
          <Route path="/stock"          element={<RoleRoute minRole="staff"   element={<StockPage />} />} />
          <Route path="/goods/receiving" element={<RoleRoute minRole="staff" element={<GoodsPage initialTab="receiving" />} />} />
          <Route path="/goods/transfers" element={<RoleRoute minRole="staff" element={<GoodsPage initialTab="transfers" />} />} />
          <Route path="/goods/counts"    element={<RoleRoute minRole="staff" element={<GoodsPage initialTab="counts" />} />} />
          <Route path="/restock"        element={<RoleRoute minRole="staff"   element={<RestockPage />} />} />
          <Route path="/sales/entry"    element={<RoleRoute minRole="staff"   element={<SaleEntryPage />} />} />
          <Route path="/sales/documents/:id" element={<RoleRoute minRole="staff" element={<SaleDocumentPage />} />} />
          <Route path="/sales/payments/:id/evidence" element={<RoleRoute minRole="staff" element={<PaymentEvidencePage />} />} />
          <Route path="/closing"        element={<RoleRoute minRole="staff" element={<ClosingPage />} />} />
          <Route path="/trades"         element={<RoleRoute minRole="staff" element={<TradesPage />} />} />
          <Route path="/trades/cases/:id" element={<RoleRoute minRole="staff" element={<TradeCasePage />} />} />
          <Route path="/reports"        element={<RoleRoute minRole="manager" element={<ReportsPage />} />} />
          <Route path="/sales"          element={<RoleRoute minRole="manager" element={<SalesPage />} />} />
          <Route path="/sales/day/:date" element={<RoleRoute minRole="manager" element={<DayDetailPage />} />} />
          <Route path="/users"          element={<RoleRoute minRole="admin"   element={<UsersPage />} />} />
          <Route path="/settings"       element={<RoleRoute minRole="admin"   element={<SettingsPage />} />} />
          <Route path="/schedule"       element={<RoleRoute minRole="viewer"  element={<SchedulePage />} />} />
          <Route path="*"               element={<NotFound />} />
        </Routes></Suspense>
      </ErrorBoundary>
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
          /* Nav bar is z-1000; all antd popups must sit above it */
          zIndexPopupBase:  1010,
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
