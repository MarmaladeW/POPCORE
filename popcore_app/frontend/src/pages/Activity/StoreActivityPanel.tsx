import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { getActivity, type ActivityEntry } from '../../api/activity'
import { Panel } from '../../components/pc/Panel'
import { useAppStore } from '../../store'
import { ActivityRow } from '.'
import ActivityDetailSheet from './ActivityDetail'
import './activity.css'

// The store's newest 动态 on the home page (owner, 2026-11-20): who did what today, a tap from what each changed.
export default function StoreActivityPanel() {
  const store = useAppStore(state => state.selectedStore)
  const code = store?.code
  const [entries, setEntries] = useState<ActivityEntry[]>()
  const [opened, setOpened] = useState<number | null>(null)
  useEffect(() => {
    if (!code || code === 'ALL') { setEntries([]); return }
    const controller = new AbortController()
    getActivity({ store_code: code, limit: 5 }, controller.signal).then(page => setEntries(Array.isArray(page?.entries) ? page.entries : []))
      .catch(() => { if (!controller.signal.aborted) setEntries([]) })
    return () => controller.abort()
  }, [code])
  if (!code || code === 'ALL') return null
  return <Panel zh="店里动态" en="Store activity" label="Store activity" tone="ok" action={<Link to="/activity">查看全部 All</Link>}>
    {entries === undefined ? <p className="pc-quiet">…</p>
      : entries.length ? <div className="pc-act-list pc-act-compact">{entries.map(entry => <ActivityRow key={entry.id} entry={entry} onOpen={setOpened} />)}</div>
        : <p className="pc-quiet">今天店里还没有动态 · Nothing recorded at the store today</p>}
    <ActivityDetailSheet entryId={opened} onClose={() => setOpened(null)} />
  </Panel>
}
