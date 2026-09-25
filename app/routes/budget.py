"""Budget view (PDF view 2, Step 9) and Set Budget view (PDF view 3, Step 10).

GET  /budget          the active budget (or the most recently closed one), categories, monthly and 6-month charts
POST /budget/remove   "Confirm Remove": close the active budget and its shopping list, delete the list's items
GET  /budget/new      the Set Budget form
POST /budget/new      "Confirm & Save": save the budget, make it active, start its shopping list
"""

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.services import budgets
from app.services.notifications import reset_request_cache
from app.utils.dates import default_budget_title
from app.utils.formatters import format_zar
from app.utils.money import ZERO, parse_amount

bp = Blueprint("budget", __name__, url_prefix="/budget")


@bp.get("", strict_slashes=False)
@login_required
def index():
    user_id = current_user.UserId
    active = budgets.get_active_budget(user_id)
    budget = active or budgets.last_closed_budget(user_id)
    shopping_list = budgets.get_active_list(active)
    items = budgets.list_items(shopping_list)

    context = {
        "budget": budget,
        "is_active": active is not None,
        "position": budgets.position(budget, shopping_list) if budget else None,
        "category_rows": budgets.category_rows(budget) if budget else [],
        "performance": budgets.monthly_performance(user_id),
        "series": budgets.six_month_series(user_id),
        "list_item_count": len(items),
    }
    return render_template("budget/index.html", **context)


@bp.post("/remove")
@login_required
def remove():
    removed = budgets.remove_active_budget(current_user.UserId)
    if removed is None:
        flash("You do not have an active budget to remove.", "info")
    else:
        flash(f"“{removed.Title}” and its shopping list were removed.", "success")
    return redirect(url_for("budget.index"))


def _render_new(parsed=None, status: int = 200):
    user_id = current_user.UserId
    cfg = current_app.config
    room = budgets.allowance_room(user_id)
    active = budgets.get_active_budget(user_id)
    replaces = None
    if active is not None:
        replaces = {"title": active.Title, "item_count": len(budgets.list_items(budgets.get_active_list(active)))}

    if parsed is None:
        title = default_budget_title()
        budget_type = budgets.INDIVIDUAL_TYPE
        rows = [budgets.BudgetRow(category) for category in cfg["BUDGET_CATEGORIES"]]
        errors: dict = {}
    else:
        title, budget_type, rows, errors = (
            parsed.title,
            parsed.budget_type or budgets.INDIVIDUAL_TYPE,
            parsed.rows,
            parsed.errors,
        )

    total = sum(_valid_amounts(rows), ZERO)
    return (
        render_template(
            "budget/new.html",
            title_value=title,
            budget_type=budget_type,
            rows=rows,
            errors=errors,
            replaces=replaces,
            categories=list(cfg["BUDGET_CATEGORIES"]),
            combined_key=budgets.COMBINED,
            nsfas=budgets.nsfas_notice(total, room.room),
            room=room,
            max_amount=cfg["MAX_BUDGET_AMOUNT"],
            title_max=budgets.TITLE_MAX,
            total=total,
        ),
        status,
    )


def _valid_amounts(rows):
    """Amounts already typed into the form that parse and are positive (for the running total on a re-render)."""
    for row in rows:
        amount = parse_amount(row.raw)
        if amount is not None and amount > 0:
            yield amount


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    if request.method == "GET":
        return _render_new()

    room = budgets.allowance_room(current_user.UserId)
    parsed = budgets.parse_budget_form(request.form, defaults_title=default_budget_title(), room=room)
    if not parsed.ok:
        return _render_new(parsed, status=400)

    try:
        budget = budgets.create_budget(current_user.UserId, parsed.title, parsed.entries)
    except budgets.BudgetError as error:
        parsed.errors["form"] = str(error)
        return _render_new(parsed, status=400)
    reset_request_cache()
    flash(f"“{budget.Title}” is saved and active: {format_zar(budget.TotalAmount)} in total.", "success")
    return redirect(url_for("budget.index"))
