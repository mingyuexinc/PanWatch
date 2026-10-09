import { fetchAPI } from './client'

/** 监控信号 key: {above|below}_{5|10|20},上穿=现价>=均线,下穿=现价<均线 */
export type MonitorSignal = string

export interface MonitorUniverseItem {
  id: number
  stock_id: number
  symbol: string
  name: string
  market: string
  note: string
  monitor_signal: MonitorSignal
  added_at: string | null
  updated_at: string | null
  rule_id: number | null
  rule_enabled: boolean
  rule_last_trigger_at: string | null
  rule_last_trigger_price: number | null
  /** 最近一次命中的推送结果(null=尚未命中过) */
  rule_last_hit_notify_success: boolean | null
  rule_last_hit_notify_error: string
}

export interface MonitorUniverseList {
  items: MonitorUniverseItem[]
  total: number
  saved_at: string | null
  /** 可选监控信号目录(按 上穿5/10/20 → 下穿5/10/20 排序) */
  signals: MonitorSignal[]
}

export interface MonitorUniverseMutationResult {
  id?: number
  created?: boolean
  removed?: number
  updated?: boolean
  items: MonitorUniverseItem[]
  total: number
  saved_at: string | null
  signals: MonitorSignal[]
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
  updateSignal: (id: number, monitor_signal: MonitorSignal) =>
    fetchAPI<MonitorUniverseMutationResult>(`/monitor-universe/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ monitor_signal }),
    }),
  search: (q: string) =>
    fetchAPI<StockSearchResult[]>(
      `/stocks/search?q=${encodeURIComponent(q)}`,
    ),
}
