import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { Auth0Provider } from '@auth0/auth0-react'
import App from './App'
import 'antd/dist/reset.css'
import './index.css'

const AUTH0_DOMAIN    = import.meta.env.VITE_AUTH0_DOMAIN    as string
const AUTH0_CLIENT_ID = import.meta.env.VITE_AUTH0_CLIENT_ID as string
const AUTH0_AUDIENCE  = import.meta.env.VITE_AUTH0_AUDIENCE  as string

// Fail loud instead of redirecting to https://undefined/authorize: a bundle
// built without VITE_AUTH0_* (missing .env.production) must never ship silently.
if (!AUTH0_DOMAIN || !AUTH0_CLIENT_ID) {
  document.getElementById('root')!.innerHTML =
    '<div style="font-family:sans-serif;padding:40px;text-align:center">' +
    '<h2>Build configuration error</h2>' +
    '<p>This bundle was built without Auth0 settings (VITE_AUTH0_DOMAIN / VITE_AUTH0_CLIENT_ID).</p>' +
    '<p>Rebuild the frontend with <code>frontend/.env.production</code> present.</p></div>'
  throw new Error('Missing VITE_AUTH0_DOMAIN / VITE_AUTH0_CLIENT_ID at build time')
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Auth0Provider
      domain={AUTH0_DOMAIN}
      clientId={AUTH0_CLIENT_ID}
      authorizationParams={{
        redirect_uri: window.location.origin,
        audience: AUTH0_AUDIENCE,
      }}
      onRedirectCallback={() => {
        window.history.replaceState({}, '', '/')
      }}
    >
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </Auth0Provider>
  </React.StrictMode>,
)
