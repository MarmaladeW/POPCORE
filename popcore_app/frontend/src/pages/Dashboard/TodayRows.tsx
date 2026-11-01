import { Link } from 'react-router-dom'
import type { TodayPayload, TodayRow } from '../../api/today'
import { ListRow } from '../../components/pc/Panel'

export const sectionLabels: Record<string, { zh: string; en: string }> = {
  tasks: { zh: '待处理', en: 'Store tasks' },
  my_work: { zh: '我的工作', en: 'My work' }, operations: { zh: '门店工作', en: 'Store work' },
  financial: { zh: '销售与收款核对', en: 'Sales and tender checks' },
  catalog: { zh: '产品资料', en: 'Catalog' }, inventory: { zh: '库存提醒', en: 'Inventory notices' },
  report_stock: { zh: '日报库存核对', en: 'Report stock checks' },
}
const typeLabels: Record<string, string> = {
  sale: 'Sale', checkout: 'Checkout', financial_sale: 'Sale', allocation_exception: 'Sale',
  payment_evidence: 'Payment evidence', payment_exception: 'Payment', receipt: 'Receipt',
  count: 'Count', closing: 'Closing', cash_variance: 'Closing', delivery: 'Transfer',
  restock: 'Restock', condition_case: 'Condition case', catalog_identity: 'Product',
  inventory_notice: 'Product', report_stock: 'Report stock',
}
const typeZh: Record<string, string> = {
  sale: '销售', checkout: '订单', financial_sale: '销售', allocation_exception: '销售',
  payment_evidence: '收款凭证', payment_exception: '收款', receipt: '收货',
  count: '盘点', closing: '收工', cash_variance: '收工', delivery: '调拨',
  restock: '补货', condition_case: '品相', catalog_identity: '产品', inventory_notice: '产品', report_stock: '日报',
}
const statusLabels: Record<string, string> = {
  open: 'In progress', draft: 'Draft', posted: 'Recorded', pending: 'Pending', submitted: 'Awaiting review',
  returned: 'Returned for changes', active: 'In transit', planned: 'Planned',
  evidence_pending: 'Evidence awaiting review', unresolved: 'Identity needs review',
  out_of_stock: 'Out of stock', variance: 'Variance needs review',
  allocated: 'Stock allocated', verified: 'Verified', recorded: 'Awaiting verification',
  not_counted: 'Never counted here', not_enough_on_floor: 'Not enough on the floor',
  set_size_unknown: 'Set size not recorded', counted_same_day: 'Floor counted the same day',
}
const statusZh: Record<string, string> = {
  open: '未完成', draft: '草稿', posted: '已记录', pending: '待处理', submitted: '待审核',
  returned: '已退回', active: '运输中', planned: '已计划', evidence_pending: '凭证待审核',
  unresolved: '待审核', out_of_stock: '缺货', variance: '差异待审核',
  allocated: '已分配库存', verified: '已核实', recorded: '待核对',
  not_counted: '还没盘点', not_enough_on_floor: '楼下不够', set_size_unknown: '不知道每端几盒', counted_same_day: '当天盘点过',
}

function rowTitle(row: TodayRow) {
  if (row.label) return row.label
  return `${typeLabels[row.type] || row.type.replaceAll('_', ' ')} #${row.source_id}`
}

// Full English action keeps its meaning for screen readers; the button shows the short Figma label.
const verbs: Record<string, [string, string]> = {
  Resume: ['继续', ''], Continue: ['继续', ''], Review: ['核对', 'Review'], Resolve: ['处理', 'Resolve'],
  'Add evidence to': ['补凭证', 'Add evidence'], View: ['查看', 'View'], Open: ['打开', 'Open'],
}
function action(row: TodayRow) {
  const label = typeLabels[row.type] || 'item'
  const byType: Record<string, string> = {
    sale: 'Resume', checkout: 'Continue', financial_sale: 'Review', allocation_exception: 'Resolve',
    payment_evidence: 'Add evidence to', payment_exception: 'Review', receipt: 'Resume',
    count: row.status === 'submitted' ? 'Review' : 'Resume', closing: 'Continue',
    cash_variance: 'Review', delivery: 'Continue', restock: 'Continue',
    condition_case: 'Review', catalog_identity: 'Review', inventory_notice: 'View', report_stock: 'Review',
  }
  const verb = byType[row.type] || 'Open'
  const [zh, en] = verbs[verb]
  return { aria: `${verb} ${label.toLowerCase()} #${row.source_id}`, text: en ? `${zh} ${en}` : zh }
}

export function TodayRows({ rows, stores }: { rows: TodayRow[]; stores: TodayPayload['authorized_stores'] }) {
  const storeNames = new Map(stores.map(store => [store.id, store.name || store.code]))
  return <>{rows.map(row => {
    const next = action(row)
    const status = statusLabels[row.status] || row.status.replaceAll('_', ' ')
    return <ListRow key={`${row.type}:${row.source_id}`} label={typeZh[row.type] || typeLabels[row.type] || row.type}
      title={<><span>{rowTitle(row)}</span> · {statusZh[row.status] ? `${statusZh[row.status]} ${status}` : status}</>}
      meta={[row.store_id ? (storeNames.get(row.store_id) || `Store reference ${row.store_id}`) : null, row.updated_at].filter(Boolean).join(' · ')}
      trailing={<Link to={row.link} aria-label={next.aria} className="pc-btn">{next.text}</Link>} />
  })}</>
}

