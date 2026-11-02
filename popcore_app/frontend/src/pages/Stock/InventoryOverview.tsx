import { Button, Skeleton } from 'antd'
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { getInventoryOverview, type InventoryOverview as Overview, type OverviewChange, type OverviewFigure, type OverviewItem } from '../../api/inventoryOverview'
import { Panel } from '../../components/pc/Panel'
import { useIsPhone } from '../../components/pc/useIsPhone'

// What the floor needs, what is selling and what changed, above the product list (owner, 2026-11-01).
// Stock is the live ledger; sales are the pasted 日报, so the selling lists fill in as reports come in.

const MEASURE:Record<string,string> = {box:'盒', set:'端', piece:'个'}
const PLACES:Record<string,[string,string]> = {floor:['楼面','Floor'], display:['陈列','Display'], claw:['娃娃机','Claw machine']}
const CHANGES:Record<string,[string,string]> = {
  report:['日报','Daily report'], checkout:['买单','Checkout'], identify:['识别盲盒','Identified boxes'], count:['盘点','Count corrections'],
  receipt:['入库','Received'], delivery:['调拨','Transfers and refills'], restock:['补货','Restock'], opening:['开业盘点','Opening count'],
  move:['移动','Moved'], consume:['出库','Taken out'], open_set:['开端','Opened sets'], correction:['更正','Corrections'],
}
const MOVES = ['move','delivery','identify','open_set']
const number = (value:number) => value.toLocaleString('en-US')
const measure = (amount:number, unit:string|null) => `${number(amount)} ${MEASURE[unit??'']??''}`.trim()
const shortDate = (iso:string) => `${Number(iso.slice(5,7))}-${Number(iso.slice(8,10))}`
const plural = (amount:number, word:string) => `${amount} ${word}${amount===1?'':'s'}`
const backText = (item:OverviewItem) => [item.back&&measure(item.back,item.unit), item.back_sets&&measure(item.back_sets,'set')].filter(Boolean).join(' · ')
const pace = (value:number) => value>=10 ? String(Math.round(value)) : value.toFixed(1).replace(/\.0$/,'')
const lasts = (days:number) => days<1 ? 'less than a day' : `about ${plural(Math.round(days),'day')}`
const backName = (figure:OverviewFigure):[string,string] => figure.codes.length>1 ? ['楼上 · 仓库','Back stock'] : figure.codes[0]==='warehouse' ? ['仓库','Warehouse'] : ['楼上','Upstairs']

export default function InventoryOverview({storeCode,refresh}:{storeCode:string;refresh:number}) {
  const [result,setResult] = useState<{key:string;data?:Overview;error?:boolean}>()
  const [retry,setRetry] = useState(0)
  const key = `${storeCode}|${refresh}|${retry}`
  useEffect(()=>{
    const controller = new AbortController()
    getInventoryOverview(storeCode,controller.signal)
      .then(data=>{if(!controller.signal.aborted)setResult({key,data})})
      .catch(()=>{if(!controller.signal.aborted)setResult({key,error:true})})
    return ()=>controller.abort()
  },[key,storeCode])
  const current = result?.key===key ? result : undefined
  // Keep the last overview on screen while a refresh loads.
  const data = current?.data ?? (current ? undefined : result?.data)
  if (!current && !data) return <div className="pc-inv-overview" aria-busy="true"><Skeleton active paragraph={{rows:4}}/></div>
  if (current?.error) return <p className="pc-panel pc-quiet pc-inv-overview">The overview didn't load. The product list below is unaffected. <Button size="small" onClick={()=>setRetry(value=>value+1)}>Retry overview</Button></p>
  if (!data?.live) return null
  return <OverviewBody data={data}/>
}

