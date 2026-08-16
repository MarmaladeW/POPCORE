import { useState, useEffect, useRef } from 'react'
import { Form, Select, Row, Col, Input, Radio, message } from 'antd'
import dayjs from 'dayjs'
import {
  createShift,
  updateShift,
  deleteShift,
  checkConflicts,
  type Availability,
  type ConflictInfo,
  type Employee,
  type Shift,
} from './scheduleApi'
import {
  DEFAULT_STORE_HOURS, halfSplitFor, hoursForStore, openHoursFor, positionsForStore,
  type OpenHoursConfig, type PositionsMap, type ShiftPreset, type StoreHoursMap,
} from './openHours'
import { useAppStore } from '../../store'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog'

interface Props {
  open: boolean
  date: string | null
  employees: Employee[]
  existing: Shift | null
  availForDate: Availability[]
  /** Store preselected in the Location field for new shifts (e.g. the
   *  calendar section the manager clicked in). */
  defaultStoreCode?: string | null
  /** Per-store opening hours from the schedule config (drives the shift
   *  presets for whichever store is selected in the Location field). */
  storeHours?: StoreHoursMap
  /** Custom fixed slots from Settings → Scheduling (e.g. "3-8" = 15:00–20:00). */
  shiftPresets?: ShiftPreset[]
  /** In-store positions per store from Settings → Scheduling. */
  positionsMap?: PositionsMap
  onClose: () => void
  onSaved: () => void
}

interface ShiftFormValues {
  employee_id: number
  store_code:  string
  start_time:  string
  end_time:    string
  position?:   string
  notes?:      string
}

type SavePhase = 'idle' | 'checking' | 'conflict' | 'error'
/** Built-in presets, or `slot:<index>` for a custom fixed slot. */
type Preset = 'full' | 'first' | 'second' | 'custom' | `slot:${number}`

function buildTimeOptions() {
  const opts: { value: string; label: string }[] = []
  for (let h = 6; h < 24; h++) {
    for (const m of [0, 30]) {
      const label = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
      opts.push({ value: label, label })
    }
  }
  return opts
}
const TIME_OPTIONS = buildTimeOptions()

/** Default shift times follow store opening hours (from the schedule config);
 *  half shifts split at 17:00 weekdays, 16:30 weekends. */
function presetTimes(
  date: string | null,
  hours: OpenHoursConfig,
): Record<Exclude<Preset, 'custom'>, { start: string; end: string }> {
  const d = date ?? dayjs().format('YYYY-MM-DD')
  const { start: open, end: close } = openHoursFor(d, hours)
  const mid = halfSplitFor(d)
  return {
    full:   { start: open, end: close },
    first:  { start: open, end: mid },
    second: { start: mid,  end: close },
  }
}

function matchPreset(
  date: string | null,
  hours: OpenHoursConfig,
  slots: ShiftPreset[],
  start?: string,
  end?: string,
): Preset {
  if (!start || !end) return 'custom'
  const p = presetTimes(date, hours)
  for (const key of ['full', 'first', 'second'] as const) {
    if (p[key].start === start && p[key].end === end) return key
  }
  const slotIdx = slots.findIndex(s => s.start === start && s.end === end)
  if (slotIdx >= 0) return `slot:${slotIdx}`
  return 'custom'
}

