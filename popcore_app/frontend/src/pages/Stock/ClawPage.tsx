import { useEffect, useState } from 'react'
import { Alert } from 'antd'
import client from '../../api/client'
import PageHeader from '../../components/pc/PageHeader'
import { SectionHeading } from '../../components/pc/Panel'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { useAppStore } from '../../store'
import EventEntry from '../Home/EventEntry'
import { useStoreDay } from '../Home/storeDay'
import { useHasClaw } from './InventoryNav'
import './goods.css'

type Balance = { product_id: number; jizhanming: string | null; name_cn_en: string | null; sku: string; unit: string; location_code: string; disposition: string; quantity: number | null }

// The claw machine as a stock location (Figma Inventory · Claw machine): what is in the machine,
// today's prizes, and the 补货 / 出奖 records. It is not part of 晚盘.
export default function ClawPage() {
  const store = useAppStore(state => state.selectedStore)
  const phone = useIsPhone()
  const hasClaw = useHasClaw()
  const { data: day } = useStoreDay()
  const [machine, setMachine] = useState<{ code: string; mode: string; items: Balance[] }>()
  useEffect(() => {
    if (!store || store.code === 'ALL' || !hasClaw) return
    const controller = new AbortController()
    client.get<{ mode: string; items: Balance[] }>('/inventory/balances', { params: { store_code: store.code }, signal: controller.signal })
      .then(response => setMachine({ code: store.code, mode: response.data.mode, items: response.data.items.filter(item => item.location_code === 'claw' && item.disposition === 'saleable') }))
      .catch(() => {})
    return () => controller.abort()
  }, [store, hasClaw])
  const current = machine && machine.code === store?.code ? machine : undefined
  const known = current?.mode === 'authoritative' && current.items.every(item => item.quantity !== null)
  const inMachine = known ? current!.items.reduce((sum, item) => sum + (item.quantity || 0), 0) : null
  const prizes = (day?.events || []).filter(event => event.kind === 'claw_prize')
  const prizeCount = prizes.reduce((sum, event) => sum + (event.quantity || 0), 0)
  const prizesFor = (name: string) => prizes.filter(event => event.product_name === name).reduce((sum, event) => sum + (event.quantity || 0), 0)
  const rows = (current?.items || []).filter(item => (item.quantity || 0) > 0)

  return <div className="pc-claw">
    <PageHeader title="娃娃机" sub={phone ? undefined : `Claw machine${store?.name ? ` · ${store.name}` : ''}`} back={phone ? { label: '库存', to: '/stock' } : undefined} />
    <div className="pc-claw-grid">
      <div className="pc-claw-main">
        {hasClaw && <div className="pc-claw-stats">
          <div><span>机内 In machine</span><strong>{inMachine ?? '—'}</strong></div>
          <div><span>今日出奖 Prizes today</span><strong>{day ? prizeCount : '—'}</strong></div>
        </div>}
        <EventEntry inline />
      </div>
      {hasClaw && <section className="pc-claw-contents" aria-label="In the machine">
        <SectionHeading zh="机内商品" en="In the machine" />
        <div className="pc-panel">
          {!current ? <p className="pc-quiet">—</p>
            : !known ? <p className="pc-quiet"><strong>还没开账 · Not started yet</strong>Machine quantities appear after the opening count is loaded.</p>
            : !rows.length ? <p className="pc-quiet">机器里没有商品 · The machine is empty.</p>
            : <table className="pc-claw-table"><thead><tr><th>产品 Product</th><th>数量</th><th>今日出奖</th></tr></thead><tbody>
              {rows.map(item => { const name = item.jizhanming || item.name_cn_en || item.sku; return <tr key={item.product_id}><td>{name}<small>{item.unit}</small></td><td>{item.quantity}</td><td>{prizesFor(name)}</td></tr> })}
            </tbody></table>}
        </div>
        <p className="pc-claw-note">补货从楼面或楼上拿货放进机器；出奖从机器扣掉。娃娃机不参加晚盘。</p>
      </section>}
      {!hasClaw && store && store.code !== 'ALL' && <Alert className="pc-claw-none" type="info" showIcon message="This store has no claw machine location. Records still go to the daily summary." />}
    </div>
  </div>
}
