import { Button } from '@/components/ui/button'
import type { Availability, Employee, Shift } from './scheduleApi'
import { availabilityIssue } from './availabilityPeriod'
import { EMPLOYEE_PALETTE, textColorOn } from '@/lib/palette'
import { hoursForStore, shiftKindFor, type StoreHoursMap } from './openHours'
import { shiftKindLabel } from './schedulePresentation'

interface Props {
  date: string
  storeCode: string
  employees: Employee[]
  availability: Availability[]
  shifts: Shift[]
  employeeColors: Record<number, string>
  storeHours: StoreHoursMap
  onAssign: (employeeId?: number) => void
  onEdit: (shift: Shift) => void
}

export default function ScheduleDayPanel({ date, storeCode, employees, availability, shifts, employeeColors, storeHours, onAssign, onEdit }: Props) {
  const assigned = shifts.filter(shift => shift.date === date && shift.store_code === storeCode)
  const responses = availability.filter(day => day.date === date && day.store_code === storeCode)
  const response = (employee: Employee) => responses.find(day => day.employee_id === employee.id)
  const candidates = employees.filter(employee => employee.is_schedulable !== 0 && !assigned.some(shift => shift.employee_id === employee.id))
  const available = candidates.filter(employee => {
    const day = response(employee)
    return day?.submitted_at && day.status !== 'unavailable'
  })
  const others = candidates.filter(employee => !available.includes(employee))
  const name = (employee: Employee) => employee.name || employee.email || 'Employee'
  const elsewhere = (employeeId: number) => shifts.find(shift => shift.date === date && shift.employee_id === employeeId && shift.store_code !== storeCode)
  const employeeName = (id: number, label: string) => {
    const color = employeeColors[id] ?? EMPLOYEE_PALETTE[0]
    return <strong className="pc-employee-name" style={{ background: color, color: textColorOn(color) }}>{label}</strong>
  }

  return <section className="pc-schedule-detail" aria-label={`Schedule details ${date} ${storeCode}`}>
    <div className="pc-page-actions"><h4>{date} · {storeCode}</h4><Button variant="outline" onClick={() => onAssign()}>Assign employee / trainee</Button></div>
    <div className="pc-schedule-people">
      <section><strong>Assigned · {assigned.length}</strong>
        {assigned.length === 0 && <p className="text-muted-foreground">No shifts assigned.</p>}
        {assigned.map(shift => {
          const day = responses.find(item => item.employee_id === shift.employee_id)
          const issue = availabilityIssue(day, storeCode, shift.start_time, shift.end_time)
          const kind = shiftKindFor(date, hoursForStore(storeCode, storeHours), shift.start_time, shift.end_time)
          return <div className="pc-schedule-person" key={shift.id}><div>
            <div className="pc-assignment-person-heading">
              {employeeName(shift.employee_id, shift.employee_name || employees.find(employee => employee.id === shift.employee_id)?.name || 'Employee')}
              <span className={`pc-assignment-kind pc-assignment-kind-${kind}`}>{shiftKindLabel(kind)}</span>
            </div>
            <small>{shift.start_time}–{shift.end_time}{shift.position ? ` · ${shift.position}` : ''}</small>
            {issue && !shift.is_trainee && <small role="status">Review: {issue}</small>}
          </div><Button variant="outline" onClick={() => onEdit(shift)}>Edit</Button></div>
        })}
      </section>
      <section><strong>Available to assign · {available.length}</strong>
        {available.length === 0 && <p className="text-muted-foreground">No additional submitted availability.</p>}
        {available.map(employee => {
          const day = response(employee)!
          const conflict = elsewhere(employee.id)
          return <div className="pc-schedule-person" key={employee.id}><div>{employeeName(employee.id, name(employee))}
            <small>{day.start_time}–{day.end_time}</small>{day.notes && <small>{day.notes}</small>}
            {conflict && <small>Already assigned at {conflict.store_code}: {conflict.start_time}–{conflict.end_time}</small>}
          </div><Button variant="outline" disabled={!!conflict} onClick={() => onAssign(employee.id)}>Assign</Button></div>
        })}
      </section>
      <section><strong>Other responses</strong>
        {others.length === 0 && <p className="text-muted-foreground">Everyone considered above.</p>}
        {others.map(employee => {
          const day = response(employee)
          return <div className="pc-schedule-person" key={employee.id}><div>{employeeName(employee.id, name(employee))}
            <small>{employee.is_trainee ? 'Trainee · manual assignment' : !day?.submitted_at ? 'Not submitted' : 'Unavailable'}</small>
            {day?.notes && <small>{day.notes}</small>}
          </div>{!day?.submitted_at && <Button variant="outline" disabled={!!elsewhere(employee.id)} onClick={() => onAssign(employee.id)}>Assign</Button>}</div>
        })}
      </section>
    </div>
  </section>
}