export default function ShiftModal({
  open, date, employees, existing, availForDate, defaultStoreCode,
  storeHours = DEFAULT_STORE_HOURS, shiftPresets = [], positionsMap = {},
  onClose, onSaved,
}: Props) {
  const [form] = Form.useForm()
  const [msgApi, ctxHolder] = message.useMessage()
  const { selectedStore, stores } = useAppStore()

  // Presets follow the store chosen in the Location field
  const watchedStore: string | undefined = Form.useWatch('store_code', form)
  const openHours: OpenHoursConfig = hoursForStore(
    watchedStore || existing?.store_code || defaultStoreCode || '', storeHours,
  )

  const [savePhase,  setSavePhase]  = useState<SavePhase>('idle')
  const [conflicts,  setConflicts]  = useState<ConflictInfo[]>([])
  const [preset,     setPreset]     = useState<Preset>('custom')
  const pendingValues = useRef<ShiftFormValues | null>(null)

  const realStores = stores.filter((s) => s.code !== 'ALL')

  const availByEmpId: Record<number, Availability> = {}
  for (const a of availForDate) availByEmpId[a.employee_id] = a

  const staff    = employees.filter((e) => !e.is_trainee)
  const trainees = employees.filter((e) => !!e.is_trainee)
  const sortedStaff = [
    ...staff.filter((e) => availByEmpId[e.id]),
    ...staff.filter((e) => !availByEmpId[e.id]),
  ]

  // Positions configured for the selected store (Settings → Scheduling)
  const positionOptions = positionsForStore(
    watchedStore || existing?.store_code || defaultStoreCode || '', positionsMap,
  )
  const positionSelectOptions = [
    ...positionOptions,
    // keep a saved position visible even if it was removed from settings
    ...(existing?.position && !positionOptions.includes(existing.position)
      ? [existing.position] : []),
  ].map(p => ({ value: p, label: p }))

  /** Times for a preset — custom slots are fixed, built-ins follow store hours. */
  const timesForPreset = (p: Exclude<Preset, 'custom'>): { start: string; end: string } => {
    if (p.startsWith('slot:')) {
      const slot = shiftPresets[Number(p.slice(5))]
      return slot ? { start: slot.start, end: slot.end } : presetTimes(date, openHours).full
    }
    return presetTimes(date, openHours)[p as 'full' | 'first' | 'second']
  }

  const syncPresetFromForm = () => {
    setPreset(matchPreset(date, openHours, shiftPresets, form.getFieldValue('start_time'), form.getFieldValue('end_time')))
  }

  useEffect(() => {
    if (!open) {
      setSavePhase('idle')
      setConflicts([])
      pendingValues.current = null
      return
    }
    if (existing) {
      form.setFieldsValue({
        employee_id: existing.employee_id,
        store_code:  existing.store_code
          || (selectedStore && selectedStore.code !== 'ALL' ? selectedStore.code : undefined),
        start_time:  existing.start_time,
        end_time:    existing.end_time,
        position:    existing.position || undefined,
        notes:       existing.notes,
      })
      setPreset(matchPreset(existing.date, openHours, shiftPresets, existing.start_time, existing.end_time))
    } else {
      form.resetFields()
      form.setFieldsValue({
        store_code: defaultStoreCode
          || (selectedStore && selectedStore.code !== 'ALL' ? selectedStore.code : undefined)
          || (realStores.length === 1 ? realStores[0].code : undefined),
      })
      setPreset('custom')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, existing, form])

  // When the Location changes, a selected preset re-applies with that store's
  // hours (e.g. full day becomes 12:00–21:00 at MK); custom times just get
  // re-checked against the new store's presets.
  useEffect(() => {
    if (!open) return
    if (preset !== 'custom') {
      const t = timesForPreset(preset)
      form.setFieldsValue({ start_time: t.start, end_time: t.end })
    } else {
      syncPresetFromForm()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchedStore])

  const handleEmployeeChange = (empId: number) => {
    if (existing) return
    const avail = availByEmpId[empId]
    if (avail) {
      form.setFieldsValue({ start_time: avail.start_time, end_time: avail.end_time })
      syncPresetFromForm()
    }
  }

  const handlePresetChange = (value: Preset) => {
    setPreset(value)
    if (value === 'custom') return
    const t = timesForPreset(value)
    form.setFieldsValue({ start_time: t.start, end_time: t.end })
    form.validateFields(['start_time', 'end_time']).catch(() => {})
  }

  const doCreateShift = async (values: ShiftFormValues) => {
    await createShift({
      employee_id: values.employee_id,
      date:        date!,
      start_time:  values.start_time,
      end_time:    values.end_time,
      notes:       values.notes ?? '',
      position:    values.position ?? '',
      store_code:  values.store_code,
    })
    msgApi.success('Shift saved')
    setSavePhase('idle')
    onSaved()
    onClose()
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields() as ShiftFormValues

      if (existing) {
        // Updates can't change employee or date, so no conflict possible
        await updateShift(existing.id, {
          start_time: values.start_time,
          end_time:   values.end_time,
          notes:      values.notes ?? '',
          position:   values.position ?? '',
          store_code: values.store_code,
        })
        msgApi.success('Shift saved')
        onSaved()
        onClose()
        return
      }

      // New shift: check for conflicts before saving
      setSavePhase('checking')
      try {
        const result = await checkConflicts({
          employee_id: values.employee_id,
          date:        date!,
          store_code:  values.store_code,
        })
        if (result.has_conflict) {
          pendingValues.current = values
          setConflicts(result.conflicts)
          setSavePhase('conflict')
        } else {
          await doCreateShift(values)
        }
      } catch {
        pendingValues.current = values
        setSavePhase('error')
      }
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return
      msgApi.error('Failed to save shift')
    }
  }

  const handleConfirmAnyway = async () => {
    if (!pendingValues.current) return
    try {
      await doCreateShift(pendingValues.current)
    } catch {
      msgApi.error('Failed to save shift')
      setSavePhase('idle')
    }
  }

  const handleGoBack = () => {
    if (pendingValues.current) {
      form.setFieldsValue(pendingValues.current)
    }
    setSavePhase('idle')
    setConflicts([])
  }

  const handleDelete = async () => {
    if (!existing) return
    try {
      await deleteShift(existing.id)
      msgApi.success('Shift deleted')
      onSaved()
      onClose()
    } catch {
      msgApi.error('Failed to delete shift')
    }
  }

  const showForm = savePhase === 'idle' || savePhase === 'checking'

  const conflictEmpName = pendingValues.current
    ? (employees.find((e) => e.id === pendingValues.current!.employee_id)?.name
        || `Employee ${pendingValues.current.employee_id}`)
    : ''

  const conflictStore = pendingValues.current
    ? realStores.find((s) => s.code === pendingValues.current!.store_code)
    : null

  return (
    <>
      {ctxHolder}
      <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
        <DialogContent style={{ maxWidth: 480 }}>
          <DialogHeader>
            <DialogTitle>Assign shift — {date ?? ''}</DialogTitle>
          </DialogHeader>

          {/* Conflict warning panel */}
          {savePhase === 'conflict' && (
            <div className="rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 space-y-2">
              <p className="font-semibold text-amber-900 text-sm">
                Warning: {conflictEmpName} already has a shift on {date} at:
              </p>
              <ul className="space-y-0.5 pl-1">
                {conflicts.map((c) => (
                  <li key={c.shift_id} className="text-sm text-amber-800">
                    <span className="font-medium">{c.store_name || c.store_code}</span>
                    {': '}
                    {c.start_time.slice(11, 16)} – {c.end_time.slice(11, 16)}
                  </li>
                ))}
              </ul>
              <p className="text-sm text-amber-800">
                Do you still want to assign this shift at {conflictStore?.name || conflictStore?.code || 'this store'}?
              </p>
            </div>
          )}

          {/* Conflict check error panel */}
          {savePhase === 'error' && (
            <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3">
              <p className="text-sm text-red-700">
                Could not check for conflicts. Save anyway?
              </p>
            </div>
          )}

          {/* Availability summary — only when form is visible */}
          {showForm && availForDate.length > 0 && (
            <div className="rounded-lg bg-emerald-50 border border-emerald-200 px-3 py-2 text-sm">
              <div className="font-semibold text-emerald-800 mb-1">
                Available on {date}:
              </div>
              <div className="flex flex-wrap gap-1.5">
                {availForDate.map((a) => {
                  const emp  = employees.find((e) => e.id === a.employee_id)
                  const name = emp
                    ? (emp.name || emp.email || `ID ${emp.id}`)
                    : (a.employee_name || `ID ${a.employee_id}`)
                  return (
                    <Badge key={a.id} variant="outline" className="text-emerald-700 border-emerald-300 bg-white">
                      {name} {a.start_time}–{a.end_time}
                    </Badge>
                  )
                })}
              </div>
            </div>
          )}

          {/* Form — kept mounted to preserve values; hidden during conflict/error */}
          <div style={showForm ? {} : { display: 'none' }}>
            <Form form={form} layout="vertical">
              <Row gutter={12}>
                <Col span={14}>
                  <Form.Item
                    name="employee_id"
                    label="Employee"
                    rules={[{ required: true, message: 'Select an employee' }]}
                  >
                    <Select
                      showSearch
                      placeholder="Select employee"
                      optionFilterProp="label"
                      disabled={!!existing}
                      onChange={handleEmployeeChange}
                      getPopupContainer={(trigger) => trigger.parentElement!}
                      options={[
                        {
                          label: 'Staff',
                          options: sortedStaff.map((e) => {
                            const avail    = availByEmpId[e.id]
                            const baseName = e.name || e.email || e.auth0_id
                            return {
                              value: e.id,
                              label: avail
                                ? `${baseName}  ·  ${avail.start_time}–${avail.end_time}`
                                : baseName,
                            }
                          }),
                        },
                        ...(trainees.length > 0 ? [{
                          label: 'Trainees',
                          options: trainees.map((e) => ({
                            value: e.id,
                            label: `${e.name || `Trainee ${e.id}`} (Trainee)`,
                          })),
                        }] : []),
                      ]}
                    />
                  </Form.Item>
                </Col>
                <Col span={10}>
                  <Form.Item
                    name="store_code"
                    label="Location"
                    rules={[{ required: true, message: 'Select a location' }]}
                  >
                    <Select
                      placeholder="Store"
                      getPopupContainer={(trigger) => trigger.parentElement!}
                      options={realStores.map((s) => ({
                        value: s.code,
                        label: s.name || s.code,
                      }))}
                    />
                  </Form.Item>
                </Col>
              </Row>

              <Form.Item label="Shift type" style={{ marginBottom: 12 }}>
                <Radio.Group
                  optionType="button"
                  buttonStyle="solid"
                  size="small"
                  value={preset}
                  onChange={(e) => handlePresetChange(e.target.value as Preset)}
                >
                  <Radio.Button value="full">
                    Full day ({presetTimes(date, openHours).full.start}–{presetTimes(date, openHours).full.end})
                  </Radio.Button>
                  <Radio.Button value="first">
                    Half ({presetTimes(date, openHours).first.start}–{presetTimes(date, openHours).first.end})
                  </Radio.Button>
                  <Radio.Button value="second">
                    Half ({presetTimes(date, openHours).second.start}–{presetTimes(date, openHours).second.end})
                  </Radio.Button>
                  {shiftPresets.map((s, i) => (
                    <Radio.Button key={`slot:${i}`} value={`slot:${i}`}>
                      {s.label} ({s.start}–{s.end})
                    </Radio.Button>
                  ))}
                  <Radio.Button value="custom">Custom</Radio.Button>
                </Radio.Group>
              </Form.Item>

              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item
                    name="start_time"
                    label="Start time"
                    rules={[{ required: true, message: 'Required' }]}
                  >
                    <Select
                      showSearch
                      placeholder="09:00"
                      options={TIME_OPTIONS}
                      style={{ width: '100%' }}
                      onChange={syncPresetFromForm}
                      getPopupContainer={(trigger) => trigger.parentElement!}
                    />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item
                    name="end_time"
                    label="End time"
                    dependencies={['start_time']}
                    rules={[
                      { required: true, message: 'Required' },
                      ({ getFieldValue }) => ({
                        validator(_, value) {
                          if (!value || !getFieldValue('start_time')) return Promise.resolve()
                          if (value > getFieldValue('start_time')) return Promise.resolve()
                          return Promise.reject(new Error('Must be after start'))
                        },
                      }),
                    ]}
                  >
                    <Select
                      showSearch
                      placeholder="17:00"
                      options={TIME_OPTIONS}
                      style={{ width: '100%' }}
                      onChange={syncPresetFromForm}
                      getPopupContainer={(trigger) => trigger.parentElement!}
                    />
                  </Form.Item>
                </Col>
              </Row>

              {(positionSelectOptions.length > 0) && (
                <Form.Item
                  name="position"
                  label="Position in store (optional)"
                  style={{ marginBottom: 12 }}
                >
                  <Select
                    allowClear
                    placeholder="e.g. Front / Cashier / End"
                    options={positionSelectOptions}
                    getPopupContainer={(trigger) => trigger.parentElement!}
                  />
                </Form.Item>
              )}

              <Form.Item name="notes" label="Notes (optional)">
                <Input.TextArea rows={2} />
              </Form.Item>
            </Form>
          </div>

          <DialogFooter>
            {savePhase === 'conflict' && (
              <>
                <Button variant="outline" onClick={handleGoBack}>Go Back</Button>
                <Button onClick={handleConfirmAnyway}>Confirm Anyway</Button>
              </>
            )}
            {savePhase === 'error' && (
              <>
                <Button variant="outline" onClick={() => setSavePhase('idle')}>Cancel</Button>
                <Button onClick={handleConfirmAnyway}>Save Anyway</Button>
              </>
            )}
            {showForm && (
              <>
                {existing && (
                  <Button variant="destructive" onClick={handleDelete}>Delete</Button>
                )}
                <Button variant="outline" onClick={onClose}>Cancel</Button>
                <Button onClick={handleSave} disabled={savePhase === 'checking'}>
                  {savePhase === 'checking' ? 'Checking…' : 'Save'}
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
