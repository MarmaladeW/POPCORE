import { Alert, Select } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { searchGoodsProducts, type GoodsProduct } from '../api/goods'
import { productLabel } from '../lib/productLabel'
import { unreceivable } from '../lib/goodsLines'

/** Search box that hands each chosen product to onPick and clears, ready for the next one.
 * Products that cannot be received (unverified identity, no stock unit) are listed but cannot be chosen. */
export default function GoodsProductPicker({ products, onPick, disabled = false }: {
  products: GoodsProduct[]
  onPick: (product: GoodsProduct) => void
  disabled?: boolean
}) {
  const [options, setOptions] = useState(products), [searching, setSearching] = useState(false), [error, setError] = useState('')
  const search = useRef<AbortController>()
  useEffect(() => setOptions(products), [products])
  useEffect(() => () => search.current?.abort(), [])
  async function find(query: string) {
    search.current?.abort()
    setError('')
    if (!query.trim()) { setOptions(products); setSearching(false); return }
    const controller = new AbortController()
    search.current = controller
    setSearching(true)
    try {
      const results = await searchGoodsProducts(query, controller.signal)
      if (!controller.signal.aborted) setOptions(results)
    } catch {
      if (!controller.signal.aborted) setError('Product search failed. Type again to retry.')
    } finally { if (!controller.signal.aborted) setSearching(false) }
  }
  return <>
    <Select aria-label="添加产品 Add product" style={{ width: '100%' }} showSearch filterOption={false} loading={searching} disabled={disabled} value={null}
      placeholder="搜索 记账名 / SKU，选中即加入 Search to add a product" onSearch={find}
      onChange={(id: number) => { const product = options.find(item => item.id === id); if (product && !unreceivable(product)) onPick(product) }}
      options={options.map(item => { const reason = unreceivable(item); return { value: item.id, label: reason ? `${productLabel(item)} · ${reason}` : productLabel(item), disabled: !!reason } })} />
    {error && <Alert role="alert" type="error" showIcon message={error} />}
  </>
}
