"""Search / Browse view (PDF view 4, Step 11).

    GET /search      the page: search bar, filters, sorting, product cards, details modal, "Recommended for you"

The page itself holds no product data. It calls ``/api/search``, ``/api/product``, ``/api/recommendations``,
``/api/stores/near`` and ``POST /api/list/items`` (see routes/api_shop.py).
"""

from flask import Blueprint, current_app, render_template
from flask_login import current_user, login_required

from app.services import budgets
from app.services.search import SORT_LABELS, SORTS

bp = Blueprint("search", __name__, url_prefix="/search")


@bp.get("", strict_slashes=False)
@login_required
def index():
    cfg = current_app.config
    budget = budgets.get_active_budget(current_user.UserId)
    position = budgets.position(budget, budgets.get_active_list(budget)) if budget else None
    return render_template(
        "search/index.html",
        categories=list(cfg["BUDGET_CATEGORIES"]),
        sorts=[(key, SORT_LABELS[key]) for key in SORTS],
        radius_default=cfg["DEFAULT_SEARCH_RADIUS_KM"],
        radius_max=cfg["SEARCH_MAX_RADIUS_KM"],
        budget=budget,
        position=position.to_dict() if position else None,
    )
