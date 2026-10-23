import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.budget import Budget
from app.models.transaction import Transaction
from app.schemas.budget import BudgetCreate, BudgetPlanCellWrite, BudgetYearApply
from app.services.budget_plan_service import (
    PlanError,
    apply_year_plan,
    get_year_plan,
    history_months,
    propose_amounts,
    suggest_year_budget,
)
from app.services.budget_service import create_budget


def _m(year: int, month: int) -> date:
    return date(year, month, 1)


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_history_months_are_complete_months_before_today():
    assert history_months(date(2026, 10, 17), 3) == [
        date(2026, 7, 1),
        date(2026, 8, 1),
        date(2026, 9, 1),
    ]


def test_history_months_cross_year_boundary():
    assert history_months(date(2026, 2, 5), 4) == [
        date(2025, 10, 1),
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
    ]


def test_median_ignores_a_single_spike():
    history = [(_m(2025, m), Decimal("100")) for m in range(1, 12)]
    history.append((_m(2025, 12), Decimal("1200")))
    average, median, proposed = propose_amounts(history, "median")
    assert median == Decimal("100")
    assert average == Decimal("192")  # 2300 / 12 = 191.67, dragged up by the spike
    assert proposed == [Decimal("100")] * 12


def test_average_method_uses_the_mean():
    history = [(_m(2025, m), Decimal("100")) for m in range(1, 12)]
    history.append((_m(2025, 12), Decimal("1200")))
    _, _, proposed = propose_amounts(history, "average")
    assert proposed == [Decimal("192")] * 12


def test_lumpy_category_falls_back_to_average_not_zero():
    # One 1200 payment in twelve months: the median is 0, but the category
    # clearly costs something, so the proposal must not be zero.
    history = [(_m(2025, m), Decimal("0")) for m in range(1, 13)]
    history[3] = (_m(2025, 4), Decimal("1200"))
    _, median, proposed = propose_amounts(history, "median")
    assert median == Decimal("0")
    assert proposed == [Decimal("100")] * 12


def test_intermittent_category_uses_average_even_when_median_is_not_zero():
    # Spent in only 5 of 12 months: the median (45) would budget a quarter of
    # what the category really costs (~146/month).
    amounts = ["0", "0", "350", "0", "120", "0", "980", "0", "210", "90", "0", "0"]
    history = [(_m(2025, i + 1), Decimal(a)) for i, a in enumerate(amounts)]
    average, median, proposed = propose_amounts(history, "median")
    assert median == Decimal("0")
    assert average == Decimal("146")  # 1750 / 12
    assert proposed == [Decimal("146")] * 12


def test_an_occasional_quiet_month_keeps_the_median():
    history = [(_m(2025, 1), Decimal("0"))] + [(_m(2025, m), Decimal("100")) for m in range(2, 13)]
    _, median, proposed = propose_amounts(history, "median")
    assert median == Decimal("100")
    assert proposed == [Decimal("100")] * 12


def test_seasonal_blends_same_month_with_baseline():
    history = [(_m(2025, m), Decimal("100")) for m in range(1, 13)]
    history[11] = (_m(2025, 12), Decimal("300"))  # December is expensive
    _, _, proposed = propose_amounts(history, "seasonal")
    assert proposed[11] == Decimal("200")  # 0.5 * 100 + 0.5 * 300
    assert proposed[0] == Decimal("100")


def test_seasonal_with_short_history_uses_baseline_for_unseen_months():
    history = [(_m(2026, m), Decimal("50")) for m in range(7, 10)]  # Jul-Sep only
    _, _, proposed = propose_amounts(history, "seasonal")
    assert proposed == [Decimal("50")] * 12


# ---------------------------------------------------------------------------
# DB-backed behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
async def account(session: AsyncSession, test_user) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="PlanAcc",
        type="checking",
        balance=Decimal("5000"),
        currency="BRL",
    )
    session.add(acc)
    await session.commit()
    return acc


