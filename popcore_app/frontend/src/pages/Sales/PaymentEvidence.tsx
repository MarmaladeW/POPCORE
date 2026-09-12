import { Alert, Button, Card, Result, Typography, Upload } from 'antd'
import type { UploadFile } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { useBeforeUnload, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { fetchSale, newRequestKey, uploadPaymentEvidence } from '../../api/salesDocuments'
import { useHistoryReconciliationGuard } from '../../lib/reconciliationNavigation'

const { Title, Text } = Typography

export default function PaymentEvidencePage() {
  const { id } = useParams()
  const paymentId = Number(id)
  const [params] = useSearchParams()
  const saleId = Number(params.get('sale_id'))
  const navigate = useNavigate()
  const [files, setFiles] = useState<UploadFile[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState<number | null>(null)
  const requestKey = useRef(newRequestKey())
  const submittedFile = useRef<File | null>(null)
  const [pending, setPending] = useState(false)
  const [verified, setVerified] = useState(!Number.isInteger(saleId) || saleId < 1)

  useHistoryReconciliationGuard(pending, 'This upload is not confirmed. Leave it reconciliation-pending and exit this page?')
  useBeforeUnload(event => { if (pending) event.preventDefault() })
  useEffect(() => {
    if (!pending) return
    const confirmLink = (event: MouseEvent) => {
      const link = (event.target as HTMLElement).closest('a')
      if (link && link.target !== '_blank' && !window.confirm('This upload is not confirmed. Leave it reconciliation-pending and exit this page?')) event.preventDefault()
    }
    document.addEventListener('click', confirmLink, true)
    return () => document.removeEventListener('click', confirmLink, true)
  }, [pending])

  useEffect(() => {
    if (!Number.isInteger(saleId) || saleId < 1) return
    const controller = new AbortController(); setVerified(false); setError('')
    fetchSale(saleId, controller.signal).then(sale => {
      if (!sale.payments?.some(payment => payment.id === paymentId)) setError('This payment does not belong to the referenced sale.')
      else setVerified(true)
    }).catch(cause => { if ((cause as {code?:string})?.code !== 'ERR_CANCELED') setError('Unable to verify the referenced sale.') })
    return () => controller.abort()
  }, [paymentId, saleId])

  async function submit() {
    const file = submittedFile.current ?? files[0]?.originFileObj
    if (!file) return
    submittedFile.current = file
    setPending(true)
    setSaving(true)
    setError('')
    try {
      const result = await uploadPaymentEvidence(paymentId, file, requestKey.current)
      setSaved(result.evidence_id)
      requestKey.current = newRequestKey()
      submittedFile.current = null
      setPending(false)
    } catch (cause) {
      const status = (cause as {response?:{status?:number}})?.response?.status
      if (status && status < 500) { submittedFile.current = null; requestKey.current = newRequestKey(); setPending(false) }
      setError('Upload was not confirmed. Retry the same image to recover it safely.')
    } finally { setSaving(false) }
  }

  if (saved) {
    return <Result status="success" title="Evidence saved for review"
      subTitle={`Private attachment #${saved}. Payment verification remains separate.`}
      extra={<Button onClick={() => navigate(Number.isInteger(saleId) && saleId > 0 ? `/sales/documents/${saleId}` : '/sales/entry')}>Back to sale</Button>} />
  }
  return (
    <div style={{ maxWidth: 600, margin: '0 auto' }}>
      <Title level={3}>Add payment evidence</Title>
      <Text type="secondary">Take or choose one clear JPEG, PNG, or WebP image.</Text>
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 16 }} />}
      <Card style={{ marginTop: 16 }}>
        <Upload accept="image/jpeg,image/png,image/webp" capture="environment" disabled={pending || !verified}
          maxCount={1} fileList={files} beforeUpload={() => false}
          onChange={({ fileList }) => { if (!pending) setFiles(fileList) }}>
          <Button>Choose or take photo</Button>
        </Upload>
        <Button type="primary" disabled={!files.length || !verified} loading={saving}
          onClick={submit} style={{ marginTop: 16 }}>
          {pending ? 'Retry identical upload' : 'Upload privately'}
        </Button>
      </Card>
    </div>
  )
}
