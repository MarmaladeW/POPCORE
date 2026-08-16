import { useEffect, useState } from 'react'
import { Input, Popconfirm, message } from 'antd'
import { Plus, Pencil, Check, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  createTrainee, deleteTrainee, getTrainees, renameEmployee, type Employee,
} from './scheduleApi'

/** Manage trainees: people without their own sign-in who can still be
 *  scheduled. They appear under "Trainees" in the shift dialog and their
 *  shifts render amber + dashed on the calendar. */
export default function Trainees() {
  const [trainees, setTrainees]   = useState<Employee[]>([])
  const [loading, setLoading]     = useState(true)
  const [newName, setNewName]     = useState('')
  const [adding, setAdding]       = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editName, setEditName]   = useState('')
  const [msgApi, msgCtx] = message.useMessage()

  const load = () => {
    setLoading(true)
    getTrainees()
      .then(setTrainees)
      .catch(() => msgApi.error('Could not load trainees'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const handleAdd = async () => {
    const name = newName.trim()
    if (!name) return
    setAdding(true)
    try {
      await createTrainee(name)
      setNewName('')
      load()
      msgApi.success('Trainee added — they can now be scheduled')
    } catch {
      msgApi.error('Could not add trainee')
    } finally {
      setAdding(false)
    }
  }

  const handleRename = async (id: number) => {
    const name = editName.trim()
    if (!name) { setEditingId(null); return }
    try {
      await renameEmployee(id, name)
      setEditingId(null)
      load()
    } catch {
      msgApi.error('Could not rename trainee')
    }
  }

  const handleRemove = async (id: number) => {
    try {
      await deleteTrainee(id)
      load()
      msgApi.success('Trainee removed (past shifts stay on record)')
    } catch {
      msgApi.error('Could not remove trainee')
    }
  }

  return (
    <div className="space-y-4 max-w-xl">
      {msgCtx}
      <p className="text-sm text-muted-foreground m-0">
        Trainees don't get their own sign-in. Add them here, then assign
        their shifts on the Team Schedule like anyone else — trainee shifts
        show up <span
          className="px-1 rounded font-semibold"
          style={{ background: '#FEF3C7', color: '#92400E', border: '1px dashed #F59E0B' }}
        >amber + dashed</span> so it's obvious a trainee is working.
        They also appear in the coverage checklist.
      </p>

      <div className="flex gap-2">
        <Input
          placeholder="Trainee name"
          value={newName}
          onChange={e => setNewName(e.target.value)}
          onPressEnter={handleAdd}
          maxLength={120}
          style={{ maxWidth: 260 }}
        />
        <Button onClick={handleAdd} disabled={adding || !newName.trim()}>
          <Plus className="size-4 mr-1" />
          Add trainee
        </Button>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : trainees.length === 0 ? (
        <p className="text-sm text-muted-foreground">No trainees yet.</p>
      ) : (
        <ul className="m-0 p-0 list-none space-y-1.5">
          {trainees.map(t => (
            <li
              key={t.id}
              className="flex items-center gap-2 rounded-lg border border-border px-3 py-2"
            >
              <span
                className="shrink-0 text-[9px] font-bold rounded px-1"
                style={{ background: '#FEF3C7', color: '#92400E', border: '1px dashed #F59E0B' }}
              >
                TRAINEE
              </span>
              {editingId === t.id ? (
                <>
                  <Input
                    size="small"
                    value={editName}
                    onChange={e => setEditName(e.target.value)}
                    onPressEnter={() => handleRename(t.id)}
                    maxLength={120}
                    autoFocus
                    style={{ maxWidth: 220 }}
                  />
                  <Button size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={() => handleRename(t.id)}>
                    <Check className="size-4" />
                  </Button>
                  <Button size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={() => setEditingId(null)}>
                    <X className="size-4" />
                  </Button>
                </>
              ) : (
                <>
                  <span className="font-medium text-sm truncate">{t.name || `Trainee ${t.id}`}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 w-7 p-0"
                    title="Rename"
                    onClick={() => { setEditingId(t.id); setEditName(t.name || '') }}
                  >
                    <Pencil className="size-3.5" />
                  </Button>
                </>
              )}
              <Popconfirm
                title={`Remove trainee ${t.name || t.id}?`}
                description="They disappear from scheduling; past shifts stay on record."
                onConfirm={() => handleRemove(t.id)}
                okText="Remove"
                okButtonProps={{ danger: true }}
              >
                <Button size="sm" variant="destructive" className="ml-auto h-7">
                  Remove
                </Button>
              </Popconfirm>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
