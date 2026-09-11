import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { defineConfig } from '../../frontend/node_modules/vite/dist/node/index.js'

const here = dirname(fileURLToPath(import.meta.url))
const frontend = resolve(here, '../../frontend')

export default defineConfig({
  root: frontend,
  define: {
    'import.meta.env.VITE_AUTH0_DOMAIN': JSON.stringify('fixture.invalid'),
    'import.meta.env.VITE_AUTH0_CLIENT_ID': JSON.stringify('fixture-client'),
    'import.meta.env.VITE_AUTH0_AUDIENCE': JSON.stringify('fixture-audience'),
  },
  resolve: {
    alias: {
      '@auth0/auth0-react': resolve(here, 'mock-auth.tsx'),
      '@': resolve(frontend, 'src'),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: true,
    fs: { allow: [resolve(here, '../../..')] },
  },
})
