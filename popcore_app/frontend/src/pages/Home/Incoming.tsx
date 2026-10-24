import { Link } from 'react-router-dom'
import { Alert, Button, Skeleton } from 'antd'
import Icon, { type IconName } from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { ListRow, SectionHeading } from '../../components/pc/Panel'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { useStoreDay } from './storeDay'
import './Home.css'

// Receipt and transfer states as labels of one width (Figma 草稿中 / 运输中 / 已入库).
const statusLabels: Record<string, [string, 'warn' | 'ok' | 'out']> = {
  draft: ['草稿中', 'warn'], posted: ['已入库', 'ok'], cancelled: ['已取消', 'out'],
  planned: ['待发出', 'warn'], active: ['运输中', 'warn'], partial: ['部分收', 'warn'], completed: ['已收齐', 'ok'],
}

export function Status({ status }: { status: string }) {
  const [zh, tone] = statusLabels[status] || [status, 'warn']
  return <span className="pc-hm-status"><i className={`pc-dot pc-dot-${tone}`} aria-hidden="true" />{zh}<span className="pc-sr"> {status}</span></span>
}

const choices: Array<{ to: string; icon: IconName; zh: string; en: string; sub: string }> = [
  { to: '/goods/receiving', icon: 'in', zh: '收货', en: 'Receive shipment', sub: '供应商到货，核对数量和状况' },
  { to: '/goods/transfers', icon: 'swap', zh: '调入', en: 'Receive transfer', sub: '从楼上或其他门店调来' },
  { to: '/incoming/display', icon: 'box', zh: '入 display', en: 'Display arrival', sub: '已拆开的展示品进店' },
]

export default function IncomingPage() {
  const { store, data, error, retry } = useStoreDay()
  const phone = useIsPhone()
  return <div className="pc-incoming">
    <PageHeader title="入店" sub={phone ? undefined : `Receive goods${store?.name ? ` · ${store.name}` : ''}`} back={phone ? { label: '首页', to: '/' } : undefined} />
    {!store || store.code === 'ALL' ? <Alert type="info" showIcon message="Choose one store before receiving goods." /> : <>
      <p className="pc-incoming-ask">货从哪里来 · Where did the goods come from?</p>
      <nav className="pc-incoming-choices" aria-label="Receiving options">
        {choices.map(choice => <Link key={choice.to} to={choice.to}>
          <span className="pc-incoming-icon"><Icon name={choice.icon} size={18} /></span>
          <span className="pc-nav-text"><strong>{choice.zh} <small>{choice.en}</small></strong><span>{choice.sub}</span></span>
          <Icon name="right" size={16} className="pc-incoming-chevron" />
        </Link>)}
      </nav>
      <section className="pc-incoming-records" aria-label="Incoming records">
        <SectionHeading zh="今日入店" en="Today's incoming" action={<Link to="/today">待处理<span className="pc-sr"> Pending store tasks</span></Link>} />
        <div className="pc-panel">
          {error ? <Alert role="alert" type="error" message={error} action={<Button onClick={retry}>Retry</Button>} />
            : !data ? <div className="pc-quiet"><Skeleton active title={false} paragraph={{ rows: 2 }} /></div>
            : !data.receipts.length && !data.transfers.length ? <p className="pc-quiet">No incoming records available for today.</p>
            : <>
              {data.receipts.map(receipt => <Link key={`receipt-${receipt.id}`} className="pc-incoming-row" to={`/goods/receiving?receipt_id=${receipt.id}`}>
                <ListRow title={`收货 · ${receipt.shipment_reference || `Receipt #${receipt.id}`}`} trailing={<Status status={receipt.status} />} />
              </Link>)}
              {data.transfers.map(transfer => <Link key={`transfer-${transfer.id}`} className="pc-incoming-row" to={`/goods/transfers?transfer_id=${transfer.id}`}>
                <ListRow title={`调入 · Transfer #${transfer.id}`} trailing={<Status status={transfer.status} />} />
              </Link>)}
            </>}
        </div>
      </section>
    </>}
  </div>
}
