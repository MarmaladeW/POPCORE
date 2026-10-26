import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isStaleChunkError } from './lazyPage.ts'

test('page files missing after a deploy are recognised in each browser', () => {
  assert.ok(isStaleChunkError(new TypeError('Failed to fetch dynamically imported module: https://popcore.store/assets/Stock-CuEr20vv.js')))
  assert.ok(isStaleChunkError(new TypeError('Importing a module script failed.')))
  assert.ok(isStaleChunkError(new TypeError("Failed to load module script: Expected a JavaScript module script but the server responded with a MIME type of \"text/html\".")))
  assert.ok(isStaleChunkError(new Error('error loading dynamically imported module')))
})

test('ordinary page errors are not mistaken for a new version', () => {
  assert.ok(!isStaleChunkError(new TypeError("Cannot read properties of undefined (reading 'map')")))
  assert.ok(!isStaleChunkError(new Error('Request failed with status code 500')))
})
