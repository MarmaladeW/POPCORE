import { useEffect, useState } from 'react'
import { Alert, Button, Input, Modal, message } from 'antd'
import { LockOutlined } from '@ant-design/icons'
import client from '../../api/client'
import Icon from '../../components/pc/Icon'
import InventoryPhoto from '../Stock/InventoryPhoto'
import type { RestockSession } from './index'
import { withUnit } from './units'
import SeriesCard from './SeriesCard'
import { familyGroups, partLabel, searchSeries, type SeriesResult } from './series'

interface Props {
  session:   RestockSession
  onRefresh: () => void
}

const UNITS: Record<string, string> = { set: '端', box: '盒', piece: '个' }

function kindOf(result: SeriesResult) {
  const parts = [result.kind === 'box' ? '盲盒' : result.kind === 'series' ? '系列' : '单品']
  if (result.designs) parts.push(`${result.designs} 款`)
  if (result.sets) parts.push('整端')
  return parts.join(' · ')
}

function backOf(result: SeriesResult) {
  if (result.stock_hidden) return result.out_of_stock ? '楼上缺货' : '楼上有货'
  const back = result.back || {}
  const shown = ['set', 'box', 'piece'].filter(unit => back[unit]).map(unit => `${back[unit]} ${UNITS[unit]}`)
  return shown.length ? `楼上 ${shown.join(' · ')}` : '楼上 0'
}

