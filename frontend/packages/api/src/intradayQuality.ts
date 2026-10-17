import { fetchAPI } from './client'

export type IntradaySampleType = 'positive' | 'negative' | 'boundary'

export interface IntradayQualityItem {
  id: number
  symbol: string
  market: string
  name: string
  test_date: string
  sample_type: IntradaySampleType
  strength_score: number | null
  note: string
  updated_at: string | null
}

export interface IntradayQualityListResponse {
  items: IntradayQualityItem[]
  saved_at: string | null
  sample_types: IntradaySampleType[]
}

export interface IntradayQualityComputeResponse extends IntradayQualityListResponse {
  errors: Array<{ id: number; symbol: string; test_date: string; message: string }>
  computed_at: string
  computed_count?: number
}

export const intradayQualityApi = {
  list(): Promise<IntradayQualityListResponse> {
    return fetchAPI<IntradayQualityListResponse>('/intraday-quality')
  },

  add(payload: {
    symbol: string
    market: string
    name: string
    test_date?: string
    sample_type?: IntradaySampleType
    note?: string
  }): Promise<IntradayQualityListResponse> {
    return fetchAPI<IntradayQualityListResponse>('/intraday-quality', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  update(
    id: number,
    payload: Partial<{
      test_date: string
      sample_type: IntradaySampleType
      note: string
    }>,
  ): Promise<IntradayQualityListResponse> {
    return fetchAPI<IntradayQualityListResponse>(`/intraday-quality/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
  },

  remove(id: number): Promise<IntradayQualityListResponse & { removed: number }> {
    return fetchAPI(`/intraday-quality/${id}`, { method: 'DELETE' })
  },

  batchRemove(ids: number[]): Promise<IntradayQualityListResponse & { removed: number }> {
    return fetchAPI('/intraday-quality/batch', {
      method: 'DELETE',
      body: JSON.stringify({ ids }),
    })
  },

  compute(ids?: number[]): Promise<IntradayQualityComputeResponse> {
    return fetchAPI<IntradayQualityComputeResponse>('/intraday-quality/compute', {
      method: 'POST',
      body: JSON.stringify(ids?.length ? { ids } : {}),
    })
  },

  /** @deprecated 使用 compute() */
  computeAll(): Promise<IntradayQualityComputeResponse> {
    return this.compute()
  },

  search(q: string) {
    return fetchAPI<Array<{ symbol: string; market: string; name: string }>>(
      `/stocks/search?q=${encodeURIComponent(q)}`,
    )
  },
}
