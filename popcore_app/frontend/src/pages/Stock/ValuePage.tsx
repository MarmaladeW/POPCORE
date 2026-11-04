import { Alert, Button, Skeleton } from 'antd'
import { useEffect, useState, type CSSProperties } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { getInventoryValue, UNSORTED, type InventoryValue, type ValueGroup, type ValueTotal } from '../../api/inventoryValue'
import Icon from '../../components/pc/Icon'
import PageHeader from '../../components/pc/PageHeader'
import { Panel } from '../../components/pc/Panel'
import { useIsPhone } from '../../components/pc/useIsPhone'
import { formatCents, formatWholeDollars } from '../../lib/money'
import { useAppStore } from '../../store'
import './value.css'

// 库存价值 Stock value by IP line (owner, 2026-11-03): what each place's stock is worth, split into IP lines
// (Smiski, Sonny Angel, Hirono …), and what one line is made of. Reached from the 库存 dashboard's tiles.

const PLACES:Record<string,[string,string]> = {
  all:['全部','All'], floor:['楼面','Floor'], display:['陈列','Display'], claw:['娃娃机','Claw machine'],
}
const MEASURE:Record<string,string> = { box:'盒', set:'端', piece:'个' }
const FORMS:Record<string,string> = { sealed_set:'整端', random_box:'单盒', confirmed_design:'确认款', ordinary:'单品' }
const number = (value:number) => value.toLocaleString('en-US')
const plural = (amount:number, word:string) => `${number(amount)} ${word}${amount === 1 ? '' : 's'}`
const percent = (share:number) => share >= 0.1 ? `${(share * 100).toFixed(0)}%` : share > 0 ? `${(share * 100).toFixed(1)}%` : '0%'
const unitsText = (byUnit:Partial<Record<string, number>>) =>
  Object.keys(MEASURE).filter(unit => byUnit[unit]).map(unit => `${number(byUnit[unit]!)} ${MEASURE[unit]}`).join(' · ')
const lineName = (name:string) => name === UNSORTED ? '未分类 Unsorted' : name
const backName = (codes:string[]):[string,string] => codes.length > 1 ? ['楼上 · 仓库','Back stock'] : codes[0] === 'warehouse' ? ['仓库','Warehouse'] : ['楼上','Upstairs']

export default function ValuePage() {
  const store = useAppStore(state => state.selectedStore)
  const phone = useIsPhone()
  const [params] = useSearchParams()
  const place = params.get('place') || 'all'
  const line = params.get('line')
  const storeCode = store?.code ?? ''
  const [retry, setRetry] = useState(0)
  const key = `${storeCode}|${place}|${line}|${retry}`
  const [result, setResult] = useState<{ key:string; data?:InventoryValue; error?:string }>()
  useEffect(() => {
    if (!storeCode) return
    const controller = new AbortController()
    getInventoryValue(storeCode, place, line, controller.signal)
      .then(data => { if (!controller.signal.aborted) setResult({ key, data }) })
      .catch(cause => { if (!controller.signal.aborted) setResult({ key, error:(cause as { _serverMessage?:string })?._serverMessage || 'Stock value did not load.' }) })
    return () => controller.abort()
  }, [key, storeCode, place, line])
  const loaded = result?.key === key ? result : undefined
  const data = loaded?.data
  const placeLink = (next:string, nextLine:string|null = line) =>
    `/stock/value?place=${next}${nextLine ? `&line=${encodeURIComponent(nextLine)}` : ''}`
  const linesLink = placeLink(place, null)
  const where = data?.places?.find(item => item.key === place)
  const placeTitle = place === 'all' ? '全部 All' : where?.key === 'back' ? backName(where.codes).join(' ') : (PLACES[place] ?? [place, '']).join(' ')

  return <div className="pc-value">
    <PageHeader title={line ? lineName(line) : '库存价值'}
      sub={phone ? undefined : `${line ? '库存价值 Stock value' : 'Stock value'}${store?.name ? ` · ${store.name}` : ''}`}
      back={phone ? (line ? { label:'库存价值', to:linesLink } : { label:'库存', to:'/stock' }) : undefined} />
    {!phone && <nav className="pc-value-crumbs" aria-label="Breadcrumb">
      <Link to="/stock">库存 Inventory</Link><Icon name="right" size={12} />
      {line ? <><Link to={linesLink}>库存价值 Stock value</Link><Icon name="right" size={12} /><span aria-current="page">{lineName(line)}</span></>
        : <span aria-current="page">库存价值 Stock value</span>}
    </nav>}
    {!storeCode ? <Alert type="info" showIcon message="Choose a store to see its stock value." />
      : loaded?.error ? <Alert type="error" showIcon message={loaded.error} action={<Button onClick={() => setRetry(value => value + 1)}>Retry</Button>} />
      : !data ? <Skeleton active paragraph={{ rows:6 }} />
      : !data.live ? <p className="pc-notice">{storeCode} 还没上线 · Stock value shows once this store’s stock is counted and live.</p>
      : <>
        {!line && <nav className="pc-figures pc-value-places" aria-label="Places" style={{ '--places':(data.places?.length ?? 0) + 1 } as CSSProperties}>
          <PlaceTile to={placeLink('all')} current={place === 'all'} zh="全部" en="All" total={data.total!} />
          {data.places?.map(item => {
            const [zh, en] = item.key === 'back' ? backName(item.codes) : PLACES[item.key] ?? [item.codes.join(' · '), '']
            return <PlaceTile key={item.key} to={placeLink(item.key)} current={place === item.key} zh={zh} en={en} total={item} back={item.key === 'back'} />
          })}
        </nav>}
        {line ? <LineDetail data={data} place={place} placeTitle={placeTitle} placeLink={placeLink} />
          : <Lines data={data} placeTitle={placeTitle} lineLink={name => placeLink(place, name)} />}
      </>}
  </div>
}

