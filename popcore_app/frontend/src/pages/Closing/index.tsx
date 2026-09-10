import { Alert, Button, Card, Checkbox, DatePicker, Descriptions, Form, Input, InputNumber, Result, Select, Space, Statistic, Steps, Typography } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { addCashCount, addCashEvent, closeClosing, createClosing, fetchClosing, newRequestKey, returnClosing, submitClosing, updateClosing, type ClosingSession } from '../../api/closing'
import { useHasRole } from '../../auth/useRole'
import { useAppStore } from '../../store'

const { Title, Text } = Typography
const denominations = [
  ['10000', '$100'], ['5000', '$50'], ['2000', '$20'], ['1000', '$10'],
  ['500', '$5'], ['200', '$2'], ['100', '$1'], ['25', '25¢'], ['10', '10¢'], ['5', '5¢'],
]

export default function ClosingPage() {
  const store = useAppStore(state => state.selectedStore)
  const [session, setSession] = useState<ClosingSession | null>(null)
  const [error, setError] = useState('')
  const [working, setWorking] = useState(false)
  const [closed, setClosed] = useState(false)
  const [reviewReason, setReviewReason] = useState('')
  const [exceptionReasons, setExceptionReasons] = useState<Record<string, string>>({})
  const [cashEventType, setCashEventType] = useState<'paid_in'|'refund'|'payout'>('paid_in')
  const [cashEventAmount, setCashEventAmount] = useState<number | null>(null)
  const [cashEventReason, setCashEventReason] = useState('')
  const [params, setParams] = useSearchParams()
  const isManager = useHasRole('manager')
  const createKey = useRef(newRequestKey())
  const updateKey = useRef(newRequestKey())
  const countKey = useRef(newRequestKey())
  const submitKey = useRef(newRequestKey())
  const closeKey = useRef(newRequestKey())
  const returnKey = useRef(newRequestKey())
  const cashEventKey = useRef(newRequestKey())

  useEffect(() => {
    const closingId = Number(params.get('closing_id'))
    if (!closingId) return
    fetchClosing(closingId).then(setSession).catch(() => setError('Unable to resume this closing session.'))
  }, [params])

  if (!store || store.code === 'ALL') {
    return <Alert type="info" showIcon message="Select one store before closing a day." />
  }

  async function start(values: { business_date: dayjs.Dayjs }) {
    setWorking(true); setError('')
    try {
      const result = await createClosing(store!.id, values.business_date.format('YYYY-MM-DD'), createKey.current)
      setSession(result); setParams({ closing_id: String(result.closing_id) }); createKey.current = newRequestKey()
    } catch { setError('Unable to open the closing session. Retry safely.') }
    finally { setWorking(false) }
  }

  async function markComplete(checked: boolean) {
    if (!session) return
    setWorking(true); setError('')
    try {
      const result = await updateClosing(session.closing_id, session.version, checked, updateKey.current)
      setSession(result); updateKey.current = newRequestKey()
    } catch { setError('The closing changed. Refresh before saving this declaration.') }
    finally { setWorking(false) }
  }

  async function saveCount(values: Record<string, number>) {
    if (!session) return
    const counts = Object.fromEntries(denominations.map(([key]) => [key, values[`d_${key}`] ?? 0]))
    setWorking(true); setError('')
    try {
      const result = await addCashCount(
        session.closing_id, session, values.opening_coin_cents,
        values.retained_coin_cents, counts, countKey.current,
      )
      setSession(result); countKey.current = newRequestKey()
    } catch { setError('The cash count was not saved. Refresh changed source facts, then retry.') }
    finally { setWorking(false) }
  }

  async function saveCashEvent() {
    if (!session || !cashEventAmount || !cashEventReason.trim()) return
    setWorking(true); setError('')
    try {
      const result = await addCashEvent(session.closing_id, session, cashEventType, cashEventAmount, cashEventReason.trim(), cashEventKey.current)
      setSession(result); cashEventKey.current = newRequestKey(); setCashEventAmount(null); setCashEventReason('')
    } catch { setError('The cash event was not saved. Refresh and retry the same event safely.') }
    finally { setWorking(false) }
  }

  async function refresh() {
    if (!session) return
    try { setSession(await fetchClosing(session.closing_id)); setError('') }
    catch { setError('Unable to refresh this closing session.') }
  }

  async function submit() {
    if (!session) return
    setWorking(true); setError('')
    try {
      const result = await submitClosing(session, submitKey.current)
      setSession(result); submitKey.current = newRequestKey()
    } catch { setError('Closing cannot be submitted. Resolve the listed blockers and refresh.') }
    finally { setWorking(false) }
  }

  async function signOff() {
    if (!session) return
    setWorking(true); setError('')
    try {
      await closeClosing(session, exceptionReasons, closeKey.current)
      closeKey.current = newRequestKey(); setClosed(true)
    } catch { setError('Sign-off failed because the sources changed or review is incomplete.') }
    finally { setWorking(false) }
  }

  async function sendBack() {
    if (!session || !reviewReason.trim()) return
    setWorking(true); setError('')
    try {
      const result = await returnClosing(session, reviewReason.trim(), returnKey.current)
      setSession(result); returnKey.current = newRequestKey(); setReviewReason('')
    } catch { setError('The closing could not be returned. Refresh and retry with the same reason.') }
    finally { setWorking(false) }
  }

  if (closed) return <Result status="success" title="Store day closed"
    subTitle="The reviewed snapshot and cash removal were recorded once." />

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <Title level={3}>Store closing</Title>
      <Text type="secondary">{store.name}</Text>
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 16 }} />}
      {!session ? (
        <Card style={{ marginTop: 16 }}>
          <Form layout="vertical" onFinish={start} initialValues={{ business_date: dayjs() }}>
            <Form.Item name="business_date" label="Business date" rules={[{ required: true }]}>
              <DatePicker style={{ width: '100%' }} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={working}>Start closing</Button>
          </Form>
        </Card>
      ) : (
        <>
          <Steps style={{ marginTop: 20 }} current={session.latest_cash_count ? 3 : session.intake_complete ? 2 : 0}
            items={[{ title: 'Sales' }, { title: 'Restock' }, { title: 'Cash' }, { title: 'Counts' }, { title: 'Review' }]} />
          <Card title="Sales completeness" style={{ marginTop: 20 }}>
            <Checkbox checked={Boolean(session.intake_complete)} disabled={working}
              onChange={event => markComplete(event.target.checked)}>
              All completed POS sales and missing transactions for this day have been entered.
            </Checkbox>
          </Card>
          <Card title="Source documents" style={{ marginTop: 16 }}>
            <Space direction="vertical">
              {(session.source_documents?.sales ?? []).map(item => <Link key={`sale-${item.id}`} to={`/sales/documents/${item.id}`}>Sale #{item.id}: {item.status}, {item.allocation_status}</Link>)}
              {(session.source_documents?.receipts ?? []).map(item => <Link key={`receipt-${item.id}`} to={`/goods/receiving?receipt_id=${item.id}`}>Receipt #{item.id}: {item.status}</Link>)}
              {(session.source_documents?.counts ?? []).map(item => <Link key={`count-${item.id}`} to={`/goods/counts?count_id=${item.id}`}>Count #{item.id}: {item.status}</Link>)}
              {!session.source_documents?.sales.length && !session.source_documents?.receipts.length && !session.source_documents?.counts.length && <Text type="secondary">No linked documents for this business date.</Text>}
            </Space>
          </Card>
          {session.status === 'draft' && <Card title="Cash events" style={{ marginTop: 16 }}>
            <Space wrap>
              <Select aria-label="Cash event type" value={cashEventType}
                options={[['paid_in','Paid in'],['refund','Cash refund'],['payout','Paid out']].map(([value,label]) => ({ value, label }))}
                onChange={setCashEventType} />
              <InputNumber aria-label="Cash event amount cents" min={1} precision={0}
                placeholder="Amount cents" value={cashEventAmount} onChange={setCashEventAmount} />
              <Input aria-label="Cash event reason" placeholder="Required reason"
                value={cashEventReason} onChange={event => setCashEventReason(event.target.value)} />
              <Button loading={working} disabled={!cashEventAmount || !cashEventReason.trim()}
                onClick={saveCashEvent}>Record cash event</Button>
            </Space>
            <Descriptions size="small" column={2} style={{ marginTop: 12 }}>
              {Object.entries(session.cash.event_totals_cents).map(([name,value]) =>
                <Descriptions.Item key={name} label={name.replace('_',' ')}>${(value/100).toFixed(2)}</Descriptions.Item>)}
            </Descriptions>
          </Card>}
          <Card title="Cash count" style={{ marginTop: 16 }}>
            {session.cash.unknown_cash_payment_ids.length > 0 && (
              <Alert type="warning" showIcon message="Unverified or unknown cash payments remain"
                description={session.cash.unknown_cash_payment_ids.join(', ')} style={{ marginBottom: 16 }} />
            )}
            <Form layout="vertical" onFinish={saveCount}>
              <Space wrap align="start">
                <Form.Item name="opening_coin_cents" label="Opening coins (cents)" rules={[{ required: true }]}>
                  <InputNumber min={0} precision={0} />
                </Form.Item>
                <Form.Item name="retained_coin_cents" label="Retained coins (cents)" rules={[{ required: true }]}>
                  <InputNumber min={0} precision={0} />
                </Form.Item>
                {denominations.map(([key, label]) => (
                  <Form.Item key={key} name={`d_${key}`} label={`${label} count`} rules={[{ required: true }]}>
                    <InputNumber min={0} precision={0} />
                  </Form.Item>
                ))}
              </Space>
              <Button type="primary" htmlType="submit" loading={working}>Save cash count</Button>
            </Form>
          </Card>
          {session.latest_cash_count && (
            <Card title="Cash result" style={{ marginTop: 16 }}>
              <Space wrap size="large">
                <Statistic title="Expected" value={session.latest_cash_count.expected_cents / 100} precision={2} prefix="$" />
                <Statistic title="Counted" value={session.latest_cash_count.counted_cents / 100} precision={2} prefix="$" />
                <Statistic title="Variance" value={session.latest_cash_count.variance_cents / 100} precision={2} prefix="$" />
                <Statistic title="Removal" value={session.latest_cash_count.removal_cents == null ? 'Float shortfall' : `$${(session.latest_cash_count.removal_cents / 100).toFixed(2)}`} />
              </Space>
            </Card>
          )}
          {session.status === 'draft' && session.latest_cash_count && (
            <Card title="Submit for manager review" style={{ marginTop: 16 }}>
              {session.hard_blockers?.length ? (
                <Alert type="error" showIcon message="Closing blockers"
                  description={session.hard_blockers.join(', ')} />
              ) : null}
              <Space style={{ marginTop: 12 }}>
                <Button onClick={refresh}>Refresh sources</Button>
                <Button type="primary" loading={working} onClick={submit}>Submit</Button>
              </Space>
            </Card>
          )}
          {session.status === 'submitted' && isManager && (
            <Card title="Manager review" style={{ marginTop: 16 }}>
              {(session.review_exceptions ?? []).length > 0 && (
                <Alert type="warning" showIcon message="Exceptions need a reason"
                  description={(session.review_exceptions ?? []).join(', ')} />
              )}
              {(session.review_exceptions ?? []).map(code => <Input key={code}
                aria-label={`${code} acceptance reason`} value={exceptionReasons[code] ?? ''}
                onChange={event => setExceptionReasons({ ...exceptionReasons, [code]: event.target.value })}
                placeholder={`Reason for accepting ${code}`} style={{ marginTop: 12 }} />)}
              <Input value={reviewReason} onChange={event => setReviewReason(event.target.value)}
                placeholder="Reason for returning this closing" style={{ marginTop: 12 }} />
              <Space style={{ marginTop: 12 }}>
                <Button onClick={refresh}>Refresh sources</Button>
                <Button danger loading={working} disabled={!reviewReason.trim()} onClick={sendBack}>Return for changes</Button>
                <Button type="primary" loading={working}
                  disabled={(session.review_exceptions ?? []).some(code => !exceptionReasons[code]?.trim())}
                  onClick={signOff}>Close store day</Button>
              </Space>
            </Card>
          )}
        </>
      )}
    </div>
  )
}