// 录入 (owner, 2026-11-03): search a product, its series opens, choose how many whole sets, single
// boxes and confirmed designs; the sheet lists what was asked for, series by series.
export default function RequestStep({ session, onRefresh }: Props) {
  const [query,      setQuery]      = useState('')
  const [results,    setResults]    = useState<SeriesResult[] | null>(null)
  const [searchError, setSearchError] = useState('')
  const [openId,     setOpenId]     = useState<number | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const isReadOnly = session.status !== 'pending'
  const groups = familyGroups(session.items)

  useEffect(() => {
    const text = query.trim()
    if (isReadOnly || !text) { setResults(null); setSearchError(''); return }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      searchSeries(session.id, text, controller.signal).then(found => { setResults(found); setSearchError('') })
        .catch(cause => { if (!controller.signal.aborted) setSearchError(cause?._serverMessage || 'Search failed. Try again.') })
    }, 250)
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [session.id, query, isReadOnly])

  function closeCard(saved: boolean) {
    setOpenId(null)
    if (saved) { setQuery(''); setResults(null); onRefresh() }
  }

  // ── Submit (Modal confirm) ────────────────────────────────────────────────

  function handleSubmitClick() {
    if (session.items.length === 0) { message.warning('请至少添加一个补货产品'); return }
    Modal.confirm({
      title:   '确认提交补货申请？',
      content: `共 ${groups.length} 个系列、${session.items.length} 行，提交后将锁定清单，仓库开始拣货。`,
      okText:  '确认提交',
      cancelText: '取消',
      onOk:    doSubmit,
    })
  }

  async function doSubmit() {
    setSubmitting(true)
    try {
      await client.post(`/restock/session/${session.id}/submit`)
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { error?: string }; status?: number }; message?: string; code?: string }
      const serverMsg = axiosErr?.response?.data?.error
      const status = axiosErr?.response?.status
      if (serverMsg) {
        message.error(`提交失败：${serverMsg}`)
      } else if (status) {
        message.error(`提交失败：HTTP ${status}`)
      } else {
        message.error(`提交失败：${axiosErr?.code ?? axiosErr?.message ?? '未知错误'}`)
      }
      setSubmitting(false)
      return
    }
    setSubmitting(false)
    message.success('补货申请已提交，仓库快照已锁定，进入拣货阶段')
    onRefresh()
  }

  // ── Read-only banner ──────────────────────────────────────────────────────

  function statusBanner() {
    if (session.status === 'submitted' || session.status === 'picking') {
      return <Alert type="info" showIcon icon={<LockOutlined />} message="补货申请已提交，仓库拣货进行中" />
    }
    if (session.status === 'completed') {
      return <Alert type="success" showIcon
        message={`本日补货已完成${session.completed_at ? `（${session.completed_at.slice(0, 16)}）` : ''}`} />
    }
    return null
  }

  const sheet = <section className="pc-rq-sheet" aria-label="补货清单">
    <h3 className="pc-rq-sub">补货清单 Sheet <small>{groups.length ? `${groups.length} 个系列 · ${session.items.length} 行` : ''}</small></h3>
    {groups.length === 0
      ? <p className="pc-rq-empty"><strong>{isReadOnly ? '暂无补货项目' : '还没有补货项目'}</strong>
          {isReadOnly ? 'Nothing was asked for on this sheet.' : 'Search a series, then choose whole sets, single boxes or designs.'}</p>
      : <ul className="pc-rq-groups">
        {groups.map(group => {
          const body = <>
            <span className="pc-rq-group-head">
              <strong>{group.title}</strong>
              {group.sku && <small>{group.sku}</small>}
              {!isReadOnly && <Icon name="right" size={14} />}
            </span>
            <ul className="pc-rq-group-lines">
              {group.items.map(item => <li key={item.id}>
                <span>{partLabel(item.role, item.design_name, item.jizhanming || item.name_cn_en)}</span>
                <b>{withUnit(item.requested_qty, item.stock_unit)}</b>
              </li>)}
            </ul>
          </>
          return <li key={group.id}>
            {isReadOnly ? <div className="pc-rq-group">{body}</div>
              : <button type="button" className="pc-rq-group" aria-label={`修改 ${group.title}`} onClick={() => setOpenId(group.id)}>{body}</button>}
          </li>
        })}
      </ul>}
  </section>

  if (isReadOnly) return <div className="pc-rq is-read">{statusBanner()}{sheet}</div>

  return (
    <div className={`pc-rq${openId ? ' is-card' : ''}`}>
      <div className="pc-rq-find">
        {openId
          ? <SeriesCard key={openId} sessionId={session.id} productId={openId} onClose={closeCard} />
          : <>
            <Input className="pc-rq-search" allowClear value={query} prefix={<Icon name="search" size={16} />}
              aria-label="搜索系列 Search a series" placeholder="搜索系列 / 记账名 / SKU / 款式"
              onChange={event => setQuery(event.target.value)} />
            {searchError && <Alert type="error" showIcon message={searchError} />}
            {results && (results.length
              ? <ul className="pc-rq-results" aria-label="系列 Series">
                {results.map(result => <li key={result.product_id}>
                  <button type="button" onClick={() => setOpenId(result.product_id)}>
                    <InventoryPhoto filename={result.image} name={result.title} />
                    <span className="pc-rq-result-text">
                      <strong>{result.title}</strong>
                      <small>{[result.sku, kindOf(result)].filter(Boolean).join(' · ')}</small>
                      <small className={`pc-rq-result-back${result.stock_hidden ? (result.out_of_stock ? ' is-out' : '') : ''}`}>{backOf(result)}</small>
                    </span>
                    {result.on_sheet > 0 && <span className="pc-rq-added">已加 {result.on_sheet}</span>}
                    <Icon name="right" size={14} />
                  </button>
                </li>)}
              </ul>
              : <p className="pc-rq-empty"><strong>没有找到</strong>No series matches. Try the 记账名, the SKU or a design name.</p>)}
          </>}
      </div>
      <div className="pc-rq-side">
        {sheet}
        {session.items.length > 0 && <footer className="pc-rq-bar">
          <span className="pc-rq-bar-text">
            <strong>{groups.length} 个系列 · {session.items.length} 行</strong>
            <small>提交后清单锁定，仓库开始拣货</small>
          </span>
          <Button type="primary" size="large" loading={submitting} onClick={handleSubmitClick}>提交 Submit</Button>
        </footer>}
      </div>
    </div>
  )
}
