import { useRef, useState } from 'react'
import { Alert, Button, Skeleton } from 'antd'
import { Link } from 'react-router-dom'
import { useHasRole } from '../../auth/useRole'
import { useStoreDay } from './storeDay'
import './Home.css'

export default function StoreSummary() {
  const [date, setDate] = useState('')
  const { store, day, data, error, retry } = useStoreDay(date)
  const manager = useHasRole('manager')
  const [copied, setCopied] = useState('')
  const text = useRef<HTMLTextAreaElement>(null)
  async function copy() {
    try { await navigator.clipboard.writeText(data?.summary_text || ''); setCopied('Summary copied / 已复制') }
    catch { text.current?.focus(); text.current?.select(); setCopied('Select and copy the summary below.') }
  }
  return <div className="pc-home pc-home-task">
    <Link className="pc-home-back" to="/">← Back to home</Link>
    <header className="pc-home-heading"><h1>汇总 / Daily summary</h1><p>{store?.name} · {manager ? 'Store records' : 'Your checkout & event records'}</p></header>
    <div className="pc-home-summary-toolbar"><label>Business date<input type="date" value={day} onChange={event => { setDate(event.target.value); setCopied('') }} /></label><Button onClick={retry}>Refresh records</Button></div>
    {!store || store.code === 'ALL' ? <Alert type="info" showIcon message="Choose one store to review its summary." />
      : error ? <Alert role="alert" type="error" message={error} action={<Button onClick={retry}>Retry</Button>} />
      : !data ? <Skeleton active /> : <>
        <p className="pc-home-muted">{data.scope === 'personal' ? 'Your own orders and events, plus incoming goods you can access. A manager reviews the complete store day.' : 'Recorded in POPCORE. Check source completeness before closing; Clover synchronization is not connected.'}</p>
        <textarea ref={text} className="pc-home-summary-text" aria-label="Summary text" value={data.summary_text} readOnly rows={16} />
        <div className="pc-home-summary-toolbar"><Button type="primary" onClick={copy}>Copy summary</Button><span role="status">{copied}</span></div>
      </>}
    <nav className="pc-home-choices" aria-label="Summary actions">
      <Link to="/checkout/history"><strong>Order history</strong><span>Review orders and payment evidence.</span></Link>
      <Link to="/closing"><strong>Cash count & closing</strong><span>Count the drawer, review discrepancies and close the day.</span></Link>
      {manager && <Link to="/sales"><strong>Import historical summaries</strong><span>Paste and review older daily summaries.</span></Link>}
    </nav>
  </div>
}
