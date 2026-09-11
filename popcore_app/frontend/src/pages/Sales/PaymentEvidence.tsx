import { Alert, Button, Card, Result, Typography, Upload } from 'antd'
import type { UploadFile } from 'antd'
import { useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { newRequestKey, uploadPaymentEvidence } from '../../api/salesDocuments'

const { Title, Text } = Typography

export default function PaymentEvidencePage() {
  const { id } = useParams()
  const paymentId = Number(id)
  const navigate = useNavigate()
  const [files, setFiles] = useState<UploadFile[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState<number | null>(null)
  const requestKey = useRef(newRequestKey())

  async function submit() {
    const file = files[0]?.originFileObj
    if (!file) return
    setSaving(true)
    setError('')
    try {
      const result = await uploadPaymentEvidence(paymentId, file, requestKey.current)
      setSaved(result.evidence_id)
      requestKey.current = newRequestKey()
    } catch {
      setError('Upload was not confirmed. Retry the same image to recover it safely.')
    } finally { setSaving(false) }
  }

  if (saved) {
    return <Result status="success" title="Evidence saved for review"
      subTitle={`Private attachment #${saved}. Payment verification remains separate.`}
      extra={<Button onClick={() => navigate(-1)}>Back to sale</Button>} />
  }
  return (
    <div style={{ maxWidth: 600, margin: '0 auto' }}>
      <Title level={3}>Add payment evidence</Title>
      <Text type="secondary">Take or choose one clear JPEG, PNG, or WebP image.</Text>
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 16 }} />}
      <Card style={{ marginTop: 16 }}>
        <Upload accept="image/jpeg,image/png,image/webp" capture="environment"
          maxCount={1} fileList={files} beforeUpload={() => false}
          onChange={({ fileList }) => setFiles(fileList)}>
          <Button>Choose or take photo</Button>
        </Upload>
        <Button type="primary" disabled={!files.length} loading={saving}
          onClick={submit} style={{ marginTop: 16 }}>
          Upload privately
        </Button>
      </Card>
    </div>
  )
}
