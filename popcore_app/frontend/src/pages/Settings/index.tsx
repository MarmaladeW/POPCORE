import { useState, useEffect } from 'react'
import {
  Card, TimePicker, InputNumber, Select, Input, Button,
  message, Typography, Space,
} from 'antd'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'
import { useHasRole } from '../../auth/useRole'
import client from '../../api/client'

const { Text } = Typography

const DAYS_OF_WEEK = [
  { value: 'Monday',    label: '周一 / Monday'    },
  { value: 'Tuesday',   label: '周二 / Tuesday'   },
  { value: 'Wednesday', label: '周三 / Wednesday' },
  { value: 'Thursday',  label: '周四 / Thursday'  },
  { value: 'Friday',    label: '周五 / Friday'    },
  { value: 'Saturday',  label: '周六 / Saturday'  },
  { value: 'Sunday',    label: '周日 / Sunday'    },
]

interface RawSettings {
  insight_generate_time:        string
  insight_high_price_threshold: string
  insight_dead_stock_days:      string
  insight_stockout_days:        string
  insight_velocity_ratio:       string
  report_weekly_day:            string
  report_weekly_time:           string
  report_monthly_day:           string
  report_monthly_time:          string
  report_quarterly_time:        string
  store_dt_name:                string
  store_mk_name:                string
}

const ROW = { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 } as const
const LABEL: React.CSSProperties = { minWidth: 240, color: '#374151', fontSize: 14, flexShrink: 0 }
const CARD: React.CSSProperties  = { marginBottom: 24 }

