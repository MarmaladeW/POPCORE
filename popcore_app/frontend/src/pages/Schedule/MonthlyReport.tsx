import { useState, useEffect, useCallback } from 'react'
import { DatePicker, Table, Typography, Tooltip, Spin, Alert, InputNumber, Button, message } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
import { getMonthlyReport, type EmployeeMonthlyHours, type MonthlyReport as MonthlyReportData } from './scheduleApi'
import { useAppStore } from '../../store'
import { useHasRole } from '../../auth/useRole'
import client from '../../api/client'

dayjs.extend(isoWeek)

const { Text } = Typography

function weeksInRange(start: string, end: string): string[] {
  // Return ISO week keys (YYYY-Www) touched by the period start..end
  const seen = new Set<string>()
  let d = dayjs(start)
  const last = dayjs(end)
  while (d.isBefore(last) || d.isSame(last, 'day')) {
    seen.add(`${d.isoWeekYear()}-W${String(d.isoWeek()).padStart(2, '0')}`)
    d = d.add(1, 'day')
  }
  return Array.from(seen).sort()
}

export default function MonthlyReport() {
  const [month, setMonth] = useState<Dayjs>(dayjs())
  const [report, setReport] = useState<MonthlyReportData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [startDayDraft, setStartDayDraft] = useState<number | null>(null)
  const [savingStartDay, setSavingStartDay] = useState(false)
  const { selectedStore } = useAppStore()
  const isAdmin = useHasRole('admin')
  const [msgApi, msgCtx] = message.useMessage()

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    getMonthlyReport(month.year(), month.month() + 1, selectedStore?.code)
      .then((r) => { setReport(r); setLoading(false) })
      .catch(() => { setError('Failed to load report'); setLoading(false) })
  }, [month, selectedStore?.code])

  useEffect(() => { load() }, [load])

  const data: EmployeeMonthlyHours[] = report?.employees ?? []
  const weeks = report ? weeksInRange(report.period_start, report.period_end) : []

  const saveStartDay = async () => {
    if (startDayDraft == null || !report) return
    setSavingStartDay(true)
    try {
      await client.put('/settings', { schedule_month_start_day: String(startDayDraft) })
      msgApi.success(`Month now runs from day ${startDayDraft} to day ${startDayDraft - 1 || 'end'} of the next month`)
      setStartDayDraft(null)
      load()
    } catch {
      msgApi.error('Failed to save (admin only)')
    } finally {
      setSavingStartDay(false)
    }
  }

  const columns = [
    {
      title: 'Employee',
      dataIndex: 'name',
      key: 'name',
      fixed: 'left' as const,
      width: 160,
      render: (v: string, row: EmployeeMonthlyHours) => v || row.email || `ID ${row.id}`,
    },
    ...weeks.map((wk) => ({
      title: (
        <Tooltip title={`ISO week ${wk}`}>
          <span>{wk.replace(/^\d{4}-/, '')}</span>
        </Tooltip>
      ),
      key: wk,
      width: 80,
      align: 'center' as const,
      render: (_: unknown, row: EmployeeMonthlyHours) => {
        const weekData = row.weeks[wk]
        if (!weekData) return <Text type="secondary">—</Text>
        const dayList = Object.entries(weekData.days)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([date, h]) => `${date}: ${h}h`)
          .join('\n')
        return (
          <Tooltip title={<pre style={{ margin: 0, fontSize: 12 }}>{dayList}</pre>}>
            <span>{weekData.total}h</span>
          </Tooltip>
        )
      },
    })),
    {
      title: 'Total',
      key: 'total',
      width: 90,
      align: 'center' as const,
      fixed: 'right' as const,
      render: (_: unknown, row: EmployeeMonthlyHours) => (
        <Text strong>{row.total_hours}h</Text>
      ),
    },
  ]

  return (
    <div>
      {msgCtx}
      <div className="flex items-center gap-3 mb-2 flex-wrap">
        <h2 className="text-sm font-semibold m-0">Monthly hours report</h2>
        <DatePicker
          picker="month"
          value={month}
          onChange={(v) => v && setMonth(v)}
          allowClear={false}
        />
        {report && (
          <Text type="secondary">
            Pay period: {dayjs(report.period_start).format('MMM D')} – {dayjs(report.period_end).format('MMM D, YYYY')}
          </Text>
        )}
      </div>

      <div className="flex items-center gap-2 mb-4 flex-wrap text-sm">
        {isAdmin ? (
          <>
            <Text type="secondary">Month starts on day</Text>
            <InputNumber
              min={1}
              max={28}
              size="small"
              value={startDayDraft ?? report?.month_start_day ?? 4}
              onChange={(v) => setStartDayDraft(typeof v === 'number' ? v : null)}
            />
            {startDayDraft != null && startDayDraft !== report?.month_start_day && (
              <Button size="small" type="primary" loading={savingStartDay} onClick={saveStartDay}>
                Save
              </Button>
            )}
          </>
        ) : (
          report && (
            <Text type="secondary">
              Month starts on day {report.month_start_day} (admin-configurable)
            </Text>
          )
        )}
      </div>

      {error && <Alert type="error" message={error} style={{ marginBottom: 12 }} />}

      <Spin spinning={loading}>
        <Table
          dataSource={data}
          columns={columns}
          rowKey="id"
          scroll={{ x: 'max-content' }}
          pagination={false}
          size="small"
          summary={(rows) => {
            const totalAll = rows.reduce((s, r) => s + r.total_hours, 0)
            return (
              <Table.Summary.Row>
                <Table.Summary.Cell index={0}>
                  <Text strong>Team total</Text>
                </Table.Summary.Cell>
                {weeks.map((wk, i) => {
                  const wkTotal = rows.reduce((s, r) => s + (r.weeks[wk]?.total ?? 0), 0)
                  return (
                    <Table.Summary.Cell key={wk} index={i + 1} align="center">
                      <Text strong>{wkTotal > 0 ? `${Math.round(wkTotal * 10) / 10}h` : '—'}</Text>
                    </Table.Summary.Cell>
                  )
                })}
                <Table.Summary.Cell index={weeks.length + 1} align="center">
                  <Text strong>{Math.round(totalAll * 10) / 10}h</Text>
                </Table.Summary.Cell>
              </Table.Summary.Row>
            )
          }}
        />
      </Spin>
    </div>
  )
}