async def _spend(session, test_user, account, category, on: date, amount: str, kind="debit"):
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=account.id,
            category_id=category.id,
            description="x",
            amount=Decimal(amount),
            date=on,
            type=kind,
            source="manual",
            created_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_year_plan_shows_planned_and_actual_per_month(
    session, test_user, test_workspace, test_categories, account
):
    food = test_categories[0]
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(category_id=food.id, amount=Decimal("500"), month=_m(2024, 3)),
    )
    await _spend(session, test_user, account, food, date(2024, 3, 10), "120")
    await _spend(session, test_user, account, food, date(2024, 3, 20), "30")
    await _spend(session, test_user, account, food, date(2024, 5, 2), "75")

    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)

    assert plan.year == 2024
    row = next(r for r in plan.rows if r.category_id == food.id)
    assert [c.month for c in row.cells] == list(range(1, 13))
    march, may = row.cells[2], row.cells[4]
    assert march.planned == Decimal("500")
    assert march.actual == Decimal("150")
    assert may.planned is None
    assert may.actual == Decimal("75")
    assert row.actual_total == Decimal("225")
    assert row.planned_total == Decimal("500")
    assert plan.planned_by_month[2] == Decimal("500")
    assert plan.actual_by_month[2] == Decimal("150")
    assert plan.actual_total == Decimal("225")


@pytest.mark.asyncio
async def test_year_plan_refund_nets_against_spend(
    session, test_user, test_workspace, test_categories, account
):
    food = test_categories[0]
    await _spend(session, test_user, account, food, date(2024, 6, 3), "200")
    await _spend(session, test_user, account, food, date(2024, 6, 9), "40", kind="credit")
    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)
    row = next(r for r in plan.rows if r.category_id == food.id)
    assert row.cells[5].actual == Decimal("160")


@pytest.mark.asyncio
async def test_year_plan_recurring_budget_applies_from_effective_month(
    session, test_user, test_workspace, test_categories
):
    cat = test_categories[1]
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=cat.id, amount=Decimal("300"), month=_m(2024, 6), is_recurring=True
        ),
    )
    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)
    row = next(r for r in plan.rows if r.category_id == cat.id)
    planned = [c.planned for c in row.cells]
    assert planned[:5] == [None] * 5
    assert planned[5:] == [Decimal("300")] * 7


@pytest.mark.asyncio
async def test_suggestion_is_based_on_history_and_skips_unspent_categories(
    session, test_user, test_workspace, test_categories, account
):
    food, transport, _income = test_categories
    window = history_months(app_today(), 6)
    for month in window:
        await _spend(session, test_user, account, food, month.replace(day=5), "200")
    # A refund in one month nets against that month's spend.
    await _spend(session, test_user, account, food, window[0].replace(day=9), "50", kind="credit")
    # Budget but no spending at all: not enough to learn from.
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(category_id=transport.id, amount=Decimal("400"), month=window[-1]),
    )

    suggestion = await suggest_year_budget(
        session,
        test_workspace.id,
        test_user.id,
        year=app_today().year + 1,
        lookback_months=6,
        method="median",
    )

    assert suggestion.history_start == window[0]
    assert suggestion.history_end == window[-1]
    assert {r.category_id for r in suggestion.rows} == {food.id}
    row = suggestion.rows[0]
    assert row.proposed == [Decimal("200")] * 12  # median of 150,200,200,200,200,200
    assert row.history_average == Decimal("192")  # 1150 / 6 = 191.67
    assert row.existing == [None] * 12


@pytest.mark.asyncio
async def test_suggestion_reports_budgets_already_in_effect(
    session, test_user, test_workspace, test_categories, account
):
    food = test_categories[0]
    for month in history_months(app_today(), 3):
        await _spend(session, test_user, account, food, month.replace(day=5), "100")
    year = app_today().year + 1
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(category_id=food.id, amount=Decimal("999"), month=_m(year, 2)),
    )

    suggestion = await suggest_year_budget(
        session, test_workspace.id, test_user.id, year=year, lookback_months=3
    )
    existing = suggestion.rows[0].existing
    assert existing[1] == Decimal("999")
    assert existing[0] is None and existing[2] is None


