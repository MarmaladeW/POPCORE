import client from './client'

// 待盘点 Count next time (popcore_app/count_marks.py): a manager marks a product at places of one store;
// the next count there lists it first, and a saved count of it there closes the mark.
export interface CountMark {
  id: number
  store_id: number
  location_id: number
  place: string
  place_name: string
  product_id: number
  sku: string | null
  name: string
  design_name: string | null
  stock_form: string | null
  reason: string
  source_type: 'report_stock_issue' | null
  source_id: number | null
  marked_by: string
  marked_at: string
  status: 'open' | 'counted' | 'removed'
  closed_by: string | null
  closed_at: string | null
  image: string | null
}

export interface MarkResult { added: string[]; already: string[]; marks: CountMark[] }

export const getCountMarks = (store: string, productId?: number, signal?: AbortSignal) =>
  client.get<{ marks: CountMark[] }>('/counting/marks', {
    params: { store_code: store, ...(productId ? { product_id: productId } : {}) }, signal,
  }).then(response => response.data.marks)

export const markForCount = (body: {
  store_code: string; product_id: number; locations: string[]; reason: string; notice_id?: number
}) => client.post<MarkResult>('/counting/marks', body).then(response => response.data)

export const removeCountMark = (id: number) =>
  client.post<{ id: number; status: 'removed'; marks: CountMark[] }>(`/counting/marks/${id}/remove`).then(response => response.data)
