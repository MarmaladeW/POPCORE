import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { defineConfig } from '../../frontend/node_modules/vite/dist/node/index.js'

const here = dirname(fileURLToPath(import.meta.url))
const frontend = resolve(here, '../../frontend')

export default defineConfig({
  root: frontend,
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
