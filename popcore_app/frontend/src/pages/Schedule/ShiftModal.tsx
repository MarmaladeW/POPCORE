import { useEffect } from 'react'
import { Modal, Form, Select, TimePicker, Input, Button, message } from 'antd'
import dayjs from 'dayjs'
import {
  createShift,
  updateShift,
  deleteShift,
  type Employee,
  type Shift,
} from './scheduleApi'

interface Props {
  open: boolean
  date: string | null
  employees: Employee[]
  existing: Shift | null
  onClose: () => void
  onSaved: () => void
}

const FMT = 'HH:mm'

export default function ShiftModal({ open, date, employees, existing, onClose, onSaved }: Props) {
  const [form] = Form.useForm()
  const [msgApi, ctxHolder] = message.useMessage()

  useEffect(() => {
    if (!open) return
    if (existing) {
      form.setFieldsValue({
        employee_id: existing.employee_id,
        time: [dayjs(existing.start_time, FMT), dayjs(existing.end_time, FMT)],
        notes: existing.notes,
      })
    } else {
      form.resetFields()
    }
  }, [open, existing, form])

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      const [start, end] = values.time as [dayjs.Dayjs, dayjs.Dayjs]
      if (existing) {
        await updateShift(existing.id, {
          start_time: start.format(FMT),
          end_time: end.format(FMT),
          notes: values.notes ?? '',
        })
      } else {
        await createShift({
          employee_id: values.employee_id,
          date: date!,
          start_time: start.format(FMT),
          end_time: end.format(FMT),
          notes: values.notes ?? '',
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
      <Modal
        title={`Assign shift — ${date ?? ''}`}
        open={open}
        onCancel={onClose}
        footer={[
          existing && (
            <Button key="del" danger onClick={handleDelete}>
              Delete
            </Button>
          ),
          <Button key="cancel" onClick={onClose}>
            Cancel
          </Button>,
          <Button key="save" type="primary" onClick={handleSave}>
            Save
          </Button>,
        ]}
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
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
              options={employees.map((e) => ({
                value: e.id,
                label: e.name || e.email || e.auth0_id,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="time"
            label="Shift hours"
            rules={[{ required: true, message: 'Please set a time range' }]}
          >
            <TimePicker.RangePicker format={FMT} minuteStep={15} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="notes" label="Notes (optional)">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
