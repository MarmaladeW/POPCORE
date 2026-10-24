import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import client from '../../api/client'
import Icon, { type IconName } from '../../components/pc/Icon'
import { useAppStore } from '../../store'

// Whether the selected store has a claw machine location (only DT today).
export function useHasClaw() {
  const store = useAppStore(state => state.selectedStore)
  const [result, setResult] = useState<{ code?: string; claw: boolean }>()
  useEffect(() => {
    if (!store || store.code === 'ALL') return
    const controller = new AbortController()
    client.get<Array<{ code: string }>>('/inventory/locations', { params: { store_code: store.code }, signal: controller.signal })
      .then(response => setResult({ code: store.code, claw: response.data.some(location => location.code === 'claw') }))
      .catch(() => { if (!controller.signal.aborted) setResult({ code: store.code, claw: false }) })
    return () => controller.abort()
  }, [store])
  return !!store && result?.code === store.code && result.claw
}

const links: Array<{ to: string; zh: string; en: string; icon: IconName; claw?: boolean }> = [
  { to: '/goods/receiving', zh: '入库', en: 'Receive', icon: 'in' },
  { to: '/goods/transfers', zh: '调拨', en: 'Transfer', icon: 'swap' },
  { to: '/stock/count', zh: '晚盘', en: 'Count', icon: 'count' },
  { to: '/stock/claw', zh: '娃娃机', en: 'Claw machine', icon: 'gift', claw: true },
]

// Inventory's tools (Figma: 入库 · 调拨 · 晚盘 in the header on desktop, a row on phone).
export default function InventoryNav({ hasClaw, phone }: { hasClaw: boolean; phone: boolean }) {
  const path = useLocation().pathname
  return <nav className={phone ? 'pc-inv-nav pc-inv-nav-phone' : 'pc-inv-nav'} aria-label="Inventory sections">
    {links.filter(link => !link.claw || hasClaw).map(link => <Link key={link.to} to={link.to} className="pc-btn" aria-current={path === link.to ? 'page' : undefined}>
      <Icon name={link.icon} size={18} />{link.zh} <small>{link.en}</small>
    </Link>)}
  </nav>
}
