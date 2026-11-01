import { Link } from 'react-router-dom'
import Icon from '../../components/pc/Icon'
import { useInventoryVisible } from '../../auth/useInventoryVisible'

// Figma Home: 买单 as the large dark tile, then 入店 / 库存 / 汇总. Without 库存查看 (owner,
// 2026-11-01) 入店 and 库存 give way to 晚盘 and 补货.
export default function HomeTiles({ details = false }: { details?: boolean }) {
  const inventory = useInventoryVisible()
  return <nav className="pc-hm-tiles" aria-label="Store actions">
    <Link className="pc-hm-hero" to="/checkout">
      <span className="pc-hm-hero-icon"><Icon name="pay" size={20} /></span>
      <span className="pc-hm-hero-text"><strong>买单</strong><span>Checkout</span><small>订单 · 折扣 · 收款凭证</small></span>
      <Icon name="right" size={18} className="pc-hm-hero-arrow" />
    </Link>
    {inventory ? <>
      <Link className="pc-hm-tile" to="/incoming"><Icon name="in" size={18} /><span><strong>入店</strong><span>Receive goods</span>{details && <small>收货 · 调入 · Display</small>}</span></Link>
      <Link className="pc-hm-tile" to="/stock"><Icon name="box" size={18} /><span><strong>库存</strong><span>Inventory</span>{details && <small>查库存 · 移到楼面 · 晚盘</small>}</span></Link>
    </> : <>
      <Link className="pc-hm-tile" to="/stock/count"><Icon name="count" size={18} /><span><strong>晚盘</strong><span>Evening count</span>{details && <small>今晚清单 · 记录</small>}</span></Link>
      <Link className="pc-hm-tile" to="/restock"><Icon name="swap" size={18} /><span><strong>补货</strong><span>Restock</span>{details && <small>楼上拿货到楼面</small>}</span></Link>
    </>}
    <Link className="pc-hm-tile" to="/summary"><Icon name="doc" size={18} /><span><strong>汇总</strong><span>Daily summary</span>{details && <small>当日记录 · 对账 · 历史汇总</small>}</span></Link>
  </nav>
}
