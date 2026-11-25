import { Alert, Button, InputNumber, Tag, message } from 'antd'
import { useState } from 'react'

import client from '../../api/client'
import { eveningAmount, eveningText } from '../../lib/eveningText'
import { markText } from '../../lib/marks'

export interface LiveEveningItem {
  product_id: number; sku: string; jizhanming: string | null; name_cn_en: string | null; ip_series: string | null
  per_set: number | null; expected_sets: number | null; expected_loose: number | null
  saved: null | { sets: number | null; loose: number | null; matches: boolean; difference: number | null; by: string | null; at: string }
}
export interface LiveEveningData { date: string; store_code: string; live: true; items: LiveEveningItem[] }

const nameOf = (item: LiveEveningItem) => item.jizhanming || item.name_cn_en || item.sku

// 晚盘 at a live store (owner, 2026-11-20/21): count each bestseller on the floor every night, as staff write it in
// WeChat, whole sets (端) and loose boxes (散) on the floor only. Saving marks each ✅ or 🥲 against POPCORE and keeps who
// counted; 复制到微信 copies the WeChat message, so it is typed once. It may be saved again the same night.
export default function LiveEveningCount({ data, onSaved }: { data: LiveEveningData; onSaved: (data: LiveEveningData) => void }) {
  const [counts, setCounts] = useState<Record<number, { sets: number; loose: number }>>(() => Object.fromEntries(
    data.items.filter(item => item.saved).map(item => [item.product_id, { sets: item.saved!.sets ?? 0, loose: item.saved!.loose ?? 0 }])))
  const [saving, setSaving] = useState(false)
  const typed = data.items.filter(item => counts[item.product_id])
  const set = (id: number, field: 'sets' | 'loose', value: number | null) =>
    setCounts(current => {
      const before = current[id] ?? { sets: 0, loose: 0 }
      return { ...current, [id]: { ...before, [field]: Math.max(0, Math.floor(value ?? 0)) } }
    })

  async function save() {
    if (!typed.length) { message.warning('先填数量 Type the counts first'); return }
    setSaving(true)
    try {
      const { data: saved } = await client.post<LiveEveningData & { differ: number }>('/inventory-check/submit', {
        store_code: data.store_code,
        checks: typed.map(item => ({ product_id: item.product_id, ...counts[item.product_id] })),
      })
      message.success(saved.differ ? `晚盘已保存，${saved.differ} 项不一样 🥲 Saved; ${saved.differ} differ` : '晚盘已保存，全部对上 ✅ Saved; all match')
      onSaved({ ...data, items: saved.items })
    } catch (cause: any) {
      message.error(cause?._serverMessage ?? '没有保存 Not saved')
    } finally {
      setSaving(false)
    }
  }

  async function copy() {
    const text = eveningText(data.date, data.store_code, typed.map(item => ({
      ...counts[item.product_id], name: nameOf(item), per_set: item.per_set,
      matches: item.saved && item.saved.sets === counts[item.product_id].sets && item.saved.loose === counts[item.product_id].loose
        ? item.saved.matches : null,
    })))
    try {
      await navigator.clipboard.writeText(text)
      message.success('已复制，去微信粘贴 Copied: paste it in WeChat')
    } catch {
      window.prompt('复制这段 Copy this', text)
    }
  }

  return <div className="pc-evening" style={{ paddingTop: 16 }}>
    <Alert type="info" showIcon style={{ marginBottom: 12 }}
      message="只数楼下（店内）· Floor only"
      description="每个畅销品填楼下的 端（整端）和 散（散盒）。保存后对上是 ✅，不一样是 🥲。不会改库存；要改用 盘点。Type the floor's whole sets and loose boxes; saving marks each ✅ or 🥲. It changes no stock (盘点 does)." />
    <div role="list" aria-label="Evening count" className="pc-evening-list">
      {data.items.map(item => {
        const value = counts[item.product_id]
        const expected = item.expected_sets !== null || item.expected_loose !== null
          ? eveningAmount({ per_set: item.per_set, sets: item.expected_sets ?? 0, loose: item.expected_loose ?? 0 }) : null
        return <div role="listitem" key={item.product_id} className="pc-evening-row">
          <div className="pc-evening-name">
            <strong>{nameOf(item)}</strong>
            <small>{[item.sku, item.per_set ? `1 端 = ${item.per_set} 盒` : null, expected ? `POPCORE ${expected}` : null].filter(Boolean).join(' · ')}</small>
            {item.saved && <small>
              <Tag color={item.saved.matches ? 'success' : 'warning'} style={{ marginRight: 6 }}>
                {item.saved.matches ? '✅ 对上 Matches' : `🥲 不一样 Differs${item.saved.difference != null ? ` ${item.saved.difference > 0 ? '+' : ''}${item.saved.difference} 盒` : ''}`}
              </Tag>{markText(item.saved.by, item.saved.at)}
            </small>}
          </div>
          <div className="pc-evening-inputs">
            {item.per_set ? <label>端<InputNumber aria-label={`${nameOf(item)} sets`} min={0} precision={0} inputMode="numeric"
              value={value?.sets} onChange={next => set(item.product_id, 'sets', next)} style={{ width: 72 }} /></label> : null}
            <label>{item.per_set ? '散' : '个'}<InputNumber aria-label={`${nameOf(item)} ${item.per_set ? 'loose boxes' : 'pieces'}`} min={0} precision={0} inputMode="numeric"
              value={value?.loose} onChange={next => set(item.product_id, 'loose', next)} style={{ width: 72 }} /></label>
          </div>
        </div>
      })}
      {!data.items.length && <p className="pc-quiet">还没有畅销品：在 产品 Products 里打开 畅销 for the products to count.</p>}
    </div>
    <div className="pc-evening-actions">
      <Button type="primary" size="large" loading={saving} onClick={save}>保存晚盘 Save</Button>
      <Button size="large" disabled={!typed.length} onClick={copy}>复制到微信 Copy for WeChat</Button>
    </div>
  </div>
}
