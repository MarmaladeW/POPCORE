import { useState } from 'react'
import { Alert, Button, Popconfirm } from 'antd'
import { useNavigate } from 'react-router-dom'

import {
  approveDexListings, retryDexListings, withdrawDexListing, type DexListingCandidate, type DexListingRow, type DexListings,
} from '../../api/dex'
import { ListRow, Panel } from '../../components/pc/Panel'
import { LISTING_KIND, LISTING_STATE, candidateState, codeHint, listingFacts, waitText } from '../../lib/dexListings'
import { when } from './DexParts'

const SHOWN = 30

function More({ total, all, toggle }: { total: number; all: boolean; toggle: () => void }) {
  return total > SHOWN ? <Button type="link" onClick={toggle}>{all ? 'Show fewer' : `Show all ${total}`}</Button> : null
}

// 网站上架 Listings (contract §7, step 4): stocked items with no listing on popcore.ca. A manager approves them; they go
// as drafts on the Dex sync's pass once it is on and the item's book entry is on the website. Nothing is published.
export default function DexListingsPanels({ data, busy, run }: {
  data: DexListings; busy: string; run: (key: string, action: () => Promise<string>) => void
}) {
  const navigate = useNavigate()
  const [allCandidates, setAllCandidates] = useState(false)
  const [allApproved, setAllApproved] = useState(false)
  const [allDone, setAllDone] = useState(false)
  const counts = data.counts
  const candidates = allCandidates ? data.candidates : data.candidates.slice(0, SHOWN)
  const approved = allApproved ? data.approved : data.approved.slice(0, SHOWN)
  const done = allDone ? data.done : data.done.slice(0, SHOWN)
  const title = (row: { name: string; popcore_id: string }) => <><strong>{row.popcore_id}</strong> {row.name}</>
  const label = (item: DexListingCandidate) => LISTING_KIND[item.kind].zh

  const withdraw = (row: DexListingRow) => <Popconfirm title={`Take ${row.popcore_id} off the list?`}
    description="It is not sent, and shows in the list to approve again." okText="Withdraw"
    onConfirm={() => run(`withdraw:${row.product_id}`, async () => { await withdrawDexListing(row.product_id); return `${row.popcore_id} withdrawn` })}>
    <Button size="small" aria-label={`Withdraw ${row.popcore_id}`} loading={busy === `withdraw:${row.product_id}`} disabled={!!busy}>撤回 Withdraw</Button>
  </Popconfirm>

  return <>
    <Panel zh="网站上架" en="Listings on popcore.ca" label="Listings on popcore.ca" count={counts.candidates}
      tone={counts.not_in_book || counts.refused ? 'warn' : 'ok'}
      action={counts.approvable > 0 ? <Popconfirm title={`Approve ${counts.approvable} listings?`}
        description="Each goes to popcore.ca as a draft once the Dex sync is on and its book entry is on the website. Nothing is published."
        okText="Approve" onConfirm={() => run('approve:all', async () => {
          const answer = await approveDexListings({ all: true, count: counts.approvable })
          return `${answer.approved} approved`
        })}>
        <Button size="small" type="primary" loading={busy === 'approve:all'} disabled={!!busy}>全部批准 Approve all {counts.approvable}</Button>
      </Popconfirm> : undefined}>
      <p className="pc-quiet">有库存、网站没有商品页 · Items in stock with no listing on popcore.ca, from the stock sync’s catalogue
        read {data.catalogue_read_at ? `of ${when(data.catalogue_read_at)}` : '(none yet)'}. popcore.ca makes each one a draft by its own rules:
        a blind box with Single Box and Whole Set, a design tied to its box. Its name starts from the book; stock, price and pickup
        stores follow through the stock sync. Nothing goes until a manager approves it{data.enabled ? '' : ', and nothing goes while the Dex sync is off'}.</p>
      {!data.catalogue_read_at && <Alert type="warning" showIcon message="还没读过网站产品 · No catalogue read yet"
        description="Without the stock sync’s read of popcore.ca’s products, POPCORE cannot tell which items have a listing. Read the website’s products on the 网站 Website page first." />}
      {data.last_error && <Alert type="error" showIcon message={data.last_error.message}
        description={`上次失败 Last failed ${when(data.last_error.at)} (${data.last_error.code}). The next pass tries again; nothing from that call was marked.`} />}
      <div className="pc-dex-sync-counts">
        <div><strong>{counts.ready}</strong><span>可发送 Ready</span></div>
        <div><strong>{counts.book_waiting}</strong><span>等图鉴 Waiting for the book send</span></div>
        <div><strong>{counts.not_in_book}</strong><span>不在图鉴 Not in the book</span></div>
        <div><strong>{counts.approved}</strong><span>已批准 Approved</span></div>
        <div><strong>{counts.refused}</strong><span>被拒 Refused</span></div>
        <div><strong>{counts.done}</strong><span>已完成 Done</span>{counts.done > 0 && <small>{counts.created} new · {counts.completed} added · {counts.exists} there</small>}</div>
      </div>
      {candidates.length > 0 && <section aria-label="To approve">
        <h3 className="pc-dex-sync-sub">待批准 To approve <small>{counts.new} new listings, {counts.complete} listings missing an option</small></h3>
        {candidates.map(item => <ListRow key={item.product_id} label={label(item)} title={title(item)}
          meta={`${candidateState(item)} · ${listingFacts(item)}${item.again ? ' · made before, not on the website any more' : ''}`}
          trailing={item.state === 'not_in_book'
            ? <Button size="small" aria-label={`Put ${item.popcore_id} in the book`}
              onClick={() => navigate(`/dex/unlinked?q=${encodeURIComponent(item.popcore_id)}`)}>放进图鉴 Put in the book</Button>
            : <Button size="small" aria-label={`Approve ${item.popcore_id}`} loading={busy === `approve:${item.product_id}`} disabled={!!busy}
              onClick={() => run(`approve:${item.product_id}`, async () => { await approveDexListings([item.product_id]); return `${item.popcore_id} approved` })}>
              批准 Approve</Button>} />)}
        <More total={data.candidates.length} all={allCandidates} toggle={() => setAllCandidates(value => !value)} />
      </section>}
    </Panel>

    {data.approved.length > 0 && <Panel zh="已批准" en="Approved, not sent yet" label="Approved listings" count={counts.approved} tone="ok">
      {approved.map(row => <ListRow key={row.product_id} label="批准" title={title(row)}
        meta={`${waitText(row, data.enabled)} · approved ${when(row.approved_at)}`} trailing={withdraw(row)} />)}
      <More total={data.approved.length} all={allApproved} toggle={() => setAllApproved(value => !value)} />
    </Panel>}

    {data.refused.length > 0 && <Panel zh="网站拒收" en="Listings refused by popcore.ca" label="Refused listings" count={counts.refused} tone="warn"
      action={<Button size="small" loading={busy === 'listing-retry:all'} disabled={!!busy}
        onClick={() => run('listing-retry:all', async () => { const answer = await retryDexListings('all'); return `${answer.retried} go again with the next pass` })}>
        全部重试 Retry all</Button>}>
      <p className="pc-quiet">popcore.ca did not make these. They are not sent again until what POPCORE sends for them changes or a manager retries.</p>
      {data.refused.map(row => <ListRow key={row.product_id} label={LISTING_STATE[row.state].split(' ')[0]} title={title(row)}
        meta={`${codeHint(row.code)} (${row.code}${row.message ? `: ${row.message}` : ''})${row.website_product_id ? ` · website #${row.website_product_id}` : ''}`}
        trailing={<span className="pc-dex-listing-actions">
          <Button size="small" aria-label={`Retry ${row.popcore_id}`} loading={busy === `listing-retry:${row.product_id}`} disabled={!!busy}
            onClick={() => run(`listing-retry:${row.product_id}`, async () => { await retryDexListings(row.product_id); return `${row.popcore_id} goes again with the next pass` })}>
            重试 Retry</Button>
          {withdraw(row)}
        </span>} />)}
    </Panel>}

    {data.done.length > 0 && <Panel zh="已完成" en="Answered by popcore.ca" label="Answered listings" count={counts.done} tone="ok">
      <p className="pc-quiet">Drafts are never published by POPCORE. A Whole Set added to a published listing waits unpublished for a person to check.</p>
      {done.map(row => <ListRow key={row.product_id} label={LISTING_STATE[row.state].split(' ')[0]} title={title(row)}
        meta={`${LISTING_STATE[row.state]}${row.website_product_id ? ` · website #${row.website_product_id}` : ''} · ${when(row.answered_at)}${row.message ? ` · ${row.message}` : ''}`} />)}
      <More total={data.done.length} all={allDone} toggle={() => setAllDone(value => !value)} />
    </Panel>}
  </>
}
