import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, Button, Card, InputNumber, Space, Switch, TimePicker, Typography, message } from 'antd'
import dayjs from 'dayjs'

import client from '../../api/client'
import { getWatch, type WatchStatus } from '../../api/watch'
import { statusLine } from '../Watch/watchView'

type Values = { enabled: boolean; claude: boolean; cap: number; time: dayjs.Dayjs }

// 异常提醒 Watcher (owner, 2026-11-24): its switch, the nightly Claude review's switch, the review time and the most
// the review may cost in a day. Admins only, as all of 设置.
export default function WatcherSettings() {
  const [values, setValues] = useState<Values>()
  const [status, setStatus] = useState<WatchStatus>()
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)

  function load() {
    setFailed(false)
    client.get<Record<string, string>>('/settings').then(({ data }) => setValues({
      enabled: data.watcher_enabled === '1', claude: data.watcher_claude !== '0',
      cap: Number(data.watcher_daily_cap_usd) || 0, time: dayjs(data.watcher_review_time || '07:00', 'HH:mm'),
    })).catch(() => setFailed(true))
    getWatch().then(view => setStatus(view.status)).catch(() => undefined)
  }
  useEffect(load, [])

  async function save() {
    if (!values) return
    setSaving(true)
    try {
      await client.put('/settings', {
        watcher_enabled: values.enabled ? '1' : '0', watcher_claude: values.claude ? '1' : '0',
        watcher_daily_cap_usd: values.cap.toFixed(2), watcher_review_time: values.time.format('HH:mm'),
      })
      message.success('设置已保存 / Settings saved')
      load()
    } catch (cause: any) {
      message.error(cause?._serverMessage ?? '保存失败 / Unable to save')
    } finally { setSaving(false) }
  }

  const set = (change: Partial<Values>) => setValues(value => value && { ...value, ...change })
  return <Card loading={!values && !failed}>
    {failed && <Alert type="error" showIcon message="设置加载失败 / Settings could not be loaded" action={<Button onClick={load}>重试 / Retry</Button>} />}
    {values && <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        POPCORE 每小时自己检查：损耗（被偷、丢失）、盘点少 10 个以上或多 30 个以上、日报现金差 $20 以上、一份日报 5 条以上库存差异、
        网站或图鉴同步出错一小时。提醒给管理员和该店经理；要紧的马上推送到手机，推送只说有几条。
        <br />POPCORE checks every hour by itself: losses, count gaps (10+ short or 30+ over), 日报 cash gaps over $20,
        5+ stock notices from one report and a sync failing for an hour. Admins and that store's managers are told;
        urgent ones are pushed at once, and a push says only how many.
      </Typography.Paragraph>
      <label className="pc-settings-switch"><Switch checked={values.enabled} onChange={enabled => set({ enabled })} />
        <span>异常提醒 开启 · Watcher on</span></label>
      <label className="pc-settings-switch"><Switch checked={values.claude} disabled={!values.enabled} onChange={claude => set({ claude })} />
        <span>每晚 Claude 检查 · Nightly Claude review</span></label>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        Claude 只看到代号（S1 门店、P3 产品）和数字，看不到店名、产品名、员工或备注；POPCORE 自己把名字放回去。
        <br />Claude sees only stand-in codes (S1 a store, P3 a product) and numbers, never a store, product or staff name or a
        note; POPCORE puts the names back itself.
      </Typography.Paragraph>
      {status && !status.has_key && values.claude && <Alert type="info" showIcon
        message="服务器没有 ANTHROPIC_API_KEY：只用规则，没有 Claude 的说明 · No Claude key on the server: rules only, no Claude notes" />}
      <Space wrap>
        <span>每晚检查时间 Review time</span>
        <TimePicker format="HH:mm" value={values.time} allowClear={false} disabled={!values.enabled || !values.claude}
          onChange={time => time && set({ time })} />
      </Space>
      <Space wrap>
        <span>每日花费上限 Daily cap</span>
        <InputNumber min={0} max={5} step={0.1} precision={2} prefix="US$" value={values.cap} disabled={!values.enabled || !values.claude}
          onChange={cap => set({ cap: Number(cap) || 0 })} style={{ width: 140 }} />
      </Space>
      {status && <Typography.Text type="secondary">{statusLine(status)}</Typography.Text>}
      <Space wrap>
        <Button type="primary" loading={saving} onClick={save}>保存 / Save</Button>
        <Link to="/watch">查看提醒 Open alerts</Link>
      </Space>
    </Space>}
  </Card>
}
