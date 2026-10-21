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
}
