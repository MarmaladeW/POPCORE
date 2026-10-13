import { Alert, Button, Skeleton } from 'antd'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import dayjs from 'dayjs'
import { getStoreOverview, type StoreOverview } from '../../api/storeInsights'
import { useAppStore } from '../../store'
import { formatCents } from '../../lib/money'
import { torontoDate } from '../Dashboard/todayPresentation'
import './storeInsights.css'

const reasonLabels:Record<string,string>={out_of_stock:'Out of stock',replenish:'Replenish floor',condition:'Held or damaged stock',unverified:'Stock needs verification'}
const failure=(cause:unknown)=>(cause as {_serverMessage?:string})?._serverMessage||'Unable to load store insights. Retry to check the selected period.'
const productLink=(seriesId:number|null)=>seriesId?`/stock?series_id=${seriesId}`:'/stock/products'

export default function StoreInsights() {
  const store=useAppStore(state=>state.selectedStore)
  const [params,setParams]=useSearchParams()
  const to=params.get('to')||torontoDate(),from=params.get('from')||dayjs(to).subtract(29,'day').format('YYYY-MM-DD')
  const [data,setData]=useState<StoreOverview>(),[error,setError]=useState(''),[retry,setRetry]=useState(0)
  const [source,setSource]=useState<'posted'|'historical'>('posted')
  const valid=[from,to].every(value=>/^\d{4}-\d{2}-\d{2}$/.test(value)&&dayjs(value).format('YYYY-MM-DD')===value)
  const rangeError=!valid?'Choose valid start and end dates.':from>to?'From date must be on or before through date.':dayjs(to).diff(dayjs(from),'day')>365?'Choose a period of 366 days or less.':''
  useEffect(()=>{
    setData(undefined);setError('')
    if(!store?.code||rangeError)return
    const controller=new AbortController()
    getStoreOverview({store_code:store.code,from,to},controller.signal).then(result=>{if(!controller.signal.aborted)setData(result)})
      .catch(cause=>{if(!controller.signal.aborted)setError(failure(cause))})
    return()=>controller.abort()
  },[store?.code,from,to,retry,rangeError])
  const change=(key:string,value:string)=>{const next=new URLSearchParams(params);next.set(key,value);setParams(next)}
  const historicalDays=new Map(data?.historical_reports.daily.map(day=>[day.date,day]))
  const dates=`from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`
  return <main className="pc-insights">
    <header className="pc-insights-heading"><div><h1>Insights &amp; reports</h1><p>{store?.name||'Choose a store'} · Business dates in Toronto</p></div><nav aria-label="Manager review"><Link to={`/sales/matching?${dates}`}>Review past names</Link><Link to={`/reports?report=inventory&${dates}`}>Detailed reports</Link></nav></header>
    <div className="pc-insights-controls"><label>From date<input type="date" value={from} onChange={event=>change('from',event.target.value)}/></label><label>Through date<input type="date" value={to} onChange={event=>change('to',event.target.value)}/></label><Button onClick={()=>setRetry(value=>value+1)} disabled={!!rangeError}>Refresh insights</Button></div>
    {rangeError?<Alert type="error" message={rangeError}/>:error?<Alert type="error" message={error} action={<Button onClick={()=>setRetry(value=>value+1)}>Retry insights</Button>}/>:!store?<p>Select a store to begin.</p>:!data?<Skeleton active paragraph={{rows:6}}/>:<>
      {!data.stores.length&&<Alert type="info" message="No authorized stores in this view."/>}
      <section className="pc-insights-summary" aria-label="Recorded sales summary"><div><h2>Recorded sales</h2><p>Posted sale documents, including completed checkouts linked to those documents.</p></div><dl>
        <div><dt>Sale documents</dt><dd>{data.posted_sales.document_count}</dd></div>
        <div><dt>{data.posted_sales.gross_complete?'Recorded gross':'Known gross subtotal'}</dt><dd>{data.posted_sales.document_count?formatCents(data.posted_sales.known_gross_cents):'No records'}</dd></div>
        <div><dt>Unknown gross</dt><dd>{data.posted_sales.unknown_gross_count} {data.posted_sales.unknown_gross_count===1?'document':'documents'}</dd></div>
      </dl><Link to={`/reports?report=sales&${dates}`}>Inspect recorded sales</Link></section>
      <section className="pc-insights-section" aria-labelledby="insight-actions"><div className="pc-insights-section-title"><h2 id="insight-actions">Next actions</h2><Link to="/today">Store tasks</Link></div><ul className="pc-insights-actions">
        <li><div><strong>{data.checkout_activity.open_count} orders in progress in this period</strong><span>Unfinished orders are separate from posted sales.</span></div><Link to="/checkout/history">Review orders</Link></li>
        {data.checkout_activity.completed_without_posted_sale>0&&<li><div><strong>{data.checkout_activity.completed_without_posted_sale} completed orders need a sales link</strong><span>These orders are excluded from posted sales totals above.</span></div><Link to="/checkout/history">Review orders</Link></li>}
        <li><div><strong>{data.historical_reports.days_with_rows} of {data.period.calendar_days} dates have historical report rows</strong><span>{data.historical_reports.row_count} saved {data.historical_reports.row_count===1?'row':'rows'} across {data.historical_reports.product_count} {data.historical_reports.product_count===1?'product':'products'}. A missing report is not a zero-sales day.</span></div><Link to={`/sales?date=${encodeURIComponent(to)}`}>Review daily reports</Link></li>
        <li><div><strong>{data.current_inventory.total_exception_products} {data.current_inventory.total_exception_products===1?'product needs':'products need'} inventory attention</strong><span>Current stock snapshot · {data.current_inventory.reviewed_location_count} reviewed locations · {data.current_inventory.unreviewed_location_count} unreviewed</span></div><Link to="/stock">Open inventory</Link></li>
      </ul></section>
      <section className="pc-insights-section" aria-labelledby="product-performance"><div className="pc-insights-section-title"><h2 id="product-performance">Product performance</h2><label>Source<select aria-label="Performance source" value={source} onChange={event=>setSource(event.target.value as typeof source)}><option value="posted">Recorded sales</option><option value="historical">Historical daily reports</option></select></label></div>
        <p>{source==='posted'?'Top products by posted quantity. Sets, boxes, and pieces stay separate.':'Top products by reported quantity. These aggregate reports can overlap recorded sales; their quantities are not added to recorded sales totals.'}</p>
        <div className="pc-insights-table-wrap"><table><thead><tr><th scope="col">Product</th><th scope="col">{source==='posted'?'Posted quantity':'Reported quantity'}</th><th scope="col">{source==='posted'?'Identity':'Report dates'}</th></tr></thead><tbody>
          {source==='posted'?data.posted_sales.top_products.map((product,index)=><tr key={`${product.product_id}|${product.native_unit}|${index}`}><th scope="row">{product.series_id?<Link to={productLink(product.series_id)}>{product.product_name}</Link>:product.product_name}</th><td>{product.quantity} {product.native_unit||'unknown unit'}</td><td>{product.identity_complete?'Reviewed':'Needs review'}</td></tr>):data.historical_reports.top_products.map(product=><tr key={product.product_id}><th scope="row">{product.product_name}{product.raw_names.length>0&&<small className="pc-insights-recorded-names">Reported as: {product.raw_names.join(' · ')}</small>}</th><td>{product.reported_quantity}</td><td>{product.days_reported}</td></tr>)}
        </tbody></table>{!(source==='posted'?data.posted_sales.top_products:data.historical_reports.top_products).length&&<p className="pc-insights-empty">No {source==='posted'?'posted product lines':'historical report rows'} in this period.</p>}</div>
        <small>{source==='posted'?'Up to 20 products per stock unit.':'Up to 20 products.'} Historical quantities do not establish native stock units, costs or profit.</small>
      </section>
      <section className="pc-insights-section" aria-labelledby="stock-attention"><div className="pc-insights-section-title"><h2 id="stock-attention">Stock to check now</h2><span>Current, independent of the date range</span></div>
        {data.current_inventory.mode!=='authoritative'&&<Alert type="warning" message="Opening inventory is not active. Stock remains unverified."/>}
        <ul className="pc-insights-stock">{data.current_inventory.items.map(product=><li key={product.product_id}><div><strong>{product.product_name}</strong><span>{product.reasons.map(reason=>reasonLabels[reason]||reason.replaceAll('_',' ')).join(' · ')}</span></div><Link to={productLink(product.series_id)}>View stock</Link></li>)}</ul>
        {!data.current_inventory.items.length&&<p>No inventory exceptions were returned for this scope.</p>}
        {data.current_inventory.total_exception_products>data.current_inventory.items.length&&<Link to="/stock">View all {data.current_inventory.total_exception_products} products needing attention</Link>}
      </section>
      <details className="pc-insights-section"><summary>Daily coverage · {data.period.calendar_days} dates</summary><p>No records means the source has no entry; it does not prove the store made no sales. All-store coverage means at least one authorized store reported that date.</p><div className="pc-insights-table-wrap pc-insights-coverage"><table><thead><tr><th>Date</th><th>Posted sales</th><th>Historical report</th></tr></thead><tbody>{data.posted_sales.daily.map(day=>{const historical=historicalDays.get(day.date);return <tr key={day.date}><th scope="row">{day.date}</th><td>{day.coverage==='no_records'?'No records':`${day.document_count} documents`}</td><td>{historical?.coverage==='report_rows'?`${historical.reported_quantity} reported`:historical?.coverage==='metadata_only'?'Notes only':'No report'}</td></tr>})}</tbody></table></div></details>
      {data.limitations.length>0&&<details className="pc-insights-limits"><summary>What these numbers cover</summary><ul>{data.limitations.map(item=><li key={item}>{item}</li>)}</ul></details>}
    </>}
  </main>
}
