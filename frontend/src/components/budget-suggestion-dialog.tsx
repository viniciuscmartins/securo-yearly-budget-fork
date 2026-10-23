import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { budgets as budgetsApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { monthLabel } from '@/lib/month-utils'
import { MONTH_NUMBERS, buildApplyCells, cellKey } from '@/lib/budget-plan-utils'
import type { BudgetSuggestionMethod } from '@/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { CategoryIcon } from '@/components/category-icon'

const SELECT_CLASS =
  'border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

const LOOKBACK_OPTIONS = [6, 12, 24] as const
const METHODS: BudgetSuggestionMethod[] = ['median', 'average', 'seasonal']
const METHOD_LABEL_KEY: Record<BudgetSuggestionMethod, string> = {
  median: 'budgetPlan.methodMedian',
  average: 'budgetPlan.methodAverage',
  seasonal: 'budgetPlan.methodSeasonal',
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  year: number
  /** Formats a whole-unit amount (already privacy-masked). */
  fmt: (amount: number) => string
  /** Short month names, January first. */
  monthNames: string[]
}

export function BudgetSuggestionDialog({ open, onOpenChange, year, fmt, monthNames }: Props) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const uiLocale = i18n.resolvedLanguage ?? i18n.language

  const [lookback, setLookback] = useState<number>(12)
  const [method, setMethod] = useState<BudgetSuggestionMethod>('median')
  const [overwrite, setOverwrite] = useState(false)
  // Only what the user typed over the proposal; keyed by category + month.
  const [edits, setEdits] = useState<Record<string, string>>({})

  const { data, isLoading, isError } = useQuery({
    queryKey: ['budget-year-suggestion', year, lookback, method],
    queryFn: () => budgetsApi.yearSuggestion(year, lookback, method),
    enabled: open,
  })

  const cells = useMemo(
    () => (data ? buildApplyCells(data.rows, edits, overwrite) : []),
    [data, edits, overwrite],
  )

  const applyMutation = useMutation({
    mutationFn: () => budgetsApi.applyYearPlan(year, cells),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['budget-year-plan'] })
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      toast.success(t('budgetPlan.applied'))
      setEdits({})
      onOpenChange(false)
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })

  // A different window or method means different proposals, so earlier edits
  // would no longer line up with what's on screen.
  const changeLookback = (value: number) => {
    setLookback(value)
    setEdits({})
  }
  const changeMethod = (value: BudgetSuggestionMethod) => {
    setMethod(value)
    setEdits({})
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-7xl" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>{t('budgetPlan.suggestTitle', { year })}</DialogTitle>
        </DialogHeader>

        <div className="flex flex-wrap items-end gap-4">
          <label className="space-y-1 text-xs font-medium text-muted-foreground">
            <span>{t('budgetPlan.history')}</span>
            <select
              className={`${SELECT_CLASS} block`}
              value={lookback}
              onChange={(e) => changeLookback(Number(e.target.value))}
            >
              {LOOKBACK_OPTIONS.map((months) => (
                <option key={months} value={months}>
                  {t('budgetPlan.lookbackMonths', { months })}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-xs font-medium text-muted-foreground">
            <span>{t('budgetPlan.method')}</span>
            <select
              className={`${SELECT_CLASS} block`}
              value={method}
              onChange={(e) => changeMethod(e.target.value as BudgetSuggestionMethod)}
            >
              {METHODS.map((m) => (
                <option key={m} value={m}>
                  {t(METHOD_LABEL_KEY[m])}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 pb-2 cursor-pointer">
            <input
              type="checkbox"
              className="rounded border-border"
              checked={overwrite}
              onChange={(e) => setOverwrite(e.target.checked)}
            />
            <span className="text-sm text-foreground">{t('budgetPlan.overwriteExisting')}</span>
          </label>
        </div>

        {data && data.rows.length > 0 && (
          <p className="text-xs text-muted-foreground">
            {t('budgetPlan.suggestIntro', {
              from: monthLabel(data.history_start.slice(0, 7), uiLocale),
              to: monthLabel(data.history_end.slice(0, 7), uiLocale),
            })}
          </p>
        )}

        <div className="max-h-[55vh] overflow-auto rounded-lg border border-border">
          {isLoading ? (
            <div className="space-y-2 p-4">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-8 w-full" />
              ))}
            </div>
          ) : isError ? (
            <p className="py-10 text-center text-sm text-muted-foreground">{t('common.error')}</p>
          ) : !data || data.rows.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">{t('budgetPlan.noHistory')}</p>
          ) : (
            <table className="w-full text-xs">
              <thead className="sticky top-0 z-10 bg-card">
                <tr className="border-b border-border">
                  <th className="sticky left-0 z-10 bg-card py-2 pl-3 pr-2 text-left font-medium text-muted-foreground">
                    {t('budgets.category')}
                  </th>
                  {MONTH_NUMBERS.map((m) => (
                    <th key={m} className="px-1 py-2 text-center font-medium capitalize text-muted-foreground">
                      {monthNames[m - 1]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.category_id} className="border-b border-border last:border-0">
                    <td className="sticky left-0 z-[1] bg-card py-1.5 pl-3 pr-2">
                      <span className="flex min-w-[9rem] items-center gap-2 text-sm font-medium text-foreground">
                        <CategoryIcon icon={row.category_icon} color={row.category_color} size="sm" />
                        <span className="truncate">{row.category_name}</span>
                      </span>
                    </td>
                    {MONTH_NUMBERS.map((m) => {
                      const i = m - 1
                      const existing = row.existing[i]
                      const key = cellKey(row.category_id, m)
                      if (existing != null && !overwrite) {
                        return (
                          <td
                            key={m}
                            className="px-1 py-1.5 text-center tabular-nums text-muted-foreground"
                            title={t('budgetPlan.existingKept', { amount: fmt(existing) })}
                          >
                            {fmt(existing)}
                          </td>
                        )
                      }
                      return (
                        <td key={m} className="px-1 py-1.5">
                          <Input
                            type="number"
                            min={0}
                            step={1}
                            inputMode="decimal"
                            aria-label={t('budgetPlan.cellLabel', {
                              category: row.category_name,
                              month: monthNames[i],
                            })}
                            title={
                              existing != null
                                ? t('budgetPlan.existingReplaced', { amount: fmt(existing) })
                                : undefined
                            }
                            className="h-8 w-[4.5rem] px-1.5 text-right text-xs tabular-nums"
                            value={key in edits ? edits[key] : String(row.proposed[i])}
                            onChange={(e) => setEdits((prev) => ({ ...prev, [key]: e.target.value }))}
                          />
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <p className="text-xs text-muted-foreground">{t('budgetPlan.skipHint')}</p>

        <DialogFooter className="items-center sm:justify-between">
          <span className="text-sm text-muted-foreground tabular-nums">
            {t('budgetPlan.entriesToSave', { total: cells.length })}
          </span>
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              type="button"
              disabled={cells.length === 0 || applyMutation.isPending}
              onClick={() => applyMutation.mutate()}
            >
              {t('budgetPlan.apply')}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
