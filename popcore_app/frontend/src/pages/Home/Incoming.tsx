import { Link } from 'react-router-dom'
import { Alert, Button, Skeleton } from 'antd'
import { useStoreDay } from './storeDay'
import './Home.css'

export default function IncomingPage() {
  const { store, data, error, retry } = useStoreDay()
  return <div className="pc-home pc-home-task">
    <Link className="pc-home-back" to="/">← Back to home</Link>
    <header className="pc-home-heading"><h1>入店 / Receive goods</h1><p>{store?.name} · Choose where the goods came from.</p></header>
    {!store || store.code === 'ALL' ? <Alert type="info" showIcon message="Choose one store before receiving goods." /> : <nav className="pc-home-choices" aria-label="Receiving options">
      <Link to="/goods/receiving"><strong>收货 / Receive shipment</strong><span>Goods arriving from a supplier. Check quantities and condition.</span></Link>
      <Link to="/goods/transfers"><strong>调入 / Transfer goods</strong><span>Goods from upstairs or another location. Receive the existing transfer.</span></Link>
      <Link to="/incoming/display"><strong>入 display / Display arrival</strong><span>Record an opened display entering the store.</span></Link>
    </nav>}
    {store && store.code !== 'ALL' && <section className="pc-home-records" aria-label="Incoming records">
      <div className="pc-home-section-heading"><h2>Today's incoming goods</h2><Link to="/today">Pending store tasks</Link></div>
      {error ? <Alert role="alert" type="error" message={error} action={<Button onClick={retry}>Retry</Button>} />
        : !data ? <Skeleton active paragraph={{ rows: 2 }} />
        : <ul>
          {data.receipts.map(receipt => <li key={`receipt-${receipt.id}`}><Link to={`/goods/receiving?receipt_id=${receipt.id}`}>{receipt.shipment_reference || `Receipt #${receipt.id}`}</Link><span>{receipt.status}</span></li>)}
          {data.transfers.map(transfer => <li key={`transfer-${transfer.id}`}><Link to={`/goods/transfers?transfer_id=${transfer.id}`}>Transfer #{transfer.id}</Link><span>{transfer.status}</span></li>)}
          {!data.receipts.length && !data.transfers.length && <li className="pc-home-muted">No incoming records available for today.</li>}
        </ul>}
    </section>}
  </div>
}
