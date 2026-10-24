import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import client from '../../api/client'
import Icon from '../../components/pc/Icon'

// Open special orders, for the count on the 特殊订单 row (phone) and tab (desktop).
export function useOpenSpecialOrders(enabled = true) {
  const [count, setCount] = useState<number>()
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    client.get<unknown[]>('/special-orders', { params: { status: 'open' }, signal: controller.signal, timeout: 15000 })
      .then(response => setCount(response.data.length)).catch(() => {})
    return () => controller.abort()
  }, [enabled])
  return count
}

const tabs = [
  { to: '/checkout', zh: '订单', en: 'Orders' },
  { to: '/special-orders', zh: '特殊订单', en: 'Special orders' },
  { to: '/checkout/cash-exchange', zh: '换现金', en: 'Cash exchange' },
  { to: '/checkout/history', zh: '订单记录', en: 'Order history' },
]

function activeTab(path: string, history: boolean) {
  if (path.startsWith('/special-orders')) return '/special-orders'
  if (path === '/checkout/cash-exchange') return '/checkout/cash-exchange'
  return history ? '/checkout/history' : '/checkout'
}

// Desktop: Checkout's sections as tabs (Figma Desktop · Checkout · Special orders).
export function CheckoutTabs({ history = false, specialCount }: { history?: boolean; specialCount?: number }) {
  const active = activeTab(useLocation().pathname, history)
  return <nav className="co-tabs" aria-label="Checkout sections">
    {tabs.map(tab => <Link key={tab.to} to={tab.to} aria-current={active === tab.to ? 'page' : undefined}>
      {tab.zh} <span>{tab.en}</span>{tab.to === '/special-orders' && specialCount ? <b> · {specialCount}</b> : null}
    </Link>)}
  </nav>
}

// Phone: 特殊订单 and 换现金 as rows under the checkout buttons (Figma Special orders · 1 · Checkout row).
export function CheckoutShortcuts({ specialCount, disabled }: { specialCount?: number; disabled?: boolean }) {
  const guard = (event: React.MouseEvent) => { if (disabled) event.preventDefault() }
  return <nav className="co-shortcuts" aria-label="Checkout tools">
    <Link to="/special-orders" onClick={guard} aria-disabled={disabled}>
      <Icon name="gift" size={18} /><span><strong>特殊订单 Special orders</strong><small>不在产品列表里的预订</small></span>
      {specialCount ? <span className="co-shortcut-count"><i className="pc-dot pc-dot-warn" aria-hidden="true" />{specialCount} 单</span> : null}
      <Icon name="right" size={16} />
    </Link>
    <Link to="/checkout/cash-exchange" onClick={guard} aria-disabled={disabled}>
      <Icon name="swap" size={18} /><span><strong>换现金 Cash exchange</strong><small>顾客电子付款，换出现金</small></span>
      <Icon name="right" size={16} />
    </Link>
  </nav>
}
