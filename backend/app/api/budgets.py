import uuid
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.budget import (
    BudgetCreate,
    BudgetRead,
    BudgetUpdate,
    BudgetVsActual,
    BudgetYearApply,
    BudgetYearApplyResult,
    BudgetYearPlan,
    BudgetYearSuggestion,
)
from app.services import budget_plan_service, budget_service

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


@router.get("", response_model=list[BudgetRead])
async def list_budgets(
    month: Optional[date] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await budget_service.get_budgets(session, ctx.workspace.id, month)


@router.post("", response_model=BudgetRead, status_code=status.HTTP_201_CREATED)
async def create_budget(
    data: BudgetCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await budget_service.create_budget(session, ctx.workspace.id, ctx.user_id, data)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.patch("/{budget_id}", response_model=BudgetRead)
async def update_budget(
    budget_id: uuid.UUID,
    data: BudgetUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    budget = await budget_service.update_budget(session, budget_id, ctx.workspace.id, data)
    if not budget:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")
    return budget


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(
    budget_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    deleted = await budget_service.delete_budget(session, budget_id, ctx.workspace.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")


@router.get("/comparison", response_model=list[BudgetVsActual])
async def budget_comparison(
    month: Optional[date] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await budget_service.get_budget_vs_actual(session, ctx.workspace.id, ctx.user_id, month)


@router.get("/year-plan", response_model=BudgetYearPlan)
async def year_plan(
    year: Optional[int] = Query(None, ge=2000, le=2100),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Planned vs executed spending for every category and month of a year."""
    return await budget_plan_service.get_year_plan(session, ctx.workspace.id, ctx.user_id, year)


@router.get("/year-plan/suggestion", response_model=BudgetYearSuggestion)
async def year_plan_suggestion(
    year: Optional[int] = Query(None, ge=2000, le=2100),
    lookback_months: int = Query(12, ge=3, le=24),
    method: Literal["median", "average", "seasonal"] = Query("median"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Proposed monthly budget per category, derived from recent spending.

    Read-only: nothing is saved until the client sends the reviewed numbers
    to ``PUT /year-plan``.
    """
    return await budget_plan_service.suggest_year_budget(
        session, ctx.workspace.id, ctx.user_id, year, lookback_months, method
    )


@router.put("/year-plan", response_model=BudgetYearApplyResult)
async def apply_year_plan(
    data: BudgetYearApply,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Bulk-save month-specific budgets (``amount: null`` clears a month)."""
    try:
        return await budget_plan_service.apply_year_plan(
            session, ctx.workspace.id, ctx.user_id, data
        )
    except budget_plan_service.PlanError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except budget_plan_service.PlanConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
