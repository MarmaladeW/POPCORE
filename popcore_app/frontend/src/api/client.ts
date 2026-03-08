import axios from 'axios'

// Injected once from App.tsx after Auth0 is ready
let _getToken: (() => Promise<string>) | null = null

export function setTokenGetter(fn: () => Promise<string>) {
  _getToken = fn
}

const client = axios.create({ baseURL: '/api' })

client.interceptors.request.use(async (config) => {
  if (_getToken) {
    try {
      const token = await _getToken()
      config.headers.Authorization = `Bearer ${token}`
    } catch {
      // token fetch failed — request will get a 401 and we handle below
    }
  }
  return config
})

client.interceptors.response.use(
  (r) => r,
  (err) => {
    // Auth0 SDK handles re-login on 401 via useAuth0().loginWithRedirect
    return Promise.reject(err)
  },
)

export default client
