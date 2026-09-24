"""Shopping List History (PDF "Other views", Step 14).

GET  /history                     finished trips, newest first, with date / store / category filters
GET  /history/<id>                one trip as a read-only invoice, with the route map from home to its stores
GET  /history/<id>/route          that route as JSON (road distance and line from OSRM, or an estimate)
POST /history/<id>/reorder        put the trip's items back on the active list at today's prices
"""

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.services import budgets, history, routing, shopping
from app.services.invoice import build_invoice
from app.services.notifications import reset_request_cache
from app.services.retail_api import RetailAPIError, RetailConfigError, get_retail_provider

bp = Blueprint("history", __name__, url_prefix="/history")


@bp.get("", strict_slashes=False)
@login_required
def index():
    filters, errors = history.parse_filters(request.args)
    page = history.find_trips(current_user.UserId, filters)
    args = {k: v for k, v in request.args.items() if k != "page" and v}
    return render_template(
        "history/index.html",
        filters=filters,
        errors=errors,
        page=page,
        months=history.group_by_month(page.trips),
        options=history.filter_options(current_user.UserId),
        args=args,
        raw=request.args,
        store_points=history.store_points(current_user.UserId),
        trip_maps={trip.id: history.trip_map(trip, shopping.student_center(current_user)) for trip in page.trips},
        home_known=current_user.has_location,
    )


@bp.get("/<list_id>")
@login_required
def detail(list_id):
    trip = history.get_trip(current_user.UserId, list_id)
    if trip is None:
        abort(404)
    budget = budgets.get_active_budget(current_user.UserId)
    active_items = budgets.list_items(budgets.get_active_list(budget)) if budget else []
    route = history.trip_map(trip, shopping.student_center(current_user))
    order = [stop["name"] for stop in route["stops"]]
    return render_template(
        "history/detail.html",
        trip=trip,
        invoice=build_invoice(trip.items, order),
        route=route,
        home_known=current_user.has_location,
        has_budget=budget is not None,
        active_count=len(active_items),
    )


@bp.get("/<list_id>/route")
@login_required
def route(list_id):
    """The trip's route for the map: home, the stores in visiting order, road distance, time and the line to draw."""
    trip = history.get_trip(current_user.UserId, list_id)
    if trip is None:
        return jsonify(error="That trip was not found."), 404
    payload = routing.plan(shopping.student_center(current_user), build_invoice(trip.items).groups, return_home=True)
    payload["home_known"] = current_user.has_location
    return jsonify(payload)


@bp.post("/<list_id>/reorder")
@login_required
def reorder(list_id):
    trip = history.get_trip(current_user.UserId, list_id)
    if trip is None:
        abort(404)
    if budgets.get_active_budget(current_user.UserId) is None:
        flash("Create a budget first. Reordered items go onto the shopping list of your active budget.", "info")
        return redirect(url_for("budget.new"))
    replace = request.form.get("mode", "replace") != "merge"
    try:
        result = history.reorder(get_retail_provider(), current_user, trip, replace)
    except shopping.ShoppingError as error:
        flash(str(error), "danger")
        return redirect(url_for("history.detail", list_id=list_id))
    except (RetailAPIError, RetailConfigError):
        flash(
            "We could not reach the price service right now. Nothing was changed. Please try again in a moment.",
            "danger",
        )
        return redirect(url_for("history.detail", list_id=list_id))
    reset_request_cache()
    added = result.added
    flash(
        f"“{trip.title}” reordered at today's prices: {added} item{'' if added == 1 else 's'} on your list. {result.summary()}",
        "success",
    )
    if result.position is not None and result.position.is_over:
        flash(shopping.over_budget_message(result.position), "warning")
    return redirect(url_for("shopping.index"))
