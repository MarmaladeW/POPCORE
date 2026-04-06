import { useEffect } from 'react'
import { Modal, Form, Select, Row, Col, Input, Button, message } from 'antd'
import { upsertAvailability, deleteAvailability, type Availability } from './scheduleApi'

interface Props {
  open: boolean
  date: string | null
  existing: Availability | null
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

export default function AvailabilityModal({ open, date, existing, onClose, onSaved }: Props) {
  const [form] = Form.useForm()
  const [msgApi, ctxHolder] = message.useMessage()

  useEffect(() => {
    if (!open) return
    if (existing) {
      form.setFieldsValue({
        start_time: existing.start_time,
        end_time:   existing.end_time,
        notes:      existing.notes,
      })
    } else {
      form.resetFields()
    }
  }, [open, existing, form])

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      await upsertAvailability({
        date:       date!,
        start_time: values.start_time as string,
        end_time:   values.end_time   as string,
        notes:      values.notes ?? '',
      })
      msgApi.success('Availability saved')
      onSaved()
      onClose()
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return
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
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item
                name="start_time"
                label="Start time"
                rules={[{ required: true, message: 'Required' }]}
              >
                <Select
                  showSearch
                  placeholder="06:00"
                  options={TIME_OPTIONS}
                  style={{ width: '100%' }}
                />
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
                <Select
                  showSearch
                  placeholder="17:00"
                  options={TIME_OPTIONS}
                  style={{ width: '100%' }}
                />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="notes" label="Notes (optional)">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
