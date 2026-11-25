import { Alert, Button, Modal } from 'antd'
import { useEffect, useRef, useState } from 'react'

import client, { PERSON_MINUTES, onActingPerson, setActingPerson, setPersonAsker, type ActingPerson } from '../../api/client'
import { useAppStore } from '../../store'
import './people.css'

type Person = { id: number; name: string; has_pin: boolean; on_shift: boolean }

// 谁在操作？Who is doing this? On the shared store computer every change asks for a name and that person's PIN
// (owner, 2026-11-20), so 动态 and the website notes name who did it. Staff phones never see this.
export default function PersonPicker() {
  const store = useAppStore(state => state.selectedStore)
  const [open, setOpen] = useState(false)
  const [people, setPeople] = useState<Person[]>()
  const [chosen, setChosen] = useState<Person>()
  const [pin, setPin] = useState('')
  const [error, setError] = useState('')
  const [checking, setChecking] = useState(false)
  const waiting = useRef<((person: ActingPerson | null) => void) | null>(null)

  useEffect(() => {
    setPersonAsker(() => new Promise(resolve => {
      waiting.current = resolve
      setChosen(undefined); setPin(''); setError(''); setOpen(true)
    }))
    return () => setPersonAsker(null)
  }, [])
  useEffect(() => {
    if (!open) return
    client.get<{ people: Person[] }>('/counter/people', { params: { store_code: store?.code !== 'ALL' ? store?.code : undefined } })
      .then(response => setPeople(response.data.people)).catch(() => setPeople([]))
  }, [open, store?.code])

  function finish(person: ActingPerson | null) {
    setOpen(false)
    waiting.current?.(person)
    waiting.current = null
  }
  async function press(digit: string) {
    if (!chosen || checking) return
    const next = (pin + digit).slice(0, 4)
    setPin(next); setError('')
    if (next.length < 4) return
    setChecking(true)
    try {
      const { data } = await client.post<{ name: string; employee_id: number }>('/counter/check', { employee_id: chosen.id, pin: next })
      const person = { employeeId: data.employee_id, pin: next, name: data.name, until: Date.now() + PERSON_MINUTES * 60_000 }
      setActingPerson(person)
      finish(person)
    } catch (cause: any) {
      setError(cause?._serverMessage || 'PIN 不对 Wrong PIN')
      setPin('')
    } finally {
      setChecking(false)
    }
  }

  return <Modal open={open} title={chosen ? `${chosen.name} · 输入 PIN Type your PIN` : '谁在操作？Who is doing this?'}
    onCancel={() => finish(null)} footer={chosen ? <Button onClick={() => { setChosen(undefined); setPin(''); setError('') }}>换人 Someone else</Button> : null}
    // Above the confirm bubble of the button that asked (it waits behind the dimmed page until the PIN is in).
    width={420} zIndex={1100} destroyOnClose>
    {!chosen ? <div className="pc-person-list" role="list" aria-label="People">
      {people === undefined ? <p className="pc-quiet">…</p>
        : people.length === 0 ? <p className="pc-quiet">这家店还没有人设置 PIN。Nobody here has a PIN yet: set one from 我的 PIN on your phone.</p>
        : people.map(person => <button key={person.id} type="button" role="listitem" disabled={!person.has_pin}
            className="pc-person" onClick={() => setChosen(person)}>
            <strong>{person.name}</strong><small>{person.on_shift ? '今天上班 On shift' : person.has_pin ? '' : '没有 PIN No PIN yet'}</small>
          </button>)}
    </div> : <div className="pc-pin">
      <div className="pc-pin-dots" aria-label={`${pin.length} of 4 digits`}>{[0, 1, 2, 3].map(index => <i key={index} className={index < pin.length ? 'on' : ''} />)}</div>
      {error && <Alert type="error" showIcon message={error} />}
      <div className="pc-pin-pad">
        {['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', '⌫'].map((key, index) => key === '' ? <span key={index} />
          : <button key={index} type="button" disabled={checking} aria-label={key === '⌫' ? 'Delete' : key}
              onClick={() => key === '⌫' ? setPin(pin.slice(0, -1)) : press(key)}>{key}</button>)}
      </div>
    </div>}
  </Modal>
}

/** Who is picked at the store computer right now, with a way to stop. */
export function ActingPersonBar() {
  const [person, setPerson] = useState<ActingPerson | null>(null)
  useEffect(() => onActingPerson(setPerson), [])
  useEffect(() => {
    if (!person) return
    const timer = window.setTimeout(() => setActingPerson(null), Math.max(0, person.until - Date.now()))
    return () => window.clearTimeout(timer)
  }, [person])
  if (!person) return null
  return <div className="pc-person-bar" role="status">
    <span><strong>{person.name}</strong> 正在操作 is using this computer</span>
    <Button size="small" onClick={() => setActingPerson(null)}>结束 Done</Button>
  </div>
}
