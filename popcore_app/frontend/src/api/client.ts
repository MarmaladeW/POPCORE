import axios, { type AxiosError } from 'axios'
import { Modal } from 'antd'

// Injected once from App.tsx after Auth0 is ready
let _getToken: (() => Promise<string>) | null = null
let _sessionWarningShown = false
let _tokenWarningShown = false

export function setTokenGetter(fn: (() => Promise<string>) | null) {
  _getToken = fn
}

export function resetAuthWarnings() {
  _sessionWarningShown = false
  _tokenWarningShown = false
}

const client = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '/api' })

// Name + PIN on the shared store computer (owner, 2026-11-20): the person at the computer stays picked for a short
// while, so a few changes in a row ask once. Kept in memory only, never stored.
export type ActingPerson = { employeeId: number; pin: string; name: string; until: number }
export const PERSON_MINUTES = 2
let _person: ActingPerson | null = null
let _askForPerson: (() => Promise<ActingPerson | null>) | null = null
const personListeners = new Set<(person: ActingPerson | null) => void>()

export function actingPerson(): ActingPerson | null {
  if (_person && _person.until < Date.now()) setActingPerson(null)
  return _person
}
export function setActingPerson(person: ActingPerson | null) {
  _person = person
  personListeners.forEach(listener => listener(person))
}
export function onActingPerson(listener: (person: ActingPerson | null) => void) {
  personListeners.add(listener)
  return () => { personListeners.delete(listener) }
}
/** The picker (PersonPicker.tsx) registers how to ask who is at the computer. */
export function setPersonAsker(ask: (() => Promise<ActingPerson | null>) | null) {
  _askForPerson = ask
}

client.interceptors.request.use(async (config) => {
  if (!_getToken) return Promise.reject(new Error('Authentication is not ready'))
  let token: string
  try {
    token = await _getToken()
  } catch (error) {
    if (!_tokenWarningShown) {
      _tokenWarningShown = true
      Modal.confirm({
        title: 'Unable to continue sign-in',
        content: 'Authentication could not provide an access token. Check the Auth0 configuration or connection, then sign in again.',
        okText: 'Sign in again',
        cancelText: 'Stay here',
        onOk: () => window.dispatchEvent(new Event('popcore:reauthenticate')),
      })
    }
    return Promise.reject(error)
  }
  config.headers.Authorization = `Bearer ${token}`
  const person = actingPerson()
  if (person) config.headers['X-Popcore-Person'] = `${person.employeeId}:${person.pin}`
  return config
})

client.interceptors.response.use(
  (r) => r,
  async (err: AxiosError) => {
    const code = (err.response?.data as any)?.code
    const original = err.config as (typeof err.config & { _askedForPerson?: boolean }) | undefined
    if (original && !original._askedForPerson && _askForPerson && (code === 'person_needed' || code === 'pin_wrong' && original.headers?.['X-Popcore-Person'])) {
      // The store computer: ask who is doing this, then send the same request once more with their name + PIN.
      if (code === 'pin_wrong') setActingPerson(null)
      const person = await _askForPerson()
      if (person) {
        original._askedForPerson = true
        return client.request(original)
      }
    }
    if (err.response?.status === 401 && !_sessionWarningShown) {
      _sessionWarningShown = true
      Modal.confirm({
        title: 'Sign-in rejected',
        content: 'The API rejected the current access token. Sign in again to continue; your current page and unsaved entries will remain open.',
        okText: 'Sign in again',
        cancelText: 'Stay here',
        onOk: () => window.dispatchEvent(new Event('popcore:reauthenticate')),
      })
    }
    const serverMessage = (err.response?.data as any)?.error
    const enriched = serverMessage ? Object.assign(err, { _serverMessage: serverMessage }) : err
    return Promise.reject(enriched)
  },
)

export default client
