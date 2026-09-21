"""JSON endpoints for searching, the shopping list and recommendations (Steps 11 and 12).

    GET    /api/search               ?q=&category=&min_price=&max_price=&stores=&radius=&sort=&page=
    GET    /api/product              ?barcode=&store_id=  (or ?name=&store_name=)   one product, every store
    GET    /api/recommendations      the "Recommended for you" carousel (?limit=)
    GET    /api/savings              the Dashboard "Potential Savings" card
    GET    /api/weather              ?lat=&lng=  (optional)  current weather, high/low, humidity; cached 30 minutes
    GET    /api/route                the shopping route: stores in order, distance, time, line to draw
    GET    /api/list                 the active list, its budget position and items
    PATCH  /api/list                 {"title": "..."}   rename the list
    POST   /api/list/items           {"barcode", "store_id"/"store_name", "name"}   add (or +1 quantity)
    PATCH  /api/list/items/<id>      {"quantity": n}
    DELETE /api/list/items/<id>
    POST   /api/list/items/<id>/replace     take the cheaper alternative

All of them need a signed-in student and answer with JSON. Prices are always looked up again on the server.
"""

import re
from io import BytesIO
from urllib.parse import urlsplit

import requests
from flask import Blueprint, current_app, jsonify, request, url_for
from flask_login import current_user, login_required

from app.extensions import search_limiter
from app.services import budgets, products, recommender, routing, search, shopping
from app.services.invoice import build_invoice
from app.services.retail_api import RetailAPIError, RetailConfigError, clean_image_url, get_retail_provider
from app.services.weather import WeatherError, get_weather, place_label
from app.utils.geo import is_valid_coordinate, map_center_for

bp = Blueprint("shop_api", __name__, url_prefix="/api")


def _error(message: str, status: int = 400, **extra):
    response = jsonify(error=message, **extra)
    response.status_code = status
    return response


def _provider():
    return get_retail_provider()


def _limited():
    """A 429 when this student is searching too fast, else ``None``."""
    if not current_app.config["RATELIMIT_ENABLED"]:
        return None
    if search_limiter.hit(f"search:{current_user.UserId}"):
        return None
    response = _error("You are searching very quickly. Please wait a moment and try again.", 429)
    response.headers["Retry-After"] = str(int(current_app.config["SEARCH_REQUEST_WINDOW"]))
    return response


@bp.errorhandler(shopping.ShoppingError)
def _shopping_error(error):
    extra = (
        {"create_budget_url": url_for("budget.new")} if error.status == 409 and "budget" in str(error).lower() else {}
    )
    return _error(str(error), error.status, **extra)


@bp.errorhandler(RetailAPIError)
def _retail_error(_error_obj):
    current_app.logger.warning("Retail data error", exc_info=True)
    return _error("We could not reach the price service right now. Please try again in a moment.", 502)


@bp.errorhandler(RetailConfigError)
def _retail_config_error(_error_obj):
    current_app.logger.error("Retail provider is not configured", exc_info=True)
    return _error("Product prices are not available on this server yet.", 503)


def _active_items():
    return budgets.list_items(budgets.get_active_list(budgets.get_active_budget(current_user.UserId)))


@bp.get("/product-image")
@login_required
def product_image():
    """Proxy a retailer image so CDN hotlink/referrer rules do not turn valid photos into broken icons."""
    image_url = clean_image_url(request.args.get("url"))
    parsed = urlsplit(image_url or "")
    if not image_url or parsed.scheme != "https" or not parsed.hostname:
        return _error("Invalid product image URL.", 400)
    try:
        response = requests.get(
            image_url,
            headers={"User-Agent": "AISmartShopper/1.0", "Accept": "image/avif,image/webp,image/*"},
            timeout=8,
        )
    except requests.RequestException:
        return _error("Product image is unavailable.", 502)
    if response.status_code != 200 or not response.content:
        return _error("Product image is unavailable.", 502)
    content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
    if not content_type.startswith("image/"):
        return _error("Product image is unavailable.", 502)
    return current_app.response_class(
        BytesIO(response.content), mimetype=content_type, headers={"Cache-Control": "public, max-age=900"}
    )


