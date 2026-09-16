import {useEffect,useRef,useState} from 'react'
import {Alert,Button} from 'antd'
import {useBeforeUnload} from 'react-router-dom'
import client from '../../api/client'
import {useHistoryReconciliationGuard} from '../../lib/reconciliationNavigation'
const errorText=(error:unknown)=>(error as {_serverMessage?:string})?._serverMessage || 'Unable to confirm this request. Retry to check its result.'

export default function useCheckoutMutation(done:(data:any)=>void) {
  const alive=useRef(true)
  useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[])
  const locked = useRef(false)
  const request = useRef<{url:string;body:unknown;key:string}|null>(null)
  const [saving,setSaving] = useState(false), [pending,setPending] = useState(false), [error,setError] = useState('')
  useHistoryReconciliationGuard(pending,'Your last action is unconfirmed. Leave and check the saved record before trying again?')
  useBeforeUnload(event => { if (pending) event.preventDefault() })
  useEffect(() => {
    if (!pending) return
    const guard = (event:MouseEvent) => {
      const link = (event.target as HTMLElement).closest('a')
      if (link && link.target !== '_blank') {
        event.preventDefault(); event.stopPropagation()
        setError('Retry your last action before leaving.')
      }
    }
    document.addEventListener('click',guard,true)
    return ()=>document.removeEventListener('click',guard,true)
  },[pending])
  async function run(url?:string,body?:unknown) {
    if (locked.current) return
    if (!request.current && url) request.current = {url,body,key:crypto.randomUUID()}
    if (!request.current) return
    locked.current=true
    setSaving(true); setPending(true); setError('')
    try {
      const intent = request.current
      const response = await client.post(intent.url,intent.body,{headers:{'Idempotency-Key':intent.key},timeout:15000})
      if(!alive.current)return
      request.current = null; setPending(false); done(response.data); return response.data
    } catch (cause) {
      if(!alive.current)return
      const status = (cause as {response?:{status:number}})?.response?.status
      if (status && status < 500) { request.current = null; setPending(false) }
      if(status===401||status===403)window.dispatchEvent(new Event('popcore:checkout-access-denied'))
      setError(errorText(cause))
    } finally { locked.current=false; if(alive.current)setSaving(false) }
  }
  return {run,saving,pending,error,notice:<>{error && <Alert type="error" showIcon message={error} style={{marginBottom:16}} />}{pending && !saving && <Button onClick={() => run()} style={{marginBottom:16}}>Retry</Button>}</>}
}
