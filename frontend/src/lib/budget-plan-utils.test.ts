import { describe, expect, it } from 'vitest'
import type { BudgetSuggestionRow } from '@/types'
import {
  buildApplyCells,
  cellKey,
  cellVariance,
  isFutureMonth,
  parseAmount,
  percentUsed,
} from './budget-plan-utils'

function row(over: Partial<BudgetSuggestionRow> = {}): BudgetSuggestionRow {
  return {
    category_id: 'cat-1',
    category_name: 'Food',
    category_icon: 'x',
    category_color: '#000000',
    group_id: null,
    group_name: null,
    history_average: 100,
    history_median: 100,
    proposed: Array(12).fill(100),
    existing: Array(12).fill(null),
    ...over,
  }
}

describe('isFutureMonth', () => {
  const now = new Date(2026, 9, 17) // 17 Oct 2026
  it('treats later months of the current year as future', () => {
    expect(isFutureMonth(2026, 11, now)).toBe(true)
    expect(isFutureMonth(2026, 10, now)).toBe(false)
    expect(isFutureMonth(2026, 1, now)).toBe(false)
  })
  it('treats whole later years as future and earlier years as past', () => {
    expect(isFutureMonth(2027, 1, now)).toBe(true)
    expect(isFutureMonth(2025, 12, now)).toBe(false)
  })
})

describe('cellVariance', () => {
  it('is planned minus actual', () => {
    expect(cellVariance({ planned: 500, actual: 380 }, false)).toBe(120)
    expect(cellVariance({ planned: 500, actual: 620 }, false)).toBe(-120)
  })
  it('is null with no budget or for a future month', () => {
    expect(cellVariance({ planned: null, actual: 50 }, false)).toBeNull()
    expect(cellVariance({ planned: 500, actual: 0 }, true)).toBeNull()
  })
  it('keeps a zero budget comparable', () => {
    expect(cellVariance({ planned: 0, actual: 40 }, false)).toBe(-40)
  })
})

describe('percentUsed', () => {
  it('rounds to whole percent and can exceed 100', () => {
    expect(percentUsed(300, 100)).toBe(33)
    expect(percentUsed(100, 250)).toBe(250)
  })
  it('is null without a positive budget', () => {
    expect(percentUsed(null, 10)).toBeNull()
    expect(percentUsed(0, 10)).toBeNull()
  })
})

describe('parseAmount', () => {
  it('parses numbers, including a decimal comma', () => {
    expect(parseAmount('250')).toBe(250)
    expect(parseAmount(' 12,5 ')).toBe(12.5)
    expect(parseAmount('0')).toBe(0)
  })
  it('returns null for empty and undefined for invalid input', () => {
    expect(parseAmount('')).toBeNull()
    expect(parseAmount('   ')).toBeNull()
    expect(parseAmount('abc')).toBeUndefined()
    expect(parseAmount('-5')).toBeUndefined()
  })
})

describe('buildApplyCells', () => {
  it('saves the proposal for every month when nothing is edited', () => {
    const cells = buildApplyCells([row()], {}, false)
    expect(cells).toHaveLength(12)
    expect(cells[0]).toEqual({ category_id: 'cat-1', month: 1, amount: 100 })
  })

  it('uses edited values over the proposal', () => {
    const cells = buildApplyCells([row()], { [cellKey('cat-1', 3)]: '175' }, false)
    expect(cells.find((c) => c.month === 3)?.amount).toBe(175)
    expect(cells.find((c) => c.month === 4)?.amount).toBe(100)
  })

  it('skips months that already have a budget unless overwrite is on', () => {
    const existing = Array(12).fill(null)
    existing[1] = 999
    const r = row({ existing })
    expect(buildApplyCells([r], {}, false).map((c) => c.month)).not.toContain(2)
    expect(buildApplyCells([r], {}, true).map((c) => c.month)).toContain(2)
  })

  it('skips cells the user emptied or typed garbage into, instead of saving zero', () => {
    const cells = buildApplyCells(
      [row()],
      { [cellKey('cat-1', 1)]: '', [cellKey('cat-1', 2)]: 'oops', [cellKey('cat-1', 3)]: '0' },
      false,
    )
    const months = cells.map((c) => c.month)
    expect(months).not.toContain(1)
    expect(months).not.toContain(2)
    expect(cells.find((c) => c.month === 3)?.amount).toBe(0) // an explicit zero is kept
  })
})
