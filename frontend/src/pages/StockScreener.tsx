import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchAPI,
  screenerApi,
  type NotifyChannel,
  type ScreenerQuery,
  type ScreenerMergedMock,
  type ScreenerStrategyPayload,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useTranslation } from 'react-i18next'
import { marketSignTextClass } from '@/lib/market-colors'

const PAGE_SIZE = 10
const SELECT_TYPES = ['A股', '港股', '美股', '基金', 'ETF', '可转债', '板块'] as const

const GAIN_PCT_OPTIONS = Array.from({ length: 11 }, (_, i) => i)

const MONITOR_WINDOW_PRESETS = [
  { id: 'open5' as const, start: '09:30', end: '09:35' },
  { id: 'open10' as const, start: '09:30', end: '09:40' },
  { id: 'open30' as const, start: '09:30', end: '10:00' },
]

function normalizeHm(value: string): string {
  const parts = value.trim().split(':')
  if (parts.length < 2) return value
  const h = Number(parts[0])
  const m = Number(parts[1])
  if (!Number.isFinite(h) || !Number.isFinite(m)) return value
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

function hmToMinutes(value: string): number | null {
  const norm = normalizeHm(value)
  const parts = norm.split(':')
  if (parts.length !== 2) return null
  const h = Number(parts[0])
  const m = Number(parts[1])
  if (!Number.isFinite(h) || !Number.isFinite(m)) return null
  return h * 60 + m
}

function isMonitorWindowValid(start: string, end: string): boolean {
  const a = hmToMinutes(start)
  const b = hmToMinutes(end)
  return a != null && b != null && a < b
}

function matchingMonitorPreset(start: string, end: string) {
  const s = normalizeHm(start)
  const e = normalizeHm(end)
  return MONITOR_WINDOW_PRESETS.find(p => p.start === s && p.end === e)?.id ?? null
}

type ResultTab = 'screener' | 'triggers'

/** 选股结果固定展示列（与东财返回的其它字段解耦，避免整表渲染导致布局撑爆） */
const ROW_FIELD_RULES = {
  code: [/代码/i, /^code$/i],
  name: [/名称/i, /^name$/i, /简称/],
  price: [/股价/, /最新价/, /现价/, /价格/, /^price$/i, /成交价/],
  changePct: [/涨跌幅/, /^pct$/i, /涨幅/],
} as const

function pickRowField(row: Record<string, string>, rules: readonly RegExp[]): string {
  for (const [key, val] of Object.entries(row)) {
    if (val == null || String(val).trim() === '') continue
    if (rules.some(r => r.test(key))) return String(val).trim()
  }
  return '—'
}

function parseChangePctNum(raw: string): number | null {
  if (!raw || raw === '—') return null
  const num = parseFloat(raw.replace(/[^\d.-]/g, ''))
  return Number.isFinite(num) ? num : null
}

/** 东财返回的涨跌幅常为裸数字，统一为带符号与 % 的展示（涨为正且带 +） */
function formatChangePctDisplay(raw: string): string {
  const num = parseChangePctNum(raw)
  if (num == null) return raw || '—'
  const sign = num > 0 ? '+' : ''
  return `${sign}${num.toFixed(2)}%`
}

/** ISO / 后端 datetime 展示为 YYYY-MM-DD HH:mm:ss */
function formatTriggerAtDisplay(raw: string | null | undefined): string {
  if (!raw || raw === '—') return '—'
  const text = raw.trim()
  const noFrac = text.replace(/\.\d+(?=[Z+-]|$)/, '').replace(/Z$/i, '')
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$/.test(noFrac)) {
    return noFrac.replace('T', ' ')
  }
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}$/.test(noFrac)) {
    return `${noFrac.replace('T', ' ')}:00`
  }
  return text.replace('T', ' ')
}

type PanelId = number | 'merged'
type ScreenEngine = 'em' | 'operators'
type PlanTemplate = 'interval_rank' | 'intraday_pattern'

type OperatorDraft = {
  template: PlanTemplate
  excludeSt: boolean
  excludeDelist: boolean
  excludeBjs: boolean
  excludeKcb: boolean
  rankStart: string
  rankEnd: string
  rankTopN: string
  priceMin: string
  priceMax: string
  prevDayBearishOrDown: boolean
  todayBullish: boolean
  limitUpWithinDays: boolean
  notLimitUpToday: boolean
  tradingDaysN: string
}

const DEFAULT_OPERATOR_DRAFT: OperatorDraft = {
  template: 'interval_rank',
  excludeSt: true,
  excludeDelist: true,
  excludeBjs: true,
  excludeKcb: true,
  rankStart: '2026-08-03',
  rankEnd: '2026-08-31',
  rankTopN: '100',
  priceMin: '1',
  priceMax: '20',
  prevDayBearishOrDown: true,
  todayBullish: true,
  limitUpWithinDays: true,
  notLimitUpToday: true,
  tradingDaysN: '7',
}

