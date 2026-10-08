import { useEffect, useMemo, useRef, useState } from 'react'
import { Search, Trash2, Radar } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { monitorUniverseApi, type MonitorUniverseItem, type StockSearchResult } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

const SEARCH_DEBOUNCE_MS = 400

// 后端时间为 naive UTC(isoformat 不带时区后缀)。直接 new Date() 会被浏览器
// 按本地时区解释,A股盘中(UTC 00:00-15:59)触发的时间会被判成前一天。
// 解析前统一补 Z;已带时区后缀(Z 或 ±hh:mm)的串保持原样。
function parseServerDate(iso: string): Date {
  const value = iso.includes('T') ? iso : iso.replace(' ', 'T')
  return new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`)
}

function formatClock(iso: string | null): string {
  if (!iso) return '--:--:--'
  try {
    return parseServerDate(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return '--:--:--'
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '-'
  try {
    return parseServerDate(iso).toLocaleDateString()
  } catch {
    return '-'
  }
}

function isTodayUtc(iso: string | null): boolean {
  if (!iso) return false
  try {
    const d = parseServerDate(iso)
    const now = new Date()
    return d.getUTCFullYear() === now.getUTCFullYear()
      && d.getUTCMonth() === now.getUTCMonth()
      && d.getUTCDate() === now.getUTCDate()
  } catch {
    return false
  }
}

export default function MonitorUniversePage() {
  const { t } = useTranslation('configuration')
  const monitorT = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (translationKey: string, interpolation?: Record<string, unknown>) => string)(
      `monitorUniverse.${key}`,
      options,
    )
  const { toast } = useToast()

  const [loading, setLoading] = useState(true)
  const [items, setItems] = useState<MonitorUniverseItem[]>([])
  const [savedAt, setSavedAt] = useState<string | null>(null)
  const [maPeriod, setMaPeriod] = useState(5)
  const [mutating, setMutating] = useState(false)
  const [selected, setSelected] = useState<number[]>([])

  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<StockSearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [showDropdown, setShowDropdown] = useState(false)
  const searchTimer = useRef<ReturnType<typeof setTimeout>>()
  const searchBoxRef = useRef<HTMLDivElement>(null)

  const symbolsInUniverse = useMemo(
    () => new Set(items.map(i => `${i.market}:${i.symbol}`)),
    [items],
  )

  const applyList = (payload: {
    items: MonitorUniverseItem[]
    saved_at: string | null
    ma_period: number
  }) => {
    setItems(payload.items || [])
    setSavedAt(payload.saved_at)
    if (payload.ma_period) setMaPeriod(payload.ma_period)
    setSelected(prev => prev.filter(id => (payload.items || []).some(i => i.id === id)))
  }

  const load = async () => {
    setLoading(true)
    try {
      applyList(await monitorUniverseApi.list())
    } catch (e) {
      toast(e instanceof Error ? e.message : monitorT('loadFailed'), 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  // 点击搜索框外部时收起下拉
  useEffect(() => {
    const onClickAway = (e: MouseEvent) => {
      if (searchBoxRef.current && !searchBoxRef.current.contains(e.target as Node)) {
        setShowDropdown(false)
      }
    }
    document.addEventListener('mousedown', onClickAway)
    return () => document.removeEventListener('mousedown', onClickAway)
  }, [])

  // 防抖搜索
  useEffect(() => {
    const q = searchQuery.trim()
    if (searchTimer.current) clearTimeout(searchTimer.current)
    if (!q) {
      setSearchResults([])
      setSearching(false)
      return
    }
    setSearching(true)
    searchTimer.current = setTimeout(async () => {
      try {
        const results = await monitorUniverseApi.search(q)
        setSearchResults(results || [])
        setShowDropdown(true)
      } catch {
        setSearchResults([])
      } finally {
        setSearching(false)
      }
    }, SEARCH_DEBOUNCE_MS)
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current)
    }
  }, [searchQuery])

  const addStock = async (result: StockSearchResult) => {
    if (mutating) return
    setMutating(true)
    try {
      const payload = await monitorUniverseApi.add({
        symbol: result.symbol,
        market: result.market,
        name: result.name,
      })
      applyList(payload)
      toast(monitorT('added', { name: result.name, symbol: result.symbol }), 'success')
      setSearchQuery('')
      setSearchResults([])
      setShowDropdown(false)
    } catch (e) {
      toast(e instanceof Error ? e.message : monitorT('addFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const removeOne = async (item: MonitorUniverseItem) => {
    if (mutating) return
    if (!window.confirm(monitorT('deleteConfirm', { name: item.name, symbol: item.symbol }))) return
    setMutating(true)
    try {
      applyList(await monitorUniverseApi.remove(item.id))
      toast(monitorT('removed', { name: item.name, symbol: item.symbol }), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : monitorT('removeFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const batchRemove = async () => {
    if (mutating || selected.length === 0) return
    if (!window.confirm(monitorT('batchDeleteConfirm', { count: selected.length }))) return
    setMutating(true)
    try {
      const payload = await monitorUniverseApi.batchRemove(selected)
      applyList(payload)
      setSelected([])
      toast(monitorT('batchRemoved', { count: payload.removed ?? 0 }), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : monitorT('removeFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const toggleSelected = (id: number) => {
    setSelected(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]))
  }

  const allVisibleSelected = items.length > 0 && items.every(i => selected.includes(i.id))

  const ruleStatus = (item: MonitorUniverseItem): { key: string; tone: string } => {
    if (item.rule_id == null) return { key: 'status.missing', tone: 'text-muted-foreground' }
    if (isTodayUtc(item.rule_last_trigger_at)) {
      if (item.rule_last_hit_notify_success === false)
        return { key: 'status.capturedPushFailed', tone: 'text-red-600 dark:text-red-400' }
      return { key: 'status.capturedToday', tone: 'text-emerald-600 dark:text-emerald-400' }
    }
    if (item.rule_enabled) return { key: 'status.active', tone: 'text-primary' }
    if (item.rule_last_hit_notify_success === false)
      return { key: 'status.disabledPushFailed', tone: 'text-red-600 dark:text-red-400' }
    return { key: 'status.disabled', tone: 'text-amber-600 dark:text-amber-400' }
  }

  return (
    <div className="mx-auto w-full max-w-4xl space-y-4">
      <div className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold flex items-center gap-2">
          <Radar className="w-5 h-5 text-primary" />
          {monitorT('title')}
        </h1>
        <p className="text-[12px] text-muted-foreground">{monitorT('description')}</p>
      </div>

      {/* 搜索添加 */}
      <div ref={searchBoxRef} className="relative">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input
              className="pl-9"
              value={searchQuery}
              placeholder={monitorT('searchPlaceholder')}
              onChange={e => setSearchQuery(e.target.value)}
              onFocus={() => searchResults.length > 0 && setShowDropdown(true)}
            />
          </div>
        </div>
        {showDropdown && (searchQuery.trim().length > 0) && (
          <div className="absolute z-40 mt-1 w-full rounded-xl border border-border/60 bg-card shadow-xl overflow-hidden">
            {searching && (
              <div className="px-3 py-2 text-[12px] text-muted-foreground">{monitorT('searching')}</div>
            )}
            {!searching && searchResults.length === 0 && (
              <div className="px-3 py-2 text-[12px] text-muted-foreground">{monitorT('noResults')}</div>
            )}
            {!searching && searchResults.map(r => {
              const inUniverse = symbolsInUniverse.has(`${r.market}:${r.symbol}`)
              return (
                <button
                  key={`${r.market}-${r.symbol}`}
                  type="button"
                  disabled={inUniverse || mutating}
                  onClick={() => addStock(r)}
                  className={`flex w-full items-center justify-between px-3 py-2 text-left text-[13px] transition-colors ${
                    inUniverse
                      ? 'cursor-default opacity-60'
                      : 'hover:bg-accent'
                  }`}
                >
                  <span className="flex items-baseline gap-2">
                    <span className="font-mono">{r.symbol}</span>
                    <span>{r.name}</span>
                    <span className="text-[10px] text-muted-foreground">{r.market}</span>
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    {inUniverse ? monitorT('inPool') : monitorT('add')}
                  </span>
                </button>
              )
            })}
          </div>
        )}
      </div>

      {/* 列表 */}
      <div className="rounded-xl border border-border/60 bg-card overflow-hidden">
        <div className="flex items-center justify-between border-b border-border/40 px-4 py-2.5">
          <div className="text-[12px] text-muted-foreground">
            {monitorT('count', { count: items.length })}
            <span className="ml-2 text-muted-foreground/70">{monitorT('maNote', { period: maPeriod })}</span>
          </div>
          <Button
            variant="destructive"
            size="sm"
            className="h-7 text-[11px]"
            disabled={selected.length === 0 || mutating}
            onClick={batchRemove}
          >
            <Trash2 className="w-3.5 h-3.5 mr-1" />
            {monitorT('batchDelete', { count: selected.length })}
          </Button>
        </div>

        {loading ? (
          <div className="px-4 py-8 text-center text-[12px] text-muted-foreground">{monitorT('loading')}</div>
        ) : items.length === 0 ? (
          <div className="px-4 py-10 text-center text-[12px] text-muted-foreground">{monitorT('empty')}</div>
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-border/40 text-left text-[11px] text-muted-foreground">
                <th className="w-10 px-4 py-2">
                  <input
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={() =>
                      setSelected(allVisibleSelected ? [] : items.map(i => i.id))
                    }
                  />
                </th>
                <th className="px-2 py-2 font-medium">{monitorT('columns.symbol')}</th>
                <th className="px-2 py-2 font-medium">{monitorT('columns.name')}</th>
                <th className="px-2 py-2 font-medium">{monitorT('columns.market')}</th>
                <th className="px-2 py-2 font-medium">{monitorT('columns.status')}</th>
                <th className="px-2 py-2 font-medium">{monitorT('columns.addedAt')}</th>
                <th className="w-14 px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {items.map(item => {
                const status = ruleStatus(item)
                return (
                  <tr key={item.id} className="border-b border-border/20 last:border-0">
                    <td className="px-4 py-2">
                      <input
                        type="checkbox"
                        checked={selected.includes(item.id)}
                        onChange={() => toggleSelected(item.id)}
                      />
                    </td>
                    <td className="px-2 py-2 font-mono">{item.symbol}</td>
                    <td className="px-2 py-2">{item.name}</td>
                    <td className="px-2 py-2 text-muted-foreground">{item.market}</td>
                    <td className={`px-2 py-2 ${status.tone}`}>{monitorT(status.key)}</td>
                    <td className="px-2 py-2 text-muted-foreground">{formatDate(item.added_at)}</td>
                    <td className="px-2 py-2 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0"
                        disabled={mutating}
                        onClick={() => removeOne(item)}
                        title={monitorT('actions.remove')}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* 自动保存状态 */}
      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-500" />
        {monitorT('savedAt', { time: formatClock(savedAt) })}
        <span className="text-muted-foreground/70">{monitorT('autosaveHint')}</span>
      </div>
    </div>
  )
}
