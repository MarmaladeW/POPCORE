import { useEffect, useState } from 'react'
import { Skeleton } from 'antd'

import { entryPrices, type DexPrices } from '../../api/dex'
import { Panel } from '../../components/pc/Panel'
import { torontoDay, trend } from '../../lib/dexPrices'
import { formatCents } from '../../lib/money'

const W = 320, H = 72

// 价格走势 Price trend on a design's page (owner: "just like on the website, we should store these data, so it
// becomes a price trend over time"). It reads the prices managers set on 确认款价格 Design prices or in the design
// editor (design_prices), newest first, and the website's market prices kept in the design's details. Read only.
export default function DexPriceTrend({ entryId, linked, hasWebsite }: { entryId: number; linked: boolean; hasWebsite: boolean }) {
  const [data, setData] = useState<DexPrices>()
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let live = true
    setData(undefined); setFailed(false)
    entryPrices(entryId).then(next => { if (live) setData(next) }).catch(() => { if (live) setFailed(true) })
    return () => { live = false }
  }, [entryId, linked])

  if (!linked && !hasWebsite) return null
  const rows = data ? [
    ...data.points.map((point, index) => ({ key: `p${index}`, at: point.set_at, cents: point.cents, web: false,
      who: point.set_by_name || point.set_by })),
    ...data.website.map((point, index) => ({ key: `w${index}`, at: point.recorded_at ?? '', cents: Math.round(point.price * 100),
      web: true, who: point.source || '' })),
  ].sort((a, b) => (b.at || '').localeCompare(a.at || '')) : []
  const drawn = data && trend(data.points.map(p => ({ at: p.set_at, cents: p.cents })),
    data.website.map(p => ({ at: p.recorded_at ?? '', cents: Math.round(p.price * 100) })), W, H)

  return <Panel zh="价格走势" en="Price trend" label="Price trend" className="pc-dex-prices">
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
      {data.linked && data.points.length === 0 && <p className="pc-quiet">还没有定过价 No price has been set for this design yet.</p>}
      {rows.length > 0 && <ol className="pc-dex-price-list">
        {rows.map(row => <li key={row.key}>
          <span>{torontoDay(row.at) || '—'}</span>
          <strong>{formatCents(row.cents)}</strong>
          <small>{row.web ? <><i className="pc-dex-web">网站 Website</i>{row.who && ` ${row.who}`}</> : row.who}</small>
        </li>)}
      </ol>}
    </>}
  </Panel>
}
