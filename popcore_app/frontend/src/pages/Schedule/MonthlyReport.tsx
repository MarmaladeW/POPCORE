import { useState, useEffect } from 'react'
import { DatePicker, Table, Typography, Tooltip, Spin, Alert } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
import { getMonthlyReport, type EmployeeMonthlyHours } from './scheduleApi'

dayjs.extend(isoWeek)

const { Title, Text } = Typography

function weeksInMonth(year: number, month: number): string[] {
  // Return ISO week keys that contain days in this month (YYYY-Www)
  const seen = new Set<string>()
  const days = dayjs(`${year}-${String(month).padStart(2, '0')}-01`)
  const total = days.daysInMonth()
  for (let d = 1; d <= total; d++) {
    const day = days.date(d)
    const wk  = `${day.isoWeekYear()}-W${String(day.isoWeek()).padStart(2, '0')}`
    seen.add(wk)
  }
  return Array.from(seen).sort()
}

export default function MonthlyReport() {
  const [month, setMonth] = useState<Dayjs>(dayjs())
  const [data, setData] = useState<EmployeeMonthlyHours[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    getMonthlyReport(month.year(), month.month() + 1)
      .then((r) => { setData(r.employees); setLoading(false) })
      .catch(() => { setError('Failed to load report'); setLoading(false) })
  }, [month])

  const weeks = weeksInMonth(month.year(), month.month() + 1)

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
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <Title level={5} style={{ margin: 0 }}>Monthly hours report</Title>
        <DatePicker
          picker="month"
          value={month}
          onChange={(v) => v && setMonth(v)}
          allowClear={false}
        />
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
