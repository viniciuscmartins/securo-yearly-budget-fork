import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class BudgetCreate(BaseModel):
    category_id: uuid.UUID
    amount: Decimal
    month: _Date  # First day of month
    is_recurring: bool = False


class BudgetUpdate(BaseModel):
    amount: Optional[Decimal] = None
    effective_month: Optional[_Date] = None


class BudgetRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    category_id: uuid.UUID
    amount: Decimal
    month: _Date
    is_recurring: bool

    model_config = ConfigDict(from_attributes=True)


class BudgetVsActual(BaseModel):
    category_id: uuid.UUID
    category_name: str
    category_icon: str
    category_color: str
    group_id: Optional[uuid.UUID] = None
    group_name: Optional[str] = None
    budget_amount: Optional[Decimal] = None
    actual_amount: Decimal
    projected_amount: Decimal = Decimal("0")
    prev_month_amount: Decimal = Decimal("0")
    projected_prev_month_amount: Decimal = Decimal("0")
    percentage_used: Optional[float] = None
    is_recurring: bool = False


# ---------------------------------------------------------------------------
# Annual plan: planned vs executed per category and month
# ---------------------------------------------------------------------------


class BudgetYearCell(BaseModel):
    month: int  # 1..12
    planned: Optional[Decimal] = None  # None = no budget set for this month
    actual: Decimal = Decimal("0")


class BudgetYearRow(BaseModel):
    category_id: uuid.UUID
    category_name: str
    category_icon: str
    category_color: str
    group_id: Optional[uuid.UUID] = None
    group_name: Optional[str] = None
    cells: list[BudgetYearCell]  # always 12 entries, January first
    planned_total: Decimal
    actual_total: Decimal


class BudgetYearPlan(BaseModel):
    year: int
    rows: list[BudgetYearRow]
    planned_by_month: list[Decimal]  # 12 entries
    actual_by_month: list[Decimal]  # 12 entries
    planned_total: Decimal
    actual_total: Decimal


class BudgetSuggestionRow(BaseModel):
    category_id: uuid.UUID
    category_name: str
    category_icon: str
    category_color: str
    group_id: Optional[uuid.UUID] = None
    group_name: Optional[str] = None
    history_average: Decimal  # mean monthly spend over the lookback window
    history_median: Decimal
    proposed: list[Decimal]  # 12 entries, January first
    existing: list[Optional[Decimal]]  # budget currently in effect, 12 entries


class BudgetYearSuggestion(BaseModel):
    year: int
    method: Literal["median", "average", "seasonal"]
    lookback_months: int
    history_start: _Date  # first day of the oldest month used
    history_end: _Date  # first day of the newest month used
    rows: list[BudgetSuggestionRow]


class BudgetPlanCellWrite(BaseModel):
    category_id: uuid.UUID
    month: int = Field(ge=1, le=12)
    # None removes the month-specific budget (a recurring default, if any,
    # applies again). Zero is a valid budget.
    amount: Optional[Decimal] = Field(default=None, ge=0)


class BudgetYearApply(BaseModel):
    year: int = Field(ge=2000, le=2100)
    cells: list[BudgetPlanCellWrite] = Field(max_length=12 * 500)


class BudgetYearApplyResult(BaseModel):
    saved: int
    deleted: int
