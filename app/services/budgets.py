"""Budgets (Steps 9 and 10): create, remove, complete, and the numbers the Budget view shows.

How the money adds up (read this first)
---------------------------------------
* ``Budget.UsedAmount`` is **everything the student has put towards this budget**: the sum of ``UnitCost x
  ItemQuantity`` over every item on the budget's shopping lists. It includes the active list, and it is
  recalculated (:func:`recalculate`) whenever an item is added, changed, replaced or removed, exactly as the
  Search view asks ("recalculate ShoppingList.TotalCost and the active Budget's UsedAmount").
* ``SubBudget.UsedAmount`` is the same sum for the items filed under that category.
* The Shopping List view splits ``UsedAmount`` into **Used** (what was already used before this list,
  ``UsedAmount - list total``) and **In List** (the list total), so ``Remaining = Total - Used - In List``.
* A list is *over budget* when ``UsedAmount > TotalAmount``. That never blocks adding items, and the R1 750
  NSFAS reference never blocks anything; only "Proceed to Summary" is refused while over budget.

Only one budget is active per student, and it has one active shopping list. Removing a budget, or replacing it
by saving a new one, closes the budget and its list and *deletes the list's items* (the Budget view's confirm
box says so). Such a closed budget ends with ``UsedAmount = 0``; that is how "abandoned" budgets are told
apart from budgets that were actually shopped (which always have a positive ``UsedAmount``).

Functions that change data commit the session themselves when they are a whole action (create, remove,
complete); the small helpers leave committing to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from flask import current_app
from sqlalchemy import delete, func, select

from app.extensions import db
from app.models import Budget, ListItem, ShoppingList, SubBudget
from app.utils.dates import (
    default_budget_title,
    month_label,
    previous_months,
    sast_day_bounds,
    to_sast,
    today_sast,
    utcnow,
)
from app.utils.formatters import format_zar
from app.utils.money import ZERO, money, parse_amount, to_decimal

TITLE_MAX = 80  # + " Shopping List" must fit the 100-character ShoppingList.Title column
COMBINED = "Combined"
INDIVIDUAL_TYPE, COMBINED_TYPE = "individual", "combined"


class BudgetError(ValueError):
    """A budget action that cannot be done; the message is safe to show the student."""


# ---------------------------------------------------------------------------------------------------- lookups
def get_active_budget(user_id: str) -> Budget | None:
    return db.session.scalar(
        select(Budget)
        .where(Budget.UserId == user_id, Budget.IsActive.is_(True))
        .order_by(Budget.DateCreated.desc(), Budget.BudgetId)
        .limit(1)
    )


def get_active_list(budget: Budget | None) -> ShoppingList | None:
    if budget is None:
        return None
    return db.session.scalar(
        select(ShoppingList)
        .where(ShoppingList.BudgetId == budget.BudgetId, ShoppingList.IsActive.is_(True))
        .order_by(ShoppingList.DateCreated.desc(), ShoppingList.ShoppingListId)
        .limit(1)
    )


def ensure_active_list(budget: Budget) -> ShoppingList:
    """The budget's active shopping list, created (empty) if it is somehow missing."""
    shopping_list = get_active_list(budget)
    if shopping_list is None:
        shopping_list = ShoppingList(
            UserId=budget.UserId,
            BudgetId=budget.BudgetId,
            Title=_list_title(budget.Title),
            TotalCost=ZERO,
            IsActive=True,
            DateCreated=utcnow(),
        )
        db.session.add(shopping_list)
        db.session.flush()
    return shopping_list


def list_items(shopping_list: ShoppingList | None) -> list[ListItem]:
    if shopping_list is None:
        return []
    return list(
        db.session.scalars(
            select(ListItem)
            .where(ListItem.ShoppingListId == shopping_list.ShoppingListId)
            .order_by(ListItem.DateCreated, ListItem.ListItemId)
        )
    )


def is_abandoned(budget: Budget) -> bool:
    """Closed without ever being shopped (removed, or replaced by a new budget)."""
    return (not budget.IsActive) and to_decimal(budget.UsedAmount) <= 0


def last_closed_budget(user_id: str) -> Budget | None:
    """The most recently closed budget that was actually shopped; failing that, the most recent closed one."""
    closed = select(Budget).where(Budget.UserId == user_id, Budget.IsActive.is_(False))
    order = (func.coalesce(Budget.ClosedDate, Budget.DateCreated).desc(), Budget.BudgetId)
    shopped = db.session.scalar(closed.where(Budget.UsedAmount > 0).order_by(*order).limit(1))
    return shopped or db.session.scalar(closed.order_by(*order).limit(1))


