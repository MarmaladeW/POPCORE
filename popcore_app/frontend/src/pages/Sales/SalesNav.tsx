import { Link, useLocation } from 'react-router-dom'
import Icon from '../../components/pc/Icon'
import { torontoDate } from '../Dashboard/todayPresentation'

const tabs = [
  { to: '/summary', zh: '每日汇总', en: 'Daily summary' },
  { to: '/closing', zh: '收工', en: 'Closing' },
  { to: '/sales', zh: '历史日报', en: 'Historical reports' },
]

// Manager 销售 Sales: 每日汇总 · 收工 · 历史日报 (Figma Summary desktop). Staff reach 汇总 and 收工 from Home.
export function SalesTabs() {
  const path = useLocation().pathname
  const active = path.startsWith('/sales') ? '/sales' : path
  return <nav className="pc-page-tabs" aria-label="Sales sections">
    {tabs.map(tab => <Link key={tab.to} to={tab.to} aria-current={active === tab.to ? 'page' : undefined}>{tab.zh} <span>{tab.en}</span></Link>)}
  </nav>
}

const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
function shift(day: string, days: number) {
  const date = new Date(`${day}T12:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

export function dayTitle(day: string) {
  const date = new Date(`${day}T12:00:00Z`)
  return Number.isNaN(date.getTime()) ? day : `${date.getUTCMonth() + 1}月${date.getUTCDate()}日 ${weekdays[date.getUTCDay()]}`
}

// ‹ 10月22日 周三 › — tapping the date opens the date picker (the input sits over the label).
export function DayStepper({ value, onChange, label = 'Business date', disabled }: { value: string; onChange: (day: string) => void; label?: string; disabled?: boolean }) {
  const today = torontoDate()
  return <div className="pc-day">
    <button type="button" aria-label="Previous day" disabled={disabled} onClick={() => onChange(shift(value, -1))}><Icon name="back" size={16} /></button>
    <label className="pc-day-date">
      <strong>{dayTitle(value)}</strong>
      <small>{value}{value === today ? ' · 今天' : ''}</small>
      <input type="date" aria-label={label} value={value} max={today} disabled={disabled}
        onClick={event => { try { (event.currentTarget as HTMLInputElement & { showPicker?: () => void }).showPicker?.() } catch { /* the native field still opens */ } }}
        onChange={event => { if (event.target.value) onChange(event.target.value) }} />
    </label>
    <button type="button" aria-label="Next day" disabled={disabled || value >= today} onClick={() => onChange(shift(value, 1))}><Icon name="right" size={16} /></button>
  </div>
}