# ------------------------------------------------------------------------------------------------ search
@bp.get("/search")
@login_required
def search_products():
    params, errors = search.parse_search_params(request.args)
    if errors:
        return _error(next(iter(errors.values())), 400, fields=errors)
    if params.q or params.category:
        if (limited := _limited()) is not None:
            return limited
    result = search.run_search(_provider(), current_user, params, _active_items())
    return jsonify(result.to_dict(params.q))


@bp.get("/product")
@login_required
def product_details():
    """One product at every store near the student, cheapest first (the details modal)."""
    barcode = (request.args.get("barcode") or "").strip() or None
    store_id = (request.args.get("store_id") or "").strip() or None
    store_name = (request.args.get("store_name") or "").strip() or None
    name = " ".join((request.args.get("name") or "").split()) or None
    if not barcode and not name:
        return _error("Send a barcode, or a product name and store.")
    if (limited := _limited()) is not None:
        return limited

    provider = _provider()
    center = shopping.student_center(current_user)
    radius = current_app.config["SEARCH_MAX_RADIUS_KM"]
    if barcode:
        offers = provider.get_offers_by_barcode(barcode, center["lat"], center["lng"], radius)
    else:
        offers = [
            o
            for o in provider.search_products(name, center["lat"], center["lng"], radius)
            if o.name.casefold() == name.casefold()
        ]
    if not offers:
        return _error("We could not find that product any more.", 404)

    viewed = next(
        (o for o in offers if (store_id and o.store_id == store_id) or (store_name and o.store_name == store_name)),
        offers[0],
    )
    quantities = products.in_list_quantities(_active_items())
    cheapest = products.cheapest_in_stock(offers)
    ordered = sorted(offers, key=lambda o: (not o.in_stock, o.price, o.store_name))
    cards = []
    for offer in ordered:
        badges = ["CHEAPEST"] if len([o for o in offers if o.in_stock]) > 1 and offer is cheapest else []
        cards.append(products.offer_to_card(offer, badges, quantities))
    return jsonify(
        product=products.offer_to_card(viewed, [], quantities),
        offers=cards,
        cheapest_key=products.offer_to_card(cheapest, [], quantities)["key"] if cheapest else None,
        savings=products.savings_text(viewed, offers),
    )


@bp.get("/recommendations")
@login_required
def recommendations_strip():
    """The "Recommended for you" carousel: favourites to buy again, then the hybrid recommendations (Step 15)."""
    if (limited := _limited()) is not None:
        return limited
    try:
        limit = max(1, min(int(request.args.get("limit", 12)), 24))
    except ValueError:
        return _error("limit must be a whole number.")
    return jsonify(results=recommender.carousel(current_user.UserId, limit=limit, provider=_provider()))


@bp.get("/savings")
@login_required
def savings():
    """The Dashboard "Potential Savings" card: what the active list could save with cheaper alternatives (Step 15)."""
    if (limited := _limited()) is not None:
        return limited
    recommender.refresh_active_alternatives(current_user, _provider())
    return jsonify(recommender.savings_report(current_user.UserId))


# ------------------------------------------------------------------------------------------------ the shopping list
@bp.get("/list")
@login_required
def get_list():
    return jsonify(shopping.list_state(current_user))


