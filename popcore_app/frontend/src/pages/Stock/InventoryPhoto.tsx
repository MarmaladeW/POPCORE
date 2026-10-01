import { Button, Image } from 'antd'
import { useEffect, useRef, useState } from 'react'
import client from '../../api/client'

export default function InventoryPhoto({filename,name,large=false}:{filename?:string|null;name:string;large?:boolean}) {
  const [url,setUrl]=useState(''), [failed,setFailed]=useState(false), [retry,setRetry]=useState(0)
  const preview=useRef<HTMLDivElement>(null)
  useEffect(()=>{if(retry)preview.current?.focus()},[retry])
  useEffect(()=>{
    setUrl('');setFailed(false)
    if(!filename)return
    const controller=new AbortController()
    let objectUrl=''
    const origin=new URL(client.defaults.baseURL||'/api',window.location.href).origin
    client.get(`/hidden_imgs/${filename.split('/').map(encodeURIComponent).join('/')}`,{baseURL:origin,responseType:'blob',signal:controller.signal})
      .then(response=>{if(!controller.signal.aborted){objectUrl=URL.createObjectURL(response.data);setUrl(objectUrl)}})
      .catch(()=>{if(!controller.signal.aborted)setFailed(true)})
    return ()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl)}
  },[filename,retry])
  const imageFailed=()=>{setUrl('');setFailed(true)}
  if(!large)return !filename?null:url?<img className="pc-series-thumbnail" src={url} alt="" onError={imageFailed}/>:<span className="pc-series-photo-placeholder"/>
  return <div ref={preview} tabIndex={-1}>
    {!filename?<p className="pc-series-photo-note">No reference photo yet. Photos can be added in Products.</p>:url?<Image className="pc-series-reference-photo" src={url} alt={`Reference photo for ${name}`} onError={imageFailed}/>:<span className="pc-series-photo-note">{failed?<>Photo unavailable. <Button onClick={()=>setRetry(value=>value+1)}>Retry photo</Button></>:'Loading photo…'}</span>}
  </div>
}
