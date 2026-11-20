import client from './client'

// 可疑人员 Watch list (owner, 2026-11-19): everyone signed in reads it; managers and admins write. Photos come as blobs
// through the signed-in API, never from a public address.
export type WatchStatus = 'open' | 'resolved'
export interface WatchPhoto { id:number; width:number; height:number }
export interface WatchRecord {
  id:number; info:string; store_code:string|null; store_name:string|null; seen_on:string; status:WatchStatus
  resolved_at:string|null; resolved_by_name:string|null; resolve_note:string|null
  created_at:string; created_by_name:string|null; updated_at:string|null; updated_by_name:string|null
  photos:WatchPhoto[]
}
export interface WatchListPage {
  records:WatchRecord[]; stores:Array<{ code:string; name:string }>; today:string; can_edit:boolean; max_photos:number
}
export interface WatchFields { info:string; store_code:string|null; seen_on:string }

// One photo per request keeps each upload under the server's request size limit.
const UPLOAD_TIMEOUT = 60_000
const photoForm = (photo:File, fields?:WatchFields) => {
  const body = new FormData()
  if (fields) { body.append('info', fields.info); body.append('store_code', fields.store_code ?? ''); body.append('seen_on', fields.seen_on) }
  body.append('photos', photo)
  return body
}

export const getWatchList = (signal?:AbortSignal) =>
  client.get<WatchListPage>('/watch-list', { signal }).then(response => response.data)
export const getWatchPhoto = (photoId:number, signal?:AbortSignal) =>
  client.get<Blob>(`/watch-list/photos/${photoId}`, { responseType:'blob', signal }).then(response => response.data)
export const addWatchRecord = (fields:WatchFields, firstPhoto:File) =>
  client.post<WatchRecord>('/watch-list', photoForm(firstPhoto, fields), { timeout:UPLOAD_TIMEOUT }).then(response => response.data)
export const addWatchPhoto = (recordId:number, photo:File) =>
  client.post<WatchRecord>(`/watch-list/${recordId}/photos`, photoForm(photo), { timeout:UPLOAD_TIMEOUT }).then(response => response.data)
export const updateWatchRecord = (recordId:number, fields:Partial<WatchFields>) =>
  client.patch<WatchRecord>(`/watch-list/${recordId}`, fields).then(response => response.data)
export const removeWatchPhoto = (recordId:number, photoId:number) =>
  client.delete<WatchRecord>(`/watch-list/${recordId}/photos/${photoId}`).then(response => response.data)
export const resolveWatchRecord = (recordId:number, note:string) =>
  client.post<WatchRecord>(`/watch-list/${recordId}/resolve`, { note }).then(response => response.data)
export const reopenWatchRecord = (recordId:number) =>
  client.post<WatchRecord>(`/watch-list/${recordId}/reopen`, {}).then(response => response.data)
export const deleteWatchRecord = (recordId:number) =>
  client.delete<{ deleted:number; photos_removed:number }>(`/watch-list/${recordId}`).then(response => response.data)
