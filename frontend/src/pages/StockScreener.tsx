import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { screenerApi, type ScreenerQuery, type ScreenerMergedMock } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useTranslation } from 'react-i18next'
import { marketSignTextClass } from '@/lib/market-colors'

const PAGE_SIZE = 10
const SELECT_TYPES = ['A股', '港股', '美股', '基金', 'ETF', '可转债', '板块'] as const

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

function rowCodeKey(row: Record<string, string>): string {
  const raw = pickRowField(row, ROW_FIELD_RULES.code)
  if (raw === '—') return ''
  if (/^\d+$/.test(raw)) return raw.padStart(6, '0')
  return raw
}

function formatIntradayScore(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return value.toFixed(1)
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

function badgeLabel(status: string, t: (k: string) => string, rowCount: number) {
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
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [computingScores, setComputingScores] = useState(false)
  const [selectedCodes, setSelectedCodes] = useState<string[]>([])
  const [draftLabel, setDraftLabel] = useState('')
  const [draftText, setDraftText] = useState('')
  const [draftType, setDraftType] = useState<string>('A股')
  const [screenEngine, setScreenEngine] = useState<ScreenEngine>('em')
  const [operatorDraft, setOperatorDraft] = useState<OperatorDraft>(DEFAULT_OPERATOR_DRAFT)
  const contextRef = useRef<{ id: number; x: number; y: number } | null>(null)
  const [contextOpen, setContextOpen] = useState(false)
  const [contextPos, setContextPos] = useState({ x: 0, y: 0 })

  const loadList = useCallback(async () => {
    const res = await screenerApi.listQueries()
    setItems(res.items || [])
    return res.items || []
  }, [])

  const loadMerged = useCallback(async () => {
    const data = await screenerApi.getMergedMock()
    setMerged(data)
  }, [])

  const loadDetail = useCallback(async (id: number) => {
    const data = await screenerApi.getQuery(id)
    setDetail(data)
    setDraftLabel(data.label)
    setDraftText(data.query_text)
    setDraftType(data.select_type)
    setPage(1)
    setSelectedCodes([])
    return data
  }, [])

  useEffect(() => {
    ;(async () => {
      try {
        setLoading(true)
        const list = await loadList()
        await loadMerged()
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
  }, [loadDetail, loadList, loadMerged, t, toast])

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
    setSaving(true)
    try {
      const updated = await screenerApi.updateQuery(active, {
        label: draftLabel,
        query_text: screenEngine === 'em' ? draftText : detail.query_text,
        select_type: draftType,
      })
      setDetail(updated)
      setItems(prev => prev.map(it => (it.id === updated.id ? { ...it, ...updated, rows: undefined, columns: undefined } : it)))
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

  const scoreEntries = useMemo(
    () => detail?.intraday_scores?.scores ?? {},
    [detail?.intraday_scores?.scores],
  )

  const lookupScore = useCallback(
    (code: string) => {
      if (!code) return { score: null as number | null, error: null as string | null }
      const entry = scoreEntries[code]
      return {
        score: entry?.score ?? null,
        error: entry?.error ?? null,
      }
    },
    [scoreEntries],
  )

  const displayRows = useMemo(() => detail?.rows || [], [detail?.rows])

  const pagedRows = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return displayRows.slice(start, start + PAGE_SIZE)
  }, [displayRows, page])

  const pageCount = Math.max(1, Math.ceil(displayRows.length / PAGE_SIZE))

  const cnQuery = (detail?.select_type || draftType) === 'A股'

  const toggleRowSelected = (code: string) => {
    if (!code) return
    setSelectedCodes(prev => (prev.includes(code) ? prev.filter(c => c !== code) : [...prev, code]))
  }

  const allPagedSelected =
    pagedRows.length > 0 &&
    pagedRows.every(row => {
      const code = rowCodeKey(row)
      return code && selectedCodes.includes(code)
    })

  const togglePageSelection = () => {
    const codes = pagedRows.map(rowCodeKey).filter(Boolean)
    if (codes.length === 0) return
    if (allPagedSelected) {
      setSelectedCodes(prev => prev.filter(c => !codes.includes(c)))
    } else {
      setSelectedCodes(prev => [...new Set([...prev, ...codes])])
    }
  }

  const applyIntradayCompute = (
    updated: ScreenerQuery,
    scope: 'all' | 'selected',
    selected: string[],
  ) => {
    setDetail(updated)
    setPage(1)
    setSelectedCodes([])
    const meta = updated.intraday_compute
    const errList = meta?.errors ?? []
    const computedCount = meta?.computed_count ?? 0
    const failed = errList.length
    const ok = Math.max(0, computedCount - failed)
    if (failed > 0) {
      const detailText = errList
        .slice(0, 2)
        .map(e => `${e.symbol}(${e.test_date}): ${e.message}`)
        .join('；')
      toast(`${t('computePartial', { ok, failed })} ${detailText}`, 'error')
    } else {
      toast(
        scope === 'selected'
          ? t('computeSelectedDone', { count: computedCount })
          : t('computeDone', { count: computedCount }),
        'success',
      )
    }
  }

  const onComputeAllScores = async () => {
    if (typeof active !== 'number' || !cnQuery) {
      toast(t('scoreCnOnly'), 'info')
      return
    }
    if ((detail?.rows?.length || 0) === 0) return
    setComputingScores(true)
    try {
      applyIntradayCompute(await screenerApi.computeIntradayScores(active), 'all', [])
    } catch (e) {
      toast(e instanceof Error ? e.message : t('computeFailed'), 'error')
    } finally {
      setComputingScores(false)
    }
  }

  const onBatchComputeScores = async () => {
    if (typeof active !== 'number' || !cnQuery) {
      toast(t('scoreCnOnly'), 'info')
      return
    }
    if (selectedCodes.length === 0) return
    setComputingScores(true)
    try {
      applyIntradayCompute(
        await screenerApi.computeIntradayScores(active, selectedCodes),
        'selected',
        selectedCodes,
      )
    } catch (e) {
      toast(e instanceof Error ? e.message : t('computeFailed'), 'error')
    } finally {
      setComputingScores(false)
    }
  }

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
                  {badgeLabel(it.status, t, it.row_count)}
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

              <div className="text-[12px] text-muted-foreground rounded-lg border border-border/50 bg-accent/30 px-3 py-2">
                {screenEngine === 'operators' ? `${t('engineOperators')} · ${operatorMenuCaption} · ` : ''}
                {t(`status.${detail.status}` as 'status.draft')}
                {detail.last_run_at ? ` · ${detail.last_run_at}` : ''}
                {detail.data_source ? ` · ${detail.data_source}` : ''}
                {detail.last_error ? ` · ${detail.last_error}` : ''}
              </div>

              {(detail.rows?.length || 0) === 0 ? (
                <div className="text-center text-muted-foreground text-[13px] py-10">{t('noResult')}</div>
              ) : (
                <div className="flex flex-col gap-3 min-w-0">
                  <p className="text-[11px] text-muted-foreground leading-relaxed">{t('algorithmNote')}</p>
                  <div className="flex flex-wrap items-center gap-2 shrink-0">
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={computingScores || running || !cnQuery}
                      onClick={onComputeAllScores}
                    >
                      {computingScores ? t('computingScores') : t('computeAll')}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={computingScores || running || !cnQuery || selectedCodes.length === 0}
                      onClick={onBatchComputeScores}
                    >
                      {computingScores ? t('computingScores') : t('batchCompute', { count: selectedCodes.length })}
                    </Button>
                  </div>
                  <div className="flex flex-wrap gap-3 text-[12px] text-muted-foreground shrink-0">
                    <span>{t('totalRows', { count: displayRows.length })}</span>
                    <span>{t('perPage', { size: PAGE_SIZE })}</span>
                    <span>{t('pageOf', { page, pages: pageCount })}</span>
                    <span>{t('onlyThisQuery')}</span>
                    {detail.intraday_scores?.sorted_by_score ? (
                      <span className="text-primary">{t('scoreColumn')} ↓</span>
                    ) : null}
                  </div>
                  <div className="overflow-x-auto rounded-xl border border-border/60 max-w-full">
                    <table className="w-full table-fixed text-[13px]">
                      <colgroup>
                        <col className="w-[36px]" />
                        <col className="w-[16%]" />
                        <col className="w-[18%]" />
                        <col className="w-[16%]" />
                        <col className="w-[16%]" />
                        <col className="w-[16%]" />
                      </colgroup>
                      <thead>
                        <tr className="bg-muted/50 text-muted-foreground text-[12px]">
                          <th className="p-2.5 w-[36px]">
                            <input
                              type="checkbox"
                              className="h-4 w-4 rounded border-border"
                              checked={allPagedSelected}
                              onChange={togglePageSelection}
                              aria-label={t('batchCompute', { count: selectedCodes.length })}
                            />
                          </th>
                          <th className="text-left p-2.5">{t('resultColumns.code')}</th>
                          <th className="text-left p-2.5">{t('resultColumns.name')}</th>
                          <th className="text-left p-2.5">{t('resultColumns.price')}</th>
                          <th className="text-left p-2.5">{t('resultColumns.changePct')}</th>
                          <th className="text-left p-2.5">{t('scoreColumn')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pagedRows.map((row, idx) => {
                          const codeRaw = pickRowField(row, ROW_FIELD_RULES.code)
                          const codeKey = rowCodeKey(row)
                          const name = pickRowField(row, ROW_FIELD_RULES.name)
                          const price = pickRowField(row, ROW_FIELD_RULES.price)
                          const changePctRaw = pickRowField(row, ROW_FIELD_RULES.changePct)
                          const changeNum = parseChangePctNum(changePctRaw) ?? 0
                          const changePct = formatChangePctDisplay(changePctRaw)
                          const { score, error } = lookupScore(codeKey)
                          return (
                            <tr key={`${codeKey || idx}-${idx}`} className="border-t border-border/50 hover:bg-accent/30">
                              <td className="p-2.5 w-[36px]">
                                <input
                                  type="checkbox"
                                  className="h-4 w-4 rounded border-border"
                                  checked={codeKey ? selectedCodes.includes(codeKey) : false}
                                  disabled={!codeKey}
                                  onChange={() => toggleRowSelected(codeKey)}
                                />
                              </td>
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
                              <td
                                className="p-2.5 tabular-nums font-semibold truncate"
                                title={error || undefined}
                              >
                                {formatIntradayScore(score)}
                                {error ? <span className="ml-1 text-[10px] font-normal text-destructive">!</span> : null}
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
