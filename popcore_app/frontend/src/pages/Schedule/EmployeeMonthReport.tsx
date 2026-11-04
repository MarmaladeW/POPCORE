import { Fragment, useCallback, useEffect, useState } from 'react'
import { Alert } from 'antd'
import dayjs from 'dayjs'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { getEmployeeMonthDetail, type EmployeeMonthDetail } from './scheduleApi'
import {
  countedRange, formatHours, isRegularOn, monthDay, regularSplit, shortDay, weeksOfPeriod,
} from './monthlyDetail'
import RegularMark from './RegularMark'

/** One employee's month, shift by shift and week by week, laid out to be screenshotted and sent
 *  to them. Same wage period and store scope as the monthly report row it was opened from. */
export default function EmployeeMonthReport({ employeeId, year, month, storeCode, storeName, onBack, onPrevious, onNext }: {
  employeeId: number
  year: number
  month: number
  storeCode: string
  storeName?: string
  onBack: () => void
  onPrevious?: () => void
  onNext?: () => void
}) {
  const [detail, setDetail] = useState<EmployeeMonthDetail | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    let current = true
    setDetail(null)
    setError('')
    getEmployeeMonthDetail(employeeId, year, month, storeCode)
      .then(data => { if (current) setDetail(data) })
      .catch((err: any) => { if (current) setError(err?.response?.data?.error ?? 'Could not load these hours') })
    return () => { current = false }
  }, [employeeId, year, month, storeCode])
  useEffect(() => load(), [load])

  const toolbar = <div className="pc-slip-toolbar">
    <button type="button" className="pc-slip-back" onClick={onBack}>
      <ChevronLeft className="h-4 w-4" aria-hidden="true" />月工时 Monthly report
    </button>
    <div className="pc-slip-step">
      <Button variant="outline" disabled={!onPrevious} onClick={onPrevious} aria-label="上一位 Previous person">
        <ChevronLeft className="h-4 w-4" aria-hidden="true" />上一位
      </Button>
      <Button variant="outline" disabled={!onNext} onClick={onNext} aria-label="下一位 Next person">
        下一位<ChevronRight className="h-4 w-4" aria-hidden="true" />
      </Button>
    </div>
  </div>

  if (error) return <div className="pc-slip-page">
    {toolbar}
    <Alert type="error" showIcon message={error} action={<Button variant="outline" onClick={() => load()}>Retry</Button>} />
  </div>
  if (!detail) return <div className="pc-slip-page">
    {toolbar}
    <div className="pc-slip pc-slip-loading" role="status" aria-label="Loading hours"><b /><i /><i /><i /></div>
  </div>

  const { employee, shifts } = detail
  const name = employee.name || employee.email || `Employee ${employee.id}`
  const weeks = weeksOfPeriod(detail.period_start, detail.period_end, shifts)
  const split = regularSplit(employee.regular_since, detail.period_start, detail.period_end, shifts)
  const regularByEnd = isRegularOn(employee.regular_since, detail.period_end)
  const allStores = detail.store_code === 'ALL'
  const columns = allStores ? 4 : 3
  const anchor = dayjs(`${detail.month}-01`)
  // The 转正 line sits before the first shift on or after the date, when that date is inside the period.
  const boundaryShift = split ? shifts.find(shift => isRegularOn(employee.regular_since, shift.date)) : undefined

  return <div className="pc-slip-page">
    {toolbar}
    <article className="pc-slip" aria-label={`${name} ${detail.month} hours`}>
      <header className="pc-slip-head">
        <div className="pc-slip-who">
          <h2>
            <span className="pc-slip-dot" style={{ background: employee.color }} aria-hidden="true" />
            {name}
            {regularByEnd && <RegularMark since={employee.regular_since} />}
            {!!employee.is_trainee && <span className="pc-slip-tag">实习生 Trainee</span>}
          </h2>
          <p>{anchor.format('YYYY年M月')}工时 · {anchor.format('MMMM YYYY')} hours</p>
          <p>{monthDay(detail.period_start)} – {monthDay(detail.period_end)} · {allStores ? '全部门店 All stores' : `${detail.store_code}${storeName && storeName !== detail.store_code ? ` ${storeName}` : ''}`}</p>
        </div>
        <div className="pc-slip-total">
          <strong>{formatHours(detail.total_hours)}<small> h</small></strong>
          <span>{shifts.length} 个班 · {shifts.length} shift{shifts.length === 1 ? '' : 's'}</span>
        </div>
      </header>
      {employee.regular_since && regularByEnd && <p className="pc-slip-regular">
        <RegularMark since={employee.regular_since} />
        {split
          ? <>{monthDay(employee.regular_since)} 转正 · 转正前 Before <b>{formatHours(split.before)} h</b> · 转正后 After <b>{formatHours(split.after)} h</b></>
          : <>{employee.regular_since} 起转正 · Regular since {employee.regular_since}</>}
      </p>}

      <table className="pc-slip-table">
        <thead><tr>
          <th scope="col">日期 Date</th>
          {allStores && <th scope="col">门店 Store</th>}
          <th scope="col">时间 Time</th>
          <th scope="col" className="pc-slip-num">时数 Hours</th>
        </tr></thead>
        {weeks.map((week, index) => <tbody key={week.start}>
          <tr className="pc-slip-week">
            <th scope="rowgroup" colSpan={columns - 1}>第 {index + 1} 周 Week {index + 1}<span>{monthDay(week.start)}{week.end !== week.start ? ` – ${monthDay(week.end)}` : ''}</span></th>
            <td className="pc-slip-num">{formatHours(week.hours)} h</td>
          </tr>
          {week.shifts.length === 0 && <tr className="pc-slip-none"><td colSpan={columns}>本周没有班 · No shifts</td></tr>}
          {week.shifts.map(shift => {
            const late = !!shift.actual_end_time && shift.actual_end_time !== shift.end_time
            return <Fragment key={shift.id}>
              {shift === boundaryShift && <tr className="pc-slip-boundary"><td colSpan={columns}>
                <span><RegularMark since={employee.regular_since} />{monthDay(employee.regular_since!)} 起转正 · Regular from here</span>
              </td></tr>}
              <tr>
                <td>{shortDay(shift.date)}</td>
                {allStores && <td>{shift.store_code || '—'}</td>}
                <td>
                  {countedRange(shift)}
                  {late && <small>原定 Planned {shift.end_time}</small>}
                  {shift.position && <small>{shift.position}</small>}
                </td>
                <td className="pc-slip-num">{formatHours(shift.hours)}</td>
              </tr>
            </Fragment>
          })}
        </tbody>)}
        <tfoot><tr>
          <th scope="row" colSpan={columns - 1}>合计 Total<span>{shifts.length} 个班 shifts</span></th>
          <td className="pc-slip-num">{formatHours(detail.total_hours)} h</td>
        </tr></tfoot>
      </table>
      <p className="pc-slip-note">时数算到实际下班时间（有记录时）· Hours count to the recorded finish when there is one.</p>
    </article>
  </div>
}
