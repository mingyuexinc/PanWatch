import { useCallback, useEffect, useRef, useState } from 'react'
import { BookOpenText, Plus, Trash2, Pencil, GripVertical, Calculator } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  tradeJournalApi,
  type TradeJournalEntry,
  type TradeJournalStats,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@panwatch/base-ui/components/ui/dialog'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { marketSignTextClass } from '@/lib/market-colors'

const PAGE_SIZE = 20

interface EntryForm {
  trade_date: string
  stock_name: string
  sector: string
  open_change_pct: string
  seal_result: string
  sell_timing: string
  return_pct: string
  review_note: string
}

const EMPTY_FORM: EntryForm = {
  trade_date: '',
  stock_name: '',
  sector: '',
  open_change_pct: '',
  seal_result: '',
  sell_timing: '',
  return_pct: '',
  review_note: '',
}

/** 宽松解析百分比编辑框: "5.2%"→5.2, "-3"→-3, ""→null, 非法→NaN */
function parsePercentInput(raw: string): number | null {
  const text = raw.trim().replace('%', '').replace('％', '')
  if (!text) return null
  return Number(text)
}

function formatPct(value: number | null): string {
  if (value == null) return '-'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

export default function TradeJournalPage() {
  const { t } = useTranslation('configuration')
  const tj = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (translationKey: string, interpolation?: Record<string, unknown>) => string)(
      `tradeJournal.${key}`,
      options,
    )
  const { toast } = useToast()

  const [loading, setLoading] = useState(true)
  const [entries, setEntries] = useState<TradeJournalEntry[]>([])
  const [total, setTotal] = useState(0)
  const [pages, setPages] = useState(1)
  const [page, setPage] = useState(1)
  const [mutating, setMutating] = useState(false)
  const [selected, setSelected] = useState<number[]>([])

  // 新增/编辑弹窗
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<EntryForm>(EMPTY_FORM)

  // 一键统计
  const [stats, setStats] = useState<TradeJournalStats | null>(null)
  const [statsLoading, setStatsLoading] = useState(false)

  // 拖拽排序
  const [draggingId, setDraggingId] = useState<number | null>(null)
  const dragSnapshotRef = useRef<TradeJournalEntry[] | null>(null)

  const load = useCallback(async (targetPage: number) => {
    setLoading(true)
    try {
      const payload = await tradeJournalApi.list(targetPage, PAGE_SIZE)
      setEntries(payload.items)
      setTotal(payload.total)
      setPages(payload.pages)
      setPage(payload.page)
      setSelected(prev => prev.filter(id => payload.items.some(i => i.id === id)))
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.loadFailed'), 'error')
    } finally {
      setLoading(false)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    load(1)
  }, [load])

  const openCreate = () => {
    setEditingId(null)
    setForm({ ...EMPTY_FORM, trade_date: new Date().toISOString().slice(0, 10) })
    setDialogOpen(true)
  }

  const openEdit = (entry: TradeJournalEntry) => {
    setEditingId(entry.id)
    setForm({
      trade_date: entry.trade_date,
      stock_name: entry.stock_name,
      sector: entry.sector,
      open_change_pct: entry.open_change_pct != null ? String(entry.open_change_pct) : '',
      seal_result: entry.seal_result,
      sell_timing: entry.sell_timing,
      return_pct: entry.return_pct != null ? String(entry.return_pct) : '',
      review_note: entry.review_note,
    })
    setDialogOpen(true)
  }

  const submitForm = async () => {
    if (mutating) return
    if (!form.trade_date) {
      toast(tj('validation.dateRequired'), 'error')
      return
    }
    if (!form.stock_name.trim()) {
      toast(tj('validation.nameRequired'), 'error')
      return
    }
    const openChangePct = parsePercentInput(form.open_change_pct)
    if (Number.isNaN(openChangePct)) {
      toast(tj('validation.openChangeInvalid'), 'error')
      return
    }
    const returnPct = parsePercentInput(form.return_pct)
    if (Number.isNaN(returnPct)) {
      toast(tj('validation.returnInvalid'), 'error')
      return
    }
    setMutating(true)
    try {
      const payload = {
        trade_date: form.trade_date,
        stock_name: form.stock_name.trim(),
        sector: form.sector.trim(),
        open_change_pct: openChangePct,
        seal_result: form.seal_result.trim(),
        sell_timing: form.sell_timing.trim(),
        return_pct: returnPct,
        review_note: form.review_note.trim(),
      }
      if (editingId == null) {
        await tradeJournalApi.create(payload)
        toast(tj('messages.created'), 'success')
        // 新记录插到最前,回到第一页即可看到
        await load(1)
      } else {
        await tradeJournalApi.update(editingId, payload)
        toast(tj('messages.updated'), 'success')
        await load(page)
      }
      setDialogOpen(false)
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.saveFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const removeOne = async (entry: TradeJournalEntry) => {
    if (mutating) return
    if (!window.confirm(tj('messages.deleteConfirm', { name: entry.stock_name }))) return
    setMutating(true)
    try {
      await tradeJournalApi.remove(entry.id)
      toast(tj('messages.deleted'), 'success')
      await load(page)
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.deleteFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const batchRemove = async () => {
    if (mutating || selected.length === 0) return
    if (!window.confirm(tj('messages.batchDeleteConfirm', { count: selected.length }))) return
    setMutating(true)
    try {
      const result = await tradeJournalApi.batchRemove(selected)
      setSelected([])
      toast(tj('messages.batchDeleted', { count: result.removed ?? 0 }), 'success')
      await load(page)
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.deleteFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const runStats = async () => {
    if (statsLoading) return
    setStatsLoading(true)
    try {
      setStats(await tradeJournalApi.stats())
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('stats.loadFailed'), 'error')
    } finally {
      setStatsLoading(false)
    }
  }

  // ---- 页内拖拽排序:预览即时重排,松手后提交整页新顺序 ----
  const moveById = (list: TradeJournalEntry[], fromId: number, toId: number) => {
    const fromIdx = list.findIndex(x => x.id === fromId)
    const toIdx = list.findIndex(x => x.id === toId)
    if (fromIdx < 0 || toIdx < 0 || fromIdx === toIdx) return list
    const next = [...list]
    const [moved] = next.splice(fromIdx, 1)
    next.splice(toIdx, 0, moved)
    return next
  }

  const commitReorder = async () => {
    const current = entries
    if (!current || current.length < 2) return
    try {
      await tradeJournalApi.reorder(current.map(e => e.id))
    } catch (e) {
      if (dragSnapshotRef.current) setEntries(dragSnapshotRef.current)
      toast(e instanceof Error ? e.message : tj('messages.reorderFailed'), 'error')
    }
  }

  const toggleSelected = (id: number) => {
    setSelected(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]))
  }

  const allVisibleSelected = entries.length > 0 && entries.every(i => selected.includes(i.id))

  // 页码按钮:窗口化显示,最多 7 个,超出用省略号
  const pageNumbers: Array<number | '...'> = (() => {
    if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1)
    const pager: Array<number | '...'> = [1]
    const start = Math.max(2, page - 1)
    const end = Math.min(pages - 1, page + 1)
    if (start > 2) pager.push('...')
    for (let p = start; p <= end; p++) pager.push(p)
    if (end < pages - 1) pager.push('...')
    pager.push(pages)
    return pager
  })()

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4">
      <div className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold flex items-center gap-2">
          <BookOpenText className="w-5 h-5 text-primary" />
          {tj('title')}
        </h1>
        <p className="text-[12px] text-muted-foreground">{tj('description')}</p>
      </div>

      {/* 工具栏: 新增 + 一键统计 */}
      <div className="flex items-center justify-between gap-2">
        <Button size="sm" className="h-8" disabled={mutating} onClick={openCreate}>
          <Plus className="w-4 h-4 mr-1" />
          {tj('addRecord')}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-8"
          disabled={statsLoading}
          onClick={runStats}
        >
          <Calculator className="w-4 h-4 mr-1" />
          {statsLoading ? tj('stats.calculating') : tj('stats.button')}
        </Button>
      </div>

      {/* 一键统计结果(点击按钮后展示,范围为全部记录) */}
      {stats && (
        <div className="rounded-xl border border-border/60 bg-card px-4 py-3">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <div>
              <div className="text-[11px] text-muted-foreground">{tj('stats.total')}</div>
              <div className="font-mono text-lg font-semibold">{stats.total}</div>
            </div>
            <div>
              <div className="text-[11px] text-muted-foreground">{tj('stats.counted')}</div>
              <div className="font-mono text-lg font-semibold">{stats.counted}</div>
            </div>
            <div>
              <div className="text-[11px] text-muted-foreground">{tj('stats.winRate')}</div>
              <div className={`font-mono text-lg font-semibold ${marketSignTextClass((stats.win_rate ?? 0) - 0.5)}`}>
                {stats.win_rate == null ? '--' : `${(stats.win_rate * 100).toFixed(1)}%`}
              </div>
              <div className="text-[10px] text-muted-foreground">
                {tj('stats.winCount', { win: stats.win_count, counted: stats.counted })}
              </div>
            </div>
            <div>
              <div className="text-[11px] text-muted-foreground">{tj('stats.totalReturn')}</div>
              <div className={`font-mono text-lg font-semibold ${marketSignTextClass(stats.total_return_pct)}`}>
                {stats.total_return_pct == null ? '--' : formatPct(stats.total_return_pct)}
              </div>
              <div className="text-[10px] text-muted-foreground">{tj('stats.totalReturnHint')}</div>
            </div>
          </div>
        </div>
      )}

      {/* 记录列表 */}
      <div className="rounded-xl border border-border/60 bg-card overflow-hidden">
        <div className="flex items-center justify-between border-b border-border/40 px-4 py-2.5">
          <div className="text-[12px] text-muted-foreground">
            {tj('count', { count: total })}
            <span className="ml-2 text-muted-foreground/70">{tj('dragHint')}</span>
          </div>
          <Button
            variant="destructive"
            size="sm"
            className="h-7 text-[11px]"
            disabled={selected.length === 0 || mutating}
            onClick={batchRemove}
          >
            <Trash2 className="w-3.5 h-3.5 mr-1" />
            {tj('batchDelete', { count: selected.length })}
          </Button>
        </div>

        {loading ? (
          <div className="px-4 py-8 text-center text-[12px] text-muted-foreground">{tj('loading')}</div>
        ) : entries.length === 0 ? (
          <div className="px-4 py-10 text-center text-[12px] text-muted-foreground">{tj('empty')}</div>
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
                        setSelected(allVisibleSelected ? [] : entries.map(i => i.id))
                      }
                    />
                  </th>
                  <th className="w-8 px-1 py-2" />
                  <th className="px-2 py-2 font-medium whitespace-nowrap">{tj('columns.tradeDate')}</th>
                  <th className="px-2 py-2 font-medium whitespace-nowrap">{tj('columns.stockName')}</th>
                  <th className="px-2 py-2 font-medium whitespace-nowrap">{tj('columns.sector')}</th>
                  <th className="px-2 py-2 font-medium whitespace-nowrap text-right">{tj('columns.openChangePct')}</th>
                  <th className="px-2 py-2 font-medium whitespace-nowrap">{tj('columns.sealResult')}</th>
                  <th className="px-2 py-2 font-medium whitespace-nowrap">{tj('columns.sellTiming')}</th>
                  <th className="px-2 py-2 font-medium whitespace-nowrap text-right">{tj('columns.returnPct')}</th>
                  <th className="px-2 py-2 font-medium whitespace-nowrap">{tj('columns.reviewNote')}</th>
                  <th className="w-20 px-2 py-2" />
                </tr>
              </thead>
              <tbody>
                {entries.map(entry => (
                  <tr
                    key={entry.id}
                    draggable
                    onDragStart={e => {
                      dragSnapshotRef.current = entries
                      setDraggingId(entry.id)
                      e.dataTransfer.effectAllowed = 'move'
                    }}
                    onDragOver={e => {
                      e.preventDefault()
                      e.dataTransfer.dropEffect = 'move'
                      if (draggingId != null && draggingId !== entry.id) {
                        setEntries(prev => moveById(prev, draggingId, entry.id))
                      }
                    }}
                    onDrop={e => {
                      e.preventDefault()
                      void commitReorder()
                      setDraggingId(null)
                      dragSnapshotRef.current = null
                    }}
                    onDragEnd={() => {
                      setDraggingId(null)
                      dragSnapshotRef.current = null
                    }}
                    className={`group border-b border-border/20 last:border-0 hover:bg-accent/30 transition-colors ${draggingId === entry.id ? 'opacity-60' : ''}`}
                  >
                    <td className="px-4 py-2">
                      <input
                        type="checkbox"
                        checked={selected.includes(entry.id)}
                        onChange={() => toggleSelected(entry.id)}
                      />
                    </td>
                    <td className="px-1 py-2 text-muted-foreground/50 cursor-grab active:cursor-grabbing">
                      <GripVertical className="w-3.5 h-3.5" />
                    </td>
                    <td className="px-2 py-2 font-mono whitespace-nowrap">{entry.trade_date}</td>
                    <td className="px-2 py-2 font-medium whitespace-nowrap">{entry.stock_name}</td>
                    <td className="px-2 py-2 text-muted-foreground whitespace-nowrap">{entry.sector || '-'}</td>
                    <td className={`px-2 py-2 text-right font-mono whitespace-nowrap ${marketSignTextClass(entry.open_change_pct)}`}>
                      {formatPct(entry.open_change_pct)}
                    </td>
                    <td className="px-2 py-2 whitespace-nowrap">{entry.seal_result || '-'}</td>
                    <td className="px-2 py-2 whitespace-nowrap">{entry.sell_timing || '-'}</td>
                    <td className={`px-2 py-2 text-right font-mono font-semibold whitespace-nowrap ${marketSignTextClass(entry.return_pct)}`}>
                      {formatPct(entry.return_pct)}
                    </td>
                    <td className="px-2 py-2 text-muted-foreground">
                      <span className="line-clamp-2 max-w-[16rem] whitespace-pre-wrap" title={entry.review_note}>
                        {entry.review_note || '-'}
                      </span>
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0"
                          disabled={mutating}
                          onClick={() => openEdit(entry)}
                          title={tj('actions.edit')}
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                          disabled={mutating}
                          onClick={() => removeOne(entry)}
                          title={tj('actions.remove')}
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

        {/* 分页: 每页 20 条,可点击页码跳转 */}
        {pages > 1 && (
          <div className="flex items-center justify-center gap-1.5 border-t border-border/40 px-4 py-2.5">
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-[11px]"
              disabled={page <= 1 || loading}
              onClick={() => load(page - 1)}
            >
              {tj('pagination.previous')}
            </Button>
            {pageNumbers.map((p, idx) =>
              p === '...' ? (
                <span key={`ellipsis-${idx}`} className="px-1 text-[11px] text-muted-foreground">…</span>
              ) : (
                <button
                  key={p}
                  disabled={loading}
                  onClick={() => load(p)}
                  className={`h-7 min-w-7 rounded-md px-1.5 text-[11px] font-mono transition-colors ${
                    p === page
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                  }`}
                >
                  {p}
                </button>
              )
            )}
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-[11px]"
              disabled={page >= pages || loading}
              onClick={() => load(page + 1)}
            >
              {tj('pagination.next')}
            </Button>
          </div>
        )}
      </div>

      {/* 新增/编辑弹窗 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingId == null ? tj('dialog.createTitle') : tj('dialog.editTitle')}</DialogTitle>
            <DialogDescription>{tj('dialog.description')}</DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>{tj('columns.tradeDate')}</Label>
              <Input
                type="date"
                value={form.trade_date}
                onChange={e => setForm({ ...form, trade_date: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>{tj('columns.stockName')}</Label>
              <Input
                value={form.stock_name}
                placeholder={tj('placeholder.stockName')}
                onChange={e => setForm({ ...form, stock_name: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>{tj('columns.sector')}</Label>
              <Input
                value={form.sector}
                placeholder={tj('placeholder.sector')}
                onChange={e => setForm({ ...form, sector: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>{tj('columns.openChangePct')}</Label>
              <Input
                inputMode="decimal"
                value={form.open_change_pct}
                placeholder={tj('placeholder.openChangePct')}
                onChange={e => setForm({ ...form, open_change_pct: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>{tj('columns.sealResult')}</Label>
              <Input
                value={form.seal_result}
                placeholder={tj('placeholder.sealResult')}
                onChange={e => setForm({ ...form, seal_result: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>{tj('columns.sellTiming')}</Label>
              <Input
                value={form.sell_timing}
                placeholder={tj('placeholder.sellTiming')}
                onChange={e => setForm({ ...form, sell_timing: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>{tj('columns.returnPct')}</Label>
              <Input
                inputMode="decimal"
                value={form.return_pct}
                placeholder={tj('placeholder.returnPct')}
                onChange={e => setForm({ ...form, return_pct: e.target.value })}
              />
            </div>
            <div className="space-y-1.5 col-span-2">
              <Label>{tj('columns.reviewNote')}</Label>
              <textarea
                className="flex min-h-[72px] w-full rounded-lg border border-border/60 bg-background px-3 py-2 text-[13px] placeholder:text-muted-foreground/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                value={form.review_note}
                placeholder={tj('placeholder.reviewNote')}
                onChange={e => setForm({ ...form, review_note: e.target.value })}
              />
            </div>
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setDialogOpen(false)} disabled={mutating}>
              {tj('dialog.cancel')}
            </Button>
            <Button size="sm" onClick={submitForm} disabled={mutating}>
              {mutating ? tj('dialog.saving') : tj('dialog.save')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