# ---------------------------------------------------------------------------------------------------- numbers
@dataclass(frozen=True)
class BudgetPosition:
    """Where a budget stands. ``remaining`` and ``over_by`` are never both non-zero."""

    total: Decimal
    used: Decimal  # everything counted against the budget, including the list
    in_list: Decimal  # the active list's total
    already_used: Decimal  # used - in_list
    remaining: Decimal  # total - used (negative when over budget)
    percent_used: float  # used / total, not capped at 100
    over_by: Decimal  # max(0, used - total)

    @property
    def is_over(self) -> bool:
        return self.over_by > 0

    @property
    def budget_left_for_list(self) -> Decimal:
        """What the list may cost before it goes over budget: ``total - already_used``."""
        return self.total - self.already_used

    def to_dict(self) -> dict:
        return {
            "total": str(self.total),
            "used": str(self.used),
            "in_list": str(self.in_list),
            "already_used": str(self.already_used),
            "remaining": str(self.remaining),
            "percent_used": self.percent_used,
            "over_by": str(self.over_by),
            "is_over": self.is_over,
        }


def position(budget: Budget, shopping_list: ShoppingList | None = None) -> BudgetPosition:
    """The numbers as currently stored (call :func:`recalculate` first if items just changed)."""
    total, used = money(budget.TotalAmount), money(budget.UsedAmount)
    if shopping_list is None:
        shopping_list = get_active_list(budget) if budget.IsActive else None
    in_list = money(shopping_list.TotalCost) if shopping_list is not None else ZERO
    return BudgetPosition(
        total=total,
        used=used,
        in_list=in_list,
        already_used=used - in_list,
        remaining=total - used,
        percent_used=budget.percent_used,
        over_by=max(ZERO, used - total),
    )


def recalculate(budget: Budget) -> BudgetPosition:
    """Recompute every stored total under ``budget`` from its items. Flushes; the caller commits."""
    db.session.flush()
    lists = list(db.session.scalars(select(ShoppingList).where(ShoppingList.BudgetId == budget.BudgetId)))
    sub_budgets = list(db.session.scalars(select(SubBudget).where(SubBudget.BudgetId == budget.BudgetId)))

    per_list: dict[str, Decimal] = {sl.ShoppingListId: ZERO for sl in lists}
    per_sub: dict[str, Decimal] = {sb.SubBudgetId: ZERO for sb in sub_budgets}
    if per_list:
        for item in db.session.scalars(select(ListItem).where(ListItem.ShoppingListId.in_(per_list))):
            per_list[item.ShoppingListId] += item.LineTotal
            if item.SubBudgetId in per_sub:
                per_sub[item.SubBudgetId] += item.LineTotal

    for shopping_list in lists:
        shopping_list.TotalCost = per_list[shopping_list.ShoppingListId]
    for sub_budget in sub_budgets:
        sub_budget.UsedAmount = per_sub[sub_budget.SubBudgetId]
    budget.UsedAmount = sum(per_list.values(), ZERO)
    db.session.flush()
    return position(budget)


def sub_budget_for(budget: Budget, category: str | None) -> SubBudget | None:
    """Which sub-budget an item of ``category`` counts against: the Combined one, else the matching category.

    ``None`` when the student did not allocate money to that category. The item still counts towards the
    budget's total; the category table shows it on an "Other" line.
    """
    sub_budgets = list(db.session.scalars(select(SubBudget).where(SubBudget.BudgetId == budget.BudgetId)))
    for sub_budget in sub_budgets:
        if sub_budget.Category == COMBINED:
            return sub_budget
    wanted = (category or "").strip().casefold()
    for sub_budget in sub_budgets:
        if sub_budget.Category.casefold() == wanted:
            return sub_budget
    return None


# ---------------------------------------------------------------------------------------------------- Set Budget form
@dataclass
class BudgetRow:
    category: str
    raw: str = ""
    error: str | None = None


@dataclass
class ParsedBudgetForm:
    title: str
    budget_type: str
    rows: list[BudgetRow]
    errors: dict = field(default_factory=dict)  # field name -> message ("form" for general ones)
    entries: list[tuple[str, Decimal]] = field(default_factory=list)  # valid (category, amount), when no errors

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def total(self) -> Decimal:
        return sum((amount for _, amount in self.entries), ZERO)


