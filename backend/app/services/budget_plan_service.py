"""Annual budget planning: planned vs executed per category and month.

Everything here builds on ``budget_service.get_budget_vs_actual`` instead of
re-implementing spending aggregation, so shared splits, FX conversion, the
credit-card accounting mode and transfer exclusion behave exactly as they do
on the Budgets page.
"""

import statistics
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.budget import Budget
from app.models.category import Category
from app.schemas.budget import (
    BudgetSuggestionRow,
    BudgetVsActual,
    BudgetYearApply,
    BudgetYearApplyResult,
    BudgetYearCell,
    BudgetYearPlan,
    BudgetYearRow,
    BudgetYearSuggestion,
)
from app.services.budget_service import _build_budget_map, get_budget_vs_actual

Method = Literal["median", "average", "seasonal"]

ZERO = Decimal("0")
# Weight given to the same calendar month of the history window when
# method == "seasonal"; the rest comes from the flat baseline.
SEASONAL_WEIGHT = Decimal("0.5")


class PlanError(ValueError):
    """Raised for requests the plan endpoints can't honour (maps to HTTP 400)."""


def _month_starts(year: int) -> list[date]:
    return [date(year, m, 1) for m in range(1, 13)]


def _shift_month(d: date, delta: int) -> date:
    index = d.year * 12 + (d.month - 1) + delta
    return date(index // 12, index % 12 + 1, 1)


def history_months(today: date, lookback: int) -> list[date]:
    """The ``lookback`` complete months before ``today``'s month, oldest first."""
    current = today.replace(day=1)
    return [_shift_month(current, -(lookback - i)) for i in range(lookback)]


def _whole(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def propose_amounts(
    history: list[tuple[date, Decimal]],
    method: Method,
) -> tuple[Decimal, Decimal, list[Decimal]]:
    """Turn one category's monthly history into 12 proposed monthly amounts.

    ``history`` is (month start, net spend) for every month in the window,
    zeros included. Returns (average, median, proposals Jan..Dec).

    Baseline is the median, which shrugs off one-off spikes (a single
    expensive month won't inflate the plan). Intermittent categories, with
    no spending in at least a third of the months, use the average instead:
    their median sits near zero and would budget far less than they really
    cost over a year. ``seasonal`` blends the baseline with what the same
    calendar month cost in the window.
    """
    values = [v for _, v in history]
    average = sum(values, ZERO) / len(values) if values else ZERO
    median = Decimal(str(statistics.median(values))) if values else ZERO
    idle_months = sum(1 for v in values if v <= 0)
    intermittent = idle_months * 3 >= len(values)

    baseline = average if method == "average" or intermittent else median

    by_calendar_month = {d.month: v for d, v in history}
    proposals: list[Decimal] = []
    for month in range(1, 13):
        amount = baseline
        if method == "seasonal" and month in by_calendar_month:
            amount = baseline * (1 - SEASONAL_WEIGHT) + by_calendar_month[month] * SEASONAL_WEIGHT
        proposals.append(max(_whole(amount), ZERO))
    return _whole(average), _whole(median), proposals


async def _monthly_comparisons(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    months: list[date],
) -> list[dict[uuid.UUID, BudgetVsActual]]:
    out = []
    for month in months:
        rows = await get_budget_vs_actual(session, workspace_id, user_id, month)
        out.append({r.category_id: r for r in rows})
    return out


async def get_year_plan(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    year: Optional[int] = None,
) -> BudgetYearPlan:
    year = year or app_today().year
    per_month = await _monthly_comparisons(session, workspace_id, user_id, _month_starts(year))

    info: dict[uuid.UUID, BudgetVsActual] = {}
    planned: dict[uuid.UUID, list[Optional[Decimal]]] = {}
    actual: dict[uuid.UUID, list[Decimal]] = {}
    for idx, rows in enumerate(per_month):
        for cat_id, row in rows.items():
            info.setdefault(cat_id, row)
            planned.setdefault(cat_id, [None] * 12)[idx] = row.budget_amount
            actual.setdefault(cat_id, [ZERO] * 12)[idx] = row.actual_amount

    rows_out: list[BudgetYearRow] = []
    for cat_id, row in info.items():
        p = planned[cat_id]
        a = actual[cat_id]
        rows_out.append(
            BudgetYearRow(
                category_id=cat_id,
                category_name=row.category_name,
                category_icon=row.category_icon,
                category_color=row.category_color,
                group_id=row.group_id,
                group_name=row.group_name,
                cells=[BudgetYearCell(month=i + 1, planned=p[i], actual=a[i]) for i in range(12)],
                planned_total=sum((x for x in p if x is not None), ZERO),
                actual_total=sum(a, ZERO),
            )
        )
    rows_out.sort(key=lambda r: (r.actual_total, r.planned_total), reverse=True)

    planned_by_month = [
        sum((r.cells[i].planned or ZERO for r in rows_out), ZERO) for i in range(12)
    ]
    actual_by_month = [sum((r.cells[i].actual for r in rows_out), ZERO) for i in range(12)]
    return BudgetYearPlan(
        year=year,
        rows=rows_out,
        planned_by_month=planned_by_month,
        actual_by_month=actual_by_month,
        planned_total=sum(planned_by_month, ZERO),
        actual_total=sum(actual_by_month, ZERO),
    )


async def suggest_year_budget(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    year: Optional[int] = None,
    lookback_months: int = 12,
    method: Method = "median",
) -> BudgetYearSuggestion:
    """Propose a monthly budget per category for ``year`` from recent history.

    Nothing is written; the caller reviews the proposal and applies it via
    ``apply_year_plan``.
    """
    year = year or app_today().year
    window = history_months(app_today(), lookback_months)
    history_cmp = await _monthly_comparisons(session, workspace_id, user_id, window)

    info: dict[uuid.UUID, BudgetVsActual] = {}
    for rows in history_cmp:
        for cat_id, row in rows.items():
            info.setdefault(cat_id, row)

    # Budgets already in effect, so the UI can show what a proposal replaces.
    existing_maps = [await _build_budget_map(session, workspace_id, m) for m in _month_starts(year)]

    rows_out: list[BudgetSuggestionRow] = []
    for cat_id, row in info.items():
        history = [
            (month, cmp[cat_id].actual_amount if cat_id in cmp else ZERO)
            for month, cmp in zip(window, history_cmp)
        ]
        if not any(v > 0 for _, v in history):
            continue  # only a budget or a projection, never spent: nothing to learn from
        average, median, proposed = propose_amounts(history, method)
        existing: list[Optional[Decimal]] = []
        for m in existing_maps:
            entry = m.get(str(cat_id))
            existing.append(entry[0] if entry else None)
        rows_out.append(
            BudgetSuggestionRow(
                category_id=cat_id,
                category_name=row.category_name,
                category_icon=row.category_icon,
                category_color=row.category_color,
                group_id=row.group_id,
                group_name=row.group_name,
                history_average=average,
                history_median=median,
                proposed=proposed,
                existing=existing,
            )
        )
    rows_out.sort(key=lambda r: r.history_average, reverse=True)

    return BudgetYearSuggestion(
        year=year,
        method=method,
        lookback_months=lookback_months,
        history_start=window[0],
        history_end=window[-1],
        rows=rows_out,
    )


async def apply_year_plan(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: BudgetYearApply,
) -> BudgetYearApplyResult:
    """Upsert month-specific budgets in bulk.

    Writes non-recurring rows, which take priority over any recurring default
    for that month. ``amount=None`` deletes the month-specific row. If a cell
    appears twice the last one wins.
    """
    wanted: dict[tuple[uuid.UUID, date], Optional[Decimal]] = {}
    for cell in data.cells:
        wanted[(cell.category_id, date(data.year, cell.month, 1))] = cell.amount
    if not wanted:
        return BudgetYearApplyResult(saved=0, deleted=0)

    category_ids = {cat_id for cat_id, _ in wanted}
    found = await session.execute(
        select(Category.id).where(
            Category.workspace_id == workspace_id, Category.id.in_(category_ids)
        )
    )
    if category_ids - set(found.scalars().all()):
        raise PlanError("Unknown category in this workspace")

    existing_result = await session.execute(
        select(Budget).where(
            Budget.workspace_id == workspace_id,
            Budget.is_recurring == False,  # noqa: E712
            Budget.category_id.in_(category_ids),
            Budget.month >= date(data.year, 1, 1),
            Budget.month <= date(data.year, 12, 1),
        )
    )
    existing = {(b.category_id, b.month): b for b in existing_result.scalars().all()}

    saved = deleted = 0
    for key, amount in wanted.items():
        current = existing.get(key)
        if amount is None:
            if current is not None:
                await session.delete(current)
                deleted += 1
        elif current is not None:
            current.amount = amount
            saved += 1
        else:
            session.add(
                Budget(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    category_id=key[0],
                    amount=amount,
                    month=key[1],
                    is_recurring=False,
                )
            )
            saved += 1
    await session.commit()
    return BudgetYearApplyResult(saved=saved, deleted=deleted)
