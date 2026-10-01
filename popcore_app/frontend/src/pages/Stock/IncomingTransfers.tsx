import { Alert, Button, Card, List, Space, Spin, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { useAuth0 } from '@auth0/auth0-react'
import { Link, useSearchParams } from 'react-router-dom'
import { getIncomingTransfers, type IncomingTransfer } from '../../api/goods'
import { useRole } from '../../auth/useRole'
import { useAppStore } from '../../store'

const quantity = (amount:number, unit:string) => `${amount} ${unit}${amount === 1 ? '' : 's'}`

export default function IncomingTransfers() {
  const store = useAppStore(state => state.selectedStore)
  const { user } = useAuth0(), role = useRole(), [, setParams] = useSearchParams()
  const [refresh, setRefresh] = useState(0)
  const [result, setResult] = useState<{scope:string; items:IncomingTransfer[]; error:string}>()
  const scope = `${store?.id}|${user?.sub}|${role}|${refresh}`
  const current = result?.scope === scope ? result : undefined
  useEffect(() => {
    if (!store || store.code === 'ALL') return
    const controller = new AbortController()
    getIncomingTransfers(store.id, controller.signal)
      .then(items => { if (!controller.signal.aborted) setResult({scope,items,error:''}) })
      .catch(cause => { if (!controller.signal.aborted) setResult({scope,items:[],error:cause?._serverMessage || 'Unable to load incoming transfers. Please retry.'}) })
    return () => controller.abort()
  }, [scope, store?.id])
  return <Card title="Incoming transfers" extra={['manager','admin'].includes(role) && <Button onClick={() => setParams({create:'1'})}>Create transfer</Button>}>
    <Space direction="vertical" style={{width:'100%'}}>
      <Typography.Text type="secondary">Unfinished transfers to {store?.name}, including earlier days. Open one to confirm what arrived.</Typography.Text>
      <Button onClick={() => setRefresh(value => value + 1)}>Refresh transfers</Button>
      {!current ? <Spin /> : current.error ? <Alert role="alert" type="error" showIcon message={current.error} action={<Button onClick={() => setRefresh(value => value + 1)}>Retry</Button>} />
        : <List rowKey="id" dataSource={current.items} locale={{emptyText:'No incoming transfers to receive.'}} renderItem={transfer => <List.Item>
          <Space direction="vertical" style={{width:'100%'}}>
            <Typography.Text strong>{transfer.source_store_name} · {transfer.source_location_name}</Typography.Text>
            <Typography.Text type="secondary">To {transfer.destination_location_name} · {transfer.business_date}</Typography.Text>
            {transfer.lines.map(line => <div key={line.line_no}>
              <div>{line.product_name || line.sku}</div>
              <Space wrap>
                <Typography.Text>{quantity(line.outstanding_transit,line.native_unit)} in transit</Typography.Text>
                {line.awaiting_dispatch > 0 && <Typography.Text type="secondary">{quantity(line.awaiting_dispatch,line.native_unit)} awaiting dispatch</Typography.Text>}
              </Space>
            </div>)}
            <Link to={`/goods/transfers?transfer_id=${transfer.id}`}>Open transfer #{transfer.id}</Link>
          </Space>
        </List.Item>} />}
    </Space>
  </Card>
}
