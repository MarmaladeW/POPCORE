import { useEffect } from 'react'
import { Form, Select, Row, Col, Input, message } from 'antd'
import {
  createShift,
  updateShift,
  deleteShift,
  type Availability,
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

  const availByEmpId: Record<number, Availability> = {}
  for (const a of availForDate) availByEmpId[a.employee_id] = a

  const sortedEmployees = [
    ...employees.filter((e) => availByEmpId[e.id]),
    ...employees.filter((e) => !availByEmpId[e.id]),
  ]

  useEffect(() => {
    if (!open) return
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

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      if (existing) {
        await updateShift(existing.id, {
          start_time: values.start_time as string,
          end_time:   values.end_time   as string,
          notes:      values.notes ?? '',
        })
      } else {
        await createShift({
          employee_id: values.employee_id as number,
          date:        date!,
          start_time:  values.start_time as string,
          end_time:    values.end_time   as string,
          notes:       values.notes ?? '',
          store_code:  selectedStore?.code,
        })
      }
      msgApi.success('Shift saved')
      onSaved()
      onClose()
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return
      msgApi.error('Failed to save shift')
    }
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

          {/* Availability summary */}
          {availForDate.length > 0 && (
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

          <DialogFooter>
            {existing && (
              <Button variant="destructive" onClick={handleDelete}>Delete</Button>
            )}
            <Button variant="outline" onClick={onClose}>Cancel</Button>
            <Button onClick={handleSave}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
