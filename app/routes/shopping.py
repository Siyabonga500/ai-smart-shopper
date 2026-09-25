"""Shopping List (PDF view 5, Step 12) and Shopping List Summary (PDF view 6, Step 13).

    GET  /list            the active list: title, budget check, items, cheaper alternatives, "Proceed to Summary"
    GET  /list/fragment   just the part of the page that changes when an item does (the page swaps it in)
    GET  /list/summary    the invoice, budget impact, route map and store groups (only for a list that is not over budget)
    POST /list/complete   "Done - Purchase Completed": close the list and the budget, record the purchases
    GET  /shopping        old address: redirects to /list

Quantity, remove, replace and rename go through the JSON API (routes/api_shop.py).
"""

from flask import Blueprint, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.services import budgets, routing, shopping
from app.services.invoice import build_invoice
from app.services.notifications import reset_request_cache
from app.services.retail_api import RetailAPIError, RetailConfigError, get_retail_provider

bp = Blueprint("shopping", __name__, url_prefix="/list")
legacy_bp = Blueprint("shopping_legacy", __name__)

OVER_BUDGET_HINT = "Your shopping list is over budget. Replace items with cheaper alternatives or reduce quantities."


def not_purchased_hint(names: list[str]) -> str:
    """Why "Proceed to Summary" is off while items are not ticked as purchased."""
    count = len(names)
    return (
        f"{count} item{'' if count == 1 else 's'} {'is' if count == 1 else 'are'} not marked as purchased: "
        f"{', '.join(names)}. Mark {'it' if count == 1 else 'each one'} as purchased, or delete "
        f"{'it' if count == 1 else 'them'} from your list, before you proceed to the summary."
    )


@legacy_bp.get("/shopping")
@legacy_bp.get("/shopping/")
def old_address():
    return redirect(url_for("shopping.index"), code=302)


def _context(want_alternatives: bool) -> dict:
    """Everything the list page (and its fragment) shows, worked out from the database."""
    user = current_user
    budget = budgets.get_active_budget(user.UserId)
    if budget is None:
        return {"budget": None}
    shopping_list = budgets.ensure_active_list(budget)
    items = budgets.list_items(shopping_list)
    try:
        shopping.refresh_alternatives(items, get_retail_provider(), user)
    except (RetailAPIError, RetailConfigError):
        pass  # alternatives are a nicety: the list itself still works
    position = budgets.position(budget, shopping_list)
    center = shopping.student_center(user)
    rows = [shopping.item_to_dict(item, center) for item in items]
    savings = shopping.potential_savings(items)
    over = position.is_over
    pending = [row["name"] for row in rows if not row["collected"]]  # not ticked as purchased yet
    return {
        "budget": budget,
        "shopping_list": shopping_list,
        "position": position,
        "items": rows,
        "over": over,
        "show_alternatives": over or want_alternatives,
        "savings": savings,
        "alternatives_available": sum(1 for row in rows if row["alternative"]),
        "category_rows": budgets.category_rows(budget),
        "can_proceed": bool(rows) and not over and not pending,
        "over_hint": OVER_BUDGET_HINT if over else not_purchased_hint(pending),
        "max_quantity": current_app.config["MAX_ITEM_QUANTITY"],
        "collected_count": sum(1 for row in rows if row["collected"]),
    }


@bp.get("", strict_slashes=False)
@login_required
def index():
    context = _context(request.args.get("alternatives") == "1")
    return render_template("shopping/index.html", **context)


@bp.get("/fragment")
@login_required
def fragment():
    """The changing part of the page as HTML. The page calls this after every change, so the server stays the
    only place that works out totals, warnings and which buttons are enabled."""
    response = make_response(
        render_template("shopping/_body.html", **_context(request.args.get("alternatives") == "1"))
    )
    response.headers["X-List-Fragment"] = "1"
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/summary")
@login_required
def summary():
    budget = budgets.get_active_budget(current_user.UserId)
    if budget is None:
        flash("Create a budget first. Your shopping list belongs to your active budget.", "info")
        return redirect(url_for("budget.index"))
    shopping_list = budgets.ensure_active_list(budget)
    items = budgets.list_items(shopping_list)
    position = budgets.position(budget, shopping_list)
    if not items:
        flash("Your shopping list is empty. Add some products before you review the summary.", "info")
        return redirect(url_for("shopping.index"))
    if position.is_over:
        flash(OVER_BUDGET_HINT, "warning")
        return redirect(url_for("shopping.index"))
    pending = [item.ItemName for item in items if not item.IsCollected]
    if pending:
        flash(not_purchased_hint(pending), "warning")
        return redirect(url_for("shopping.index"))

    home = shopping.student_center(current_user)
    return_home = current_app.config["ROUTE_RETURN_HOME"]
    stops, _ = routing.stops_from_groups(build_invoice(items).groups)
    order = [stop.name for stop in routing.order_stops(home, stops, return_home)]
    invoice = build_invoice(items, order)
    position_of = {name.casefold(): index for index, name in enumerate(order, 1)}
    return render_template(
        "shopping/summary.html",
        budget=budget,
        shopping_list=shopping_list,
        position=position,
        invoice=invoice,
        stop_number=lambda group: position_of.get(group.store_name.casefold()),
        impact=budgets.category_impact(budget, items),
        return_home=return_home,
        home_known=current_user.Latitude is not None and current_user.Longitude is not None,
    )


@bp.post("/complete")
@login_required
def complete():
    """ "Done - Purchase Completed": one transaction closes the list and the budget and records the purchases."""
    active = budgets.get_active_budget(current_user.UserId)
    title = active.Title if active else ""
    pending = [i.ItemName for i in budgets.list_items(budgets.get_active_list(active)) if not i.IsCollected]
    if pending:
        flash(not_purchased_hint(pending), "warning")
        return redirect(url_for("shopping.index"))
    try:
        budget = budgets.complete_purchase(current_user.UserId)
    except budgets.BudgetError as error:
        flash(str(error), "danger")
        return redirect(url_for("shopping.index"))
    reset_request_cache()
    flash(
        f"Purchase completed. {budgets.outcome(budget).message()} “{title}” and its shopping list are now closed "
        "and saved to your history.",
        "success",
    )
    return redirect(url_for("dashboard.index"))
