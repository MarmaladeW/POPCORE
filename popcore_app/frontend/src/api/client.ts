import axios, { type AxiosError } from 'axios'
import { Modal } from 'antd'

// Injected once from App.tsx after Auth0 is ready
let _getToken: (() => Promise<string>) | null = null
let _sessionWarningShown = false

export function setTokenGetter(fn: () => Promise<string>) {
  _getToken = fn
  _sessionWarningShown = false
}

const client = axios.create({ baseURL: '/api' })

client.interceptors.request.use(async (config) => {
  if (_getToken) {
    try {
      const token = await _getToken()
      config.headers.Authorization = `Bearer ${token}`
    } catch {
      // token fetch failed — request will get a 401 handled below
    }
  }
  return config
})

client.interceptors.response.use(
  (r) => r,
  (err: AxiosError) => {
    if (err.response?.status === 401 && !_sessionWarningShown) {
      _sessionWarningShown = true
      Modal.warning({
        title: 'Session Expired',
        content: 'Your session has expired. Please reload the page to log back in.',
        okText: 'Reload',
        onOk: () => window.location.reload(),
      })
    }
    const serverMessage = (err.response?.data as any)?.error
    const enriched = serverMessage ? Object.assign(err, { _serverMessage: serverMessage }) : err
    return Promise.reject(enriched)
  },
)

export default client
