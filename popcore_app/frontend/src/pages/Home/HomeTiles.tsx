import { Link } from 'react-router-dom'
import Icon from '../../components/pc/Icon'
import { useInventoryVisible } from '../../auth/useInventoryVisible'
import { useCheckoutOpen } from '../../auth/useCheckoutOpen'

// Figma Home: the large dark tile, now 记一笔 (owner, 2026-11-21: it replaces 随手记; 买单 stays in the menu), then
// 移货 (owner, 2026-11-24: the one way to move goods, in place of 入店 and 补货) / 库存 / 汇总. Without 库存查看 (owner,
// 2026-11-01) 入店 and 库存 give way to 晚盘 and 补货. Staff see no 买单 tile until the store's Clover is connected
// (owner, 2026-11-20): its sales come from the 日报 until then.
export default function HomeTiles({ details = false }: { details?: boolean }) {
  const inventory = useInventoryVisible()
  const checkout = useCheckoutOpen()
  return <nav className="pc-hm-tiles" aria-label="Store actions">
    {checkout && <Link className="pc-hm-hero" to="/sales/quick">
      <span className="pc-hm-hero-icon"><Icon name="pay" size={20} /></span>
      <span className="pc-hm-hero-text"><strong>记一笔</strong><span>Record a sale</span><small>现金 · E-transfer · 微信 · 支付宝 · 刷卡</small></span>
      <Icon name="right" size={18} className="pc-hm-hero-arrow" />
    </Link>}
    <Link className="pc-hm-tile" to="/move"><Icon name="swap" size={18} /><span><strong>移货</strong><span>Move stock</span>{details && <small>补货 · 到货 · Display · 娃娃机</small>}</span></Link>
    {inventory
      ? <Link className="pc-hm-tile" to="/stock"><Icon name="box" size={18} /><span><strong>库存</strong><span>Inventory</span>{details && <small>查库存 · 盘点 · 晚盘</small>}</span></Link>
      : <Link className="pc-hm-tile" to="/stock/count"><Icon name="count" size={18} /><span><strong>晚盘</strong><span>Evening count</span>{details && <small>今晚清单 · 记录</small>}</span></Link>}
    <Link className="pc-hm-tile" to="/summary"><Icon name="doc" size={18} /><span><strong>汇总</strong><span>Daily summary</span>{details && <small>当日记录 · 对账 · 历史汇总</small>}</span></Link>
  </nav>
}
