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
  return config
})

client.interceptors.response.use(
  (r) => r,
  (err: AxiosError) => {
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
