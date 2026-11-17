import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Modal, Skeleton, message } from 'antd'

import client from '../../api/client'
import { entryPrices, type DexPrices } from '../../api/dex'
import { saveDesignPrices } from '../../api/designPrices'
import { Panel } from '../../components/pc/Panel'
import { torontoDay, trend } from '../../lib/dexPrices'
import { formatCents } from '../../lib/money'
import { effectText, money, parseMoney, plainMoney } from '../Products/designPriceMath'
import { errorText } from './DexParts'

const W = 320, H = 72

// 价格走势 Price trend on a design's or product's page (owner: "just like on the website, we should store these data,
// so it becomes a price trend over time"). For a confirmed design it reads the prices managers set on 确认款价格 Design
// prices or in the design editor (design_prices); for any other item, its price changes POPCORE recorded (owner,
// 2026-11-14: "Yes, every item"). Newest first, with the website's market prices kept in the entry's details. A manager
// changes the item's price here with 改价 Change price (below); the trend itself only reads.
export default function DexPriceTrend({ entryId, design, linked, hasWebsite, productId, canEdit }:
  { entryId: number; design: boolean; linked: boolean; hasWebsite: boolean; productId: number | null; canEdit: boolean }) {
  const [data, setData] = useState<DexPrices>()
  const [failed, setFailed] = useState(false)
  const [changing, setChanging] = useState(false)
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let live = true
    setData(undefined); setFailed(false)
    entryPrices(entryId).then(next => { if (live) setData(next) }).catch(() => { if (live) setFailed(true) })
    return () => { live = false }
  }, [entryId, linked, reload])

  if (!linked && !hasWebsite) return null
  const rows = data ? [
    ...data.points.map((point, index) => ({ key: `p${index}`, at: point.set_at, cents: point.cents, web: false,
      who: point.set_by_name || point.set_by })),
    ...data.website.map((point, index) => ({ key: `w${index}`, at: point.recorded_at ?? '', cents: Math.round(point.price * 100),
      web: true, who: point.source || '' })),
  ].sort((a, b) => (b.at || '').localeCompare(a.at || '')) : []
  const drawn = data && trend(data.points.map(p => ({ at: p.set_at, cents: p.cents })),
    data.website.map(p => ({ at: p.recorded_at ?? '', cents: Math.round(p.price * 100) })), W, H)

  const changeable = canEdit && productId !== null && data?.linked === true
  return <Panel zh="价格走势" en="Price trend" label="Price trend" className="pc-dex-prices"
    action={changeable && <Button size="small" onClick={() => setChanging(true)}>改价 Change price</Button>}>
    {!data && !failed && <div className="pc-quiet"><Skeleton active paragraph={{ rows: 2 }} /></div>}
    {failed && <p className="pc-quiet">Unable to load the prices.</p>}
    {data && <>
      {data.linked && <p className="pc-dex-price-now">现价 Price now <strong>{data.current_cents === null ? '—' : formatCents(data.current_cents)}</strong></p>}
      {drawn && <svg className="pc-dex-trend" viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`Price trend, ${drawn.dots.length} prices set, from ${formatCents(drawn.dots[0].cents)} to ${formatCents(drawn.dots[drawn.dots.length - 1].cents)}`}>
        <path d={drawn.line} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        {drawn.dots.map((dot, index) => <circle key={index} cx={dot.x} cy={dot.y} r="3" fill="currentColor" />)}
        {drawn.website.map((dot, index) => <circle key={`w${index}`} className="pc-dex-trend-web" cx={dot.x} cy={dot.y} r="3" />)}
      </svg>}
      {drawn && drawn.website.length > 0 && <p className="pc-dex-trend-key">● 已定价 Set in POPCORE · ○ 网站 Website</p>}
      {data.linked && data.points.length === 0 && <p className="pc-quiet">{design
        ? '还没有定过价 No price has been set for this design yet.' : '还没有价格记录 No price has been recorded for this item yet.'}</p>}
      {rows.length > 0 && <ol className="pc-dex-price-list">
        {rows.map(row => <li key={row.key}>
          <span>{torontoDay(row.at) || '—'}</span>
          <strong>{formatCents(row.cents)}</strong>
          <small>{row.web ? <><i className="pc-dex-web">网站 Website</i>{row.who && ` ${row.who}`}</> : row.who}</small>
        </li>)}
      </ol>}
    </>}
    {changing && data && productId !== null && <ChangePrice productId={productId} data={data}
      onClose={() => setChanging(false)} onSaved={() => { setChanging(false); setReload(value => value + 1) }} />}
  </Panel>
}

// 改价 Change price (owner, 2026-11-17, asked why the price could not be edited in the Dex: "Yes, build it"). The Dex
// keeps no price of its own: this saves the item's one price where it already lives. A confirmed design's is saved as
// on 确认款价格 Design prices: the design's own price, which goes to popcore.ca, refused when the price changed after
// the page loaded. Any other item's is saved as the Products edit saves it, and the website sync judges it as usual.
function ChangePrice({ productId, data, onClose, onSaved }:
  { productId: number; data: DexPrices; onClose: () => void; onSaved: () => void }) {
  const [text, setText] = useState(data.current_cents === null ? '' : plainMoney(data.current_cents))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const key = useRef('')  // one per price typed, so a retry after a lost answer is not saved twice
  const cents = parseMoney(text)
  const unusable = text.trim() !== '' && cents === null
  const ready = cents !== null && cents !== data.current_cents

  async function save() {
    if (!ready || busy) return
    setBusy(true)
    setError('')
    try {
      if (data.confirmed_design) {
        if (!key.current) key.current = crypto.randomUUID()
        const result = await saveDesignPrices([{ product_id: productId, cents, expected_cents: data.current_cents }], key.current)
        const website = result.changes[0]?.website ?? []
        message.success(`已保存 Saved ${money(cents)} · ${website.length ? website.map(effectText).join(' · ') : '不在网站 Not on popcore.ca'}`)
      } else {
        await client.patch(`/products/${productId}`, { price: cents / 100 })
        message.success(`已保存 Saved ${money(cents)}`)
      }
      onSaved()
    } catch (cause) {
      setError(errorText(cause, 'Nothing was saved. Try again.'))
      setBusy(false)
    }
  }

  return <Modal open title="改价 Change price" okText="保存 Save" cancelText="取消 Cancel" onOk={save} onCancel={onClose}
    confirmLoading={busy} okButtonProps={{ disabled: !ready }} maskClosable={!busy}>
    <div className="pc-dex-price-change">
      <p>现价 Price now <strong>{data.current_cents === null ? '—' : formatCents(data.current_cents)}</strong></p>
      <label>新价格 New price (CA$)
        <Input aria-label="New price" inputMode="decimal" autoFocus prefix="$" value={text} disabled={busy}
          status={unusable ? 'error' : undefined} onPressEnter={save}
          onChange={event => { setText(event.target.value); key.current = '' }} /></label>
      {unusable && <small className="pc-dex-price-hint">Type a price over $0, like 45.99.</small>}
      <p className="pc-quiet">{data.confirmed_design
        ? 'The design’s own price, as on 确认款价格 Design prices: what the till charges, and what popcore.ca gets. It waits for an OK only when it would end or restart a sale on the website.'
        : 'The item’s price in POPCORE, as on the Products edit: what the till charges. popcore.ca follows it; a first price, a change over 30% or a sale ending waits for an OK on 网站 Website.'}</p>
      {error && <Alert role="alert" type="error" showIcon message={error} />}
    </div>
  </Modal>
}
