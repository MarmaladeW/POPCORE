import { Tag } from 'antd'

// Without 库存查看 at the store, 补货 shows only whether upstairs is out of stock (owner, 2026-11-01).
export default function HiddenStock({ out }: { out?: boolean }) {
  return <Tag color={out ? 'red' : 'blue'}>{out ? '缺货 Out of stock' : '有货 In stock'}</Tag>
}
