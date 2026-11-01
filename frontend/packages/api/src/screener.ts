import { fetchAPI } from './client'

export type ScreenerQueryStatus = 'draft' | 'fresh' | 'stale' | 'error'

export interface ScreenerValidation {
  ok?: boolean
  checked_codes?: string[]
  failures?: string[]
  warnings?: string[]
}

export interface ScreenerIntradayScoreEntry {
  score: number | null
  error?: string | null
}

export interface ScreenerIntradayScores {
  test_date?: string
  sorted_by_score?: boolean
  scores?: Record<string, ScreenerIntradayScoreEntry>
  computed_at?: string
}

export interface ScreenerIntradayComputeMeta {
  computed_count: number
  errors: Array<{ symbol: string; test_date: string; message: string }>
  computed_at: string
}

export interface ScreenerQuery {
  id: number
  label: string
  query_text: string
  select_type: string
  sort_order: number
  status: ScreenerQueryStatus
  last_run_at: string | null
  row_count: number
  data_source: string
  last_error: string
  validation: ScreenerValidation
  intraday_scores?: ScreenerIntradayScores
  intraday_compute?: ScreenerIntradayComputeMeta
  columns?: string[]
  rows?: Record<string, string>[]
}

export interface ScreenerMergedMock {
  mock: boolean
  total: number
  contributing_queries: number
  rows: Array<{
    code: string
    name: string
    change_pct: number
    sources: string[]
    note: string
  }>
}

export interface ScreenerStrategyConfig {
  query_id: number | null
  monitor_start_minute: number
  monitor_end_minute: number
  monitor_start: string
  monitor_end: string
  gain_pct: number
  notify_channel_id?: number | null
  last_test_trigger_at?: string | null
  auto_monitor_enabled?: boolean
}

export interface ScreenerDaySession {
  trade_date: string
  query_id: number | null
  pool_status: string
  pool_count: number
  run_attempts: number
  last_error: string
  pool_built_at: string | null
}

export interface ScreenerMonitorTrigger {
  symbol: string
  name: string
  trigger_at: string | null
  trigger_price: number
  prev_close: number
  change_pct: number | null
  reason: string
}

export interface ScreenerScanPushMeta {
  attempted: number
  sent: number
  channel: { id: number; name: string; type: string } | null
  errors: string[]
}

export interface ScreenerScanMeta {
  trade_date: string
  mode: 'mkline_replay' | 'live' | 'open_auction'
  new_triggers: number
  pool_count?: number
  hits?: number
  skipped_no_prev_close?: number
  skipped_no_bars?: number
  replay_poll_points?: number
  message?: string
  pool_auto_built?: boolean
  finished_at?: string | null
  push?: ScreenerScanPushMeta
}

export interface ScreenerStrategyPayload {
  config: ScreenerStrategyConfig
  market_trade_date?: string
  session: ScreenerDaySession | null
  triggers: ScreenerMonitorTrigger[]
  scan?: ScreenerScanMeta
}

export const screenerApi = {
  listQueries: () => fetchAPI<{ items: ScreenerQuery[] }>('/screener/queries'),
  getQuery: (id: number) => fetchAPI<ScreenerQuery>(`/screener/queries/${id}`),
  createQuery: (body: { label?: string; query_text?: string; select_type?: string }) =>
    fetchAPI<ScreenerQuery>('/screener/queries', { method: 'POST', body: JSON.stringify(body) }),
  updateQuery: (
    id: number,
    body: { label?: string; query_text?: string; select_type?: string },
  ) =>
    fetchAPI<ScreenerQuery>(`/screener/queries/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  deleteQuery: (id: number) =>
    fetchAPI<{ ok: boolean }>(`/screener/queries/${id}`, { method: 'DELETE' }),
  runQuery: (id: number) =>
    fetchAPI<ScreenerQuery>(`/screener/queries/${id}/run`, { method: 'POST', timeoutMs: 120000 }),
  computeIntradayScores: (id: number, codes?: string[]) =>
    fetchAPI<ScreenerQuery>(`/screener/queries/${id}/compute-intraday-scores`, {
      method: 'POST',
      body: JSON.stringify(codes?.length ? { codes } : {}),
      timeoutMs: 600000,
    }),
  getMergedMock: () => fetchAPI<ScreenerMergedMock>('/screener/merged'),
  getStrategy: () => fetchAPI<ScreenerStrategyPayload>('/screener/strategy'),
  updateStrategy: (body: {
    query_id?: number
    monitor_start?: string
    monitor_end?: string
    gain_pct?: number
    notify_channel_id?: number | null
    auto_monitor_enabled?: boolean
  }) =>
    fetchAPI<ScreenerStrategyPayload>('/screener/strategy', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  buildPool: () =>
    fetchAPI<ScreenerStrategyPayload>('/screener/strategy/build-pool', { method: 'POST', timeoutMs: 120000 }),
  scanOnce: () => fetchAPI<ScreenerStrategyPayload>('/screener/strategy/scan-once', { method: 'POST' }),
  testTrigger: () =>
    fetchAPI<ScreenerStrategyPayload>('/screener/strategy/test-trigger', {
      method: 'POST',
      timeoutMs: 600000,
    }),
}
