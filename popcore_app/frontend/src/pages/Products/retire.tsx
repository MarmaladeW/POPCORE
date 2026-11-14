import { Modal, message } from 'antd'
import client from '../../api/client'

// 停用 Retire (owner, 2026-11-11): a product POPCORE keeps for its stock or report history leaves the
// catalog's default list, the product searches and 日报 matching, while all its history stays.

type Failure = { response?: { data?: { code?: string } }; _serverMessage?: string }
const codeOf = (cause: unknown) => (cause as Failure)?.response?.data?.code
const messageOf = (cause: unknown, fallback: string) => (cause as Failure)?._serverMessage || fallback

export const retireProduct = (id: number) => client.post(`/products/${id}/retire`)
export const unretireProduct = (id: number) => client.post(`/products/${id}/unretire`)

export function RetiredBadge({ retiredAt }: { retiredAt?: string | null }) {
  if (!retiredAt) return null
  return <span className="pc-prod-retired">已停用 Retired</span>
}

// The server keeps UTC ("YYYY-MM-DD HH:MM:SS"); show the device's local date.
export function retiredOn(text: string) {
  const when = new Date(`${text.replace(' ', 'T')}Z`)
  return Number.isNaN(when.getTime()) ? text : when.toLocaleDateString()
}

// Retire each product, once more for one refused while its sealed set or designs were still in use
// (they may be among the others). Reports what was retired and why any was not.
async function retireAll(ids: number[]) {
  const refused = new Map<number, string>()
  let pending = ids
  for (let pass = 0; pass < 2 && pending.length; pass++) {
    const again: number[] = []
    for (const id of pending) {
      try {
        await retireProduct(id)
        refused.delete(id)
      } catch (cause) {
        refused.set(id, messageOf(cause, '停用失败 Retire failed'))
        if (codeOf(cause) === 'sub_items_in_use') again.push(id)
      }
    }
    pending = again
  }
  const done = ids.length - refused.size
  if (done) message.success(`已停用 ${done} 个 · Retired ${done}`)
  if (refused.size) Modal.error({
    title: `${refused.size} 个没有停用 · ${refused.size} not retired`,
    content: <ul className="pc-prod-retire-errors">{[...refused.values()].map((text, i) => <li key={i}>{text}</li>)}</ul>,
  })
}

// Delete; when POPCORE keeps the products for their history, offer 停用 Retire instead.
export async function deleteOrRetire(ids: number[], onDone: () => void) {
  try {
    await client.post('/products/bulk_delete', ids)
    message.success(ids.length > 1 ? `已删除 ${ids.length} 个 · Deleted ${ids.length}` : '已删除 Deleted')
    onDone()
  } catch (cause) {
    if (codeOf(cause) !== 'product_history_retained') {
      message.error(messageOf(cause, '删除失败 Delete failed'))
      return
    }
    Modal.confirm({
      title: ids.length > 1 ? '有记录，不能删除 · These products have history' : '有记录，不能删除 · This product has history',
      content: <>
        <p>POPCORE 保留有库存或日报记录的产品。停用后不再出现在目录、搜索和日报匹配里，所有记录都保留。</p>
        <p>POPCORE keeps products with stock or report history. A retired product leaves the catalog, the searches
          and 日报 matching; all its history stays. Its stock must be 0.</p>
      </>,
      okText: ids.length > 1 ? `停用 ${ids.length} 个 Retire` : '停用 Retire',
      cancelText: '取消 Cancel',
      onOk: async () => { await retireAll(ids); onDone() },
    })
  }
}

export async function unretire(id: number, onDone: () => void) {
  try {
    await unretireProduct(id)
    message.success('已恢复 Back in use')
    onDone()
  } catch (cause) {
    message.error(messageOf(cause, '恢复失败 Un-retire failed'))
  }
}
