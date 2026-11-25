import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { Auth0Provider } from '@auth0/auth0-react'
import App from './App'
import './lib/reconciliationNavigation'
import 'antd/dist/reset.css'
import '@fontsource-variable/noto-sans-sc'
import './styles/tokens.css'
import './index.css'
import { safeReturn } from './lib/returnTo'

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
      // Stay signed in on phones (owner, 2026-11-20): the sign-in is kept on the device and renewed with a refresh
      // token, so reopening POPCORE does not bounce through the sign-in page. Until refresh tokens are turned on
      // for the app in Auth0, the kept sign-in lasts as long as its access token and then renews as before.
      cacheLocation="localstorage"
      useRefreshTokens
      useRefreshTokensFallback
      onRedirectCallback={(appState) => {
        // Back to the page the sign-in started from; React Router follows the history change.
        window.history.replaceState({}, '', safeReturn(appState?.returnTo))
        window.dispatchEvent(new PopStateEvent('popstate'))
      }}
    >
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </Auth0Provider>
  </React.StrictMode>,
)
