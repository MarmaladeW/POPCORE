// Where the phone is, for 打卡 (owner, 2026-11-27: punch in only at the store, by GPS) and for saving a store's spot.

export type Spot = { lat: number; lng: number; accuracy: number }
export type LocationProblem = 'denied' | 'timeout' | 'unavailable' | 'unsupported'

export class LocationError extends Error {
  readonly problem: LocationProblem
  constructor(problem: LocationProblem) { super(problem); this.problem = problem }
}

export function currentSpot(): Promise<Spot> {
  return new Promise((resolve, reject) => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) { reject(new LocationError('unsupported')); return }
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => resolve({ lat: coords.latitude, lng: coords.longitude, accuracy: coords.accuracy }),
      (error) => reject(new LocationError(error.code === 1 ? 'denied' : error.code === 3 ? 'timeout' : 'unavailable')),
      { enableHighAccuracy: true, timeout: 20_000, maximumAge: 0 },
    )
  })
}

export function locationProblemText(problem: LocationProblem): string {
  return {
    denied: '打卡要用定位，请允许 popcore.store 使用你的位置。iPhone：设置 → 隐私与安全性 → 定位服务 → Safari 网站 → 使用 App 期间 · '
      + 'Punch-in needs your location. On iPhone: Settings → Privacy & Security → Location Services → Safari Websites → While Using the App.',
    timeout: '找不到你的位置，请再试一次 · Could not find where you are. Try again.',
    unavailable: '找不到你的位置，请打开定位再试 · Could not find where you are. Turn on Location Services and try again.',
    unsupported: '这个浏览器不能定位，请换手机的 Safari 或 Chrome · This browser cannot share a location. Use Safari or Chrome on your phone.',
  }[problem]
}

/** "43.6532, -79.3832" as Google Maps copies a pin (also with spaces or no comma), or null. */
export function parseCoordinates(text: string): { lat: number; lng: number } | null {
  const match = text.trim().match(/^(-?\d{1,2}(?:\.\d+)?)\s*[,，\s]\s*(-?\d{1,3}(?:\.\d+)?)$/)
  if (!match) return null
  const lat = Number(match[1]), lng = Number(match[2])
  return Math.abs(lat) <= 90 && Math.abs(lng) <= 180 ? { lat, lng } : null
}

export const mapLink = ({ lat, lng }: { lat: number; lng: number }) => `https://www.google.com/maps?q=${lat},${lng}`