def nsfas_notice(total, allowance=None) -> dict:
    """What the NSFAS allowance bar shows for a budget of ``total``: the bar, and a warning above the reference.

    Never a block: the warning only informs (Set Budget view, section A).
    """
    allowance = to_decimal(allowance if allowance is not None else current_app.config["NSFAS_MONTHLY_ALLOWANCE"])
    total = to_decimal(total)
    percent = float(total / allowance * 100) if allowance > 0 else 0.0
    over = total - allowance
    warning = None
    if over > 0:
        warning = (
            f"Your budget is {format_zar(total)}, which is {format_zar(over)} above the "
            f"{format_zar(allowance)} NSFAS meal allowance reference."
        )
    return {
        "allocated": total,
        "allowance": allowance,
        "percent": round(percent, 1),
        "over_by": max(ZERO, over),
        "warning": warning,
    }


def parse_budget_form(form, defaults_title: str | None = None) -> ParsedBudgetForm:
    """Validate the Set Budget form. Mirrors what static/js/budget_new.js checks in the browser.

    Form fields: ``title``, ``budget_type`` (individual | combined), and one ``amount_<Category>`` field per
    category *present* (a removed row is simply not sent), ``amount_Combined`` for a combined budget.
    """
    cfg = current_app.config
    categories = tuple(cfg["BUDGET_CATEGORIES"])
    limit = to_decimal(cfg["MAX_BUDGET_AMOUNT"])
    errors: dict[str, str] = {}

    title = " ".join((form.get("title") or "").split())
    if not title and defaults_title:
        title = defaults_title
    if not title:
        errors["title"] = "Give your budget a title."
    elif len(title) > TITLE_MAX:
        errors["title"] = f"Keep the title to {TITLE_MAX} characters or fewer."

    budget_type = (form.get("budget_type") or "").strip().lower()
    if budget_type not in (INDIVIDUAL_TYPE, COMBINED_TYPE):
        errors["budget_type"] = "Choose Individual Categories or Combined Budget."

    individual_rows = [
        BudgetRow(category, form.get(f"amount_{category}") or "")
        for category in categories
        if f"amount_{category}" in form
    ]
    combined_raw = form.get(f"amount_{COMBINED}")
    rows: list[BudgetRow]
    entries: list[tuple[str, Decimal]] = []

    if budget_type == COMBINED_TYPE:
        rows = [BudgetRow(COMBINED, combined_raw or "")]
        if individual_rows:
            errors["form"] = (
                "A Combined Budget cannot be used together with individual categories. "
                "Remove all the categories first."
            )
    else:
        rows = individual_rows
        if combined_raw is not None:
            errors["form"] = "Individual categories cannot be combined with a Combined Budget."
        elif budget_type == INDIVIDUAL_TYPE and not individual_rows:
            errors["form"] = "Add at least one category, or choose a Combined Budget."

    if "form" not in errors:
        for row in rows:
            amount = parse_amount(row.raw)
            if amount is None:
                row.error = "Enter an amount, e.g. 700."
            elif amount <= 0:
                row.error = "The amount must be more than R0."
            elif amount > limit:
                row.error = f"The amount cannot be more than {format_zar(limit)}."
            else:
                entries.append((row.category, amount))
        if any(row.error for row in rows):
            errors["amounts"] = "Fix the amounts marked below."
        elif sum((a for _, a in entries), ZERO) > limit:
            errors["form"] = f"The total budget cannot be more than {format_zar(limit)}."

    if errors:
        entries = []
    return ParsedBudgetForm(title=title, budget_type=budget_type, rows=rows, errors=errors, entries=entries)


# ---------------------------------------------------------------------------------------------------- actions
def _list_title(budget_title: str) -> str:
    return f"{budget_title} Shopping List"[:100]


def abandon_active_budget(user_id: str, when: datetime | None = None) -> Budget | None:
    """Close the active budget without shopping: budget, sub-budgets and list closed, the list's items deleted.

    Returns the budget that was closed (``None`` if there was none). Flushes; the caller commits.
    """
    when = when or utcnow()
    closed = None
    for budget in db.session.scalars(select(Budget).where(Budget.UserId == user_id, Budget.IsActive.is_(True))).all():
        lists = list(db.session.scalars(select(ShoppingList).where(ShoppingList.BudgetId == budget.BudgetId)))
        list_ids = [sl.ShoppingListId for sl in lists]
        if list_ids:
            db.session.execute(
                delete(ListItem).where(ListItem.ShoppingListId.in_(list_ids), ListItem.IsPurchased.is_(False))
            )
            db.session.expire_all()  # the bulk delete bypassed the objects already loaded
        for shopping_list in lists:
            if shopping_list.IsActive:
                shopping_list.deactivate(when)
        for sub_budget in db.session.scalars(select(SubBudget).where(SubBudget.BudgetId == budget.BudgetId)):
            if sub_budget.IsActive:
                sub_budget.IsActive = False
                sub_budget.ClosedDate = when
        budget.deactivate(when)
        recalculate(budget)
        closed = closed or budget
    db.session.flush()
    return closed