def _payload() -> dict:
    """The JSON body as a dict; anything else (a list, a number, no body) counts as empty."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _text(value) -> str:
    """A stripped string, or "" when the value is not text (an int barcode is not a barcode)."""
    return value.strip() if isinstance(value, str) else ""


@bp.patch("/list")
@login_required
def rename_list():
    shopping_list = shopping.rename_list(current_user, _text(_payload().get("title")))
    return jsonify(list={"id": shopping_list.ShoppingListId, "title": shopping_list.Title})


@bp.post("/list/items")
@login_required
def add_item():
    payload = _payload()
    barcode = _text(payload.get("barcode")) or None
    store_id = _text(payload.get("store_id")) or None
    store_name = _text(payload.get("store_name")) or None
    name = " ".join(_text(payload.get("name")).split()) or None
    if not (barcode or name) or not (store_id or store_name):
        return _error("Say which product and which store to add.")
    if budgets.get_active_budget(current_user.UserId) is None:  # before any price lookups
        raise shopping.ShoppingError("Create a budget first. Your shopping list belongs to your active budget.", 409)

    offer = shopping.resolve_offer(
        _provider(), current_user, barcode=barcode, store_id=store_id, store_name=store_name, name=name
    )
    if offer is None:
        return _error("That product is no longer available at that store.", 404)
    if not offer.in_stock:
        return _error("That product is out of stock at that store.", 409)

    result = shopping.add_product(current_user, offer)
    state = shopping.list_state(current_user, shopping_list=result.shopping_list, position=result.position)
    center = shopping.student_center(current_user)
    return jsonify(
        added=shopping.item_to_dict(result.item, center),
        created=result.created,
        quantity=result.item.ItemQuantity,
        **state,
    ), (201 if result.created else 200)


@bp.patch("/list/items/<item_id>")
@login_required
def change_quantity(item_id):
    raw = _payload().get("quantity")
    if isinstance(raw, bool) or not (
        isinstance(raw, int) or (isinstance(raw, str) and re.fullmatch(r"[0-9]{1,9}", raw.strip()))
    ):
        return _error("Quantity must be a whole number.")
    quantity = int(raw)
    item, position = shopping.set_quantity(current_user, item_id, quantity)
    return jsonify(shopping.list_state(current_user, position=position))


@bp.delete("/list/items/<item_id>")
@login_required
def delete_item(item_id):
    position = shopping.remove_item(current_user, item_id)
    return jsonify(shopping.list_state(current_user, position=position))


@bp.post("/list/items/<item_id>/replace")
@login_required
def replace_item(item_id):
    if (limited := _limited()) is not None:
        return limited
    item, position = shopping.replace_with_alternative(_provider(), current_user, item_id)
    state = shopping.list_state(current_user, position=position)
    return jsonify(
        replaced=shopping.item_to_dict(item, shopping.student_center(current_user)),
        message="Replaced with the cheaper alternative.",
        **state,
    )


# ------------------------------------------------------------------------------------------------ the shopping route
@bp.get("/route")
@login_required
def shopping_route():
    """The trip for the active list: home, the stores in visiting order, road distance and time, and the line to draw.

    Worked out from the student's own list (the browser sends no coordinates), and cached, because the public OSRM
    server is for light use.
    """
    budget = budgets.get_active_budget(current_user.UserId)
    items = budgets.list_items(budgets.get_active_list(budget))
    if not items:
        return _error("Your shopping list is empty.", 409)
    home = shopping.student_center(current_user)
    payload = routing.plan(home, build_invoice(items).groups)
    payload["home_known"] = current_user.Latitude is not None and current_user.Longitude is not None
    return jsonify(payload)


# ------------------------------------------------------------------------------------------------ weather
@bp.get("/weather")
@login_required
def weather():
    """Weather for the dashboard card: at the student's saved address (else Durban), or at ``?lat=&lng=`` (their live location)."""
    raw_lat, raw_lng = request.args.get("lat"), request.args.get("lng")
    if (raw_lat is None) != (raw_lng is None):
        return _error("Send both lat and lng, or neither.")
    saved = current_user.Latitude is not None and current_user.Longitude is not None
    if raw_lat is None:
        center = map_center_for(current_user.Latitude, current_user.Longitude)
        place = place_label(current_user.ResidentialAddress, "Your area" if saved else "Durban")
    else:
        try:
            lat, lng = float(raw_lat), float(raw_lng)
        except ValueError:
            return _error("lat and lng must be numbers.")
        if lat != lat or lng != lng or not is_valid_coordinate(lat, lng):
            return _error("lat or lng is out of range.")
        center, place = {"lat": lat, "lng": lng}, "Your location"
    if (limited := _limited()) is not None:
        return limited
    try:
        data = get_weather(center["lat"], center["lng"])
    except WeatherError:
        current_app.logger.warning("Weather lookup failed", exc_info=True)
        return _error("The weather is not available right now.", 502)
    return jsonify(place=place, **data)
