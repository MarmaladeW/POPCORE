import { Tabs } from 'antd'
import { useSearchParams } from 'react-router-dom'
import { useHasRole } from '../../auth/useRole'
import PageHeader from '../../components/pc/PageHeader'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { useAppStore } from '../../store'
import BestsellerManage from '../Restock/BestsellerManage'
import EveningCheckStep from '../Restock/EveningCheckStep'
import { InventoryCheckHistory } from '../Restock/HistoryTab'
import GoodsPage from './Goods'
import './goods.css'

const tabs = ['tonight', 'other', 'history', 'settings'] as const
type Tab = typeof tabs[number]

// 晚盘 (Figma Inventory · Evening count): tonight's list, other counts with manager review, the
// count history and, for admins, the list settings. The claw machine is not part of 晚盘.
export default function CountHub() {
  const [params, setParams] = useSearchParams()
  const admin = useHasRole('admin')
  const phone = useIsPhone()
  const store = useAppStore(state => state.selectedStore)
  const requested = params.get('tab') as Tab
  const tab: Tab = tabs.includes(requested) && (requested !== 'settings' || admin) ? requested : 'tonight'
  const date = new Date().toLocaleDateString('zh-CN', { timeZone: 'America/Toronto', month: 'long', day: 'numeric' })
  return <div className="pc-count">
    <PageHeader title="晚盘" sub={`Evening count · ${phone ? date : store?.name || ''}`} back={phone ? { label: '库存', to: '/stock' } : undefined} />
    <Tabs className="pc-count-tabs" activeKey={tab} onChange={key => setParams(key === 'tonight' ? {} : { tab: key }, { replace: true })} destroyInactiveTabPane items={[
      { key: 'tonight', label: <>今晚清单 <small>Tonight</small></>, children: <EveningCheckStep /> },
      { key: 'other', label: <>其他盘点 <small>Other</small></>, children: <GoodsPage initialTab="counts" embedded /> },
      { key: 'history', label: <>记录 <small>History</small></>, children: <InventoryCheckHistory /> },
      ...(admin ? [{ key: 'settings', label: <>清单设置 <small>List settings</small></>, children: <BestsellerManage /> }] : []),
    ]} />
  </div>
}
