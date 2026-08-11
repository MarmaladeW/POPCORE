import { useState, useEffect } from 'react'
import {
  Card, Tabs, TimePicker, InputNumber, Select, Input, Button,
  message, Typography, Space, Table, Popconfirm,
} from 'antd'
import type { TabsProps } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'
import { useHasRole } from '../../auth/useRole'
import client from '../../api/client'
import UsersPage from '../Users'
import {
  DEFAULT_OPEN_HOURS, parseOpenHours, parseStaffRequirements,
  type OpenHoursConfig, type StaffRequirements,
} from '../Schedule/openHours'

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
  schedule_month_start_day?:    string
  schedule_required_staff?:     string
  schedule_open_hours?:         string
}

interface StoreRow {
  id:    number
  code:  string
  name:  string
  color: string
}

const ROW   = { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 } as const
const LABEL: React.CSSProperties = { minWidth: 240, color: '#374151', fontSize: 14, flexShrink: 0 }
const INNER = { maxWidth: 680 } as const

export default function SettingsPage() {
  const isAdmin  = useHasRole('admin')
  const navigate = useNavigate()

  // ── Settings load ────────────────────────────────────────────────────────
  const [settingsLoading, setSettingsLoading] = useState(true)
  const [saving1, setSaving1] = useState(false)
  const [saving2, setSaving2] = useState(false)

  // ── Card 1: Insight settings ─────────────────────────────────────────────
  const [insightTime,   setInsightTime]   = useState<dayjs.Dayjs>(dayjs('02:00', 'HH:mm'))
  const [highPrice,     setHighPrice]     = useState<number>(100)
  const [deadStockDays, setDeadStockDays] = useState<number>(14)
  const [stockoutDays,  setStockoutDays]  = useState<number>(7)
  const [velocityRatio, setVelocityRatio] = useState<number>(2.0)

  // ── Card 2: Report schedule ───────────────────────────────────────────────
  const [weeklyDay,     setWeeklyDay]     = useState<string>('Monday')
  const [weeklyTime,    setWeeklyTime]    = useState<dayjs.Dayjs>(dayjs('08:00', 'HH:mm'))
  const [monthlyDay,    setMonthlyDay]    = useState<number>(1)
  const [monthlyTime,   setMonthlyTime]   = useState<dayjs.Dayjs>(dayjs('08:00', 'HH:mm'))
  const [quarterlyTime, setQuarterlyTime] = useState<dayjs.Dayjs>(dayjs('08:00', 'HH:mm'))

  // ── Scheduling tab ────────────────────────────────────────────────────────
  const [staffReqs,     setStaffReqs]     = useState<StaffRequirements>({})
  const [openHours,     setOpenHours]     = useState<OpenHoursConfig>(DEFAULT_OPEN_HOURS)
  const [monthStartDay, setMonthStartDay] = useState<number>(4)
  const [saving3,       setSaving3]       = useState(false)

  // ── Stores tab ────────────────────────────────────────────────────────────
  const [stores,        setStores]        = useState<StoreRow[]>([])
  const [storesLoading, setStoresLoading] = useState(false)
  const [newCode,       setNewCode]       = useState('')
  const [newName,       setNewName]       = useState('')
  const [adding,        setAdding]        = useState(false)

  useEffect(() => {
    if (!isAdmin) { navigate('/'); return }
    client.get<RawSettings>('/settings')
      .then(r => {
        const s = r.data
        setInsightTime(dayjs(s.insight_generate_time || '02:00', 'HH:mm'))
        setHighPrice(Number(s.insight_high_price_threshold) || 100)
        setDeadStockDays(Number(s.insight_dead_stock_days)  || 14)
        setStockoutDays(Number(s.insight_stockout_days)     || 7)
        setVelocityRatio(Number(s.insight_velocity_ratio)   || 2.0)
        setWeeklyDay(s.report_weekly_day || 'Monday')
        setWeeklyTime(dayjs(s.report_weekly_time     || '08:00', 'HH:mm'))
        setMonthlyDay(Number(s.report_monthly_day)   || 1)
        setMonthlyTime(dayjs(s.report_monthly_time   || '08:00', 'HH:mm'))
        setQuarterlyTime(dayjs(s.report_quarterly_time || '08:00', 'HH:mm'))
        setStaffReqs(parseStaffRequirements(s.schedule_required_staff))
        setOpenHours(parseOpenHours(s.schedule_open_hours))
        setMonthStartDay(Number(s.schedule_month_start_day) || 4)
      })
      .catch(() => message.error('加载设置失败 / Failed to load settings'))
      .finally(() => setSettingsLoading(false))
    loadStores()
  }, [isAdmin, navigate]) // eslint-disable-line react-hooks/exhaustive-deps

  function loadStores() {
    setStoresLoading(true)
    client.get<StoreRow[]>('/stores')
      .then(r => setStores(r.data))
      .catch(() => message.error('加载门店失败 / Failed to load stores'))
      .finally(() => setStoresLoading(false))
  }

  async function addStore() {
    const code = newCode.trim().toUpperCase()
    const name = newName.trim()
    if (!code || !name) { message.warning('请填写门店代码和名称 / Enter code and name'); return }
    setAdding(true)
    try {
      await client.post('/stores', { code, name })
      setNewCode('')
      setNewName('')
      loadStores()
      message.success('门店已添加 / Store added')
    } catch (err: any) {
      message.error(err?._serverMessage ?? '添加失败 / Add failed')
    } finally {
      setAdding(false)
    }
  }

  async function deleteStore(storeId: number) {
    try {
      await client.delete(`/stores/${storeId}`)
      loadStores()
      message.success('门店已删除 / Store deleted')
    } catch (err: any) {
      message.error(err?._serverMessage ?? '删除失败 / Delete failed')
    }
  }

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
        report_weekly_day:     weeklyDay,
        report_weekly_time:    weeklyTime.format('HH:mm'),
        report_monthly_day:    String(monthlyDay),
        report_monthly_time:   monthlyTime.format('HH:mm'),
        report_quarterly_time: quarterlyTime.format('HH:mm'),
      })
      message.success('设置已保存 / Settings saved')
    } catch {
      message.error('保存失败 / Save failed')
    } finally {
      setSaving2(false)
    }
  }

  function setOpenHour(dayType: 'weekday' | 'weekend', field: 'open' | 'close', v: dayjs.Dayjs | null) {
    if (!v) return
    setOpenHours(prev => ({
      ...prev,
      [dayType]: { ...prev[dayType], [field]: v.format('HH:mm') },
    }))
  }

  async function changeStoreColor(storeId: number, color: string) {
    setStores(prev => prev.map(s => s.id === storeId ? { ...s, color } : s))
    try {
      await client.patch(`/stores/${storeId}/color`, { color })
    } catch (err: any) {
      message.error(err?._serverMessage ?? '颜色更新失败 / Color update failed')
      loadStores()
    }
  }

  function setStaffReq(code: string, field: 'weekday' | 'weekend', v: number | null) {
    setStaffReqs(prev => ({
      ...prev,
      [code]: {
        weekday: prev[code]?.weekday ?? 1,
        weekend: prev[code]?.weekend ?? 1,
        [field]: v ?? 1,
      },
    }))
  }

  async function saveScheduling() {
    setSaving3(true)
    try {
      const payload: StaffRequirements = {}
      stores.forEach(st => {
        payload[st.code] = {
          weekday: staffReqs[st.code]?.weekday ?? 1,
          weekend: staffReqs[st.code]?.weekend ?? 1,
        }
      })
      await client.put('/settings', {
        schedule_required_staff:  JSON.stringify(payload),
        schedule_open_hours:      JSON.stringify(openHours),
        schedule_month_start_day: String(monthStartDay),
      })
      message.success('设置已保存 / Settings saved')
    } catch (err: any) {
      message.error(err?._serverMessage ?? '保存失败 / Save failed')
    } finally {
      setSaving3(false)
    }
  }

  if (!isAdmin) return null

  // ── Store table columns ───────────────────────────────────────────────────
  const storeCols: ColumnsType<StoreRow> = [
    {
      title: '代码 / Code',
      dataIndex: 'code',
      width: 100,
      render: (v: string) => (
        <span style={{ fontWeight: 600, fontFamily: 'monospace', fontSize: 13 }}>{v}</span>
      ),
    },
    {
      title: '名称 / Name',
      dataIndex: 'name',
    },
    {
      title: '颜色 / Color',
      key: 'color',
      width: 90,
      align: 'center' as const,
      render: (_: unknown, row: StoreRow) => (
        <input
          type="color"
          value={row.color || '#6366f1'}
          onChange={e => changeStoreColor(row.id, e.target.value)}
          title={`更改 ${row.code} 的颜色 / Change color for ${row.code}`}
          style={{
            width: 26, height: 26, padding: 1,
            borderRadius: 6, border: '1px solid #e5e7eb',
            cursor: 'pointer', background: 'none',
          }}
        />
      ),
    },
    {
      title: '操作 / Actions',
      key: 'actions',
      width: 100,
      render: (_: unknown, row: StoreRow) => (
        <Popconfirm
          title={`删除门店 ${row.code}？/ Delete store ${row.code}?`}
          description="此操作无法撤销。/ This cannot be undone."
          onConfirm={() => deleteStore(row.id)}
          okText="删除 / Delete"
          okButtonProps={{ danger: true }}
          cancelText="取消 / Cancel"
        >
          <Button size="small" danger>删除 / Delete</Button>
        </Popconfirm>
      ),
    },
  ]

  // ── Tab content ───────────────────────────────────────────────────────────
  const insightTab = (
    <div style={INNER}>
      <Card loading={settingsLoading}>
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

        {/* FIX 1: narrow InputNumber + suffix as plain text */}
        <div style={ROW}>
          <span style={LABEL}>高价商品排除阈值 / High-price Threshold</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <InputNumber
              value={highPrice}
              onChange={v => setHighPrice(v ?? 100)}
              min={0}
              prefix="CA$"
              style={{ width: 120 }}
            />
            <Text style={{ color: '#6b7280', fontSize: 13 }}>
              以上商品不纳入洞察 / and above excluded
            </Text>
          </div>
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
    </div>
  )

  const reportTab = (
    <div style={INNER}>
      <Card loading={settingsLoading}>
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
    </div>
  )

  const schedulingTab = (
    <div style={INNER}>
      <Card loading={settingsLoading || storesLoading}>
        <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4, color: '#374151' }}>
          门店最低排班人数 / Minimum staff on shift
        </div>
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 16 }}>
          排班日历中人数不足的营业时间会标红 / Opening hours scheduled below these numbers show red in the schedule
        </Text>

        {stores.map(st => (
          <div key={st.id} style={ROW}>
            <span style={LABEL}>{st.name || st.code}（{st.code}）</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <InputNumber
                value={staffReqs[st.code]?.weekday ?? 1}
                onChange={v => setStaffReq(st.code, 'weekday', v)}
                min={0}
                max={20}
                addonBefore="周中 / weekday"
                style={{ width: 190 }}
              />
              <InputNumber
                value={staffReqs[st.code]?.weekend ?? 1}
                onChange={v => setStaffReq(st.code, 'weekend', v)}
                min={0}
                max={20}
                addonBefore="周末 / weekend"
                style={{ width: 190 }}
              />
            </div>
          </div>
        ))}

        <div style={{ fontWeight: 600, fontSize: 14, marginTop: 24, marginBottom: 4, color: '#374151' }}>
          营业时间 / Opening hours
        </div>
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 16 }}>
          用于排班日历的灰色区域与班次预设 / Drives the calendar shading and shift presets
        </Text>

        <div style={ROW}>
          <span style={LABEL}>周一至周五 / Monday–Friday</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <TimePicker
              value={dayjs(openHours.weekday.open, 'HH:mm')}
              onChange={v => setOpenHour('weekday', 'open', v)}
              format="HH:mm" minuteStep={30} allowClear={false}
            />
            <span style={{ color: '#9ca3af' }}>–</span>
            <TimePicker
              value={dayjs(openHours.weekday.close, 'HH:mm')}
              onChange={v => setOpenHour('weekday', 'close', v)}
              format="HH:mm" minuteStep={30} allowClear={false}
            />
          </div>
        </div>

        <div style={ROW}>
          <span style={LABEL}>周六周日 / Saturday–Sunday</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <TimePicker
              value={dayjs(openHours.weekend.open, 'HH:mm')}
              onChange={v => setOpenHour('weekend', 'open', v)}
              format="HH:mm" minuteStep={30} allowClear={false}
            />
            <span style={{ color: '#9ca3af' }}>–</span>
            <TimePicker
              value={dayjs(openHours.weekend.close, 'HH:mm')}
              onChange={v => setOpenHour('weekend', 'close', v)}
              format="HH:mm" minuteStep={30} allowClear={false}
            />
          </div>
        </div>

        <div style={{ ...ROW, marginTop: 24, marginBottom: 20 }}>
          <span style={LABEL}>工资月起始日 / Wage month starts on day</span>
          <InputNumber
            value={monthStartDay}
            onChange={v => setMonthStartDay(v ?? 4)}
            min={1}
            max={28}
            addonAfter="日 / of month"
          />
        </div>

        <Button type="primary" onClick={saveScheduling} loading={saving3}>
          保存 / Save
        </Button>
      </Card>
    </div>
  )

  const storesTab = (
    <div style={INNER}>
      <Card>
        <Table<StoreRow>
          rowKey="id"
          size="small"
          loading={storesLoading}
          dataSource={stores}
          columns={storeCols}
          pagination={false}
          style={{ marginBottom: 24 }}
        />

        {/* Add store form */}
        <div style={{ borderTop: '1px solid #f3f4f6', paddingTop: 16 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12, color: '#374151' }}>
            添加门店 / Add Store
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Input
              placeholder="代码 / Code (e.g. MK)"
              value={newCode}
              onChange={e => setNewCode(e.target.value.toUpperCase())}
              style={{ width: 160 }}
              maxLength={10}
              onPressEnter={addStore}
            />
            <Input
              placeholder="名称 / Name (e.g. Markham)"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              style={{ width: 220 }}
              maxLength={64}
              onPressEnter={addStore}
            />
            <Button
              type="primary"
              onClick={addStore}
              loading={adding}
            >
              添加 / Add
            </Button>
          </div>
        </div>
      </Card>
    </div>
  )

  const tabs: TabsProps['items'] = [
    { key: 'insights',   label: '洞察设置 / Insights',   children: insightTab    },
    { key: 'reports',    label: '报表计划 / Reports',    children: reportTab     },
    { key: 'scheduling', label: '排班设置 / Scheduling', children: schedulingTab },
    { key: 'stores',     label: '门店管理 / Stores',     children: storesTab     },
    { key: 'users',      label: '用户管理 / Users',      children: <UsersPage /> },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 20, fontWeight: 700, margin: 0, color: '#111827' }}>
          系统设置 / Settings
        </h2>
      </div>
      <Tabs items={tabs} />
    </div>
  )
}