function OverviewBody({data}:{data:Overview}) {
  const phone = useIsPhone()
  const limit = phone ? 3 : 5
  const multi = (data.stores?.length ?? 0) > 1
  const where = (item:{store_code:string}) => multi ? `${item.store_code} · ` : ''
  const figures = data.figures ?? []
  const noSales = !data.report_days
  const salesEmpty = noSales ? 'No 日报 in the last 7 days. Selling lists fill in once one is pasted in 销售 Sales.' : undefined
  return <section className="pc-inv-overview" aria-label="库存概况 Inventory overview">
    <div className="pc-figures pc-inv-figures" style={{'--figures':figures.length} as CSSProperties}>
      {figures.map(figure=>{
        const [zh,en] = figure.key==='back' ? backName(figure) : PLACES[figure.key] ?? [figure.codes.join(' · '),'']
        const split = Object.keys(MEASURE).map(unit=>[unit,figure.by_unit[unit as keyof typeof figure.by_unit] ?? 0] as const).filter(([,amount])=>amount)
        return <div key={figure.key} className={figure.key==='back'?'pc-inv-back':undefined}>
          <span>{zh} <small>{en}</small></span>
          <strong>{number(figure.units)}</strong>
          <small>{split.length ? split.map(([unit,amount])=>measure(amount,unit)).join(' · ') : 'Empty'}</small>
        </div>
      })}
    </div>
    <p className="pc-inv-meta">{number(data.in_stock_products ?? 0)} products in stock · {data.last_report ? <>sales from 日报 through {shortDate(data.last_report)}</> : 'no 日报 pasted yet'}</p>
    <div className="pc-inv-panels">
      <Panel zh="补货上架" en="Refill the floor" label="补货上架 Refill the floor" count={data.refill_count ?? 0} tone={data.refill_count ? 'warn' : 'ok'} className="pc-inv-panel">
        <Items items={data.refill ?? []} total={data.refill_count} limit={limit} empty="Everything kept upstairs is also on the floor."
          render={item=><Row key={`${item.store_code}|${item.product_id}`} item={item}
            meta={<>{where(item)}{item.sold_recent ? `Sold ${item.sold_recent} in 2 weeks` : item.sku}</>}
            trailing={<span className="pc-inv-num pc-inv-kraft"><small>楼上</small>{backText(item)}</span>}/>}/>
      </Panel>
      <Panel zh="快卖完" en="Running low" label="快卖完 Running low" count={data.running_low?.length ?? 0} tone={data.running_low?.length ? 'warn' : 'ok'} className="pc-inv-panel">
        <Items items={data.running_low ?? []} limit={limit} empty={salesEmpty ?? 'No floor stock runs out within 3 days at the current pace.'}
          render={item=><Row key={`${item.store_code}|${item.product_id}`} item={item}
            meta={<>{where(item)}Sells {pace(item.pace)} a day · {backText(item) ? <span className="pc-inv-kraft-text">楼上 {backText(item)}</span> : 'none upstairs'}</>}
            trailing={<span className="pc-inv-num">{number(item.floor)}<small>{item.days_left!=null ? lasts(item.days_left) : 'left'}</small></span>}/>}/>
      </Panel>
      <Panel zh="刚卖完" en="Sold out" label="刚卖完 Sold out" count={data.sold_out?.length ?? 0} tone={data.sold_out?.length ? 'out' : 'ok'} className="pc-inv-panel">
        <Items items={data.sold_out ?? []} limit={limit} empty={salesEmpty ?? 'Nothing sold in the last 2 weeks has run out.'}
          render={item=><Row key={`${item.store_code}|${item.product_id}`} item={item}
            meta={<>{where(item)}{item.last_sold ? `Last sold ${shortDate(item.last_sold)}` : item.sku}</>}
            trailing={<span className="pc-inv-num">{number(item.sold_recent)}<small>sold</small></span>}/>}/>
      </Panel>
    </div>
    <div className="pc-inv-panels pc-inv-panels-2">
      <Panel zh="本周热卖" en="Best sellers · last 7 days" label="本周热卖 Best sellers" className="pc-inv-panel">
        <Items items={data.best_sellers ?? []} limit={phone ? 5 : 8} empty={salesEmpty ?? 'Nothing sold in the last 7 days.'}
          render={(item,index)=><Row key={`${item.store_code}|${item.product_id}`} item={item} label={index+1}
            meta={<>{where(item)}{item.counted ? <>楼面 {number(item.floor)}{backText(item)&&<span className="pc-inv-kraft-text"> · 楼上 {backText(item)}</span>}</> : 'Not counted yet'}</>}
            trailing={<span className="pc-inv-num">{number(item.sold_week)}<small>sold</small></span>}/>}/>
      </Panel>
      <Panel zh="最近变动" en="Recent changes" label="最近变动 Recent changes" className="pc-inv-panel">
        {data.recent_changes?.length ? <ul className="pc-inv-rows">{data.recent_changes.map(change=><Change key={`${change.business_date}|${change.kind}|${change.store_code}`} change={change} multi={multi}/>)}</ul>
          : <p className="pc-quiet">No stock changes in the last 7 days.</p>}
      </Panel>
    </div>
    {!!data.not_set_up&&<p className="pc-inv-meta">{number(data.not_set_up)} products have no checked stock type, so they aren't counted. <Link to="/products">Set types in Products</Link></p>}
  </section>
}

function Items({items,total,limit,empty,render}:{items:OverviewItem[];total?:number;limit:number;empty:string;render:(item:OverviewItem,index:number)=>ReactNode}) {
  const [open,setOpen] = useState(false)
  if (!items.length) return <p className="pc-quiet">{empty}</p>
  const shown = open ? items : items.slice(0,limit)
  return <>
    <ul className="pc-inv-rows">{shown.map(render)}</ul>
    {items.length>limit&&<button type="button" className="pc-inv-more" aria-expanded={open} onClick={()=>setOpen(value=>!value)}>
      {open ? 'Show fewer' : `Show all ${total ?? items.length}`}
    </button>}
  </>
}

function Row({item,label,meta,trailing}:{item:OverviewItem;label?:ReactNode;meta:ReactNode;trailing:ReactNode}) {
  const to = item.link.series_id ? `/stock?series_id=${item.link.series_id}` : `/stock?product_id=${item.link.product_id}`
  return <li><Link className="pc-row pc-inv-row" to={to}>
    {label!==undefined&&<span className="pc-row-label">{label}</span>}
    <span className="pc-row-main"><strong>{item.name}</strong><small>{meta}</small></span>
    {trailing}
  </Link></li>
}

function Change({change,multi}:{change:OverviewChange;multi:boolean}) {
  const [zh,en] = CHANGES[change.kind] ?? [change.kind.replaceAll('_',' '),'']
  const net = change.added-change.removed
  const value = MOVES.includes(change.kind) ? <>{number(Math.max(change.added,change.removed))}<small>moved</small></>
    : net>0 ? `+${number(net)}` : net<0 ? `−${number(-net)}` : '0'
  return <li className="pc-row pc-inv-row">
    <span className="pc-row-label">{shortDate(change.business_date)}</span>
    <span className="pc-row-main"><strong>{zh} <small className="pc-inv-en">{en}</small></strong><small>{multi&&`${change.store_code} · `}{plural(change.documents,'record')}</small></span>
    <span className="pc-inv-num">{value}</span>
  </li>
}
