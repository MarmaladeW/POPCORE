import { useEffect } from 'react'
import { Modal, Form, TimePicker, Input, Button, message } from 'antd'
import dayjs from 'dayjs'
import { upsertAvailability, deleteAvailability, type Availability } from './scheduleApi'

interface Props {
  open: boolean
  date: string | null          // YYYY-MM-DD of the day being edited
  existing: Availability | null // pre-populate if editing
  onClose: () => void
  onSaved: () => void
}

const FMT = 'HH:mm'

export default function AvailabilityModal({ open, date, existing, onClose, onSaved }: Props) {
  const [form] = Form.useForm()
  const [msgApi, ctxHolder] = message.useMessage()

  useEffect(() => {
    if (!open) return
    if (existing) {
      form.setFieldsValue({
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
      await upsertAvailability({
        date: date!,
        start_time: start.format(FMT),
        end_time: end.format(FMT),
        notes: values.notes ?? '',
      })
      msgApi.success('Availability saved')
      onSaved()
      onClose()
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return // validation
      msgApi.error('Failed to save availability')
    }
  }

  const handleDelete = async () => {
    if (!existing) return
    try {
      await deleteAvailability(existing.id)
      msgApi.success('Availability removed')
      onSaved()
      onClose()
    } catch {
      msgApi.error('Failed to delete availability')
    }
  }

  return (
    <>
      {ctxHolder}
      <Modal
        title={`Set availability — ${date ?? ''}`}
        open={open}
        onCancel={onClose}
        footer={[
          existing && (
            <Button key="del" danger onClick={handleDelete}>
              Remove
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
            name="time"
            label="Available hours"
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
