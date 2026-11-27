import { useEffect, useState } from 'react'
import { Alert, Button, Card, Input, Popconfirm, Space, Typography, message } from 'antd'

import client from '../../api/client'
import { currentSpot, LocationError, locationProblemText, mapLink, parseCoordinates } from '../../lib/location'

type Place = { lat: number; lng: number; saved_at: string }
type View = { radius_m: number; stores: { code: string; name: string; saved: Place | null }[] }

// 打卡位置 (owner, 2026-11-27): 打卡 counts only within radius_m of the shift's store. An admin saves each store's spot:
// standing in the store ("use where I am now"), or pasting a pin copied from Google Maps. Admins only, as all of 设置.
export default function PunchPlaceSettings() {
  const [view, setView] = useState<View>()
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState('')
  const [pasted, setPasted] = useState<Record<string, string>>({})

  function load() {
    setFailed(false)
    client.get<View>('/schedule/punch-places').then(({ data }) => setView(data)).catch(() => setFailed(true))
  }
  useEffect(load, [])

  async function save(code: string, spot: { lat: number; lng: number }, note = '') {
    const { data } = await client.put<View>(`/schedule/punch-places/${code}`, { lat: spot.lat, lng: spot.lng })
    setView(data)
    setPasted(value => ({ ...value, [code]: '' }))
    message.success(`${code} 打卡位置已保存 · Punch-in location saved${note}`)
  }

  async function run(key: string, work: () => Promise<void>) {
    setBusy(key)
    try { await work() } catch (cause: any) {
      message.error(cause instanceof LocationError ? locationProblemText(cause.problem)
        : cause?._serverMessage ?? '保存失败 / Unable to save')
    } finally { setBusy('') }
  }

  const saveHere = (code: string) => run(`${code}:here`, async () => {
    const spot = await currentSpot()
    if (spot.accuracy > (view?.radius_m ?? 150)) {
      message.warning(`定位不够准（±${Math.round(spot.accuracy)} 米），没有保存；请到店里再试，或粘贴地图上的坐标 · `
        + `Location only good to ±${Math.round(spot.accuracy)} m, so nothing was saved. Try inside the store, or paste the map pin.`)
      return
    }
    await save(code, spot, ` (±${Math.round(spot.accuracy)} m)`)
  })

  const savePasted = (code: string) => run(`${code}:paste`, async () => {
    const spot = parseCoordinates(pasted[code] ?? '')
    if (!spot) { message.warning('请粘贴像 43.6532, -79.3832 这样的坐标 · Paste coordinates like 43.6532, -79.3832'); return }
    await save(code, spot)
  })

  const clear = (code: string) => run(`${code}:clear`, async () => {
    const { data } = await client.delete<View>(`/schedule/punch-places/${code}`)
    setView(data)
    message.success(`${code} 打卡位置已清除 · Punch-in location cleared`)
  })

  return <Card loading={!view && !failed}>
    {failed && <Alert type="error" showIcon message="加载失败 / Could not be loaded" action={<Button onClick={load}>重试 / Retry</Button>} />}
    {view && <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        员工打卡时，手机要在店 {view.radius_m} 米以内，上班前 30 分钟起才能打卡。没有保存位置的店不能打卡。员工的位置不会保存，动态里只记离店多少米。
        <br />Staff can punch in only with their phone within {view.radius_m} m of the store, from 30 minutes before the
        shift. A store with no location saved takes no punches. Staff locations are not kept; 动态 shows only how far from
        the store they were.
      </Typography.Paragraph>
      {view.stores.map(store => <section key={store.code} aria-label={`${store.code} punch-in location`}>
        <Typography.Text strong>{store.name}（{store.code}）</Typography.Text>
        <div style={{ margin: '4px 0 8px' }}>
          {store.saved
            ? <Typography.Text type="secondary">已保存 Saved {store.saved.saved_at.slice(0, 10)} ·{' '}
                <a href={mapLink(store.saved)} target="_blank" rel="noreferrer">{store.saved.lat}, {store.saved.lng} 地图 Map</a></Typography.Text>
            : <Typography.Text type="warning">未设置：这家店现在不能打卡 · Not set: nobody can punch in here yet</Typography.Text>}
        </div>
        <Space wrap>
          <Button type={store.saved ? 'default' : 'primary'} loading={busy === `${store.code}:here`} disabled={!!busy}
            onClick={() => void saveHere(store.code)}>用我现在的位置 Use where I am now</Button>
          <Space.Compact>
            <Input placeholder="粘贴地图坐标 Paste map pin" value={pasted[store.code] ?? ''} style={{ width: 200 }}
              aria-label={`${store.code} coordinates`} onChange={event => setPasted(value => ({ ...value, [store.code]: event.target.value }))}
              onPressEnter={() => void savePasted(store.code)} />
            <Button loading={busy === `${store.code}:paste`} disabled={!!busy} onClick={() => void savePasted(store.code)}>保存坐标 Save</Button>
          </Space.Compact>
          {store.saved && <Popconfirm title={`清除 ${store.code} 的打卡位置？之后这家店不能打卡 · Clear it? Nobody can punch in here after.`}
            okText="清除 Clear" cancelText="取消 Cancel" onConfirm={() => void clear(store.code)}>
            <Button danger disabled={!!busy} loading={busy === `${store.code}:clear`}>清除 Clear</Button>
          </Popconfirm>}
        </Space>
      </section>)}
    </Space>}
  </Card>
}
