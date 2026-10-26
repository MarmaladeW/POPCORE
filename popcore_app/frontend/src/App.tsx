import { Suspense, useEffect, useState } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { Button, ConfigProvider, Modal, Result } from 'antd'

import ProtectedRoute from './auth/ProtectedRoute'
import { useHasRole, type Role } from './auth/useRole'
import AppLayout from './components/AppLayout'
import ErrorBoundary from './components/ErrorBoundary'
import { lazyPage } from './lib/lazyPage'
import MoreMenu from './components/MoreMenu'
import { resetAuthWarnings, setTokenGetter } from './api/client'
import { useAppStore, ALL_STORES } from './store'
import type { Store } from './store'
import client from './api/client'
import { operationsTheme } from './theme/operations'
import './styles/operations.css'

const SchedulePage=lazyPage(()=>import('./pages/Schedule'))
const HomePage=lazyPage(()=>import('./pages/Home'))
const DashboardPage=lazyPage(()=>import('./pages/Dashboard'))
const CheckoutPage=lazyPage(()=>import('./pages/Sales/Checkout'))
const SpecialOrdersPage=lazyPage(()=>import('./pages/SpecialOrders'))
const IncomingPage=lazyPage(()=>import('./pages/Home/Incoming'))
const EventEntry=lazyPage(()=>import('./pages/Home/EventEntry'))
const StoreSummary=lazyPage(()=>import('./pages/Home/StoreSummary'))
const ProductsPage=lazyPage(()=>import('./pages/Products'))
const CloverMatchingPage=lazyPage(()=>import('./pages/Products/CloverMatching'))
const SeriesCatalogPage=lazyPage(()=>import('./pages/Products/SeriesCatalog'))
const StockPage=lazyPage(()=>import('./pages/Stock'))
const SeriesInventory=lazyPage(()=>import('./pages/Stock/SeriesInventory'))
const GoodsPage=lazyPage(()=>import('./pages/Stock/Goods'))
const RestockPage=lazyPage(()=>import('./pages/Restock'))
const CountHub=lazyPage(()=>import('./pages/Stock/CountHub'))
const ClawPage=lazyPage(()=>import('./pages/Stock/ClawPage'))
const HistoricalMatchReview=lazyPage(()=>import('./pages/Sales/HistoricalMatchReview'))
const SalesPage=lazyPage(()=>import('./pages/Sales'))
const DayDetailPage=lazyPage(()=>import('./pages/Sales/DayDetail'))
const SaleEntryPage=lazyPage(()=>import('./pages/Sales/Entry'))
const SaleDocumentPage=lazyPage(()=>import('./pages/Sales/SaleDocument'))
const PaymentEvidencePage=lazyPage(()=>import('./pages/Sales/PaymentEvidence'))
const ClosingPage=lazyPage(()=>import('./pages/Closing'))
const UsersPage=lazyPage(()=>import('./pages/Users'))
const SettingsPage=lazyPage(()=>import('./pages/Settings'))
const NotFound=lazyPage(()=>import('./pages/NotFound'))
const Unauthorized=lazyPage(()=>import('./pages/Unauthorized'))
const TradesPage=lazyPage(()=>import('./pages/Trades'))
const TradeCasePage=lazyPage(()=>import('./pages/Trades/TradeCase'))
const ReportsPage=lazyPage(()=>import('./pages/Reports'))

function RoleRoute({ minRole, element }: { minRole: Role; element: React.ReactNode }) {
  return useHasRole(minRole) ? <>{element}</> : <Unauthorized />
}