export default function SettingsPage() {
  const isAdmin  = useHasRole('admin')
  const navigate = useNavigate()

  const [pageLoading, setPageLoading] = useState(true)
  const [saving1, setSaving1] = useState(false)
  const [saving2, setSaving2] = useState(false)
  const [saving3, setSaving3] = useState(false)

  // ── Card 1: Insight settings ─────────────────────────────────────────────
  const [insightTime,      setInsightTime]      = useState<dayjs.Dayjs>(dayjs('02:00', 'HH:mm'))
  const [highPrice,        setHighPrice]        = useState<number>(100)
  const [deadStockDays,    setDeadStockDays]    = useState<number>(14)
  const [stockoutDays,     setStockoutDays]     = useState<number>(7)
  const [velocityRatio,    setVelocityRatio]    = useState<number>(2.0)

  // ── Card 2: Report schedule ───────────────────────────────────────────────
  const [weeklyDay,      setWeeklyDay]      = useState<string>('Monday')
  const [weeklyTime,     setWeeklyTime]     = useState<dayjs.Dayjs>(dayjs('08:00', 'HH:mm'))
  const [monthlyDay,     setMonthlyDay]     = useState<number>(1)
  const [monthlyTime,    setMonthlyTime]    = useState<dayjs.Dayjs>(dayjs('08:00', 'HH:mm'))
  const [quarterlyTime,  setQuarterlyTime]  = useState<dayjs.Dayjs>(dayjs('08:00', 'HH:mm'))

  // ── Card 3: Store names ───────────────────────────────────────────────────
  const [dtName, setDtName] = useState<string>('DT')
  const [mkName, setMkName] = useState<string>('MK')

  useEffect(() => {
    if (!isAdmin) { navigate('/'); return }
    client.get<RawSettings>('/settings')
      .then(r => {
        const s = r.data
        setInsightTime(dayjs(s.insight_generate_time || '02:00',   'HH:mm'))
        setHighPrice(Number(s.insight_high_price_threshold) || 100)
        setDeadStockDays(Number(s.insight_dead_stock_days)  || 14)
        setStockoutDays(Number(s.insight_stockout_days)     || 7)
        setVelocityRatio(Number(s.insight_velocity_ratio)   || 2.0)
        setWeeklyDay(s.report_weekly_day || 'Monday')
        setWeeklyTime(dayjs(s.report_weekly_time || '08:00',  'HH:mm'))
        setMonthlyDay(Number(s.report_monthly_day)   || 1)
        setMonthlyTime(dayjs(s.report_monthly_time || '08:00', 'HH:mm'))
        setQuarterlyTime(dayjs(s.report_quarterly_time || '08:00', 'HH:mm'))
        setDtName(s.store_dt_name || 'DT')
        setMkName(s.store_mk_name || 'MK')
      })
      .catch(() => message.error('加载设置失败 / Failed to load settings'))
      .finally(() => setPageLoading(false))
  }, [isAdmin, navigate]) // eslint-disable-line react-hooks/exhaustive-deps

  async function saveInsight() {
    setSaving1(true)
    try {
      await client.put('/settings', {
        insight_generate_time:        insightTime.format('HH:mm'),
        insight_high_price_threshold: String(highPrice),
        insight_dead_stock_days:      String(deadStockDays),
        insight_stockout_days:        String(stockoutDays),
        insight_velocity_ratio:       String(velocityRatio),
      })
      message.success('设置已保存 / Settings saved')
    } catch {
      message.error('保存失败 / Save failed')
    } finally {
      setSaving1(false)
    }
  }

  async function saveReport() {
    setSaving2(true)
    try {
      await client.put('/settings', {
        report_weekly_day:    weeklyDay,
        report_weekly_time:   weeklyTime.format('HH:mm'),
        report_monthly_day:   String(monthlyDay),
        report_monthly_time:  monthlyTime.format('HH:mm'),
        report_quarterly_time: quarterlyTime.format('HH:mm'),
      })
      message.success('设置已保存 / Settings saved')
    } catch {
      message.error('保存失败 / Save failed')
    } finally {
      setSaving2(false)
    }
  }

  async function saveStore() {
    setSaving3(true)
    try {
      await client.put('/settings', {
        store_dt_name: dtName,
        store_mk_name: mkName,
      })
      message.success('设置已保存 / Settings saved')
    } catch {
      message.error('保存失败 / Save failed')
    } finally {
      setSaving3(false)
    }
  }

  if (!isAdmin) return null

  return (
    <div style={{ maxWidth: 680 }}>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 20, fontWeight: 700, margin: 0, color: '#111827' }}>
          系统设置 / Settings
        </h2>
      </div>

      {/* ── Card 1: Daily Insight Settings ── */}
      <Card title="每日洞察设置 / Daily Insight Settings" style={CARD} loading={pageLoading}>
        <div style={ROW}>
          <span style={LABEL}>生成时间 / Generate Time</span>
          <TimePicker
            value={insightTime}
            onChange={v => v && setInsightTime(v)}
            format="HH:mm"
            minuteStep={15}
            allowClear={false}
          />
        </div>

        <div style={ROW}>
          <span style={LABEL}>高价商品排除阈值 / High-price Threshold</span>
          <InputNumber
            value={highPrice}
            onChange={v => setHighPrice(v ?? 100)}
            min={0}
            prefix="CA$"
            addonAfter="以上商品不纳入洞察 / and above excluded"
            style={{ width: 340 }}
          />
        </div>

        <div style={ROW}>
          <span style={LABEL}>滞销预警天数 / Dead Stock Threshold</span>
          <InputNumber
            value={deadStockDays}
            onChange={v => setDeadStockDays(v ?? 14)}
            min={1}
            addonAfter="天 / days"
          />
        </div>

        <div style={ROW}>
          <span style={LABEL}>断货预警天数 / Stockout Risk Threshold</span>
          <InputNumber
            value={stockoutDays}
            onChange={v => setStockoutDays(v ?? 7)}
            min={1}
            addonAfter="天 / days"
          />
        </div>

        <div style={{ ...ROW, marginBottom: 20 }}>
          <span style={LABEL}>销量异动倍率 / Velocity Spike Ratio</span>
          <InputNumber
            value={velocityRatio}
            onChange={v => setVelocityRatio(v ?? 2.0)}
            min={1.0}
            step={0.1}
            precision={1}
            addonAfter="x"
          />
        </div>

        <Button type="primary" onClick={saveInsight} loading={saving1}>
          保存 / Save
        </Button>
      </Card>

      {/* ── Card 2: Report Schedule ── */}
      <Card title="报表计划 / Report Schedule" style={CARD} loading={pageLoading}>
        <div style={ROW}>
          <span style={LABEL}>周报生成日 / Weekly Report Day</span>
          <Select
            value={weeklyDay}
            onChange={setWeeklyDay}
            options={DAYS_OF_WEEK}
            style={{ width: 210 }}
            getPopupContainer={t => t.parentElement!}
          />
        </div>

        <div style={ROW}>
          <span style={LABEL}>周报生成时间 / Weekly Report Time</span>
          <TimePicker
            value={weeklyTime}
            onChange={v => v && setWeeklyTime(v)}
            format="HH:mm"
            minuteStep={15}
            allowClear={false}
          />
        </div>

        <div style={ROW}>
          <span style={LABEL}>月报生成日 / Monthly Report Day</span>
          <InputNumber
            value={monthlyDay}
            onChange={v => setMonthlyDay(v ?? 1)}
            min={1}
            max={28}
            addonAfter="日 / day of month"
          />
        </div>

        <div style={ROW}>
          <span style={LABEL}>月报生成时间 / Monthly Report Time</span>
          <TimePicker
            value={monthlyTime}
            onChange={v => v && setMonthlyTime(v)}
            format="HH:mm"
            minuteStep={15}
            allowClear={false}
          />
        </div>

        <div style={{ ...ROW, marginBottom: 20 }}>
          <span style={LABEL}>季报生成时间 / Quarterly Report Time</span>
          <TimePicker
            value={quarterlyTime}
            onChange={v => v && setQuarterlyTime(v)}
            format="HH:mm"
            minuteStep={15}
            allowClear={false}
          />
        </div>

        <Space direction="vertical" size={8}>
          <Button type="primary" onClick={saveReport} loading={saving2}>
            保存 / Save
          </Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            报表功能即将上线 / Reports coming soon
          </Text>
        </Space>
      </Card>

      {/* ── Card 3: Store Names ── */}
      <Card title="门店名称 / Store Names" style={CARD} loading={pageLoading}>
        <div style={ROW}>
          <span style={LABEL}>DT 门店名称 / DT Store Name</span>
          <Input
            value={dtName}
            onChange={e => setDtName(e.target.value)}
            style={{ width: 200 }}
            maxLength={32}
          />
        </div>

        <div style={{ ...ROW, marginBottom: 20 }}>
          <span style={LABEL}>MK 门店名称 / MK Store Name</span>
          <Input
            value={mkName}
            onChange={e => setMkName(e.target.value)}
            style={{ width: 200 }}
            maxLength={32}
          />
        </div>

        <Button type="primary" onClick={saveStore} loading={saving3}>
          保存 / Save
        </Button>
      </Card>
    </div>
  )
}