function ConfigCheck({
  id,
  checked,
  onChange,
  label,
}: {
  id: string
  checked: boolean
  onChange: (v: boolean) => void
  label: string
}) {
  return (
    <label htmlFor={id} className="flex items-center gap-2 text-[13px] cursor-pointer select-none">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={e => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-border text-primary focus:ring-primary/30"
      />
      {label}
    </label>
  )
}

function statusBadgeClass(status: string) {
  switch (status) {
    case 'fresh':
      return 'bg-success/15 text-success'
    case 'stale':
      return 'bg-amber-500/15 text-amber-700 dark:text-amber-400'
    case 'error':
      return 'bg-destructive/12 text-destructive'
    case 'running':
      return 'bg-primary/12 text-primary'
    default:
      return 'bg-muted text-muted-foreground'
  }
}

function badgeLabel(status: string, rowCount: number) {
  if (status === 'fresh' || status === 'stale') return String(rowCount)
  if (status === 'running') return '…'
  return '—'
}

export default function StockScreenerPage() {
  const { t } = useTranslation('screener')
  const { toast } = useToast()
  const [items, setItems] = useState<ScreenerQuery[]>([])
  const [active, setActive] = useState<PanelId | null>(null)
  const [detail, setDetail] = useState<ScreenerQuery | null>(null)
  const [merged, setMerged] = useState<ScreenerMergedMock | null>(null)
  const [page, setPage] = useState(1)
  const [triggerPage, setTriggerPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [draftLabel, setDraftLabel] = useState('')
  const [draftText, setDraftText] = useState('')
  const [draftType, setDraftType] = useState<string>('A股')
  const [screenEngine, setScreenEngine] = useState<ScreenEngine>('em')
  const [operatorDraft, setOperatorDraft] = useState<OperatorDraft>(DEFAULT_OPERATOR_DRAFT)
  const contextRef = useRef<{ id: number; x: number; y: number } | null>(null)
  const [contextOpen, setContextOpen] = useState(false)
  const [contextPos, setContextPos] = useState({ x: 0, y: 0 })
  const [strategyPayload, setStrategyPayload] = useState<ScreenerStrategyPayload | null>(null)
  const [monitorStart, setMonitorStart] = useState('09:30')
  const [monitorEnd, setMonitorEnd] = useState('09:40')
  const [gainPct, setGainPct] = useState(3)
  const [resultTab, setResultTab] = useState<ResultTab>('screener')
  const [testTriggering, setTestTriggering] = useState(false)
  const [lastScanMessage, setLastScanMessage] = useState<string | null>(null)
  const [notifyChannels, setNotifyChannels] = useState<NotifyChannel[]>([])
  const [notifyChannelId, setNotifyChannelId] = useState<number | null>(null)
  const [autoMonitorEnabled, setAutoMonitorEnabled] = useState(true)

  const loadList = useCallback(async () => {
    const res = await screenerApi.listQueries()
    setItems(res.items || [])
    return res.items || []
  }, [])

  const loadMerged = useCallback(async () => {
    const data = await screenerApi.getMergedMock()
    setMerged(data)
  }, [])

  const applyStrategyPayload = useCallback((data: ScreenerStrategyPayload) => {
    setStrategyPayload(data)
    setMonitorStart(normalizeHm(data.config.monitor_start))
    setMonitorEnd(normalizeHm(data.config.monitor_end))
    setGainPct(data.config.gain_pct)
    setNotifyChannelId(data.config.notify_channel_id ?? null)
    setAutoMonitorEnabled(data.config.auto_monitor_enabled !== false)
  }, [])

  const loadStrategy = useCallback(async () => {
    const data = await screenerApi.getStrategy()
    applyStrategyPayload(data)
    return data
  }, [applyStrategyPayload])

  const loadDetail = useCallback(async (id: number) => {
    const data = await screenerApi.getQuery(id)
    setDetail(data)
    setDraftLabel(data.label)
    setDraftText(data.query_text)
    setDraftType(data.select_type)
    setPage(1)
    return data
  }, [])

  useEffect(() => {
    ;(async () => {
      try {
        setLoading(true)
        const list = await loadList()
        await loadMerged()
        await loadStrategy()
        try {
          const ch = await fetchAPI<NotifyChannel[]>('/channels')
          setNotifyChannels(ch)
        } catch {
          setNotifyChannels([])
        }
        if (list.length > 0) {
          const firstId = list[0].id
          setActive(firstId)
          await loadDetail(firstId)
        }
      } catch (e) {
        toast(e instanceof Error ? e.message : t('loadFailed'), 'error')
      } finally {
        setLoading(false)
      }
    })()
  }, [loadDetail, loadList, loadMerged, loadStrategy, t, toast])

  const strategyBoundToActive =
    typeof active === 'number' && strategyPayload?.config.query_id === active

  const applyMonitorWindow = (start: string, end: string) => {
    setMonitorStart(normalizeHm(start))
    setMonitorEnd(normalizeHm(end))
  }

  const persistMonitorStrategy = async (queryId: number) => {
    const data = await screenerApi.updateStrategy({
      query_id: queryId,
      monitor_start: monitorStart,
      monitor_end: monitorEnd,
      gain_pct: gainPct,
      notify_channel_id: notifyChannelId!,
      auto_monitor_enabled: autoMonitorEnabled,
    })
    applyStrategyPayload(data)
  }

  const onTestTrigger = async () => {
    if (typeof active !== 'number') return
    if (!isMonitorWindowValid(monitorStart, monitorEnd)) {
      toast(t('monitorTimeInvalid'), 'error')
      return
    }
    if (notifyChannelId == null) {
      toast(t('notifyChannelRequired'), 'error')
      return
    }
    setTestTriggering(true)
    try {
      await persistMonitorStrategy(active)
      const data = await screenerApi.testTrigger()
      applyStrategyPayload(data)
      if (typeof active === 'number') await loadDetail(active)
      const scan = data.scan
      if (scan) {
        const summary =
          scan.message ||
          t('testTriggerDone', { date: scan.trade_date, count: scan.new_triggers ?? 0 })
        setLastScanMessage(summary)
        toast(summary, scan.new_triggers > 0 ? 'success' : 'info')
      } else {
        setLastScanMessage(null)
        toast(t('testTriggerDone', { date: '—', count: 0 }), 'info')
      }
      setResultTab('triggers')
    } catch (e) {
      toast(e instanceof Error ? e.message : t('testTriggerFailed'), 'error')
      try {
        await loadStrategy()
      } catch {
        /* ignore */
      }
    } finally {
      setTestTriggering(false)
    }
  }

  const refreshActive = useCallback(async () => {
    if (active === 'merged') {
      await loadMerged()
      return
    }
    if (typeof active === 'number') {
      const data = await loadDetail(active)
      setItems(prev => prev.map(it => (it.id === data.id ? { ...it, ...data, rows: undefined, columns: undefined } : it)))
    }
  }, [active, loadDetail, loadMerged])

  const onSelect = async (id: PanelId) => {
    setActive(id)
    setContextOpen(false)
    if (id === 'merged') {
      await loadMerged()
      return
    }
    try {
      await loadDetail(id)
    } catch (e) {
      toast(e instanceof Error ? e.message : t('loadFailed'), 'error')
    }
  }

  const onSave = async () => {
    if (typeof active !== 'number' || !detail) return
    if (screenEngine === 'em') {
      if (!isMonitorWindowValid(monitorStart, monitorEnd)) {
        toast(t('monitorTimeInvalid'), 'error')
        return
      }
      if (notifyChannelId == null) {
        toast(t('notifyChannelRequired'), 'error')
        return
      }
    }
    setSaving(true)
    try {
      const updated = await screenerApi.updateQuery(active, {
        label: draftLabel,
        query_text: screenEngine === 'em' ? draftText : detail.query_text,
        select_type: draftType,
      })
      setDetail(updated)
      setItems(prev => prev.map(it => (it.id === updated.id ? { ...it, ...updated, rows: undefined, columns: undefined } : it)))
      if (screenEngine === 'em') {
        await persistMonitorStrategy(active)
      }
      toast(t('saved'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : t('saveFailed'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const patchOperator = (patch: Partial<OperatorDraft>) => {
    setOperatorDraft(prev => ({ ...prev, ...patch }))
  }

  const resetOperatorDraft = () => setOperatorDraft(DEFAULT_OPERATOR_DRAFT)

  const operatorMenuCaption = useMemo(() => {
    if (operatorDraft.template === 'interval_rank') {
      return t('planTemplates.interval_rank')
    }
    return t('planTemplates.intraday_pattern')
  }, [operatorDraft.template, t])

  const onRun = async () => {
    if (typeof active !== 'number') return
    if (screenEngine === 'operators') {
      toast(t('operatorPreviewNote'), 'info')
      return
    }
    setRunning(true)
    try {
      await screenerApi.updateQuery(active, {
        label: draftLabel,
        query_text: draftText,
        select_type: draftType,
      })
      const updated = await screenerApi.runQuery(active)
      setDetail(updated)
      setItems(prev => prev.map(it => (it.id === updated.id ? { ...it, ...updated, rows: undefined, columns: undefined } : it)))
    } catch (e) {
      toast(e instanceof Error ? e.message : t('runFailed'), 'error')
      await refreshActive()
    } finally {
      setRunning(false)
    }
  }

  const onAdd = async () => {
    try {
      const created = await screenerApi.createQuery({ label: t('status.draft'), query_text: '', select_type: 'A股' })
      const list = await loadList()
      setItems(list)
      setActive(created.id)
      await loadDetail(created.id)
    } catch (e) {
      toast(e instanceof Error ? e.message : t('addFailed'), 'error')
    }
  }

  const onDelete = async (id: number, label: string) => {
    if (!window.confirm(t('deleteConfirm', { label }))) return
    try {
      await screenerApi.deleteQuery(id)
      const list = await loadList()
      setItems(list)
      if (list.length === 0) return
      if (active === id) {
        setActive(list[0].id)
        await loadDetail(list[0].id)
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : t('deleteFailed'), 'error')
    }
  }

  const onContextMenu = (e: React.MouseEvent, id: number) => {
    e.preventDefault()
    contextRef.current = { id, x: e.clientX, y: e.clientY }
    setContextPos({ x: e.clientX, y: e.clientY })
    setContextOpen(true)
  }

  useEffect(() => {
    const close = () => setContextOpen(false)
    window.addEventListener('click', close)
    window.addEventListener('scroll', close, true)
    return () => {
      window.removeEventListener('click', close)
      window.removeEventListener('scroll', close, true)
    }
  }, [])

  const displayRows = useMemo(() => detail?.rows || [], [detail?.rows])

  const pagedRows = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return displayRows.slice(start, start + PAGE_SIZE)
  }, [displayRows, page])

  const pageCount = Math.max(1, Math.ceil(displayRows.length / PAGE_SIZE))

  const triggerRows = useMemo(() => strategyPayload?.triggers ?? [], [strategyPayload?.triggers])
  const triggerPageCount = Math.max(1, Math.ceil(triggerRows.length / PAGE_SIZE))
  const pagedTriggers = useMemo(() => {
    const start = (triggerPage - 1) * PAGE_SIZE
    return triggerRows.slice(start, start + PAGE_SIZE)
  }, [triggerRows, triggerPage])

  useEffect(() => {
    setTriggerPage(1)
  }, [triggerRows.length, resultTab])

  if (loading) {
    return (
      <div className="flex justify-center py-24">
        <span className="w-6 h-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto space-y-4">
      <div>
        <h1 className="text-[22px] font-bold tracking-tight">{t('title')}</h1>
        <p className="text-[13px] text-muted-foreground mt-1.5 max-w-3xl leading-relaxed">{t('subtitle')}</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-4 items-start">
        <aside className="card p-2.5">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground px-2.5 pt-2 pb-1">{t('engineSection')}</div>
          <div className="space-y-1 px-1 pb-2">
            <button
              type="button"
              onClick={() => setScreenEngine('em')}
              className={`w-full text-left px-3 py-2.5 rounded-xl text-[13px] border transition-colors ${
                screenEngine === 'em'
                  ? 'border-primary/25 bg-primary/10 font-semibold'
                  : 'border-transparent hover:bg-accent'
              }`}
            >
              <div>{t('engineEm')}</div>
              <div className="text-[11px] text-muted-foreground font-normal">{t('engineEmHint')}</div>
            </button>
            <button
              type="button"
              onClick={() => setScreenEngine('operators')}
              className={`w-full text-left px-3 py-2.5 rounded-xl text-[13px] border transition-colors ${
                screenEngine === 'operators'
                  ? 'border-primary/25 bg-primary/10 font-semibold'
                  : 'border-transparent hover:bg-accent'
              }`}
            >
              <div>{t('engineOperators')}</div>
              <div className="text-[11px] text-muted-foreground font-normal">{t('engineOperatorsHint')}</div>
            </button>
          </div>
          <div className="h-px bg-border my-1 mx-1" />
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground px-2.5 pt-2 pb-1">{t('querySection')}</div>
          <p className="text-[11px] text-muted-foreground px-2.5 pb-2">{t('querySectionHint')}</p>
          <div className="space-y-1">
            {items.map(it => (
              <button
                key={it.id}
                type="button"
                onClick={() => onSelect(it.id)}
                onContextMenu={e => onContextMenu(e, it.id)}
                className={`w-full flex items-center justify-between gap-2 text-left px-3 py-2.5 rounded-xl text-[13px] border transition-colors ${
                  active === it.id
                    ? 'border-primary/25 bg-primary/10 font-semibold'
                    : 'border-transparent hover:bg-accent'
                }`}
              >
                <span className="min-w-0">
                  <div className="truncate">{it.label}</div>
                  <div className="text-[11px] text-muted-foreground font-normal truncate">
                    {screenEngine === 'operators' && active === it.id
                      ? operatorMenuCaption
                      : screenEngine === 'operators'
                        ? t('engineOperatorsHint')
                        : it.query_text || t('status.draft')}
                  </div>
                </span>
                <span className={`text-[11px] px-2 py-0.5 rounded-full font-semibold shrink-0 ${statusBadgeClass(it.status)}`}>
                  {badgeLabel(it.status, it.row_count)}
                </span>
              </button>
            ))}
          </div>
          <Button variant="outline" className="w-[calc(100%-8px)] mx-1 mt-2 border-dashed" onClick={onAdd}>
            {t('addQuery')}
          </Button>
          <div className="h-px bg-border my-2 mx-1" />
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground px-2.5 py-1">{t('summarySection')}</div>
          <button
            type="button"
            onClick={() => onSelect('merged')}
            className={`w-full flex items-center justify-between gap-2 text-left px-3 py-2.5 rounded-xl text-[13px] border transition-colors ${
              active === 'merged' ? 'border-primary/25 bg-primary/10 font-semibold' : 'border-transparent hover:bg-accent'
            }`}
          >
            <span>
              <div>{t('merged')}</div>
              <div className="text-[11px] text-muted-foreground font-normal">{t('mergedMeta')}</div>
            </span>
            <span className="text-[11px] px-2 py-0.5 rounded-full font-semibold bg-success/15 text-success">
              {merged?.total ?? '—'}
            </span>
          </button>
        </aside>

        <main className="card p-4 md:p-5 min-h-[520px] min-w-0">
          {active === 'merged' && merged && (
            <div className="space-y-4">
              <h2 className="text-base font-bold">{t('merged')}</h2>
              <div className="text-[13px] rounded-xl border border-primary/25 bg-primary/5 p-3 leading-relaxed">
                {t('mergedMockNote', { total: merged.total, count: merged.contributing_queries })}
              </div>
              <div className="overflow-auto rounded-xl border border-border/60">
                <table className="w-full text-[13px] min-w-[520px]">
                  <thead>
                    <tr className="bg-muted/50 text-muted-foreground text-[12px]">
                      <th className="text-left p-2.5">代码</th>
                      <th className="text-left p-2.5">名称</th>
                      <th className="text-left p-2.5">来源</th>
                      <th className="text-left p-2.5">涨跌幅</th>
                      <th className="text-left p-2.5">{t('mockTag')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {merged.rows.map(row => (
                      <tr key={row.code} className="border-t border-border/50 hover:bg-accent/30">
                        <td className="p-2.5">{row.code}</td>
                        <td className="p-2.5">{row.name}</td>
                        <td className="p-2.5">
                          {row.sources.map(s => (
                            <span key={s} className="inline-block mr-1 mb-1 text-[11px] px-2 py-0.5 rounded-full bg-success/15 text-success">
                              {s}
                            </span>
                          ))}
                        </td>
                        <td className={`p-2.5 font-semibold ${marketSignTextClass(row.change_pct)}`}>+{row.change_pct}%</td>
                        <td className="p-2.5 text-muted-foreground">{row.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {typeof active === 'number' && detail && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-base font-bold">{detail.label}</h2>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    disabled={saving || running}
                    onClick={() => {
                      setDraftLabel(detail.label)
                      setDraftText(detail.query_text)
                      setDraftType(detail.select_type)
                      resetOperatorDraft()
                    }}
                  >
                    {t('reset')}
                  </Button>
                  <Button variant="secondary" disabled={saving || running} onClick={onSave}>
                    {saving ? '…' : t('save')}
                  </Button>
                  <Button disabled={running} onClick={onRun}>
                    {running ? t('running') : t('run')}
                  </Button>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-[120px_1fr] gap-3">
                <div>
                  <Label className="text-[12px]">{t('selectType')}</Label>
                  <Select value={draftType} onValueChange={setDraftType}>
                    <SelectTrigger className="mt-1 h-9"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {SELECT_TYPES.map(st => (
                        <SelectItem key={st} value={st}>{st}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <Label className="text-[12px]">{t('label')}</Label>
                  <Input className="mt-1 h-9" value={draftLabel} onChange={e => setDraftLabel(e.target.value)} />
                </div>
              </div>
              {screenEngine === 'em' ? (
                <div>
                  <Label className="text-[12px]">{t('queryText')}</Label>
                  <textarea
                    className="mt-1 w-full min-h-[88px] rounded-lg border border-border bg-background px-3 py-2 text-[13px] focus:outline-none focus:ring-2 focus:ring-primary/20"
                    value={draftText}
                    onChange={e => setDraftText(e.target.value)}
                  />
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="text-[12px] rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-900 dark:text-amber-200 px-3 py-2 leading-relaxed">
                    {t('operatorPreviewNote')}
                  </div>
                  <div>
                    <Label className="text-[12px]">{t('planTemplate')}</Label>
                    <Select
                      value={operatorDraft.template}
                      onValueChange={(v: PlanTemplate) => patchOperator({ template: v })}
                    >
                      <SelectTrigger className="mt-1 h-9">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="interval_rank">{t('planTemplates.interval_rank')}</SelectItem>
                        <SelectItem value="intraday_pattern">{t('planTemplates.intraday_pattern')}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <fieldset className="rounded-xl border border-border/60 p-3 space-y-2">
                    <legend className="text-[12px] font-medium px-1">{t('excludeSection')}</legend>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      <ConfigCheck
                        id="ex-st"
                        checked={operatorDraft.excludeSt}
                        onChange={v => patchOperator({ excludeSt: v })}
                        label={t('exclude.st')}
                      />
                      <ConfigCheck
                        id="ex-delist"
                        checked={operatorDraft.excludeDelist}
                        onChange={v => patchOperator({ excludeDelist: v })}
                        label={t('exclude.delist')}
                      />
                      <ConfigCheck
                        id="ex-bjs"
                        checked={operatorDraft.excludeBjs}
                        onChange={v => patchOperator({ excludeBjs: v })}
                        label={t('exclude.bjs')}
                      />
                      <ConfigCheck
                        id="ex-kcb"
                        checked={operatorDraft.excludeKcb}
                        onChange={v => patchOperator({ excludeKcb: v })}
                        label={t('exclude.kcb')}
                      />
                    </div>
                  </fieldset>
                  {operatorDraft.template === 'interval_rank' && (
                    <fieldset className="rounded-xl border border-border/60 p-3 space-y-3">
                      <legend className="text-[12px] font-medium px-1">{t('rankSection')}</legend>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                          <Label className="text-[12px]">{t('rankPeriodStart')}</Label>
                          <Input
                            type="date"
                            className="mt-1 h-9"
                            value={operatorDraft.rankStart}
                            onChange={e => patchOperator({ rankStart: e.target.value })}
                          />
                        </div>
                        <div>
                          <Label className="text-[12px]">{t('rankPeriodEnd')}</Label>
                          <Input
                            type="date"
                            className="mt-1 h-9"
                            value={operatorDraft.rankEnd}
                            onChange={e => patchOperator({ rankEnd: e.target.value })}
                          />
                        </div>
                      </div>
                      <div className="sm:max-w-[160px]">
                        <Label className="text-[12px]">{t('rankTopN')}</Label>
                        <Input
                          className="mt-1 h-9"
                          inputMode="numeric"
                          value={operatorDraft.rankTopN}
                          onChange={e => patchOperator({ rankTopN: e.target.value })}
                        />
                      </div>
                    </fieldset>
                  )}
                  {operatorDraft.template === 'intraday_pattern' && (
                    <fieldset className="rounded-xl border border-border/60 p-3 space-y-3">
                      <legend className="text-[12px] font-medium px-1">{t('patternSection')}</legend>
                      <div className="space-y-2">
                        <ConfigCheck
                          id="pat-prev"
                          checked={operatorDraft.prevDayBearishOrDown}
                          onChange={v => patchOperator({ prevDayBearishOrDown: v })}
                          label={t('prevDayBearishOrDown')}
                        />
                        <ConfigCheck
                          id="pat-today"
                          checked={operatorDraft.todayBullish}
                          onChange={v => patchOperator({ todayBullish: v })}
                          label={t('todayBullish')}
                        />
                        <ConfigCheck
                          id="pat-lu-window"
                          checked={operatorDraft.limitUpWithinDays}
                          onChange={v => patchOperator({ limitUpWithinDays: v })}
                          label={t('limitUpWithinDays')}
                        />
                        <ConfigCheck
                          id="pat-not-lu"
                          checked={operatorDraft.notLimitUpToday}
                          onChange={v => patchOperator({ notLimitUpToday: v })}
                          label={t('notLimitUpToday')}
                        />
                      </div>
                      <div className="sm:max-w-[160px]">
                        <Label className="text-[12px]">{t('tradingDaysN')}</Label>
                        <Input
                          className="mt-1 h-9"
                          inputMode="numeric"
                          value={operatorDraft.tradingDaysN}
                          onChange={e => patchOperator({ tradingDaysN: e.target.value })}
                        />
                      </div>
                    </fieldset>
                  )}
                  <fieldset className="rounded-xl border border-border/60 p-3 space-y-3">
                    <legend className="text-[12px] font-medium px-1">{t('priceSection')}</legend>
                    <p className="text-[11px] text-muted-foreground">{t('priceAsOfRunTime')}</p>
                    <div className="grid grid-cols-2 gap-3 sm:max-w-[280px]">
                      <div>
                        <Label className="text-[12px]">{t('priceMin')}</Label>
                        <Input
                          className="mt-1 h-9"
                          inputMode="decimal"
                          value={operatorDraft.priceMin}
                          onChange={e => patchOperator({ priceMin: e.target.value })}
                        />
                      </div>
                      <div>
                        <Label className="text-[12px]">{t('priceMax')}</Label>
                        <Input
                          className="mt-1 h-9"
                          inputMode="decimal"
                          value={operatorDraft.priceMax}
                          onChange={e => patchOperator({ priceMax: e.target.value })}
                        />
                      </div>
                    </div>
                  </fieldset>
                </div>
              )}

              {screenEngine === 'em' && (
                <section className="rounded-lg border border-border/60 bg-card/40 px-3 py-2.5 space-y-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="min-w-0 space-y-1.5">
                      <h3 className="text-[13px] font-semibold leading-tight">{t('monitorStrategy')}</h3>
                      <p className="text-[11px] text-muted-foreground leading-relaxed">{t('monitorStrategyHint')}</p>
                    </div>
                    <Button
                      size="sm"
                      className="h-8 shrink-0"
                      variant="secondary"
                      disabled={testTriggering || running || saving}
                      onClick={onTestTrigger}
                      title={t('testTriggerHint')}
                    >
                      {testTriggering ? t('testTriggerRunning') : t('testTrigger')}
                    </Button>
                  </div>
                  {!strategyBoundToActive && strategyPayload?.config.query_id != null ? (
                    <p className="text-[11px] text-amber-700 dark:text-amber-300 leading-snug">
                      {t('strategyQueryMismatch')}
                    </p>
                  ) : null}
                  <label className="flex items-center gap-2 text-[12px] text-foreground/90 cursor-pointer w-fit">
                    <input
                      type="checkbox"
                      className="rounded border-border"
                      checked={autoMonitorEnabled}
                      onChange={e => setAutoMonitorEnabled(e.target.checked)}
                    />
                    <span>{t('autoMonitorEnabled')}</span>
                  </label>
                  <div className="flex flex-col xl:flex-row xl:items-end gap-2 xl:gap-3">
                    <div className="flex-1 min-w-0 space-y-1">
                      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                        <span className="font-medium text-foreground/80 shrink-0">{t('monitorWindow')}</span>
                        <span className="hidden sm:inline truncate">{t('monitorTimeHint')}</span>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <Select
                          value={matchingMonitorPreset(monitorStart, monitorEnd) ?? undefined}
                          onValueChange={id => {
                            const preset = MONITOR_WINDOW_PRESETS.find(p => p.id === id)
                            if (preset) applyMonitorWindow(preset.start, preset.end)
                          }}
                        >
                          <SelectTrigger className="h-8 w-[10.25rem] shrink-0 px-2.5 text-[13px] [&>span]:line-clamp-none [&>span]:whitespace-nowrap">
                            <SelectValue placeholder={t('monitorPresetPlaceholder')} />
                          </SelectTrigger>
                          <SelectContent>
                            {MONITOR_WINDOW_PRESETS.map(p => (
                              <SelectItem key={p.id} value={p.id}>
                                {t(`monitorPreset.${p.id}`)}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <Input
                          type="time"
                          step={60}
                          aria-label={t('monitorStart')}
                          className="h-8 w-[6.75rem] tabular-nums shrink-0 text-[13px]"
                          value={monitorStart}
                          onChange={e => applyMonitorWindow(e.target.value, monitorEnd)}
                        />
                        <span className="text-[12px] text-muted-foreground shrink-0">{t('monitorTimeTo')}</span>
                        <Input
                          type="time"
                          step={60}
                          aria-label={t('monitorEnd')}
                          className="h-8 w-[6.75rem] tabular-nums shrink-0 text-[13px]"
                          value={monitorEnd}
                          onChange={e => applyMonitorWindow(monitorStart, e.target.value)}
                        />
                      </div>
                    </div>
                    <div className="flex flex-wrap items-end gap-2 sm:gap-3 shrink-0">
                      <div className="space-y-1 w-[4.5rem]">
                        <Label className="text-[11px] text-muted-foreground font-normal leading-none">
                          {t('gainThreshold')}
                        </Label>
                        <Select value={String(gainPct)} onValueChange={v => setGainPct(Number(v))}>
                          <SelectTrigger className="h-8 text-[13px]">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {GAIN_PCT_OPTIONS.map(p => (
                              <SelectItem key={p} value={String(p)}>
                                {t('gainThresholdOption', { pct: p })}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-1 min-w-[8.5rem] flex-1 sm:flex-none sm:w-[12rem]">
                        <Label className="text-[11px] text-muted-foreground font-normal leading-none">
                          {t('notifyChannel')}
                        </Label>
                        <Select
                          value={notifyChannelId != null ? String(notifyChannelId) : undefined}
                          onValueChange={v => setNotifyChannelId(Number(v))}
                        >
                          <SelectTrigger className="h-8 px-2.5 text-[13px] [&>span]:line-clamp-none [&>span]:whitespace-nowrap">
                            <SelectValue placeholder={t('notifyChannelPlaceholder')} />
                          </SelectTrigger>
                          <SelectContent>
                            {notifyChannels.length === 0 ? (
                              <SelectItem value="__none__" disabled>
                                {t('notifyChannelEmpty')}
                              </SelectItem>
                            ) : (
                              notifyChannels.map(ch => (
                                <SelectItem key={ch.id} value={String(ch.id)} disabled={!ch.enabled}>
                                  {ch.name}
                                  {!ch.enabled ? ` (${t('notifyChannelDisabled')})` : ''}
                                </SelectItem>
                              ))
                            )}
                          </SelectContent>
                        </Select>
                      </div>
                    </div>
                  </div>
                  {notifyChannels.length === 0 ? (
                    <p className="text-[11px] text-muted-foreground leading-snug">{t('notifyChannelEmptyHint')}</p>
                  ) : null}
                  {(strategyPayload?.session || lastScanMessage) && (
                    <div className="flex flex-col gap-1.5 pt-0.5 border-t border-border/40">
                      {strategyPayload?.session ? (
                        <p className="text-[11px] text-muted-foreground leading-snug flex flex-wrap gap-x-2 gap-y-0.5">
                          <span>
                            {t('poolStatus')} ·{' '}
                            {t(
                              `poolStatusLabels.${strategyPayload.session.pool_status}` as 'poolStatusLabels.ready',
                              { defaultValue: strategyPayload.session.pool_status },
                            )}
                          </span>
                          <span>{t('poolCount', { count: strategyPayload.session.pool_count })}</span>
                          {strategyPayload.session.pool_built_at ? (
                            <span>{t('poolBuiltAt', { time: strategyPayload.session.pool_built_at })}</span>
                          ) : null}
                          {strategyPayload.session.last_error ? (
                            <span className="text-destructive">{strategyPayload.session.last_error}</span>
                          ) : null}
                        </p>
                      ) : null}
                      {lastScanMessage ? (
                        <p className="text-[11px] leading-snug text-foreground/90">
                          <span className="font-medium text-muted-foreground">{t('testTriggerSummary')} · </span>
                          {lastScanMessage}
                        </p>
                      ) : null}
                    </div>
                  )}
                </section>
              )}

              {screenEngine === 'em' && (
                <div className="flex gap-1 border-b border-border/60">
                  <button
                    type="button"
                    className={`px-3 py-2 text-[13px] font-medium border-b-2 -mb-px transition-colors ${
                      resultTab === 'screener'
                        ? 'border-primary text-primary'
                        : 'border-transparent text-muted-foreground hover:text-foreground'
                    }`}
                    onClick={() => setResultTab('screener')}
                  >
                    {t('resultTabScreener')}
                  </button>
                  <button
                    type="button"
                    className={`px-3 py-2 text-[13px] font-medium border-b-2 -mb-px transition-colors ${
                      resultTab === 'triggers'
                        ? 'border-primary text-primary'
                        : 'border-transparent text-muted-foreground hover:text-foreground'
                    }`}
                    onClick={() => setResultTab('triggers')}
                  >
                    {t('resultTabTriggers')}
                    {strategyPayload?.triggers?.length ? (
                      <span className="ml-1.5 text-[11px] px-1.5 py-0.5 rounded-full bg-primary/15 text-primary">
                        {strategyPayload.triggers.length}
                      </span>
                    ) : null}
                  </button>
                </div>
              )}

              <div className="text-[12px] text-muted-foreground rounded-lg border border-border/50 bg-accent/30 px-3 py-2">
                {screenEngine === 'operators' ? `${t('engineOperators')} · ${operatorMenuCaption} · ` : ''}
                {screenEngine === 'em' && resultTab === 'triggers' ? (
                  <>
                    {t('lastTestTriggerAt', {
                      time:
                        strategyPayload?.config.last_test_trigger_at ??
                        strategyPayload?.scan?.finished_at ??
                        '—',
                    })}
                  </>
                ) : (
                  <>
                    {t(`status.${detail.status}` as 'status.draft')}
                    {detail.last_run_at
                      ? ` · ${t('lastQueryRunAt', { time: formatTriggerAtDisplay(detail.last_run_at) })}`
                      : ''}
                    {detail.data_source ? ` · ${detail.data_source}` : ''}
                    {detail.last_error ? ` · ${detail.last_error}` : ''}
                  </>
                )}
              </div>

              {screenEngine === 'em' && resultTab === 'triggers' ? (
                (strategyPayload?.triggers?.length || 0) === 0 ? (
                  <div className="text-center text-muted-foreground text-[13px] py-10">{t('noTriggers')}</div>
                ) : (
                  <div className="flex flex-col gap-3 min-w-0">
                    <div className="flex flex-wrap gap-3 text-[12px] text-muted-foreground shrink-0">
                      <span>{t('triggerTotalRows', { count: triggerRows.length })}</span>
                      <span>{t('perPage', { size: PAGE_SIZE })}</span>
                      <span>{t('pageOf', { page: triggerPage, pages: triggerPageCount })}</span>
                    </div>
                    <div className="overflow-x-auto rounded-xl border border-border/60 max-w-full">
                      <table className="w-full text-[13px] min-w-[640px]">
                        <thead>
                          <tr className="bg-muted/50 text-muted-foreground text-[12px]">
                            <th className="text-left p-2.5">{t('triggerColumns.code')}</th>
                            <th className="text-left p-2.5">{t('triggerColumns.name')}</th>
                            <th className="text-left p-2.5">{t('triggerColumns.triggerAt')}</th>
                            <th className="text-left p-2.5">{t('triggerColumns.triggerPrice')}</th>
                            <th className="text-left p-2.5">{t('triggerColumns.prevClose')}</th>
                            <th className="text-left p-2.5">{t('triggerColumns.changePct')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {pagedTriggers.map(tr => {
                            const ch = tr.change_pct ?? 0
                            return (
                              <tr key={`${tr.symbol}-${tr.trigger_at}`} className="border-t border-border/50 hover:bg-accent/30">
                                <td className="p-2.5 font-mono text-[12px]">{tr.symbol}</td>
                                <td className="p-2.5">{tr.name || '—'}</td>
                                <td className="p-2.5 tabular-nums">{formatTriggerAtDisplay(tr.trigger_at)}</td>
                                <td className="p-2.5 tabular-nums">{tr.trigger_price.toFixed(2)}</td>
                                <td className="p-2.5 tabular-nums">{tr.prev_close.toFixed(2)}</td>
                                <td className={`p-2.5 tabular-nums font-semibold ${marketSignTextClass(ch)}`}>
                                  {tr.change_pct != null ? `${tr.change_pct > 0 ? '+' : ''}${tr.change_pct.toFixed(2)}%` : '—'}
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                    <div className="flex shrink-0 items-center justify-between text-[13px] text-muted-foreground pt-1">
                      <span>{t('pageOf', { page: triggerPage, pages: triggerPageCount })}</span>
                      <div className="flex gap-1">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={triggerPage <= 1}
                          onClick={() => setTriggerPage(p => p - 1)}
                        >
                          上一页
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={triggerPage >= triggerPageCount}
                          onClick={() => setTriggerPage(p => p + 1)}
                        >
                          下一页
                        </Button>
                      </div>
                    </div>
                  </div>
                )
              ) : (detail.rows?.length || 0) === 0 ? (
                <div className="text-center text-muted-foreground text-[13px] py-10">{t('noResult')}</div>
              ) : (
                <div className="flex flex-col gap-3 min-w-0">
                  <div className="flex flex-wrap gap-3 text-[12px] text-muted-foreground shrink-0">
                    <span>{t('totalRows', { count: displayRows.length })}</span>
                    <span>{t('perPage', { size: PAGE_SIZE })}</span>
                    <span>{t('pageOf', { page, pages: pageCount })}</span>
                    <span>{t('onlyThisQuery')}</span>
                  </div>
                  <div className="overflow-x-auto rounded-xl border border-border/60 max-w-full">
                    <table className="w-full table-fixed text-[13px]">
                      <colgroup>
                        <col className="w-[20%]" />
                        <col className="w-[28%]" />
                        <col className="w-[22%]" />
                        <col className="w-[22%]" />
                      </colgroup>
                      <thead>
                        <tr className="bg-muted/50 text-muted-foreground text-[12px]">
                          <th className="text-left p-2.5">{t('resultColumns.code')}</th>
                          <th className="text-left p-2.5">{t('resultColumns.name')}</th>
                          <th className="text-left p-2.5">{t('resultColumns.price')}</th>
                          <th className="text-left p-2.5">{t('resultColumns.changePct')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pagedRows.map((row, idx) => {
                          const codeRaw = pickRowField(row, ROW_FIELD_RULES.code)
                          const name = pickRowField(row, ROW_FIELD_RULES.name)
                          const price = pickRowField(row, ROW_FIELD_RULES.price)
                          const changePctRaw = pickRowField(row, ROW_FIELD_RULES.changePct)
                          const changeNum = parseChangePctNum(changePctRaw) ?? 0
                          const changePct = formatChangePctDisplay(changePctRaw)
                          return (
                            <tr key={`${codeRaw}-${idx}`} className="border-t border-border/50 hover:bg-accent/30">
                              <td className="p-2.5 font-mono text-[12px] truncate" title={codeRaw}>
                                {codeRaw}
                              </td>
                              <td className="p-2.5 truncate" title={name}>
                                {name}
                              </td>
                              <td className="p-2.5 tabular-nums truncate">{price}</td>
                              <td
                                className={`p-2.5 tabular-nums truncate font-semibold ${marketSignTextClass(changeNum)}`}
                              >
                                {changePct}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div className="flex shrink-0 items-center justify-between text-[13px] text-muted-foreground pt-1">
                    <span>{t('pageOf', { page, pages: pageCount })}</span>
                    <div className="flex gap-1">
                      <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>
                        上一页
                      </Button>
                      <Button variant="outline" size="sm" disabled={page >= pageCount} onClick={() => setPage(p => p + 1)}>
                        下一页
                      </Button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </main>
      </div>

      {contextOpen && contextRef.current && (
        <div
          className="fixed z-50 min-w-[160px] rounded-xl border border-border/80 bg-card p-1.5 shadow-xl"
          style={{ left: contextPos.x, top: contextPos.y }}
          onClick={e => e.stopPropagation()}
        >
          <button
            type="button"
            className="w-full text-left px-3 py-2 rounded-lg text-[13px] text-destructive hover:bg-destructive/10 disabled:opacity-40"
            disabled={items.length <= 1}
            onClick={() => {
              const ctx = contextRef.current
              setContextOpen(false)
              if (!ctx) return
              const item = items.find(i => i.id === ctx.id)
              onDelete(ctx.id, item?.label || '')
            }}
          >
            {t('deleteQuery')}
          </button>
        </div>
      )}

    </div>
  )
}
