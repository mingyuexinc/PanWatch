import { fetchAPI } from './client'

export interface TradeJournalStrategy {
  id: number
  name: string
  description: string
  sort_order: number
  created_at: string | null
  updated_at: string | null
}

export interface TradeJournalVersion {
  id: number
  strategy_id: number
  name: string
  description: string
  sort_order: number
  created_at: string | null
  updated_at: string | null
}

/** 自定义交易记录(复盘日记)条目 */
export interface TradeJournalEntry {
  id: number
  version_id: number
  /** 开仓日期 "YYYY-MM-DD" */
  trade_date: string
  stock_name: string
  /** 板块/身位 */
  sector: string
  /** 开盘涨幅(%数值,null=未填写) */
  open_change_pct: number | null
  /** 封板结果 */
  seal_result: string
  /** 卖出时机 */
  sell_timing: string
  /** 收益率(%数值,null=未填写) */
  return_pct: number | null
  review_note: string
  sort_order: number
  created_at: string | null
  updated_at: string | null
}

export interface TradeJournalList {
  items: TradeJournalEntry[]
  total: number
  page: number
  page_size: number
  pages: number
  version_id: number
}

/** 一键统计结果(范围为指定版本;收益率未填的记录不参与计算) */
export interface TradeJournalStats {
  version_id: number
  total: number
  counted: number
  win_count: number
  win_rate: number | null
  total_return_pct: number | null
}

export type TradeJournalPayload = Omit<
  TradeJournalEntry,
  'id' | 'sort_order' | 'created_at' | 'updated_at'
>

export type TradeJournalStrategyPayload = Pick<TradeJournalStrategy, 'name' | 'description'>
export type TradeJournalVersionPayload = Pick<TradeJournalVersion, 'name' | 'description'>

export const tradeJournalApi = {
  listStrategies: () =>
    fetchAPI<{ items: TradeJournalStrategy[] }>('/trade-journal/strategies'),
  createStrategy: (payload: TradeJournalStrategyPayload) =>
    fetchAPI<TradeJournalStrategy>('/trade-journal/strategies', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateStrategy: (id: number, payload: Partial<TradeJournalStrategyPayload>) =>
    fetchAPI<TradeJournalStrategy>(`/trade-journal/strategies/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  removeStrategy: (id: number) =>
    fetchAPI<{ removed: number }>(`/trade-journal/strategies/${id}`, { method: 'DELETE' }),

  listVersions: (strategyId: number) =>
    fetchAPI<{ items: TradeJournalVersion[] }>(
      `/trade-journal/strategies/${strategyId}/versions`,
    ),
  createVersion: (strategyId: number, payload: TradeJournalVersionPayload) =>
    fetchAPI<TradeJournalVersion>(`/trade-journal/strategies/${strategyId}/versions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateVersion: (id: number, payload: Partial<TradeJournalVersionPayload>) =>
    fetchAPI<TradeJournalVersion>(`/trade-journal/versions/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  removeVersion: (id: number) =>
    fetchAPI<{ removed: number }>(`/trade-journal/versions/${id}`, { method: 'DELETE' }),

  moveToVersion: (ids: number[], targetVersionId: number) =>
    fetchAPI<{ moved: number }>('/trade-journal/entries/move-to-version', {
      method: 'POST',
      body: JSON.stringify({ ids, target_version_id: targetVersionId }),
    }),

  list: (versionId: number, page: number, pageSize = 20) =>
    fetchAPI<TradeJournalList>(
      `/trade-journal?version_id=${versionId}&page=${page}&page_size=${pageSize}`,
    ),
  create: (payload: TradeJournalPayload) =>
    fetchAPI<TradeJournalEntry>('/trade-journal', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  update: (id: number, payload: Partial<TradeJournalPayload>) =>
    fetchAPI<TradeJournalEntry>(`/trade-journal/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  remove: (id: number) =>
    fetchAPI<{ removed: number }>(`/trade-journal/${id}`, { method: 'DELETE' }),
  batchRemove: (ids: number[]) =>
    fetchAPI<{ removed: number }>('/trade-journal/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  reorder: (versionId: number, orderedIds: number[]) =>
    fetchAPI<{ ok: boolean }>('/trade-journal/reorder/batch', {
      method: 'PUT',
      body: JSON.stringify({
        version_id: versionId,
        items: orderedIds.map((id, idx) => ({ id, sort_order: idx + 1 })),
      }),
    }),
  stats: (versionId: number) =>
    fetchAPI<TradeJournalStats>(`/trade-journal/stats?version_id=${versionId}`),
}
