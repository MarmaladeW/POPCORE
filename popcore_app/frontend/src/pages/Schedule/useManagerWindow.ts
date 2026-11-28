import { useEffect, useState } from 'react'
import type { ManagerWindow } from './managerWindow'
import { getScheduleConfig } from './scheduleApi'

/** The dates this manager may look at in 排班, from the server; null for an admin, and until the server answers. */
export default function useManagerWindow(): ManagerWindow | null {
  const [range, setRange] = useState<ManagerWindow | null>(null)
  useEffect(() => {
    let live = true
    getScheduleConfig().then(config => { if (live) setRange(config.manager_window ?? null) }).catch(() => undefined)
    return () => { live = false }
  }, [])
  return range
}
