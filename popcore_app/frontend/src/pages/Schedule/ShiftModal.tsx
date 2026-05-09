import { useState, useEffect, useRef } from 'react'
import { Form, Select, Row, Col, Input, message } from 'antd'
import {
  createShift,
  updateShift,
  deleteShift,
  checkConflicts,
  type Availability,
  type ConflictInfo,
  type Employee,
  type Shift,
} from './scheduleApi'
import { useAppStore } from '../../store'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog'

interface Props {
  open: boolean
  date: string | null
  employees: Employee[]
  existing: Shift | null
  availForDate: Availability[]
  onClose: () => void
  onSaved: () => void
}

interface ShiftFormValues {
  employee_id: number
  start_time:  string
  end_time:    string
  notes?:      string
}

type SavePhase = 'idle' | 'checking' | 'conflict' | 'error'

function buildTimeOptions() {
  const opts: { value: string; label: string }[] = []
  for (let h = 6; h < 24; h++) {
    for (const m of [0, 30]) {
      const label = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
      opts.push({ value: label, label })
    }
  }
  return opts
}
const TIME_OPTIONS = buildTimeOptions()

export default function ShiftModal({
  open, date, employees, existing, availForDate, onClose, onSaved,
}: Props) {
  const [form] = Form.useForm()
  const [msgApi, ctxHolder] = message.useMessage()
  const { selectedStore } = useAppStore()

  const [savePhase,  setSavePhase]  = useState<SavePhase>('idle')
  const [conflicts,  setConflicts]  = useState<ConflictInfo[]>([])
  const pendingValues = useRef<ShiftFormValues | null>(null)

  const availByEmpId: Record<number, Availability> = {}
  for (const a of availForDate) availByEmpId[a.employee_id] = a

  const sortedEmployees = [
    ...employees.filter((e) => availByEmpId[e.id]),
    ...employees.filter((e) => !availByEmpId[e.id]),
  ]

  useEffect(() => {
    if (!open) {
      setSavePhase('idle')
      setConflicts([])
      pendingValues.current = null
      return
    }
    if (existing) {
      form.setFieldsValue({
        employee_id: existing.employee_id,
        start_time:  existing.start_time,
        end_time:    existing.end_time,
        notes:       existing.notes,
      })
    } else {
      form.resetFields()
    }
  }, [open, existing, form])

  const handleEmployeeChange = (empId: number) => {
    if (existing) return
    const avail = availByEmpId[empId]
    if (avail) {
      form.setFieldsValue({ start_time: avail.start_time, end_time: avail.end_time })
    }
  }

  const doCreateShift = async (values: ShiftFormValues) => {
    await createShift({
      employee_id: values.employee_id,
      date:        date!,
      start_time:  values.start_time,
      end_time:    values.end_time,
      notes:       values.notes ?? '',
      store_code:  selectedStore?.code,
    })
    msgApi.success('Shift saved')
    setSavePhase('idle')
    onSaved()
    onClose()
  }

  const handleSave = async () => {
    // ALL mode should never open this modal, but guard just in case
    if (selectedStore?.code === 'ALL') return

    try {
      const values = await form.validateFields() as ShiftFormValues

      if (existing) {
        // Updates can't change employee or date, so no conflict possible
        await updateShift(existing.id, {
          start_time: values.start_time,
          end_time:   values.end_time,
          notes:      values.notes ?? '',
        })
        msgApi.success('Shift saved')
        onSaved()
        onClose()
        return
      }

      // New shift: check for conflicts before saving
      setSavePhase('checking')
      try {
        const result = await checkConflicts({
          employee_id: values.employee_id,
          date:        date!,
          store_code:  selectedStore!.code,
        })
        if (result.has_conflict) {
          pendingValues.current = values
          setConflicts(result.conflicts)
          setSavePhase('conflict')
        } else {
          await doCreateShift(values)
        }
      } catch {
        pendingValues.current = values
        setSavePhase('error')
      }
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return
      msgApi.error('Failed to save shift')
    }
  }

  const handleConfirmAnyway = async () => {
    if (!pendingValues.current) return
    try {
      await doCreateShift(pendingValues.current)
    } catch {
      msgApi.error('Failed to save shift')
      setSavePhase('idle')
    }
  }

  const handleGoBack = () => {
    if (pendingValues.current) {
      form.setFieldsValue(pendingValues.current)
    }
    setSavePhase('idle')
    setConflicts([])
  }

  const handleDelete = async () => {
    if (!existing) return
    try {
      await deleteShift(existing.id)
      msgApi.success('Shift deleted')
      onSaved()
      onClose()
    } catch {
      msgApi.error('Failed to delete shift')
    }
  }

  const showForm = savePhase === 'idle' || savePhase === 'checking'

  const conflictEmpName = pendingValues.current
    ? (employees.find((e) => e.id === pendingValues.current!.employee_id)?.name
        || `Employee ${pendingValues.current.employee_id}`)
    : ''

  return (
    <>
      {ctxHolder}
      <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
        <DialogContent style={{ maxWidth: 480 }}>
          <DialogHeader>
            <DialogTitle>
              Assign shift — {date ?? ''}
              {selectedStore && (
                <span className="ml-2 text-sm font-normal text-muted-foreground">
                  @ {selectedStore.name || selectedStore.code}
                </span>
              )}
            </DialogTitle>
          </DialogHeader>

          {/* Conflict warning panel */}
          {savePhase === 'conflict' && (
            <div className="rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 space-y-2">
              <p className="font-semibold text-amber-900 text-sm">
                Warning: {conflictEmpName} already has a shift on {date} at:
              </p>
              <ul className="space-y-0.5 pl-1">
                {conflicts.map((c) => (
                  <li key={c.shift_id} className="text-sm text-amber-800">
                    <span className="font-medium">{c.store_name || c.store_code}</span>
                    {': '}
                    {c.start_time.slice(11, 16)} – {c.end_time.slice(11, 16)}
                  </li>
                ))}
              </ul>
              <p className="text-sm text-amber-800">
                Do you still want to assign this shift at {selectedStore?.name || selectedStore?.code}?
              </p>
            </div>
          )}

          {/* Conflict check error panel */}
          {savePhase === 'error' && (
            <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3">
              <p className="text-sm text-red-700">
                Could not check for conflicts. Save anyway?
              </p>
            </div>
          )}

          {/* Availability summary — only when form is visible */}
          {showForm && availForDate.length > 0 && (
            <div className="rounded-lg bg-emerald-50 border border-emerald-200 px-3 py-2 text-sm">
              <div className="font-semibold text-emerald-800 mb-1">
                Available on {date}:
              </div>
              <div className="flex flex-wrap gap-1.5">
                {availForDate.map((a) => {
                  const emp  = employees.find((e) => e.id === a.employee_id)
                  const name = emp
                    ? (emp.name || emp.email || `ID ${emp.id}`)
                    : (a.employee_name || `ID ${a.employee_id}`)
                  return (
                    <Badge key={a.id} variant="outline" className="text-emerald-700 border-emerald-300 bg-white">
                      {name} {a.start_time}–{a.end_time}
                    </Badge>
                  )
                })}
              </div>
            </div>
          )}

          {/* Form — kept mounted to preserve values; hidden during conflict/error */}
          <div style={showForm ? {} : { display: 'none' }}>
            <Form form={form} layout="vertical">
              <Form.Item
                name="employee_id"
                label="Employee"
                rules={[{ required: true, message: 'Select an employee' }]}
              >
                <Select
                  showSearch
                  placeholder="Select employee"
                  optionFilterProp="label"
                  disabled={!!existing}
                  onChange={handleEmployeeChange}
                  getPopupContainer={(trigger) => trigger.parentElement!}
                  options={sortedEmployees.map((e) => {
                    const avail    = availByEmpId[e.id]
                    const baseName = e.name || e.email || e.auth0_id
                    return {
                      value: e.id,
                      label: avail
                        ? `${baseName}  ·  ${avail.start_time}–${avail.end_time}`
                        : baseName,
                    }
                  })}
                />
              </Form.Item>

              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item
                    name="start_time"
                    label="Start time"
                    rules={[{ required: true, message: 'Required' }]}
                  >
                    <Select showSearch placeholder="09:00" options={TIME_OPTIONS} style={{ width: '100%' }} getPopupContainer={(trigger) => trigger.parentElement!} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item
                    name="end_time"
                    label="End time"
                    dependencies={['start_time']}
                    rules={[
                      { required: true, message: 'Required' },
                      ({ getFieldValue }) => ({
                        validator(_, value) {
                          if (!value || !getFieldValue('start_time')) return Promise.resolve()
                          if (value > getFieldValue('start_time')) return Promise.resolve()
                          return Promise.reject(new Error('Must be after start'))
                        },
                      }),
                    ]}
                  >
                    <Select showSearch placeholder="17:00" options={TIME_OPTIONS} style={{ width: '100%' }} getPopupContainer={(trigger) => trigger.parentElement!} />
                  </Form.Item>
                </Col>
              </Row>

              <Form.Item name="notes" label="Notes (optional)">
                <Input.TextArea rows={2} />
              </Form.Item>
            </Form>
          </div>

          <DialogFooter>
            {savePhase === 'conflict' && (
              <>
                <Button variant="outline" onClick={handleGoBack}>Go Back</Button>
                <Button onClick={handleConfirmAnyway}>Confirm Anyway</Button>
              </>
            )}
            {savePhase === 'error' && (
              <>
                <Button variant="outline" onClick={() => setSavePhase('idle')}>Cancel</Button>
                <Button onClick={handleConfirmAnyway}>Save Anyway</Button>
              </>
            )}
            {showForm && (
              <>
                {existing && (
                  <Button variant="destructive" onClick={handleDelete}>Delete</Button>
                )}
                <Button variant="outline" onClick={onClose}>Cancel</Button>
                <Button onClick={handleSave} disabled={savePhase === 'checking'}>
                  {savePhase === 'checking' ? 'Checking…' : 'Save'}
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
