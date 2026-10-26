import { lazy, type ComponentType } from 'react'

const RELOADED = 'popcore:reloaded-for-new-version'

// A deploy replaces the hashed page files, so a tab opened before it asks for files that no longer exist.
export function isStaleChunkError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error)
  return /dynamically imported module|Importing a module script failed|Loading chunk|module script.*MIME type|error loading dynamically imported/i.test(message)
}

/** React.lazy that reloads the page once to pick up the new version when a page file is gone after a deploy. */
export function lazyPage<T extends ComponentType<any>>(load: () => Promise<{ default: T }>) {
  return lazy(() => load().then(module => {
    try { sessionStorage.removeItem(RELOADED) } catch { /* storage unavailable */ }
    return module
  }).catch(error => {
    let reloaded = true
    try { reloaded = sessionStorage.getItem(RELOADED) === '1' } catch { /* storage unavailable */ }
    if (isStaleChunkError(error) && !reloaded) {
      try { sessionStorage.setItem(RELOADED, '1') } catch { /* storage unavailable */ }
      window.location.reload()
      return new Promise<{ default: T }>(() => {})  // keep the loading state until the reload happens
    }
    throw error
  }))
}
