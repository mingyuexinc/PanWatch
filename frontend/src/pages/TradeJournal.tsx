import { useCallback, useEffect, useRef, useState } from 'react'
import {
  BookOpenText,
  Plus,
  Trash2,
  Pencil,
  GripVertical,
  Calculator,
  FolderKanban,
  GitBranch,
  ArrowRightLeft,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  tradeJournalApi,
  type TradeJournalEntry,
  type TradeJournalStats,
  type TradeJournalStrategy,
  type TradeJournalVersion,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@panwatch/base-ui/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@panwatch/base-ui/components/ui/dialog'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { marketSignTextClass } from '@/lib/market-colors'

const PAGE_SIZE = 10
const SELECTION_STORAGE_KEY = 'panwatch.tradeJournal.selection'

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

interface MetaForm {
  name: string
  description: string
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

const EMPTY_META: MetaForm = { name: '', description: '' }

function readStoredSelection(): { strategyId: number | null; versionId: number | null } {
  try {
    const raw = localStorage.getItem(SELECTION_STORAGE_KEY)
    if (!raw) return { strategyId: null, versionId: null }
    const parsed = JSON.parse(raw) as { strategyId?: number; versionId?: number }
    return {
      strategyId: typeof parsed.strategyId === 'number' ? parsed.strategyId : null,
      versionId: typeof parsed.versionId === 'number' ? parsed.versionId : null,
    }
  } catch {
    return { strategyId: null, versionId: null }
  }
}

function storeSelection(strategyId: number, versionId: number) {
  localStorage.setItem(
    SELECTION_STORAGE_KEY,
    JSON.stringify({ strategyId, versionId }),
  )
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

  const [strategies, setStrategies] = useState<TradeJournalStrategy[]>([])
  const [versions, setVersions] = useState<TradeJournalVersion[]>([])
  const [strategyId, setStrategyId] = useState<number | null>(null)
  const [versionId, setVersionId] = useState<number | null>(null)
  const [hierarchyLoading, setHierarchyLoading] = useState(true)

  const [loading, setLoading] = useState(true)
  const [entries, setEntries] = useState<TradeJournalEntry[]>([])
  const [total, setTotal] = useState(0)
  const [pages, setPages] = useState(1)
  const [page, setPage] = useState(1)
  const [mutating, setMutating] = useState(false)
  const [selected, setSelected] = useState<number[]>([])

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<EntryForm>(EMPTY_FORM)

  const [strategyDialogOpen, setStrategyDialogOpen] = useState(false)
  const [strategyEditingId, setStrategyEditingId] = useState<number | null>(null)
  const [strategyForm, setStrategyForm] = useState<MetaForm>(EMPTY_META)

  const [versionDialogOpen, setVersionDialogOpen] = useState(false)
  const [versionEditingId, setVersionEditingId] = useState<number | null>(null)
  const [versionForm, setVersionForm] = useState<MetaForm>(EMPTY_META)

  const [moveDialogOpen, setMoveDialogOpen] = useState(false)
  const [moveTargetVersionId, setMoveTargetVersionId] = useState<string>('')

  const [stats, setStats] = useState<TradeJournalStats | null>(null)
  const [statsLoading, setStatsLoading] = useState(false)

  const [draggingId, setDraggingId] = useState<number | null>(null)
  const dragSnapshotRef = useRef<TradeJournalEntry[] | null>(null)

  const loadVersionsForStrategy = useCallback(
    async (sid: number, preferredVersionId: number | null) => {
      const payload = await tradeJournalApi.listVersions(sid)
      setVersions(payload.items)
      const vid =
        preferredVersionId != null && payload.items.some(v => v.id === preferredVersionId)
          ? preferredVersionId
          : payload.items[0]?.id ?? null
      setVersionId(vid)
      if (vid != null) storeSelection(sid, vid)
      return vid
    },
    [],
  )

  const loadHierarchy = useCallback(async () => {
    setHierarchyLoading(true)
    try {
      const stored = readStoredSelection()
      const { items } = await tradeJournalApi.listStrategies()
      setStrategies(items)
      const sid =
        stored.strategyId != null && items.some(s => s.id === stored.strategyId)
          ? stored.strategyId
          : items[0]?.id ?? null
      setStrategyId(sid)
      if (sid == null) {
        setVersions([])
        setVersionId(null)
        return null
      }
      return loadVersionsForStrategy(sid, stored.versionId)
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.loadFailed'), 'error')
      return null
    } finally {
      setHierarchyLoading(false)
    }
  }, [loadVersionsForStrategy]) // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(
    async (targetPage: number, vid: number) => {
      setLoading(true)
      try {
        const payload = await tradeJournalApi.list(vid, targetPage, PAGE_SIZE)
        setEntries(payload.items)
        setTotal(payload.total)
        setPages(payload.pages)
        setPage(payload.page)
        setSelected(prev => prev.filter(id => payload.items.some(i => i.id === id)))
        setStats(null)
      } catch (e) {
        toast(e instanceof Error ? e.message : tj('messages.loadFailed'), 'error')
      } finally {
        setLoading(false)
      }
    },
    [], // eslint-disable-line react-hooks/exhaustive-deps
  )

  useEffect(() => {
    void loadHierarchy()
  }, [loadHierarchy])

  useEffect(() => {
    if (versionId == null) {
      setEntries([])
      setTotal(0)
      setLoading(false)
      return
    }
    void load(1, versionId)
  }, [versionId, load])

  const onStrategyChange = async (value: string) => {
    const sid = Number(value)
    setStrategyId(sid)
    setStats(null)
    setSelected([])
    try {
      await loadVersionsForStrategy(sid, null)
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.loadFailed'), 'error')
    }
  }

  const onVersionChange = (value: string) => {
    const vid = Number(value)
    setVersionId(vid)
    if (strategyId != null) storeSelection(strategyId, vid)
    setStats(null)
    setSelected([])
  }

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
    if (mutating || versionId == null) return
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
        version_id: versionId,
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
        await load(1, versionId)
      } else {
        await tradeJournalApi.update(editingId, payload)
        toast(tj('messages.updated'), 'success')
        await load(page, versionId)
      }
      setDialogOpen(false)
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.saveFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const openStrategyDialog = (strategy?: TradeJournalStrategy) => {
    if (strategy) {
      setStrategyEditingId(strategy.id)
      setStrategyForm({ name: strategy.name, description: strategy.description })
    } else {
      setStrategyEditingId(null)
      setStrategyForm(EMPTY_META)
    }
    setStrategyDialogOpen(true)
  }

  const submitStrategyForm = async () => {
    if (mutating) return
    if (!strategyForm.name.trim()) {
      toast(tj('validation.strategyNameRequired'), 'error')
      return
    }
    setMutating(true)
    try {
      const body = {
        name: strategyForm.name.trim(),
        description: strategyForm.description.trim(),
      }
      if (strategyEditingId == null) {
        const created = await tradeJournalApi.createStrategy(body)
        toast(tj('strategy.created'), 'success')
        setStrategyDialogOpen(false)
        const { items } = await tradeJournalApi.listStrategies()
        setStrategies(items)
        setStrategyId(created.id)
        const vers = await tradeJournalApi.listVersions(created.id)
        setVersions(vers.items)
        const vid = vers.items[0]?.id ?? null
        setVersionId(vid)
        if (vid != null) storeSelection(created.id, vid)
      } else {
        await tradeJournalApi.updateStrategy(strategyEditingId, body)
        toast(tj('strategy.updated'), 'success')
        setStrategyDialogOpen(false)
        const { items } = await tradeJournalApi.listStrategies()
        setStrategies(items)
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.saveFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const deleteStrategy = async () => {
    if (mutating || strategyEditingId == null) return
    const name = strategyForm.name
    if (!window.confirm(tj('strategy.deleteConfirm', { name }))) return
    setMutating(true)
    try {
      await tradeJournalApi.removeStrategy(strategyEditingId)
      toast(tj('strategy.deleted'), 'success')
      setStrategyDialogOpen(false)
      await loadHierarchy()
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.deleteFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const openVersionDialog = (version?: TradeJournalVersion) => {
    if (version) {
      setVersionEditingId(version.id)
      setVersionForm({ name: version.name, description: version.description })
    } else {
      setVersionEditingId(null)
      setVersionForm(EMPTY_META)
    }
    setVersionDialogOpen(true)
  }

  const submitVersionForm = async () => {
    if (mutating || strategyId == null) return
    if (!versionForm.name.trim()) {
      toast(tj('validation.versionNameRequired'), 'error')
      return
    }
    setMutating(true)
    try {
      const body = {
        name: versionForm.name.trim(),
        description: versionForm.description.trim(),
      }
      if (versionEditingId == null) {
        const created = await tradeJournalApi.createVersion(strategyId, body)
        toast(tj('version.created'), 'success')
        setVersionDialogOpen(false)
        await loadVersionsForStrategy(strategyId, created.id)
      } else {
        await tradeJournalApi.updateVersion(versionEditingId, body)
        toast(tj('version.updated'), 'success')
        setVersionDialogOpen(false)
        await loadVersionsForStrategy(strategyId, versionId)
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.saveFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const deleteVersion = async () => {
    if (mutating || versionEditingId == null) return
    const name = versionForm.name
    if (!window.confirm(tj('version.deleteConfirm', { name }))) return
    setMutating(true)
    try {
      await tradeJournalApi.removeVersion(versionEditingId)
      toast(tj('version.deleted'), 'success')
      setVersionDialogOpen(false)
      if (strategyId != null) await loadVersionsForStrategy(strategyId, null)
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.deleteFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const openMoveDialog = () => {
    if (selected.length === 0) {
      toast(tj('moveToVersion.needSelection'), 'error')
      return
    }
    const other = versions.find(v => v.id !== versionId)
    setMoveTargetVersionId(other ? String(other.id) : '')
    setMoveDialogOpen(true)
  }

  const submitMove = async () => {
    if (mutating || !moveTargetVersionId) {
      toast(tj('moveToVersion.needTarget'), 'error')
      return
    }
    const targetId = Number(moveTargetVersionId)
    if (targetId === versionId) {
      setMoveDialogOpen(false)
      return
    }
    setMutating(true)
    try {
      const result = await tradeJournalApi.moveToVersion(selected, targetId)
      toast(tj('moveToVersion.moved', { count: result.moved ?? 0 }), 'success')
      setSelected([])
      setMoveDialogOpen(false)
      if (versionId != null) await load(page, versionId)
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.saveFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const removeOne = async (entry: TradeJournalEntry) => {
    if (mutating || versionId == null) return
    if (!window.confirm(tj('messages.deleteConfirm', { name: entry.stock_name }))) return
    setMutating(true)
    try {
      await tradeJournalApi.remove(entry.id)
      toast(tj('messages.deleted'), 'success')
      await load(page, versionId)
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.deleteFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const batchRemove = async () => {
    if (mutating || selected.length === 0 || versionId == null) return
    if (!window.confirm(tj('messages.batchDeleteConfirm', { count: selected.length }))) return
    setMutating(true)
    try {
      const result = await tradeJournalApi.batchRemove(selected)
      setSelected([])
      toast(tj('messages.batchDeleted', { count: result.removed ?? 0 }), 'success')
      await load(page, versionId)
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('messages.deleteFailed'), 'error')
    } finally {
      setMutating(false)
    }
  }

  const runStats = async () => {
    if (statsLoading || versionId == null) return
    setStatsLoading(true)
    try {
      setStats(await tradeJournalApi.stats(versionId))
    } catch (e) {
      toast(e instanceof Error ? e.message : tj('stats.loadFailed'), 'error')
    } finally {
      setStatsLoading(false)
    }
  }

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
    if (versionId == null) return
    const current = entries
    if (!current || current.length < 2) return
    try {
      await tradeJournalApi.reorder(versionId, current.map(e => e.id))
    } catch (e) {
      if (dragSnapshotRef.current) setEntries(dragSnapshotRef.current)
      toast(e instanceof Error ? e.message : tj('messages.reorderFailed'), 'error')
    }
  }

  const toggleSelected = (id: number) => {
    setSelected(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]))
  }

  const allVisibleSelected = entries.length > 0 && entries.every(i => selected.includes(i.id))
  const currentStrategy = strategies.find(s => s.id === strategyId)
  const currentVersion = versions.find(v => v.id === versionId)
  const moveTargetOptions = versions.filter(v => v.id !== versionId)

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
        {currentStrategy?.description ? (
          <p className="text-[11px] text-muted-foreground/80">{currentStrategy.description}</p>
        ) : null}
        {currentVersion?.description ? (
          <p className="text-[11px] text-muted-foreground/80">{currentVersion.description}</p>
        ) : null}
      </div>

      {/* 策略 / 版本切换 */}
      <div className="flex flex-col gap-2 rounded-xl border border-border/60 bg-card px-4 py-3 sm:flex-row sm:flex-wrap sm:items-end">
        <div className="space-y-1 min-w-[180px] flex-1">
          <Label className="text-[11px] text-muted-foreground">{tj('strategy.label')}</Label>
          <div className="flex gap-1.5">
            <Select
              value={strategyId != null ? String(strategyId) : undefined}
              onValueChange={onStrategyChange}
              disabled={hierarchyLoading || strategies.length === 0}
            >
              <SelectTrigger className="h-8 text-[12px] flex-1">
                <SelectValue placeholder={tj('strategy.empty')} />
              </SelectTrigger>
              <SelectContent>
                {strategies.map(s => (
                  <SelectItem key={s.id} value={String(s.id)}>
                    {s.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="sm"
              className="h-8 shrink-0 px-2"
              disabled={hierarchyLoading}
              onClick={() => openStrategyDialog(currentStrategy)}
              title={tj('strategy.manage')}
            >
              <FolderKanban className="w-4 h-4" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 shrink-0 px-2"
              disabled={hierarchyLoading}
              onClick={() => openStrategyDialog()}
              title={tj('strategy.add')}
            >
              <Plus className="w-4 h-4" />
            </Button>
          </div>
        </div>
        <div className="space-y-1 min-w-[180px] flex-1">
          <Label className="text-[11px] text-muted-foreground">{tj('version.label')}</Label>
          <div className="flex gap-1.5">
            <Select
              value={versionId != null ? String(versionId) : undefined}
              onValueChange={onVersionChange}
              disabled={hierarchyLoading || strategyId == null || versions.length === 0}
            >
              <SelectTrigger className="h-8 text-[12px] flex-1">
                <SelectValue placeholder={tj('version.empty')} />
              </SelectTrigger>
              <SelectContent>
                {versions.map(v => (
                  <SelectItem key={v.id} value={String(v.id)}>
                    {v.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="sm"
              className="h-8 shrink-0 px-2"
              disabled={strategyId == null}
              onClick={() => openVersionDialog(currentVersion)}
              title={tj('version.manage')}
            >
              <GitBranch className="w-4 h-4" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 shrink-0 px-2"
              disabled={strategyId == null}
              onClick={() => openVersionDialog()}
              title={tj('version.add')}
            >
              <Plus className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between gap-2 flex-wrap">
        <Button
          size="sm"
          className="h-8"
          disabled={mutating || versionId == null}
          onClick={openCreate}
        >
          <Plus className="w-4 h-4 mr-1" />
          {tj('addRecord')}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-8"
          disabled={statsLoading || versionId == null}
          onClick={runStats}
        >
          <Calculator className="w-4 h-4 mr-1" />
          {statsLoading ? tj('stats.calculating') : tj('stats.button')}
        </Button>
      </div>

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
              <div
                className={`font-mono text-lg font-semibold ${marketSignTextClass((stats.win_rate ?? 0) - 0.5)}`}
              >
                {stats.win_rate == null ? '--' : `${(stats.win_rate * 100).toFixed(1)}%`}
              </div>
              <div className="text-[10px] text-muted-foreground">
                {tj('stats.winCount', { win: stats.win_count, counted: stats.counted })}
              </div>
            </div>
            <div>
              <div className="text-[11px] text-muted-foreground">{tj('stats.totalReturn')}</div>
              <div
                className={`font-mono text-lg font-semibold ${marketSignTextClass(stats.total_return_pct)}`}
              >
                {stats.total_return_pct == null ? '--' : formatPct(stats.total_return_pct)}
              </div>
              <div className="text-[10px] text-muted-foreground">{tj('stats.totalReturnHint')}</div>
            </div>
          </div>
        </div>
      )}

      <div className="rounded-xl border border-border/60 bg-card overflow-hidden">
        <div className="flex items-center justify-between border-b border-border/40 px-4 py-2.5 gap-2 flex-wrap">
          <div className="text-[12px] text-muted-foreground">
            {tj('count', { count: total })}
            <span className="ml-2 text-muted-foreground/70">{tj('dragHint')}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-[11px]"
              disabled={selected.length === 0 || mutating || versionId == null}
              onClick={openMoveDialog}
            >
              <ArrowRightLeft className="w-3.5 h-3.5 mr-1" />
              {tj('moveToVersion.button')}
            </Button>
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
        </div>

        {loading || hierarchyLoading ? (
          <div className="px-4 py-8 text-center text-[12px] text-muted-foreground">{tj('loading')}</div>
        ) : versionId == null ? (
          <div className="px-4 py-10 text-center text-[12px] text-muted-foreground">{tj('version.empty')}</div>
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

        {pages > 1 && versionId != null && (
          <div className="flex items-center justify-center gap-1.5 border-t border-border/40 px-4 py-2.5">
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-[11px]"
              disabled={page <= 1 || loading}
              onClick={() => load(page - 1, versionId)}
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
                  onClick={() => load(p, versionId)}
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
              onClick={() => load(page + 1, versionId)}
            >
              {tj('pagination.next')}
            </Button>
          </div>
        )}
      </div>

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

      <Dialog open={strategyDialogOpen} onOpenChange={setStrategyDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {strategyEditingId == null ? tj('strategy.add') : tj('strategy.edit')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>{tj('strategy.name')}</Label>
              <Input
                value={strategyForm.name}
                onChange={e => setStrategyForm({ ...strategyForm, name: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>{tj('strategy.description')}</Label>
              <textarea
                className="flex min-h-[72px] w-full rounded-lg border border-border/60 bg-background px-3 py-2 text-[13px]"
                value={strategyForm.description}
                onChange={e => setStrategyForm({ ...strategyForm, description: e.target.value })}
              />
            </div>
          </div>
          <div className="mt-2 flex justify-between gap-2">
            {strategyEditingId != null && strategies.length > 1 ? (
              <Button variant="destructive" size="sm" onClick={deleteStrategy} disabled={mutating}>
                {tj('metaDialog.delete')}
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setStrategyDialogOpen(false)} disabled={mutating}>
                {tj('metaDialog.cancel')}
              </Button>
              <Button size="sm" onClick={submitStrategyForm} disabled={mutating}>
                {mutating ? tj('metaDialog.saving') : tj('metaDialog.save')}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={versionDialogOpen} onOpenChange={setVersionDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {versionEditingId == null ? tj('version.add') : tj('version.edit')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>{tj('version.name')}</Label>
              <Input
                value={versionForm.name}
                onChange={e => setVersionForm({ ...versionForm, name: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>{tj('version.description')}</Label>
              <textarea
                className="flex min-h-[72px] w-full rounded-lg border border-border/60 bg-background px-3 py-2 text-[13px]"
                value={versionForm.description}
                onChange={e => setVersionForm({ ...versionForm, description: e.target.value })}
              />
            </div>
          </div>
          <div className="mt-2 flex justify-between gap-2">
            {versionEditingId != null && versions.length > 1 ? (
              <Button variant="destructive" size="sm" onClick={deleteVersion} disabled={mutating}>
                {tj('metaDialog.delete')}
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setVersionDialogOpen(false)} disabled={mutating}>
                {tj('metaDialog.cancel')}
              </Button>
              <Button size="sm" onClick={submitVersionForm} disabled={mutating}>
                {mutating ? tj('metaDialog.saving') : tj('metaDialog.save')}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={moveDialogOpen} onOpenChange={setMoveDialogOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{tj('moveToVersion.title')}</DialogTitle>
            <DialogDescription>{tj('moveToVersion.description')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label>{tj('moveToVersion.target')}</Label>
            <Select value={moveTargetVersionId} onValueChange={setMoveTargetVersionId}>
              <SelectTrigger className="h-8 text-[12px]">
                <SelectValue placeholder={tj('moveToVersion.needTarget')} />
              </SelectTrigger>
              <SelectContent>
                {moveTargetOptions.map(v => (
                  <SelectItem key={v.id} value={String(v.id)}>
                    {v.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setMoveDialogOpen(false)} disabled={mutating}>
              {tj('metaDialog.cancel')}
            </Button>
            <Button size="sm" onClick={submitMove} disabled={mutating || moveTargetOptions.length === 0}>
              {tj('moveToVersion.confirm')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