function PlaceTile({ to, current, zh, en, total, back }:{ to:string; current:boolean; zh:string; en:string; total:ValueTotal; back?:boolean }) {
  return <Link to={to} aria-current={current ? 'page' : undefined} className={back ? 'pc-value-back' : undefined}>
    <span>{zh} <small>{en}</small></span>
    <strong>{formatWholeDollars(total.value_cents)}</strong>
    <small>{plural(total.units, 'unit')}</small>
  </Link>
}

function Lines({ data, placeTitle, lineLink }:{ data:InventoryValue; placeTitle:string; lineLink:(name:string)=>string }) {
  const lines = data.categories ?? []
  const shown = data.shown!
  const guessed = lines.reduce((sum, item) => sum + item.guessed_units, 0)
  return <Panel zh="按 IP 系列" en={`By IP line · ${placeTitle}`} label="按 IP 系列 By IP line" className="pc-value-panel">
    {!lines.length ? <p className="pc-quiet"><strong>这里没有库存</strong>Nothing is stocked here yet.</p>
      : <ol className="pc-value-rows">{lines.map((item, index) => <li key={item.name}>
        <Link className="pc-row pc-value-row" to={lineLink(item.name)}>
          <span className="pc-row-label">{index + 1}</span>
          <span className="pc-row-main">
            <strong>{lineName(item.name)}</strong>
            <small>{plural(item.units, 'unit')} · {plural(item.products, 'product')}{item.guessed_units ? ` · ${number(item.guessed_units)} placed by name` : ''}</small>
            <span className="pc-value-bar" aria-hidden="true"><i style={{ width:`${Math.max(item.share * 100, item.value_cents ? 0.6 : 0)}%` }} /></span>
          </span>
          <span className="pc-value-num">{formatWholeDollars(item.value_cents)}<small>{percent(item.share)}</small></span>
          <Icon name="right" size={14} className="pc-value-go" />
        </Link>
      </li>)}</ol>}
    {(shown.unpriced_units > 0 || guessed > 0) && <p className="pc-value-note">
      {shown.unpriced_units > 0 && `${plural(shown.unpriced_units, 'unit')} have no price, so they add nothing. `}
      {guessed > 0 && <>Products without an IP line in the catalogue are placed by their name (SA → Sonny Angel, SP → SkullPanda,
        小野 → Hirono …). <Link to="/products">Set the IP line in Products</Link> to make it exact.</>}
    </p>}
  </Panel>
}

