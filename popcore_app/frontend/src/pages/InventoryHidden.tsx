import { Link } from 'react-router-dom'
import { Result } from 'antd'
import { useAppStore } from '../store'

// 库存 and its tools for an account without 库存查看 at this store (owner, 2026-11-01).
export default function InventoryHidden() {
  const store = useAppStore(state => state.selectedStore)
  const where = store && store.code !== 'ALL' ? ` at ${store.code}` : ''
  return <Result status="403" title="库存查看未开启 · Inventory view is off"
    subTitle={`Stock${where} is shown only to workers an admin picks in Users (库存查看 Inventory view). 晚盘 and 补货 are still open to you.`}
    extra={<><Link className="pc-btn" to="/stock/count">晚盘 Evening count</Link> <Link className="pc-btn" to="/restock">补货 Restock</Link></>} />
}
