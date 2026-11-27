import { Link } from 'react-router-dom'
import { useAppStore } from '../store'
import { NoAccess } from './Unauthorized'

// 库存 and its tools for an account without 库存查看 at this store (owner, 2026-11-01).
export default function InventoryHidden() {
  const store = useAppStore(state => state.selectedStore)
  const at = store && store.code !== 'ALL' ? store.code : ''
  return <NoAccess zh="库存查看未开启" en="Inventory view is off"
    why={`${at ? `${at} 的` : ''}库存只给管理员在 员工 里勾选了 库存查看 的人看。晚盘和移货你照常能用。`}
    whyEn={`Stock${at ? ` at ${at}` : ''} is shown only to people an admin ticked for Inventory view in Users. Evening count and Move stock are still open to you.`}>
    <Link className="pc-btn pc-btn-ink" to="/stock/count">晚盘 Evening count</Link>
    <Link className="pc-btn" to="/move">移货 Move stock</Link>
  </NoAccess>
}
