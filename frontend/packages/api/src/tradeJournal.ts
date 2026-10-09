import { fetchAPI } from './client'

/** 自定义交易记录(复盘日记)条目 */
export interface TradeJournalEntry {
  id: number
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
}

/** 一键统计结果(范围为全部记录;收益率未填的记录不参与计算) */
export interface TradeJournalStats {
  total: number
  counted: number
  win_count: number
  /** 盈利概率 = 盈利次数/有效次数,0~1;无有效记录时为 null */
  win_rate: number | null
  /** 总收益率 = ∏(1+r/100)-1,以百分比表达;无有效记录时为 null */
  total_return_pct: number | null
}

export type TradeJournalPayload = Omit<
  TradeJournalEntry,
  'id' | 'sort_order' | 'created_at' | 'updated_at'
>

export const tradeJournalApi = {
  list: (page: number, pageSize = 20) =>
    fetchAPI<TradeJournalList>(`/trade-journal?page=${page}&page_size=${pageSize}`),
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
  /** 拖拽排序:提交当前页全部条目按新顺序排列的 id 列表 */
  reorder: (orderedIds: number[]) =>
    fetchAPI<{ ok: boolean }>('/trade-journal/reorder/batch', {
      method: 'PUT',
      body: JSON.stringify({
        items: orderedIds.map((id, idx) => ({ id, sort_order: idx + 1 })),
      }),
    }),
  stats: () => fetchAPI<TradeJournalStats>('/trade-journal/stats'),
}
