import { Button, Modal } from 'antd'
import { useEffect, useRef, useState } from 'react'

import client, { PERSON_MINUTES, onActingPerson, setActingPerson, setPersonAsker, type ActingPerson } from '../../api/client'
import { useAppStore } from '../../store'
import './people.css'

type Person = { id: number; name: string; on_shift: boolean }

// 谁在操作？Who is doing this? On the shared store computer every change asks for a name (owner, 2026-11-20), so 动态
// and the website notes name who did it. One tap, no PIN (owner, 2026-11-26). Staff phones never see this.
export default function PersonPicker() {
  const store = useAppStore(state => state.selectedStore)
  const [open, setOpen] = useState(false)
  const [people, setPeople] = useState<Person[]>()
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const waiting = useRef<((person: ActingPerson | null) => void) | null>(null)

  useEffect(() => {
    setPersonAsker(() => new Promise(resolve => {
      waiting.current = resolve
      setPeople(undefined); setFailed(false); setOpen(true)
    }))
    return () => setPersonAsker(null)
  }, [])
  useEffect(() => {
    if (!open) return
    let live = true
    setFailed(false)
    client.get<{ people: Person[] }>('/counter/people', { params: { store_code: store?.code !== 'ALL' ? store?.code : undefined } })
      .then(response => { if (live) setPeople(response.data.people) }).catch(() => { if (live) setFailed(true) })
    return () => { live = false }
  }, [open, store?.code, attempt])

  function finish(person: ActingPerson | null) {
    setOpen(false)
    waiting.current?.(person)
    waiting.current = null
  }
  function pick(chosen: Person) {
    const person = { employeeId: chosen.id, name: chosen.name, until: Date.now() + PERSON_MINUTES * 60_000 }
    setActingPerson(person)
    finish(person)
  }

  return <Modal open={open} title="谁在操作？Who is doing this?" onCancel={() => finish(null)} footer={null}
    // Above the confirm bubble of the button that asked (it waits behind the dimmed page until a name is tapped).
    width={420} zIndex={1100} destroyOnClose>
    {failed ? <p className="pc-person-note" role="alert">名单没加载出来。The list did not load.{' '}
        <Button size="small" onClick={() => setAttempt(count => count + 1)}>重试 Retry</Button></p>
      : people === undefined ? <p className="pc-person-note" role="status">正在加载名单… Loading names…</p>
      : people.length === 0 ? <p className="pc-person-note">这家店还没有员工。管理员在 员工 Users 里加人。Nobody works at this store yet: an admin adds people in Users.</p>
      : <ul className="pc-person-list" aria-label="People">
          {people.map(person => <li key={person.id}><button type="button" className="pc-person" onClick={() => pick(person)}>
            <strong>{person.name}</strong><small>{person.on_shift ? '今天上班 On shift' : ''}</small>
          </button></li>)}
        </ul>}
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
