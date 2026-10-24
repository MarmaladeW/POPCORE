import { Link } from 'react-router-dom'
import Icon from '../../components/pc/Icon'

// Figma Home: 买单 as the large dark tile, then 入店 / 库存 / 汇总.
export default function HomeTiles({ details = false }: { details?: boolean }) {
  return <nav className="pc-hm-tiles" aria-label="Store actions">
    <Link className="pc-hm-hero" to="/checkout">
      <span className="pc-hm-hero-icon"><Icon name="pay" size={20} /></span>
      <span className="pc-hm-hero-text"><strong>买单</strong><span>Checkout</span><small>订单 · 折扣 · 收款凭证</small></span>
      <Icon name="right" size={18} className="pc-hm-hero-arrow" />
    </Link>
    <Link className="pc-hm-tile" to="/incoming"><Icon name="in" size={18} /><span><strong>入店</strong><span>Receive goods</span>{details && <small>收货 · 调入 · Display</small>}</span></Link>
    <Link className="pc-hm-tile" to="/stock"><Icon name="box" size={18} /><span><strong>库存</strong><span>Inventory</span>{details && <small>查库存 · 移到楼面 · 晚盘</small>}</span></Link>
    <Link className="pc-hm-tile" to="/summary"><Icon name="doc" size={18} /><span><strong>汇总</strong><span>Daily summary</span>{details && <small>当日记录 · 对账 · 历史汇总</small>}</span></Link>
  </nav>
}
