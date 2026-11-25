import { Alert, Button, Input, Modal, message } from 'antd'
import './people.css'
import { useEffect, useState } from 'react'

import client from '../../api/client'

export type PinState = { has_profile: boolean; has_pin: boolean }

export function useMyPin(): [PinState | undefined, () => void] {
  const [state, setState] = useState<PinState>()
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let live = true
    client.get<PinState>('/me/pin').then(response => { if (live) setState(response.data) }).catch(() => undefined)
    return () => { live = false }
  }, [reload])
  return [state, () => setReload(n => n + 1)]
}

// 我的 PIN (owner, 2026-11-20): 4 digits for the shared store computer, so what you do there carries your name.
export default function MyPinModal({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved?: () => void }) {
  const [pin, setPin] = useState('')
  const [again, setAgain] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => { if (open) { setPin(''); setAgain(''); setError('') } }, [open])
  const digits = (value: string) => value.replace(/\D/g, '').slice(0, 4)
  async function save() {
    if (pin.length !== 4) { setError('PIN 是 4 位数字 A PIN is 4 digits'); return }
    if (pin !== again) { setError('两次不一样 The two PINs differ'); return }
    setSaving(true)
    try {
      await client.post('/me/pin', { pin })
      message.success('PIN 已设置 PIN saved')
      onSaved?.()
      onClose()
    } catch (cause: any) {
      setError(cause?._serverMessage || '没有保存 Not saved')
    } finally {
      setSaving(false)
    }
  }
  return <Modal open={open} title="我的 PIN · My PIN" onCancel={onClose} onOk={save} okText="保存 Save" cancelText="取消 Cancel"
    confirmLoading={saving} width={380} destroyOnClose>
    <p>在店里的电脑上做事时，点你的名字再输入这 4 位数字，记录就会写你的名字。On the store computer, tap your name and type these 4 digits, so what you do there is marked as yours.</p>
    <label htmlFor="my-pin">新 PIN New PIN</label>
    <Input.Password id="my-pin" inputMode="numeric" autoComplete="new-password" maxLength={4} value={pin} onChange={event => setPin(digits(event.target.value))} />
    <label htmlFor="my-pin-again" style={{ display: 'block', marginTop: 8 }}>再输一次 Again</label>
    <Input.Password id="my-pin-again" inputMode="numeric" autoComplete="new-password" maxLength={4} value={again} onChange={event => setAgain(digits(event.target.value))} onPressEnter={save} />
    {error && <Alert style={{ marginTop: 8 }} type="error" showIcon message={error} />}
  </Modal>
}

/** A short prompt on the home page for staff who have no PIN yet. */
export function PinPrompt() {
  const [state, refresh] = useMyPin()
  const [open, setOpen] = useState(false)
  if (!state?.has_profile || state.has_pin) return null
  return <div className="pc-pin-prompt" role="note">
    <span><strong>设置你的 4 位 PIN</strong> Set your PIN for the store computer</span>
    <Button size="small" type="primary" onClick={() => setOpen(true)}>设置 Set</Button>
    <MyPinModal open={open} onClose={() => setOpen(false)} onSaved={refresh} />
  </div>
}
