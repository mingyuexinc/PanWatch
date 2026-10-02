import { fetchAPI } from './client'

export interface MonitorUniverseItem {
  id: number
  stock_id: number
  symbol: string
  name: string
  market: string
  note: string
  added_at: string | null
  updated_at: string | null
  rule_id: number | null
  rule_enabled: boolean
  rule_last_trigger_at: string | null
  rule_last_trigger_price: number | null
}

export interface MonitorUniverseList {
  items: MonitorUniverseItem[]
  total: number
  saved_at: string | null
  ma_period: number
}

export interface MonitorUniverseMutationResult {
  id?: number
  created?: boolean
  removed?: number
  items: MonitorUniverseItem[]
  total: number
  saved_at: string | null
  ma_period: number
}

export interface StockSearchResult {
  symbol: string
  name: string
  market: string
}

export const monitorUniverseApi = {
  list: () => fetchAPI<MonitorUniverseList>('/monitor-universe'),
  add: (payload: { symbol: string; market: string; name?: string }) =>
    fetchAPI<MonitorUniverseMutationResult>('/monitor-universe', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  remove: (id: number) =>
    fetchAPI<MonitorUniverseMutationResult>(`/monitor-universe/${id}`, {
      method: 'DELETE',
    }),
  batchRemove: (ids: number[]) =>
    fetchAPI<MonitorUniverseMutationResult>('/monitor-universe/batch', {
      method: 'DELETE',
      body: JSON.stringify({ ids }),
    }),
  search: (q: string) =>
    fetchAPI<StockSearchResult[]>(
      `/stocks/search?q=${encodeURIComponent(q)}`,
    ),
}