def create_budget(user_id: str, title: str, entries: list[tuple[str, Decimal]]) -> Budget:
    """Save a budget with its sub-budgets, make it the active one, and start its shopping list (Step 10).

    Any previously active budget is closed first (see :func:`abandon_active_budget`).
    """
    if not entries:
        raise BudgetError("A budget needs at least one amount.")
    names = [category for category, _ in entries]
    if len(set(names)) != len(names):
        raise BudgetError("Each category can only be listed once.")
    if COMBINED in names and len(names) > 1:
        raise BudgetError("A Combined Budget cannot be mixed with individual categories.")
    if any(money(amount) <= ZERO for _, amount in entries):
        raise BudgetError("Every amount must be more than R0.")
    now = utcnow()
    try:
        abandon_active_budget(user_id, when=now)
        budget = Budget(
            UserId=user_id,
            Title=title,
            TotalAmount=sum((money(a) for _, a in entries), ZERO),
            UsedAmount=ZERO,
            IsActive=True,
            DateCreated=now,
        )
        db.session.add(budget)
        db.session.flush()
        for category, amount in entries:
            db.session.add(
                SubBudget(
                    UserId=user_id,
                    BudgetId=budget.BudgetId,
                    Category=category,
                    AllocatedAmount=money(amount),
                    UsedAmount=ZERO,
                    IsActive=True,
                    DateCreated=now,
                )
            )
        db.session.add(
            ShoppingList(
                UserId=user_id,
                BudgetId=budget.BudgetId,
                Title=_list_title(title),
                TotalCost=ZERO,
                IsActive=True,
                DateCreated=now,
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return budget


def remove_active_budget(user_id: str) -> Budget | None:
    """ "Confirm Remove" on the Budget view. Returns the removed budget, or ``None`` if nothing was active."""
    try:
        closed = abandon_active_budget(user_id)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return closed


def complete_purchase(user_id: str) -> Budget:
    """ "Done - Purchase Completed" (Step 13): the whole trip is recorded in one transaction.

    The list is closed, every item is marked purchased, the budget and its sub-budgets are closed.
    Refused (``BudgetError``) when there is nothing to buy or the list is over budget.
    """
    budget = get_active_budget(user_id)
    shopping_list = get_active_list(budget)
    if budget is None or shopping_list is None:
        raise BudgetError("You do not have an active budget and shopping list.")
    items = list_items(shopping_list)
    if not items:
        raise BudgetError("Your shopping list is empty.")
    if recalculate(budget).is_over:
        raise BudgetError(
            "Your shopping list is over budget. Replace items with cheaper alternatives " "or reduce quantities."
        )
    now = utcnow()
    try:
        for item in items:
            item.IsPurchased = True
            item.PurchasedDate = now
        shopping_list.deactivate(now)
        for sub_budget in db.session.scalars(select(SubBudget).where(SubBudget.BudgetId == budget.BudgetId)):
            sub_budget.IsActive = False
            sub_budget.ClosedDate = now
        budget.deactivate(now)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return budget


# ---------------------------------------------------------------------------------------------------- Budget view data
OTHER_LABEL = "Other (not budgeted)"


@dataclass(frozen=True)
class CategoryRow:
    category: str
    allocated: Decimal
    used: Decimal
    remaining: Decimal
    percent: float  # used / allocated (0 when nothing is allocated)
    is_other: bool = False


def category_rows(budget: Budget) -> list[CategoryRow]:
    """Rows of the Budget Category Summary (Category | Allocated | Used | Remaining).

    A Combined budget has the single "Combined" row. Spending on a category the student gave no money to is
    shown on an "Other (not budgeted)" row so the rows always add up to the budget's total used.
    """
    order = {name: index for index, name in enumerate(current_app.config["BUDGET_CATEGORIES"])}
    subs = sorted(
        db.session.scalars(select(SubBudget).where(SubBudget.BudgetId == budget.BudgetId)),
        key=lambda sb: (order.get(sb.Category, 99), sb.Category),
    )
    rows = []
    for sub in subs:
        allocated, used = money(sub.AllocatedAmount), money(sub.UsedAmount)
        rows.append(
            CategoryRow(
                sub.Category,
                allocated,
                used,
                allocated - used,
                float(round(used / allocated * 100, 1)) if allocated > 0 else 0.0,
            )
        )
    other = money(budget.UsedAmount) - sum((row.used for row in rows), ZERO)
    if other != 0:
        rows.append(CategoryRow(OTHER_LABEL, ZERO, other, -other, 100.0, is_other=True))
    return rows


@dataclass(frozen=True)
class CategoryImpact:
    """One line of the Summary's budget impact: Grocery Budget R800, Previously Used R500, This Shopping R250, Remaining R50."""

    category: str
    allocated: Decimal
    previously_used: Decimal
    this_shopping: Decimal
    remaining: Decimal
    is_other: bool = False


def category_impact(budget: Budget, items: list[ListItem]) -> list[CategoryImpact]:
    """Per category: what the trip adds on top of what was already spent. Only categories with something to show."""
    this: dict[str, Decimal] = {}
    for item in items:
        sub_budget = sub_budget_for(budget, item.Category)
        name = sub_budget.Category if sub_budget else OTHER_LABEL
        this[name] = this.get(name, ZERO) + item.LineTotal
    impact = []
    for row in category_rows(budget):
        spent_now = money(this.get(row.category, ZERO))
        impact.append(
            CategoryImpact(row.category, row.allocated, row.used - spent_now, spent_now, row.remaining, row.is_other)
        )
    return impact


@dataclass(frozen=True)
class MonthPerformance:
    key: tuple[int, int]
    label: str  # "September"
    allocated: Decimal
    used: Decimal
    percent: float  # used / allocated; 0 with no budget that month
    vs_reference: Decimal  # R1 750 - used: positive = left over, negative = over the reference
    has_budget: bool


def _budgets_by_month(user_id: str, months: list[tuple[int, int]]) -> dict[tuple[int, int], list[Budget]]:
    """The student's budgets per month (by the month they were created, SAST); abandoned budgets are left out."""
    lower, _ = sast_day_bounds(date(months[0][0], months[0][1], 1), None)
    found: dict[tuple[int, int], list[Budget]] = {key: [] for key in months}
    query = select(Budget).where(Budget.UserId == user_id, Budget.DateCreated >= lower).order_by(Budget.DateCreated)
    for budget in db.session.scalars(query):
        created = to_sast(budget.DateCreated)
        if created is None or is_abandoned(budget):
            continue
        key = (created.year, created.month)
        if key in found:
            found[key].append(budget)
    return found


def monthly_performance(user_id: str, today: date | None = None) -> list[MonthPerformance]:
    """This month and last month: Used vs Allocated, and the amount left / over against the NSFAS reference."""
    today = today or today_sast()
    reference = to_decimal(current_app.config["NSFAS_MONTHLY_ALLOWANCE"])
    months = previous_months(today, 2)
    grouped = _budgets_by_month(user_id, months)
    result = []
    for key in reversed(months):  # current month first, then the previous one
        budgets = grouped[key]
        allocated = sum((money(b.TotalAmount) for b in budgets), ZERO)
        used = sum((money(b.UsedAmount) for b in budgets), ZERO)
        result.append(
            MonthPerformance(
                key=key,
                label=month_label(key, long=True).split()[0],
                allocated=allocated,
                used=used,
                percent=float(round(used / allocated * 100, 1)) if allocated > 0 else 0.0,
                vs_reference=reference - used,
                has_budget=bool(budgets),
            )
        )
    return result


def six_month_series(user_id: str, today: date | None = None) -> dict:
    """Chart data for the Six-Month Spending Graph: labels, allocated and used per month (oldest first)."""
    today = today or today_sast()
    months = previous_months(today, current_app.config["SPENDING_HISTORY_MONTHS"])
    grouped = _budgets_by_month(user_id, months)
    allocated = [sum((money(b.TotalAmount) for b in grouped[key]), ZERO) for key in months]
    used = [sum((money(b.UsedAmount) for b in grouped[key]), ZERO) for key in months]
    return {
        "labels": [month_label(key) for key in months],
        "long_labels": [month_label(key, long=True) for key in months],
        "allocated": [float(v) for v in allocated],
        "used": [float(v) for v in used],
        "has_data": any(v > 0 for v in allocated + used),
    }


def default_title() -> str:
    return default_budget_title()
