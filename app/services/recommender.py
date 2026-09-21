"""The recommendation engine (Step 15): "Recommended for You", cheaper alternatives and potential savings.

Everything here is about the signed-in student's own data plus, for the collaborative part, *anonymous* purchase
counts of other students (barcodes only: no names, no amounts, no dates ever leave this module).

``recently_purchased``      what the student buys most often (last 90 days), tagged Recently / Lastly purchased.
``recommend_for_user``      hybrid ranking of products the student has *not* bought lately:

    1. content-based   the categories of their Preferences and of what they bought, plus preferred brands / stores;
    2. collaborative   products bought by students whose purchase history looks like theirs (cosine similarity on
                       the student x barcode matrix, computed in plain Python: the matrix is tiny and sparse);
    3. price-aware     a boost for a product that is on promotion (well below its own recent prices), one that is
                       much cheaper at one store than another, and for the cheaper alternative of something that is
                       already on the active list.

``cheaper_alternatives``    the same barcode at other stores, cheapest three.
``potential_savings``       what the active list could save with the cheapest alternatives (the number the
                            Dashboard shows).

Every recommendation carries ``reasons`` (why it is shown) and a short ``tag``. Nothing is a black box.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Mapping

from flask import current_app
from sqlalchemy import func, select

from app.extensions import db
from app.models import ListItem, User
from app.services import budgets, shopping
from app.services.products import cheapest_in_stock, offer_to_card
from app.services.recommendations import preference_profile, purchase_tag
from app.services.retail_api import (
    Product,
    RetailAPIError,
    RetailConfigError,
    RetailProvider,
    get_retail_provider,
    group_by_barcode,
)
from app.utils.dates import as_utc, utcnow
from app.utils.formatters import format_zar
from app.utils.money import ZERO, money, to_decimal

MAX_NEIGHBOURS = 25  # the most similar students whose purchases are used
MAX_CANDIDATES_PER_CATEGORY = 8
PROMO_CHECKS = 20  # price histories looked at per request (each is one provider call)


# ------------------------------------------------------------------------------------------------ small helpers
def _item_key(barcode: str | None, name: str | None) -> str:
    return (barcode or (name or "")).strip().casefold()


def _window_start(now: datetime | None = None) -> datetime:
    return (now or utcnow()) - timedelta(days=current_app.config["RECOMMENDER_WINDOW_DAYS"])


def _provider(provider: RetailProvider | None) -> RetailProvider:
    return provider or get_retail_provider()


def _purchased_rows(user_id: str, since: datetime) -> list[ListItem]:
    return list(
        db.session.scalars(
            select(ListItem)
            .where(ListItem.UserId == user_id, ListItem.IsPurchased.is_(True), ListItem.PurchasedDate >= since)
            .order_by(ListItem.PurchasedDate.desc(), ListItem.ListItemId)
        )
    )


# ------------------------------------------------------------------------------------------------ recently purchased
@dataclass(frozen=True)
class PurchasedItem:
    key: str  # barcode, or the lower-cased name when the product had no barcode
    barcode: str | None
    name: str
    category: str | None
    times: int  # how many times it was bought in the window
    quantity: int  # how many units in total
    last_purchased: datetime
    tag: str  # "Recently purchased" or "Lastly purchased"

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "barcode": self.barcode,
            "name": self.name,
            "category": self.category,
            "times": self.times,
            "quantity": self.quantity,
            "last_purchased": self.last_purchased.isoformat(),
            "tag": self.tag,
        }


def recently_purchased(user_id: str, limit: int = 10, now: datetime | None = None) -> list[PurchasedItem]:
    """The student's most frequently bought products of the last 90 days, most frequent first (ties: bought most
    recently). Products are told apart by barcode, else by name. The tag is ``Recently purchased`` when the last
    purchase was within ``RECENT_PURCHASE_DAYS`` days and ``Lastly purchased`` when it was longer ago."""
    now = now or utcnow()
    groups: dict[str, dict] = {}
    for item in _purchased_rows(user_id, _window_start(now)):
        key = _item_key(item.BarCode, item.ItemName)
        when = as_utc(item.PurchasedDate) or now
        group = groups.setdefault(
            key,
            {
                "barcode": item.BarCode,
                "name": item.ItemName,
                "category": item.Category,
                "times": 0,
                "quantity": 0,
                "last": when,
            },
        )
        group["times"] += 1
        group["quantity"] += int(item.ItemQuantity or 0)
        if when > group["last"]:
            group["last"] = when
    ranked = sorted(
        groups.items(), key=lambda pair: (-pair[1]["times"], -pair[1]["last"].timestamp(), pair[1]["name"].casefold())
    )
    return [
        PurchasedItem(
            key,
            g["barcode"],
            g["name"],
            g["category"],
            g["times"],
            g["quantity"],
            g["last"],
            purchase_tag(g["last"], now),
        )
        for key, g in ranked[: max(0, limit)]
    ]


# ------------------------------------------------------------------------------------------------ collaborative part
def cosine(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """Cosine similarity of two sparse vectors (``{barcode: times bought}``); 0 when either is empty."""
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(value * large.get(key, 0.0) for key, value in small.items())
    if dot <= 0:
        return 0.0
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


def purchase_vectors(user_id: str, since: datetime) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """``(the student's vector, {other student: vector})``: rows of the student x barcode matrix that matter.

    Only students who bought at least one of the same barcodes are loaded (everyone else has similarity 0), and
    accounts an admin has deactivated are left out."""

    def counts(condition):
        return db.session.execute(
            select(ListItem.UserId, ListItem.BarCode, func.count(ListItem.ListItemId))
            .join(User, User.UserId == ListItem.UserId)
            .where(
                ListItem.IsPurchased.is_(True),
                ListItem.PurchasedDate >= since,
                ListItem.BarCode.is_not(None),
                User.IsActive.is_(True),
                condition,
            )
            .group_by(ListItem.UserId, ListItem.BarCode)
        ).all()

    mine = {barcode: float(n) for _, barcode, n in counts(ListItem.UserId == user_id)}
    if not mine:
        return {}, {}
    neighbours_ids = list(
        db.session.scalars(
            select(ListItem.UserId)
            .join(User, User.UserId == ListItem.UserId)
            .where(
                ListItem.IsPurchased.is_(True),
                ListItem.PurchasedDate >= since,
                ListItem.UserId != user_id,
                ListItem.BarCode.in_(list(mine)),
                User.IsActive.is_(True),
            )
            .distinct()
            .limit(500)
        )
    )
    others: dict[str, dict[str, float]] = defaultdict(dict)
    if neighbours_ids:
        for other_id, barcode, n in counts(ListItem.UserId.in_(neighbours_ids)):
            others[other_id][barcode] = float(n)
    return mine, dict(others)


def collaborative_scores(user_id: str, now: datetime | None = None) -> dict[str, float]:
    """``{barcode: 0..1}`` for products the student has not bought, from students with a similar history.

    A product's score is the similarity-weighted share of the (up to ``MAX_NEIGHBOURS``) most similar students who
    bought it: 1.0 would mean every one of them did. Students with similarity 0 never count."""
    mine, others = purchase_vectors(user_id, _window_start(now))
    similar = sorted(
        ((cosine(mine, vector), other, vector) for other, vector in others.items()), key=lambda t: (-t[0], t[1])
    )
    similar = [t for t in similar if t[0] > 0][:MAX_NEIGHBOURS]
    total = sum(sim for sim, _, _ in similar)
    if not total:
        return {}
    scores: dict[str, float] = defaultdict(float)
    for sim, _, vector in similar:
        for barcode in vector:
            if barcode not in mine:
                scores[barcode] += sim / total
    return dict(scores)


# ------------------------------------------------------------------------------------------------ content-based part
def category_signals(user_id: str, now: datetime | None = None) -> tuple[Counter, set[str]]:
    """``(purchases per category in the window, the categories ticked in Preferences)``, both in canonical spelling."""
    canonical = {c.casefold(): c for c in current_app.config["BUDGET_CATEGORIES"]}
    bought: Counter = Counter()
    for item in _purchased_rows(user_id, _window_start(now)):
        category = canonical.get((item.Category or "").casefold())
        if category:
            bought[category] += 1
    preferred = {canonical[value] for value in preference_profile(user_id).categories if value in canonical}
    return bought, preferred


def category_weights(user_id: str, now: datetime | None = None) -> dict[str, float]:
    """``{category: 0..1}``: how much the student cares about each category, from what they bought (their share of
    the student's purchases, worth up to 0.6) and what they ticked in Preferences (worth 0.4)."""
    bought, preferred = category_signals(user_id, now)
    total = sum(bought.values())
    weights = {category: 0.6 * count / total for category, count in bought.items()}
    for category in preferred:
        weights[category] = weights.get(category, 0.0) + 0.4
    return weights


# ------------------------------------------------------------------------------------------------ price-aware part
def promotion_discount(provider: RetailProvider, offer: Product) -> float:
    """How far below its own recent prices this offer is (0.15 = 15% off), or 0 when it is not on promotion.

    Retail feeds do not flag promotions, so a promotion is *derived*: the price now is at least
    ``RECOMMENDER_PROMO_DROP`` (10%) under the median of that store's earlier prices for the barcode. Providers
    with no price history simply never produce a promotion."""
    if not offer.barcode or not offer.store_id:
        return 0.0
    try:
        history = provider.get_price_history(offer.barcode, offer.store_id)
    except (RetailAPIError, RetailConfigError):
        return 0.0
    earlier = [float(point.price) for point in history[:-1]]
    if len(earlier) < 3:
        return 0.0
    typical = statistics.median(earlier)
    now = float(offer.price)
    if typical <= 0 or now >= typical:
        return 0.0
    drop = (typical - now) / typical
    return drop if drop >= current_app.config["RECOMMENDER_PROMO_DROP"] else 0.0


def _spread(offers: list[Product]) -> tuple[Decimal, Product | None]:
    """``(how much dearer the dearest in-stock store is than the cheapest, that dearest offer)``."""
    stocked = [o for o in offers if o.in_stock]
    if len(stocked) < 2:
        return ZERO, None
    cheapest, dearest = min(stocked, key=lambda o: o.price), max(stocked, key=lambda o: o.price)
    return money(dearest.price) - money(cheapest.price), dearest


# ------------------------------------------------------------------------------------------------ the hybrid
@dataclass
class Recommendation:
    offer: Product
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    tag: str = ""
    content: float = 0.0
    collaborative: float = 0.0
    price: float = 0.0
    saving: Decimal = ZERO

    def to_card(self, quantities: dict | None = None) -> dict:
        card = offer_to_card(self.offer, quantities=quantities, tag=self.tag)
        card["reasons"] = list(self.reasons)
        card["score"] = round(self.score, 3)
        return card


@dataclass
class _Candidate:
    offers: list[Product]
    content: float = 0.0
    collaborative: float = 0.0
    from_list: Decimal = ZERO  # saving on an item already on the list
    list_item: str | None = None
    category: str | None = None
    brand_match: bool = False
    store_match: bool = False


def _cheapest_or_none(offers: list[Product]) -> Product | None:
    return cheapest_in_stock([o for o in offers if o.in_stock])


def recommend_for_user(
    user_id: str, limit: int = 12, provider: RetailProvider | None = None, now: datetime | None = None
) -> list[Recommendation]:
    """Products to suggest, best first (at most ``limit``). Products bought in the last 90 days or already on the
    active list are never suggested (they are covered by :func:`recently_purchased` and by the list itself).

    Score = 0.4 x content + 0.4 x collaborative + 0.2 x price (weights in ``RECOMMENDER_WEIGHTS``), each part scaled
    to 0..1 across the candidates. A student with no purchases and no preferences gets "deals near you" only."""
    user = db.session.get(User, user_id)
    if user is None or limit <= 0:
        return []
    cfg = current_app.config
    provider = _provider(provider)
    now = now or utcnow()
    center = shopping.student_center(user)
    radius = cfg["SEARCH_MAX_RADIUS_KM"]
    profile = preference_profile(user_id)

    excluded = {p.key for p in recently_purchased(user_id, limit=10_000, now=now)}
    budget = budgets.get_active_budget(user_id)
    active_items = budgets.list_items(budgets.get_active_list(budget)) if budget else []
    on_list = {_item_key(i.BarCode, i.ItemName) for i in active_items}

    candidates: dict[str, _Candidate] = {}
    weights: dict[str, float] = {}
    bought_categories: set[str] = set()
    cold_start = False

    def candidate_for(offers: list[Product]) -> _Candidate | None:
        first = offers[0] if offers else None
        if first is None:
            return None
        key = _item_key(first.barcode, first.name)
        if key in excluded or key in on_list or not any(o.in_stock for o in offers):
            return None
        return candidates.setdefault(key, _Candidate(offers=offers, category=first.category))

    try:
        # 1. content-based: browse the categories the student cares about
        weights = category_weights(user_id, now)
        bought_categories = set(category_signals(user_id, now)[0])
        browse = sorted(weights, key=lambda c: (-weights[c], c))[:3] or []
        cold_start = not browse
        if cold_start:
            browse = list(cfg["BUDGET_CATEGORIES"])
        for category in browse:
            groups = group_by_barcode(provider.search_products("", center["lat"], center["lng"], radius, category))
            ranked = []
            for group in groups:
                offers = list(group.offers)
                best = _cheapest_or_none(offers)
                if best is None:
                    continue
                brand = bool(best.brand and best.brand.casefold() in profile.brands)
                store = any((o.retailer or "").casefold() in profile.stores for o in offers if o.in_stock)
                ranked.append((-(1 + 0.5 * brand + 0.25 * store), best.price, best.name, offers, brand, store))
            for _, _, _, offers, brand, store in sorted(ranked, key=lambda r: r[:3])[:MAX_CANDIDATES_PER_CATEGORY]:
                cand = candidate_for(offers)
                if cand is None:
                    continue
                cand.content = max(cand.content, weights.get(category, 0.0) * (1 + 0.5 * brand + 0.25 * store) / 1.75)
                cand.brand_match, cand.store_match = brand, store

        # 2. collaborative: what similar students bought
        collab = collaborative_scores(user_id, now)
        for barcode, score in sorted(collab.items(), key=lambda kv: (-kv[1], kv[0]))[:15]:
            cand = candidate_for(list(provider.get_offers_by_barcode(barcode, center["lat"], center["lng"], radius)))
            if cand is not None:
                cand.collaborative = score

        # 3. price-aware: the cheaper alternative of something already on the list
        for item in active_items:
            alt = shopping.alternative_of(item)
            if not alt or not alt.get("barcode"):
                continue
            offers = list(provider.get_offers_by_barcode(alt["barcode"], center["lat"], center["lng"], radius))
            if not any(o.in_stock and o.store_name == alt.get("store_name") for o in offers):
                continue
            first = offers[0]
            key = _item_key(first.barcode, first.name)
            cand = candidates.setdefault(key, _Candidate(offers=offers, category=first.category))
            cand.from_list = max(cand.from_list, max(ZERO, to_decimal(item.UnitCost) - to_decimal(alt.get("price"))))
            cand.list_item = item.ItemName
    except (RetailAPIError, RetailConfigError):
        current_app.logger.warning("Recommendations were cut short by a retail API error", exc_info=True)

    if not candidates:
        return []

    # price-aware scores
    max_content = max((c.content for c in candidates.values()), default=0.0) or 1.0
    max_collab = max((c.collaborative for c in candidates.values()), default=0.0) or 1.0
    results: list[Recommendation] = []
    pre = sorted(
        candidates.values(), key=lambda c: (-(c.content / max_content + c.collaborative / max_collab), c.offers[0].name)
    )
    weights_cfg = cfg["RECOMMENDER_WEIGHTS"]
    for index, cand in enumerate(pre):
        best = _cheapest_or_none(cand.offers)
        if best is None:
            continue
        reasons: list[str] = []
        price = 0.0
        spread, dearest = _spread(cand.offers)
        if cand.from_list > 0:
            price = 1.0
            reasons.append(f"Cheaper than {cand.list_item} on your list: save {format_zar(cand.from_list)}")
        else:
            if spread > 0 and dearest is not None:
                price = max(price, min(float(spread / money(dearest.price)) / 0.3, 1.0) * 0.6)
                reasons.append(f"Save {format_zar(spread)} at {best.store_name} compared with {dearest.store_name}")
            if index < PROMO_CHECKS:
                discount = promotion_discount(provider, best)
                if discount > 0:
                    price = max(price, min(discount / 0.3, 1.0))
                    reasons.insert(0, f"On promotion: {round(discount * 100)}% below its usual price")
        if cand.content > 0:
            reasons.append(
                f"Matches your {cand.category} shopping"
                if cand.category in bought_categories
                else "Matches your preferences"
            )
        if cand.brand_match:
            reasons.append("A brand you prefer")
        if cand.collaborative > 0:
            reasons.append("Bought by students with a similar shopping history")
        if cand.content <= 0 and cand.collaborative <= 0 and cand.from_list <= 0 and not (cold_start and price > 0):
            continue  # price alone never recommends (except "deals near you")
        score = (
            weights_cfg["content"] * cand.content / max_content
            + weights_cfg["collaborative"] * cand.collaborative / max_collab
            + weights_cfg["price"] * price
        )
        if cand.from_list > 0:
            tag = "Cheaper alternative"
        elif any(r.startswith("On promotion") for r in reasons):
            tag = "On promotion"
        elif cand.collaborative > 0 and cand.collaborative / max_collab >= cand.content / max_content:
            tag = "Popular with similar students"
        elif cand.content > 0:
            tag = (
                f"Because you buy {cand.category}" if cand.category in bought_categories else "Matches your preferences"
            )
        else:
            tag = "Deal near you"
        results.append(
            Recommendation(
                best,
                score,
                reasons,
                tag,
                cand.content,
                cand.collaborative,
                price,
                cand.from_list if cand.from_list > 0 else spread,
            )
        )
    results.sort(key=lambda r: (-r.score, r.offer.price, r.offer.name))
    return results[:limit]


def carousel(user_id: str, limit: int = 12, provider: RetailProvider | None = None) -> list[dict]:
    """What the "Recommended for you" carousel shows: up to four favourites to buy again (tagged Recently / Lastly
    purchased), then the hybrid recommendations. Each card says why in ``tag`` and ``reasons``."""
    provider = _provider(provider)
    user = db.session.get(User, user_id)
    if user is None:
        return []
    center = shopping.student_center(user)
    radius = current_app.config["SEARCH_MAX_RADIUS_KM"]
    budget = budgets.get_active_budget(user_id)
    quantities = {}
    if budget:
        from app.services.products import in_list_quantities

        quantities = in_list_quantities(budgets.list_items(budgets.get_active_list(budget)))
    cards: list[dict] = []
    try:
        for purchase in recently_purchased(user_id, limit=4):
            if purchase.barcode:
                offers = provider.get_offers_by_barcode(purchase.barcode, center["lat"], center["lng"], radius)
            else:
                offers = [
                    o
                    for o in provider.search_products(purchase.name, center["lat"], center["lng"], radius)
                    if o.name.casefold() == purchase.name.casefold()
                ]
            best = _cheapest_or_none(list(offers))
            if best is not None and len(cards) < limit:
                card = offer_to_card(best, quantities=quantities, tag=purchase.tag)
                card["reasons"] = [
                    f"You bought this {purchase.times} time{'s' if purchase.times != 1 else ''} in the last 90 days"
                ]
                cards.append(card)
    except (RetailAPIError, RetailConfigError):
        current_app.logger.warning("Buy-again suggestions were cut short by a retail API error", exc_info=True)
    seen = {c["key"] for c in cards}
    for rec in recommend_for_user(user_id, limit=limit, provider=provider):
        card = rec.to_card(quantities)
        if card["key"] not in seen and len(cards) < limit:
            seen.add(card["key"])
            cards.append(card)
    return cards


# ------------------------------------------------------------------------------------------------ cheaper alternatives
def cheaper_alternatives(list_item: ListItem, limit: int = 3, provider: RetailProvider | None = None) -> list[Product]:
    """The same barcode at *other* stores, for less than the item costs now: the cheapest ``limit`` (three), cheapest
    first (ties: nearer store, then name). In-stock offers only. An item with no barcode has no alternatives here."""
    if not list_item.BarCode or limit <= 0:
        return []
    user = list_item.user or db.session.get(User, list_item.UserId)
    center = shopping.student_center(user)
    radius = current_app.config["SEARCH_MAX_RADIUS_KM"]
    offers = _provider(provider).get_offers_by_barcode(list_item.BarCode, center["lat"], center["lng"], radius)
    current = money(list_item.UnitCost)
    cheaper = [
        o
        for o in offers
        if o.in_stock and money(o.price) < current and (o.store_name or "") != (list_item.StoreName or "")
    ]
    cheaper.sort(
        key=lambda o: (money(o.price), o.distance_km if o.distance_km is not None else 999.0, o.store_name or "")
    )
    return cheaper[:limit]


# ------------------------------------------------------------------------------------------------ potential savings
def _active_items(user_id: str) -> list[ListItem]:
    budget = budgets.get_active_budget(user_id)
    return budgets.list_items(budgets.get_active_list(budget)) if budget else []


def refresh_active_alternatives(user: User, provider: RetailProvider | None = None) -> list[ListItem]:
    """The active list's items, with their cheaper alternatives re-checked when stale (best effort)."""
    items = _active_items(user.UserId)
    if items:
        try:
            shopping.refresh_alternatives(items, _provider(provider), user)
        except (RetailAPIError, RetailConfigError):
            current_app.logger.warning("Could not refresh alternatives", exc_info=True)
    return items


def potential_savings(user_id: str) -> Decimal:
    """What the active list could save: the sum over its items of (price now - cheapest alternative) x quantity.

    This is the figure on the Dashboard and the Shopping List page (one source of truth): the cheapest in-stock
    offer of the same barcode elsewhere, else a similar cheaper product; an alternative must save at least R1."""
    return shopping.potential_savings(_active_items(user_id))


def savings_report(user_id: str) -> dict:
    """``potential_savings`` with the rows behind it, for ``GET /api/savings``."""
    items = _active_items(user_id)
    rows = []
    for item in items:
        alt = shopping.alternative_of(item)
        saving = shopping.item_saving(item)
        if not alt or saving <= 0:
            continue
        rows.append(
            {
                "item_id": item.ListItemId,
                "name": item.ItemName,
                "quantity": item.ItemQuantity,
                "store_name": item.StoreName,
                "current_price": str(money(item.UnitCost)),
                "cheapest": {
                    "name": alt.get("name"),
                    "price": alt.get("price"),
                    "store_name": alt.get("store_name"),
                    "same_product": bool(alt.get("same_product")),
                },
                "saving": str(money(saving)),
            }
        )
    total = shopping.potential_savings(items)
    return {
        "total": str(total),
        "formatted": format_zar(total),
        "count": len(rows),
        "items": rows,
        "item_count": len(items),
        "has_list": bool(items),
    }