function AppInner() {
  const { getAccessTokenSilently, isAuthenticated, loginWithRedirect, user } = useAuth0()
  const { setSeries, setProductTypes, setStores, setSelectedStore, selectedStore, clearAvailabilityDrafts } = useAppStore()
  const [bootstrapError, setBootstrapError] = useState(false)
  const [bootstrapAttempt, setBootstrapAttempt] = useState(0)
  const location = useLocation()

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (Object.values(useAppStore.getState().availabilityDrafts).some(draft => draft.dirty)) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [])

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
    clearAvailabilityDrafts()
    setSeries([])
    setProductTypes([])
    setStores([])
    setBootstrapError(false)
  }, [user?.sub, setProductTypes, setSeries, setStores, clearAvailabilityDrafts])

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
    const errorApp = (
      <AppLayout key={user?.sub}>
        <Result
          status="error"
          title="Unable to load store setup"
          subTitle="Check your connection, then retry."
          extra={<Button type="primary" onClick={() => setBootstrapAttempt(n => n + 1)}>Retry</Button>}
        />
      </AppLayout>
    )
    return <ConfigProvider theme={operationsTheme} button={{ autoInsertSpace: false }}>{errorApp}</ConfigProvider>
  }

  const routedApp = (
    <AppLayout key={user?.sub}>
      <ErrorBoundary resetKey={location.pathname}>
        <Suspense fallback={<div role="status" aria-live="polite">Loading page…</div>}><Routes>
          <Route path="/"               element={<RoleRoute minRole="viewer"  element={<HomePage />} />} />
          <Route path="/today" element={<RoleRoute minRole="viewer" element={<DashboardPage />} />} />
          <Route path="/checkout" element={<RoleRoute minRole="staff" element={<CheckoutPage />} />} />
          <Route path="/checkout/cash-exchange" element={<RoleRoute minRole="staff" element={<EventEntry exchange />} />} />
          <Route path="/checkout/:id" element={<RoleRoute minRole="staff" element={<CheckoutPage />} />} />
          <Route path="/special-orders" element={<RoleRoute minRole="staff" element={<SpecialOrdersPage />} />} />
          <Route path="/incoming" element={<RoleRoute minRole="staff" element={<IncomingPage />} />} />
          <Route path="/incoming/display" element={<RoleRoute minRole="staff" element={<EventEntry display />} />} />
          <Route path="/claw" element={<RoleRoute minRole="staff" element={<ClawPage />} />} />
          <Route path="/stock/claw" element={<RoleRoute minRole="staff" element={<ClawPage />} />} />
          <Route path="/stock/count" element={<RoleRoute minRole="staff" element={<CountHub />} />} />
          <Route path="/summary" element={<RoleRoute minRole="staff" element={<StoreSummary />} />} />
          <Route path="/products"       element={<ProductsPage />} />
          <Route path="/products/series" element={<SeriesCatalogPage />} />
          <Route path="/products/clover-matching" element={<RoleRoute minRole="manager" element={<CloverMatchingPage />} />} />
          <Route path="/stock"          element={<RoleRoute minRole="staff"   element={<SeriesInventory />} />} />
          <Route path="/stock/products" element={<RoleRoute minRole="staff" element={<StockPage />} />} />
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
          <Route path="/sales/matching" element={<RoleRoute minRole="manager" element={<HistoricalMatchReview />} />} />
          <Route path="/sales/day/:date" element={<RoleRoute minRole="manager" element={<DayDetailPage />} />} />
          <Route path="/users"          element={<RoleRoute minRole="admin"   element={<UsersPage />} />} />
          <Route path="/settings"       element={<RoleRoute minRole="admin"   element={<SettingsPage />} />} />
          <Route path="/schedule"       element={<RoleRoute minRole="viewer"  element={<SchedulePage />} />} />
          <Route path="/more"           element={<MoreMenu />} />
          <Route path="*"               element={<NotFound />} />
        </Routes></Suspense>
      </ErrorBoundary>
    </AppLayout>
  )
  return <ConfigProvider theme={operationsTheme} button={{ autoInsertSpace: false }}>{routedApp}</ConfigProvider>
}

export default function App() {
  return (
    <ConfigProvider theme={operationsTheme} button={{ autoInsertSpace: false }}>
      <ProtectedRoute>
        <AppInner />
      </ProtectedRoute>
    </ConfigProvider>
  )
}