@pytest.mark.asyncio
async def test_suggestion_does_not_write_anything(
    session, test_user, test_workspace, test_categories, account
):
    food = test_categories[0]
    for month in history_months(app_today(), 3):
        await _spend(session, test_user, account, food, month.replace(day=5), "100")
    await suggest_year_budget(session, test_workspace.id, test_user.id, lookback_months=3)
    assert (await session.execute(select(Budget))).scalars().all() == []


@pytest.mark.asyncio
async def test_apply_creates_updates_and_deletes(
    session, test_user, test_workspace, test_categories
):
    cat = test_categories[0]
    result = await apply_year_plan(
        session,
        test_workspace.id,
        test_user.id,
        BudgetYearApply(
            year=2027,
            cells=[
                BudgetPlanCellWrite(category_id=cat.id, month=1, amount=Decimal("100")),
                BudgetPlanCellWrite(category_id=cat.id, month=2, amount=Decimal("200")),
            ],
        ),
    )
    assert (result.saved, result.deleted) == (2, 0)

    result = await apply_year_plan(
        session,
        test_workspace.id,
        test_user.id,
        BudgetYearApply(
            year=2027,
            cells=[
                BudgetPlanCellWrite(category_id=cat.id, month=1, amount=Decimal("150")),  # update
                BudgetPlanCellWrite(category_id=cat.id, month=2, amount=None),  # delete
                BudgetPlanCellWrite(
                    category_id=cat.id, month=2, amount=None
                ),  # duplicate, harmless
                BudgetPlanCellWrite(category_id=cat.id, month=3, amount=None),  # nothing to delete
            ],
        ),
    )
    assert (result.saved, result.deleted) == (1, 1)

    rows = (await session.execute(select(Budget).order_by(Budget.month))).scalars().all()
    assert [(b.month, b.amount, b.is_recurring) for b in rows] == [
        (date(2027, 1, 1), Decimal("150.00"), False)
    ]


@pytest.mark.asyncio
async def test_apply_override_beats_recurring_default_and_clearing_restores_it(
    session, test_user, test_workspace, test_categories
):
    cat = test_categories[0]
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=cat.id, amount=Decimal("300"), month=_m(2027, 1), is_recurring=True
        ),
    )
    await apply_year_plan(
        session,
        test_workspace.id,
        test_user.id,
        BudgetYearApply(
            year=2027,
            cells=[BudgetPlanCellWrite(category_id=cat.id, month=4, amount=Decimal("450"))],
        ),
    )
    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2027)
    row = next(r for r in plan.rows if r.category_id == cat.id)
    assert row.cells[3].planned == Decimal("450")
    assert row.cells[4].planned == Decimal("300")

    await apply_year_plan(
        session,
        test_workspace.id,
        test_user.id,
        BudgetYearApply(
            year=2027, cells=[BudgetPlanCellWrite(category_id=cat.id, month=4, amount=None)]
        ),
    )
    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2027)
    row = next(r for r in plan.rows if r.category_id == cat.id)
    assert row.cells[3].planned == Decimal("300")


@pytest.mark.asyncio
async def test_apply_rejects_unknown_category_and_writes_nothing(
    session, test_user, test_workspace, test_categories
):
    cat = test_categories[0]
    with pytest.raises(PlanError):
        await apply_year_plan(
            session,
            test_workspace.id,
            test_user.id,
            BudgetYearApply(
                year=2027,
                cells=[
                    BudgetPlanCellWrite(category_id=cat.id, month=1, amount=Decimal("10")),
                    BudgetPlanCellWrite(category_id=uuid.uuid4(), month=1, amount=Decimal("10")),
                ],
            ),
        )
    assert (await session.execute(select(Budget))).scalars().all() == []


def test_apply_schema_rejects_negative_amounts_and_bad_months():
    with pytest.raises(ValueError):
        BudgetPlanCellWrite(category_id=uuid.uuid4(), month=1, amount=Decimal("-1"))
    with pytest.raises(ValueError):
        BudgetPlanCellWrite(category_id=uuid.uuid4(), month=13, amount=Decimal("1"))