function LineDetail({ data, place, placeTitle, placeLink }:{ data:InventoryValue; place:string; placeTitle:string; placeLink:(place:string)=>string }) {
  const category = data.category
  if (!category) return null
  const placeName = (key:string) => {
    const item = data.places?.find(candidate => candidate.key === key)
    return key === 'back' && item ? backName(item.codes)[0] : PLACES[key]?.[0] ?? key
  }
  const everywhere = category.by_place.reduce((sum, item) => sum + item.value_cents, 0)
  // On a line's page its own split over the places is the place switcher.
  const split = category.by_place.length > 1 && <nav className="pc-value-split" aria-label={`${lineName(category.name)} by place`}>
    <Link to={placeLink('all')} aria-current={place === 'all' ? 'page' : undefined}><span>全部</span><b>{formatWholeDollars(everywhere)}</b></Link>
    {category.by_place.map(item => <Link key={item.key} to={placeLink(item.key)} aria-current={place === item.key ? 'page' : undefined}
      className={item.key === 'back' ? 'pc-value-split-back' : undefined}><span>{placeName(item.key)}</span><b>{formatWholeDollars(item.value_cents)}</b></Link>)}
  </nav>
  if (!category.units) return <>{split}<p className="pc-panel pc-quiet"><strong>{lineName(category.name)} has no stock in {placeTitle}</strong>Pick another place.</p></>
  return <>
    <section className="pc-value-summary" aria-label={`${lineName(category.name)} summary`}>
      <div>
        <span>{placeTitle}</span>
        <strong>{formatWholeDollars(category.value_cents)}</strong>
        <small>{plural(category.units, 'unit')} · {plural(category.products, 'product')}{unitsText(category.by_unit) && ` · ${unitsText(category.by_unit)}`}</small>
      </div>
      {split}
    </section>
    <Panel zh="组成" en={`What it’s made of · ${plural(category.groups.length, 'part')}`} label="组成 What it’s made of" className="pc-value-panel">
      <ul className="pc-value-rows">{category.groups.map(group => <Group key={`${group.link.series_id ?? ''}|${group.link.product_id ?? ''}`} group={group} />)}</ul>
      {(category.unpriced_units > 0 || category.guessed_units > 0) && <p className="pc-value-note">
        {category.unpriced_units > 0 && `${plural(category.unpriced_units, 'unit')} have no price, so they add nothing. `}
        {category.guessed_units > 0 && <>Items marked 按名称 are placed in {lineName(category.name)} by their name; <Link to="/products">set their IP line in Products</Link> to make it exact.</>}
      </p>}
    </Panel>
  </>
}

function Group({ group }:{ group:ValueGroup }) {
  const to = group.link.series_id ? `/stock?series_id=${group.link.series_id}` : `/stock?product_id=${group.link.product_id}`
  const guessed = group.members.some(member => member.guessed)
  return <li>
    <details className="pc-value-group">
      <summary className="pc-row pc-value-row">
        <Icon name="right" size={14} className="pc-value-chevron" />
        <span className="pc-row-main">
          <strong>{group.name}{guessed && <span className="pc-value-tag">按名称</span>}</strong>
          <small>{plural(group.members.length, 'part')} · {unitsText(group.by_unit)}</small>
          <span className="pc-value-bar" aria-hidden="true"><i style={{ width:`${Math.max(group.share * 100, group.value_cents ? 0.6 : 0)}%` }} /></span>
        </span>
        <span className="pc-value-num">{formatWholeDollars(group.value_cents)}<small>{percent(group.share)}</small></span>
      </summary>
      <ul className="pc-value-members">{group.members.map(member => <li key={member.product_id}>
        <span className="pc-value-member-name">
          <strong>{member.name}</strong>
          <small>{[member.stock_form && FORMS[member.stock_form], member.sku, member.price_cents != null ? `${formatCents(member.price_cents)} each` : 'No price'].filter(Boolean).join(' · ')}{member.guessed && <span className="pc-value-tag">按名称</span>}</small>
        </span>
        <span className="pc-value-member-units">{member.unit ? `${number(member.units)} ${MEASURE[member.unit] ?? ''}` : number(member.units)}</span>
        <span className="pc-value-member-value">{member.price_cents == null ? '—' : formatCents(member.value_cents)}</span>
      </li>)}</ul>
      <Link className="pc-value-open" to={to}>查看库存 <small>Open its stock</small><Icon name="right" size={12} /></Link>
    </details>
  </li>
}
