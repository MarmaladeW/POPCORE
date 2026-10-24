import { Link, useLocation, useSearchParams } from 'react-router-dom'

// 报表 Insights tabs (Figma Insights): 概览 Overview · 明细报表 Detailed reports · 核对旧名称 Past names.
// The selected dates carry over between tabs. The Store tasks link is gone (Today left the menus).
export default function InsightsTabs({ dates }: { dates: string }) {
  const { pathname } = useLocation()
  const [params] = useSearchParams()
  const active = pathname === '/sales/matching' ? 'names' : params.has('report') ? 'reports' : 'overview'
  const tabs = [
    { key: 'overview', to: `/reports${dates ? `?${dates}` : ''}`, zh: '概览', en: 'Overview' },
    { key: 'reports', to: `/reports?report=inventory${dates ? `&${dates}` : ''}`, zh: '明细报表', en: 'Detailed reports' },
    { key: 'names', to: `/sales/matching${dates ? `?${dates}` : ''}`, zh: '核对旧名称', en: 'Past names', label: 'Review past names' },
  ]
  return <nav className="pc-page-tabs" aria-label="Insights sections">
    {tabs.map(tab => <Link key={tab.key} to={tab.to} aria-label={tab.label} aria-current={active === tab.key ? 'page' : undefined}>{tab.zh} <span>{tab.en}</span></Link>)}
  </nav>
}
