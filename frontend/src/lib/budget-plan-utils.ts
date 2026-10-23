import type { BudgetPlanCellWrite, BudgetSuggestionRow, BudgetYearCell } from '@/types'

export const MONTH_NUMBERS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] as const

/** True when `month` (1-12) of `year` hasn't started yet, so no spending can exist for it. */
export function isFutureMonth(year: number, month: number, now: Date = new Date()): boolean {
  const nowYear = now.getFullYear()
  return year > nowYear || (year === nowYear && month > now.getMonth() + 1)
}

/**
 * Planned minus actual for one cell. Positive = under plan (money left),
 * negative = over plan. Null when there is nothing meaningful to compare:
 * no budget was set, or the month hasn't happened yet.
 */
export function cellVariance(cell: Pick<BudgetYearCell, 'planned' | 'actual'>, future: boolean): number | null {
  if (cell.planned == null || future) return null
  return cell.planned - cell.actual
}

/** Share of the plan already spent, as a whole percent. Null without a positive budget. */
export function percentUsed(planned: number | null, actual: number): number | null {
  if (planned == null || planned <= 0) return null
  return Math.round((actual / planned) * 100)
}

/** Key for one (category, month) cell in the suggestion editor's edit map. */
export function cellKey(categoryId: string, month: number): string {
  return `${categoryId}:${month}`
}

/**
 * Parse what the user typed into a budget cell.
 * '' means "no amount" (null); anything that isn't a finite number >= 0 is
 * invalid (undefined) so callers can tell "cleared" from "garbage".
 */
export function parseAmount(raw: string): number | null | undefined {
  const trimmed = raw.trim()
  if (trimmed === '') return null
  const value = Number(trimmed.replace(',', '.'))
  if (!Number.isFinite(value) || value < 0) return undefined
  return value
}

/**
 * Turn the reviewed suggestion into the cells to save.
 *
 * `edits` holds only what the user changed; everything else is the proposal.
 * Months that already have a budget are left alone unless `overwrite` is on.
 * Empty or invalid cells are skipped rather than saved as zero.
 */
export function buildApplyCells(
  rows: BudgetSuggestionRow[],
  edits: Record<string, string>,
  overwrite: boolean,
): BudgetPlanCellWrite[] {
  const cells: BudgetPlanCellWrite[] = []
  for (const row of rows) {
    for (const month of MONTH_NUMBERS) {
      const i = month - 1
      if (row.existing[i] != null && !overwrite) continue
      const key = cellKey(row.category_id, month)
      const amount = key in edits ? parseAmount(edits[key]) : row.proposed[i]
      if (amount == null) continue
      cells.push({ category_id: row.category_id, month, amount })
    }
  }
  return cells
}
