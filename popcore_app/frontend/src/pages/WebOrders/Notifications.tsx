import { useEffect, useState } from 'react'
import { Button, Switch, message } from 'antd'

import { getWebOrderNotifications, notifyThisDevice, stopNotifyingThisDevice, testWebOrderNotification } from '../../api/webOrders'
import { keyBytes, keyText } from './webOrdersView'

type DeviceState = 'loading' | 'unsupported' | 'install' | 'blocked' | 'off' | 'on'

const SOUND_KEY = 'popcore_web_orders_sound'
let audio: AudioContext | null = null

// The board's own sound for a new order. Browsers let a page play sound only after someone has touched it, so the
// first click anywhere on the page wakes it.
function wakeSound() {
  try {
    audio ||= new AudioContext()
    void audio.resume()
  } catch { /* no sound on this browser */ }
}

export function chime() {
  if (!audio || audio.state !== 'running') return
  const start = audio.currentTime
  for (const [index, pitch] of [880, 1175, 1568].entries()) {
    const at = start + index * 0.16
    const tone = audio.createOscillator(), level = audio.createGain()
    tone.frequency.value = pitch
    level.gain.setValueAtTime(0.0001, at)
    level.gain.exponentialRampToValueAtTime(0.25, at + 0.02)
    level.gain.exponentialRampToValueAtTime(0.0001, at + 0.15)
    tone.connect(level).connect(audio.destination)
    tone.start(at)
    tone.stop(at + 0.16)
  }
}

export function soundOn() {
  try { return localStorage.getItem(SOUND_KEY) !== 'off' } catch { return true }
}

function supported() {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

// An iPhone or iPad gets web notifications only for POPCORE added to its Home Screen and opened from there.
function needsHomeScreen() {
  const apple = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
  const installed = window.matchMedia('(display-mode: standalone)').matches || (navigator as Navigator & { standalone?: boolean }).standalone === true
  return apple && !installed
}

async function currentSubscription() {
  const registration = await navigator.serviceWorker.getRegistration('/')
  return registration ? registration.pushManager.getSubscription() : null
}

// 通知 Notifications (owner, 2026-11-19: "we need our own notification thing"): this device gets a notification for
// every new paid web order of the stores this person has store operations access for, even with POPCORE closed. The
// board also chimes and pops up while it is open.
export default function Notifications() {
  const [state, setState] = useState<DeviceState>('loading')
  const [busy, setBusy] = useState(false)
  const [sound, setSound] = useState(soundOn)

  useEffect(() => {
    let live = true
    async function look() {
      if (!supported()) return needsHomeScreen() ? 'install' : 'unsupported'
      if (Notification.permission === 'denied') return 'blocked'
      const subscription = await currentSubscription()
      if (!subscription) return 'off'
      // Turned on with this POPCORE's key: tell the server again, so it is this person's and still known.
      const { key } = await getWebOrderNotifications()
      if (keyText(subscription.options.applicationServerKey) !== key) return 'off'
      await notifyThisDevice(subscription.toJSON())
      return 'on'
    }
    look().then(next => { if (live) setState(next) }).catch(() => { if (live) setState('off') })
    return () => { live = false }
  }, [])

  useEffect(() => {
    if (!sound) return
    document.addEventListener('pointerdown', wakeSound, { once: true })
    return () => document.removeEventListener('pointerdown', wakeSound)
  }, [sound])

  function toggleSound(next: boolean) {
    setSound(next)
    try { localStorage.setItem(SOUND_KEY, next ? 'on' : 'off') } catch { /* kept for this visit only */ }
    if (next) { wakeSound(); chime() }
  }

  async function turnOn() {
    setBusy(true)
    try {
      // Asked first, straight from the tap: phones refuse the question otherwise.
      const permission = await Notification.requestPermission()
      if (permission !== 'granted') { setState(permission === 'denied' ? 'blocked' : 'off'); return }
      const { key } = await getWebOrderNotifications()
      const registration = await navigator.serviceWorker.register('/sw.js')
      await navigator.serviceWorker.ready
      let subscription = await registration.pushManager.getSubscription()
      if (subscription && keyText(subscription.options.applicationServerKey) !== key) {
        await subscription.unsubscribe()
        subscription = null
      }
      subscription ||= await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) })
      await notifyThisDevice(subscription.toJSON())
      setState('on')
      message.success('This device now gets a notification for every new web order.')
    } catch (cause: any) {
      message.error(cause?._serverMessage || 'Notifications could not be turned on here. Try again.')
    } finally {
      setBusy(false)
    }
  }

  async function turnOff() {
    setBusy(true)
    try {
      const subscription = await currentSubscription()
      if (subscription) {
        await stopNotifyingThisDevice(subscription.endpoint)
        await subscription.unsubscribe()
      }
      setState('off')
    } catch (cause: any) {
      message.error(cause?._serverMessage || 'Notifications could not be turned off. Try again.')
    } finally {
      setBusy(false)
    }
  }

  async function test() {
    setBusy(true)
    try {
      const result = await testWebOrderNotification()
      if (result.sent) message.success(`Test sent to ${result.sent} device${result.sent === 1 ? '' : 's'}.`)
      else message.warning('No device took the test. Turn notifications on again here.')
    } catch (cause: any) {
      message.error(cause?._serverMessage || 'The test could not be sent.')
    } finally {
      setBusy(false)
    }
  }

  const device = {
    loading: <span>…</span>,
    unsupported: <span>This browser cannot show notifications.</span>,
    install: <span>On iPhone or iPad: Share → 添加到主屏幕 Add to Home Screen, open POPCORE from there, then turn them on here.</span>,
    blocked: <span>Blocked for POPCORE in this browser’s settings. Allow notifications there, then come back.</span>,
    off: <Button size="small" type="primary" loading={busy} onClick={turnOn}>在本设备开启 Notify me on this device</Button>,
    on: <>
      <span>本设备已开启 On for this device</span>
      <Button size="small" loading={busy} onClick={test}>测试 Test</Button>
      <Button size="small" disabled={busy} onClick={turnOff}>关闭 Turn off</Button>
    </>,
  }[state]

  return <section className="pc-panel pc-web-notify" aria-label="Notifications">
    <div><strong>新订单通知</strong> <small>New order notifications</small></div>
    <div className="pc-web-notify-row">{device}</div>
    <label className="pc-web-notify-row">
      <Switch size="small" checked={sound} onChange={toggleSound} />
      <span>提示音 Sound on this board</span>
    </label>
  </section>
}
