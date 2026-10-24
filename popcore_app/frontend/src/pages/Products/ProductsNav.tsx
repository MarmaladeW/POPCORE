import { Link, useLocation } from 'react-router-dom'
import { useHasRole } from '../../auth/useRole'

// Products tabs (Figma Products desktop): 目录 Catalog · 整端·确认款 Sets & designs · Clover 对应 Matching (managers).
export function ProductsTabs() {
  const path = useLocation().pathname
  const manager = useHasRole('manager')
  const tabs = [
    { to: '/products', zh: '目录', en: 'Catalog' },
    { to: '/products/series', zh: '整端·确认款', en: 'Sets & designs' },
    ...(manager ? [{ to: '/products/clover-matching', zh: 'Clover 对应', en: 'Matching' }] : []),
  ]
  return <nav className="pc-page-tabs" aria-label="Products sections">
    {tabs.map(tab => <Link key={tab.to} to={tab.to} aria-current={path === tab.to ? 'page' : undefined}>{tab.zh} <span>{tab.en}</span></Link>)}
  </nav>
}
