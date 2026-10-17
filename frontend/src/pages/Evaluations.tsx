import { useEffect, useMemo, useRef, useState } from 'react'
import { Calculator, ClipboardCheck, Pencil, Search, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  intradayQualityApi,
  type IntradayQualityItem,
  type IntradaySampleType,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@panwatch/base-ui/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@panwatch/base-ui/components/ui/dialog'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

const SEARCH_DEBOUNCE_MS = 400
const PAGE_SIZE = 10
const SAMPLE_TYPES: IntradaySampleType[] = ['positive', 'negative', 'boundary']

type SearchResult = { symbol: string; market: string; name: string }

function todayIso(): string {
  return new Date().toISOString().slice(0, 10)
}

function formatScore(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  return value.toFixed(1)
}

function formatClock(iso: string | null): string {
  if (!iso) return '--:--:--'
  try {
    const value = iso.includes('T') ? iso : iso.replace(' ', 'T')
    const d = new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return '--:--:--'
  }
}

export default function IntradayQualityEvaluationsPage() {
  const { t } = useTranslation('configuration')
  const iq = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (translationKey: string, interpolation?: Record<string, unknown>) => string)(
      `intradayQuality.${key}`,
      options,
    )
  const { toast } = useToast()
  const tjPagination = (key: 'previous' | 'next') =>
    (t as unknown as (translationKey: string) => string)(`tradeJournal.pagination.${key}`)

  const [loading, setLoading] = useState(true)
  const [items, setItems] = useState<IntradayQualityItem[]>([])
  const [page, setPage] = useState(1)
  const [savedAt, setSavedAt] = useState<string | null>(null)
  const [mutating, setMutating] = useState(false)
  const [computing, setComputing] = useState(false)
  const [selected, setSelected] = useState<number[]>([])

  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [showDropdown, setShowDropdown] = useState(false)
  const searchTimer = useRef<ReturnType<typeof setTimeout>>()
  const searchBoxRef = useRef<HTMLDivElement>(null)

  const [computeErrors, setComputeErrors] = useState<Record<number, string>>({})

  const [noteDialogOpen, setNoteDialogOpen] = useState(false)
  const [noteEditing, setNoteEditing] = useState<IntradayQualityItem | null>(null)
  const [noteDraft, setNoteDraft] = useState('')

  const applyList = (payload: { items: IntradayQualityItem[]; saved_at: string | null }) => {
    setItems(payload.items || [])
    setSavedAt(payload.saved_at)
    setSelected(prev => prev.filter(id => (payload.items || []).some(i => i.id === id)))
  }

  const load = async () => {
    setLoading(true)
    try {
      applyList(await intradayQualityApi.list())
    } catch (e) {
      toast(e instanceof Error ? e.message : iq('loadFailed'), 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE))

  useEffect(() => {
    if (page > pages) setPage(pages)
  }, [page, pages])

  const pageItems = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return items.slice(start, start + PAGE_SIZE)
  }, [items, page])

  const pageNumbers: Array<number | '...'> = useMemo(() => {
    if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1)
    const pager: Array<number | '...'> = [1]
    const start = Math.max(2, page - 1)
    const end = Math.min(pages - 1, page + 1)
    if (start > 2) pager.push('...')
    for (let p = start; p <= end; p++) pager.push(p)
    if (end < pages - 1) pager.push('...')
    pager.push(pages)
    return pager
  }, [page, pages])

  useEffect(() => {
    const onClickAway = (e: MouseEvent) => {
      if (searchBoxRef.current && !searchBoxRef.current.contains(e.target as Node)) {
        setShowDropdown(false)
      }
    }
    document.addEventListener('mousedown', onClickAway)
    return () => document.removeEventListener('mousedown', onClickAway)
  }, [])

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
        const results = await intradayQualityApi.search(q)
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

  const addStock = async (result: SearchResult) => {
    if (mutating) return
    setMutating(true)
    try {
      applyList(
        await intradayQualityApi.add({
          symbol: result.symbol,
          market: result.market,
          name: result.name,
          test_date: todayIso(),
          sample_type: 'positive',
        }),
      )
      toast(iq('added', { name: result.name, symbol: result.symbol }), 'success')
      setSearchQuery('')
      setSearchResults([])
      setShowDropdown(false)
    } catch (e) {
      toast(e instanceof Error ? e.message : iq('addFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const patchItem = async (
    item: IntradayQualityItem,
    patch: Partial<{ test_date: string; sample_type: IntradaySampleType; note: string }>,
  ) => {
    if (mutating) return
    setMutating(true)
    try {
      applyList(await intradayQualityApi.update(item.id, patch))
    } catch (e) {
      toast(e instanceof Error ? e.message : iq('saveFailed'), 'error')
      await load()
    } finally {
      setMutating(false)
    }
  }

  const openNoteEdit = (item: IntradayQualityItem) => {
    setNoteEditing(item)
    setNoteDraft(item.note || '')
    setNoteDialogOpen(true)
  }

  const saveNote = async () => {
    if (!noteEditing || mutating) return
    setMutating(true)
    try {
      applyList(await intradayQualityApi.update(noteEditing.id, { note: noteDraft }))
      setNoteDialogOpen(false)
      setNoteEditing(null)
    } catch (e) {
      toast(e instanceof Error ? e.message : iq('saveFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const removeOne = async (item: IntradayQualityItem) => {
    if (mutating) return
    if (!window.confirm(iq('deleteConfirm', { name: item.name, symbol: item.symbol }))) return
    setMutating(true)
    try {
      applyList(await intradayQualityApi.remove(item.id))
      toast(iq('removed', { name: item.name, symbol: item.symbol }), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : iq('removeFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const batchRemove = async () => {
    if (mutating || selected.length === 0) return
    if (!window.confirm(iq('batchDeleteConfirm', { count: selected.length }))) return
    setMutating(true)
    try {
      const payload = await intradayQualityApi.batchRemove(selected)
      applyList(payload)
      setSelected([])
      toast(iq('batchRemoved', { count: payload.removed ?? 0 }), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : iq('removeFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const applyComputeResult = (
    result: Awaited<ReturnType<typeof intradayQualityApi.compute>>,
    scope: 'all' | 'selected',
    selectedIds: number[],
  ) => {
    applyList(result)
    const errList = result.errors ?? []
    const computedCount = result.computed_count ?? result.items.length
    setComputeErrors(prev => {
      if (scope === 'all') {
        return Object.fromEntries(errList.map(e => [e.id, e.message]))
      }
      const next = { ...prev }
      const errById = Object.fromEntries(errList.map(e => [e.id, e.message]))
      for (const id of selectedIds) {
        if (errById[id]) next[id] = errById[id]
        else delete next[id]
      }
      return next
    })
    const failed = errList.length
    const ok = computedCount - failed
    if (failed > 0) {
      const detail = errList
        .slice(0, 2)
        .map(e => `${e.symbol}(${e.test_date}): ${e.message}`)
        .join('；')
      toast(
        `${iq('computePartial', { ok, failed })} ${detail}`,
        'error',
      )
    } else {
      toast(
        scope === 'selected'
          ? iq('computeSelectedDone', { count: computedCount })
          : iq('computeDone', { count: computedCount }),
        'success',
      )
    }
  }

  const runCompute = async () => {
    if (computing || items.length === 0) return
    setComputing(true)
    try {
      applyComputeResult(await intradayQualityApi.compute(), 'all', [])
    } catch (e) {
      toast(e instanceof Error ? e.message : iq('computeFailed'), 'error')
    } finally {
      setComputing(false)
    }
  }

  const runBatchCompute = async () => {
    if (computing || selected.length === 0) return
    setComputing(true)
    try {
      applyComputeResult(await intradayQualityApi.compute(selected), 'selected', selected)
    } catch (e) {
      toast(e instanceof Error ? e.message : iq('computeFailed'), 'error')
    } finally {
      setComputing(false)
    }
  }

  const toggleSelected = (id: number) => {
    setSelected(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]))
  }

  const allVisibleSelected =
    pageItems.length > 0 && pageItems.every(i => selected.includes(i.id))

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4">
      <div className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold flex items-center gap-2">
          <ClipboardCheck className="w-5 h-5 text-primary" />
          {iq('title')}
        </h1>
        <p className="text-[12px] text-muted-foreground leading-relaxed">{iq('algorithmNote')}</p>
      </div>

      <div className="flex items-center justify-between gap-2">
        <Button
          size="sm"
          className="h-8"
          disabled={computing || items.length === 0}
          onClick={runCompute}
        >
          <Calculator className="w-4 h-4 mr-1" />
          {computing ? iq('computing') : iq('computeAll')}
        </Button>
      </div>

      <div ref={searchBoxRef} className="relative">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            className="pl-9"
            value={searchQuery}
            placeholder={iq('searchPlaceholder')}
            onChange={e => setSearchQuery(e.target.value)}
            onFocus={() => searchResults.length > 0 && setShowDropdown(true)}
          />
        </div>
        {showDropdown && searchQuery.trim().length > 0 && (
          <div className="absolute z-40 mt-1 w-full rounded-xl border border-border/60 bg-card shadow-xl overflow-hidden">
            {searching && (
              <div className="px-3 py-2 text-[12px] text-muted-foreground">{iq('searching')}</div>
            )}
            {!searching && searchResults.length === 0 && (
              <div className="px-3 py-2 text-[12px] text-muted-foreground">{iq('noResults')}</div>
            )}
            {!searching &&
              searchResults.map(r => (
                <button
                  key={`${r.market}-${r.symbol}`}
                  type="button"
                  disabled={mutating}
                  onClick={() => addStock(r)}
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-[13px] hover:bg-accent transition-colors"
                >
                  <span className="flex items-baseline gap-2">
                    <span className="font-mono">{r.symbol}</span>
                    <span>{r.name}</span>
                    <span className="text-[10px] text-muted-foreground">{r.market}</span>
                  </span>
                  <span className="text-[11px] text-muted-foreground">{iq('add')}</span>
                </button>
              ))}
          </div>
        )}
      </div>

      <div className="rounded-xl border border-border/60 bg-card overflow-hidden">
        <div className="flex items-center justify-between border-b border-border/40 px-4 py-2.5 gap-2">
          <div className="text-[12px] text-muted-foreground">
            {iq('count', { count: items.length })}
            {items.length > 0 ? (
              <span className="text-muted-foreground/70">
                {' '}
                · {page}/{pages}
              </span>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-[11px]"
              disabled={selected.length === 0 || mutating || computing}
              onClick={runBatchCompute}
            >
              <Calculator className="w-3.5 h-3.5 mr-1" />
              {computing ? iq('computing') : iq('batchCompute', { count: selected.length })}
            </Button>
            <Button
              variant="destructive"
              size="sm"
              className="h-7 text-[11px]"
              disabled={selected.length === 0 || mutating}
              onClick={batchRemove}
            >
              <Trash2 className="w-3.5 h-3.5 mr-1" />
              {iq('batchDelete', { count: selected.length })}
            </Button>
          </div>
        </div>

        {loading ? (
          <div className="px-4 py-8 text-center text-[12px] text-muted-foreground">{iq('loading')}</div>
        ) : items.length === 0 ? (
          <div className="px-4 py-10 text-center text-[12px] text-muted-foreground">{iq('empty')}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-border/40 text-left text-[11px] text-muted-foreground">
                  <th className="w-10 px-4 py-2">
                    <input
                      type="checkbox"
                      checked={allVisibleSelected}
                      onChange={() =>
                        setSelected(allVisibleSelected ? [] : pageItems.map(i => i.id))
                      }
                    />
                  </th>
                  <th className="px-2 py-2 font-medium whitespace-nowrap">{iq('columns.symbol')}</th>
                  <th className="px-2 py-2 font-medium whitespace-nowrap">{iq('columns.name')}</th>
                  <th className="px-2 py-2 font-medium whitespace-nowrap">{iq('columns.testDate')}</th>
                  <th className="px-2 py-2 font-medium whitespace-nowrap">{iq('columns.sampleType')}</th>
                  <th className="px-2 py-2 font-medium whitespace-nowrap text-right">{iq('columns.score')}</th>
                  <th className="px-2 py-2 font-medium whitespace-nowrap">{iq('columns.note')}</th>
                  <th className="w-20 px-2 py-2" />
                </tr>
              </thead>
              <tbody>
                {pageItems.map(item => (
                  <tr key={item.id} className="border-b border-border/20 last:border-0 hover:bg-accent/30">
                    <td className="px-4 py-2">
                      <input
                        type="checkbox"
                        checked={selected.includes(item.id)}
                        onChange={() => toggleSelected(item.id)}
                      />
                    </td>
                    <td className="px-2 py-2 font-mono whitespace-nowrap">{item.symbol}</td>
                    <td className="px-2 py-2 whitespace-nowrap">{item.name}</td>
                    <td className="px-2 py-2 whitespace-nowrap">
                      <Input
                        type="date"
                        className="h-8 w-[9.5rem] text-[12px]"
                        value={item.test_date}
                        disabled={mutating}
                        onChange={e => {
                          const v = e.target.value
                          if (!v || v === item.test_date) return
                          setItems(prev =>
                            prev.map(row => (row.id === item.id ? { ...row, test_date: v } : row)),
                          )
                          void patchItem(item, { test_date: v })
                        }}
                      />
                    </td>
                    <td className="px-2 py-2">
                      <Select
                        value={item.sample_type}
                        onValueChange={v => patchItem(item, { sample_type: v as IntradaySampleType })}
                        disabled={mutating}
                      >
                        <SelectTrigger className="h-8 w-[7.5rem] rounded-lg px-2 text-[12px]">
                          <span>{iq(`sampleTypes.${item.sample_type}`)}</span>
                        </SelectTrigger>
                        <SelectContent>
                          {SAMPLE_TYPES.map(st => (
                            <SelectItem key={st} value={st} className="text-[12px]">
                              {iq(`sampleTypes.${st}`)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </td>
                    <td
                      className="px-2 py-2 text-right font-mono font-semibold whitespace-nowrap"
                      title={computeErrors[item.id] || undefined}
                    >
                      {formatScore(item.strength_score)}
                      {computeErrors[item.id] ? (
                        <span className="ml-1 text-[10px] font-normal text-destructive">!</span>
                      ) : null}
                    </td>
                    <td className="px-2 py-2 text-muted-foreground max-w-[14rem]">
                      <span className="line-clamp-2 whitespace-pre-wrap" title={item.note}>
                        {item.note || '-'}
                      </span>
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0"
                          disabled={mutating}
                          onClick={() => openNoteEdit(item)}
                          title={iq('actions.editNote')}
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                          disabled={mutating}
                          onClick={() => removeOne(item)}
                          title={iq('actions.remove')}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pages > 1 && !loading && items.length > 0 && (
          <div className="flex items-center justify-center gap-1.5 border-t border-border/40 px-4 py-2.5">
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-[11px]"
              disabled={page <= 1 || loading}
              onClick={() => setPage(p => Math.max(1, p - 1))}
            >
              {tjPagination('previous')}
            </Button>
            {pageNumbers.map((p, idx) =>
              p === '...' ? (
                <span key={`ellipsis-${idx}`} className="px-1 text-[11px] text-muted-foreground">
                  …
                </span>
              ) : (
                <button
                  key={p}
                  type="button"
                  disabled={loading}
                  onClick={() => setPage(p)}
                  className={`h-7 min-w-7 rounded-md px-1.5 text-[11px] font-mono transition-colors ${
                    p === page
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                  }`}
                >
                  {p}
                </button>
              ),
            )}
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-[11px]"
              disabled={page >= pages || loading}
              onClick={() => setPage(p => Math.min(pages, p + 1))}
            >
              {tjPagination('next')}
            </Button>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-500" />
        {iq('savedAt', { time: formatClock(savedAt) })}
        <span className="text-muted-foreground/70">{iq('autosaveHint')}</span>
      </div>

      <Dialog open={noteDialogOpen} onOpenChange={setNoteDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{iq('noteDialog.title')}</DialogTitle>
            <DialogDescription>{iq('noteDialog.description')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="iq-note">{iq('noteDialog.label')}</Label>
            <textarea
              id="iq-note"
              className="flex min-h-[120px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={noteDraft}
              onChange={e => setNoteDraft(e.target.value)}
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setNoteDialogOpen(false)}>
              {iq('noteDialog.cancel')}
            </Button>
            <Button disabled={mutating} onClick={saveNote}>
              {iq('noteDialog.save')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
