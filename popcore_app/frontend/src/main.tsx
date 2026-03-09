import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { Auth0Provider } from '@auth0/auth0-react'
import App from './App'
import 'antd/dist/reset.css'
import './index.css'

const AUTH0_DOMAIN   = 'dev-n0833ddaix42sr23.us.auth0.com'
const AUTH0_CLIENT_ID = 'LA11pKQ6PFceQOm3dzB9M5iFkbLUFrVB'
const AUTH0_AUDIENCE  = 'https://popcore/api'

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
