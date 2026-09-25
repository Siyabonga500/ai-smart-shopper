"""Notifications (Step 17): the bell.

Every event a student should hear about becomes a row in ``notifications``:

=====================  ==============================================================================  ===============
Type                   When                                                                            Announced
=====================  ==============================================================================  ===============
``budget_threshold``   the budget has reached 50, 75, 90 or 100% used                                  once per level per budget
``over_budget``        the active list costs more than the budget                                      again if it goes over again
``nsfas_reference``    this month's planned spending is above the R1 750 NSFAS reference               once per month
``price_drop``         an item on the list is now at least 10% cheaper at its store                    once per new price
``cheaper_alternative``  a cheaper way to buy an item on the list was found                            once per alternative
=====================  ==============================================================================  ===============

Nothing here ever blocks the student; these are hints.

*Watched item*: the brief says "a watched item drops 10%". This app has no watch-list, so an item counts as
watched while it is on the active shopping list (that is what the student is about to pay for).

Checking is cheap for the budget rules (numbers already in the database) and costly for the price rules (they
call the retail provider), so :func:`evaluate` runs the first kind every time and the second kind at most once
per ``NOTIFICATION_PRICE_CHECK_TTL`` per student. Nothing runs in the background: the checks run when the bell
is drawn or ``GET /api/notifications`` is called, and ``flask notify`` runs them for every student (a cron job).

De-duplication: each notification carries a ``DedupeKey`` that is unique per student. Trying to announce the
same event twice hits the unique index, so a double click or two browser tabs cannot produce two bell entries.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from flask import current_app, g, has_request_context
from flask import url_for as flask_url_for
from flask_login import current_user
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.extensions import cache, db
from app.models import Budget, ListItem, Notification, ShoppingList, SubBudget
from app.models.notification import (
    BUDGET_THRESHOLD,
    CHEAPER_ALTERNATIVE,
    NOTIFICATION_TYPES,
    NSFAS_REFERENCE,
    OVER_BUDGET,
    PRICE_DROP,
)
from app.services import budgets
from app.utils.dates import as_utc, today_sast, utcnow
from app.utils.formatters import format_date, format_zar
from app.utils.money import money, to_decimal

log = logging.getLogger(__name__)


def url_for(endpoint: str, **values) -> str:
    """A site-relative URL that also works outside a request (``flask notify`` runs from the command line)."""
    if has_request_context():
        return flask_url_for(endpoint, **values)
    return current_app.url_map.bind("localhost").build(endpoint, values)


# ------------------------------------------------------------------------------------------- writing
def notify(
    user_id: str, kind: str, title: str, message: str, dedupe_key: str | None = None, url: str | None = None
) -> Notification | None:
    """Add a notification. Returns ``None`` (and writes nothing) when ``dedupe_key`` was already announced.

    Uses a savepoint, so losing a race against another request only skips this one row and never rolls back
    the caller's other work. The caller commits.
    """
    if kind not in NOTIFICATION_TYPES:
        raise ValueError(f"Unknown notification type: {kind!r}")
    if dedupe_key and db.session.scalar(
        select(Notification.NotificationId).where(Notification.UserId == user_id, Notification.DedupeKey == dedupe_key)
    ):
        return None
    row = Notification(
        UserId=user_id, Type=kind, Title=title[:120], Message=message, DedupeKey=dedupe_key, Url=url, CreatedOn=utcnow()
    )
    try:
        with db.session.begin_nested():
            db.session.add(row)
    except IntegrityError:  # another request announced it a moment ago
        return None
    return row


def release(user_id: str, key_prefix: str) -> None:
    """Forget that ``key_prefix...`` was announced (sets the keys to NULL) so a return of the condition announces again.

    The notifications themselves stay in the student's history.
    """
    result = db.session.execute(
        update(Notification)
        .where(Notification.UserId == user_id, Notification.DedupeKey.like(f"{key_prefix}%"))
        .values(DedupeKey=None)
    )
    if result.rowcount:
        db.session.commit()


# --------------------------------------------------------------------------------- budget rules (cheap)
def _percent_label(percent: float) -> str:
    return f"{percent:.0f}%"


def check_budget(user_id: str, now: datetime | None = None) -> list[Notification]:
    """Threshold, over-budget and monthly NSFAS notifications from numbers already in the database."""
    created: list[Notification | None] = []
    budget = budgets.get_active_budget(user_id)
    if budget is not None:
        shopping_list = budgets.get_active_list(budget)
        pos = budgets.position(budget, shopping_list)
        created.append(_check_over_budget(user_id, budget, shopping_list, pos))
        created.extend(_check_category_over_budget(user_id, budget))
        if not pos.is_over:
            created.append(_check_thresholds(user_id, budget, pos))
    created.append(_check_nsfas_month(user_id, now))
    return [row for row in created if row is not None]


def _check_over_budget(user_id, budget: Budget, shopping_list: ShoppingList | None, pos) -> Notification | None:
    list_key = shopping_list.ShoppingListId if shopping_list is not None else budget.BudgetId
    if not pos.is_over:
        release(user_id, f"over:{list_key}")  # fixed: announce again if it goes over again
        return None
    return notify(
        user_id,
        OVER_BUDGET,
        "Your list is over budget",
        f"Your shopping list is {format_zar(pos.over_by)} over {budget.Title}. You can keep adding items, but you "
        "will need to remove some or raise your budget before you can proceed to the summary.",
        dedupe_key=f"over:{list_key}",
        url=url_for("shopping.index"),
    )


def _check_category_over_budget(user_id: str, budget: Budget) -> list[Notification]:
    created: list[Notification] = []
    sub_budgets = db.session.scalars(select(SubBudget).where(SubBudget.BudgetId == budget.BudgetId))
    for sub_budget in sub_budgets:
        key = f"category-over:{budget.BudgetId}:{sub_budget.SubBudgetId}"
        over_by = money(sub_budget.UsedAmount) - money(sub_budget.AllocatedAmount)
        if over_by <= 0:
            release(user_id, key)
            continue
        row = notify(
            user_id,
            OVER_BUDGET,
            f"{sub_budget.Category} budget exceeded",
            f"You are {format_zar(over_by)} over your {sub_budget.Category} budget. Reduce or replace items before completing your shopping.",
            dedupe_key=key,
            url=url_for("shopping.index"),
        )
        if row is not None:
            created.append(row)
    return created


def _check_thresholds(user_id: str, budget: Budget, pos) -> Notification | None:
    """Announce the highest of 50/75/90/100% reached, once per level per budget (never a lower level afterwards)."""
    levels = sorted(current_app.config["NOTIFICATION_BUDGET_THRESHOLDS"])
    reached = [level for level in levels if pos.percent_used >= level]
    if not reached:
        return None
    top = reached[-1]
    prefix = f"threshold:{budget.BudgetId}:"
    announced = [
        int(key.rsplit(":", 1)[1])
        for key in db.session.scalars(
            select(Notification.DedupeKey).where(
                Notification.UserId == user_id, Notification.DedupeKey.like(f"{prefix}%")
            )
        )
        if key.rsplit(":", 1)[1].isdigit()
    ]
    if announced and top <= max(announced):
        return None
    if top >= 100:
        title, message = "Budget fully used", (
            f"You have used all of {budget.Title} ({format_zar(pos.used)} of {format_zar(pos.total)}). "
            "Anything more you add will put you over budget."
        )
    else:
        title, message = f"{top}% of your budget used", (
            f"You have used {_percent_label(pos.percent_used)} of {budget.Title}: {format_zar(pos.used)} of "
            f"{format_zar(pos.total)}, with {format_zar(pos.remaining)} left."
        )
    return notify(user_id, BUDGET_THRESHOLD, title, message, dedupe_key=f"{prefix}{top}", url=url_for("budget.index"))


def _check_nsfas_month(user_id: str, now: datetime | None) -> Notification | None:
    reference = to_decimal(current_app.config["NSFAS_MONTHLY_ALLOWANCE"])
    today = today_sast(now)
    month = next((m for m in budgets.monthly_performance(user_id, today) if m.key == (today.year, today.month)), None)
    if month is None or month.used <= reference:
        return None
    over = month.used - reference
    return notify(
        user_id,
        NSFAS_REFERENCE,
        "Above the monthly NSFAS reference",
        f"Your budgets this month add up to {format_zar(month.used)}, which is {format_zar(over)} above the "
        f"{format_zar(reference)} NSFAS meal allowance.",
        dedupe_key=f"nsfas:{today.year}-{today.month:02d}",
        url=url_for("budget.index"),
    )


# --------------------------------------------------------------------------------- price rules (costly)
def check_prices(user, provider=None) -> list[Notification]:
    """Price drops and cheaper alternatives for the items on the active list. Asks the retail provider."""
    from app.services import shopping
    from app.services.retail_api import RetailAPIError, RetailConfigError, get_retail_provider

    budget = budgets.get_active_budget(user.UserId)
    items = budgets.list_items(budgets.get_active_list(budget)) if budget else []
    if not items:
        return []
    try:
        provider = provider or get_retail_provider()
        created = _price_drops(user, items, provider)
        shopping.refresh_alternatives(items, provider, user)
    except RetailAPIError:
        log.warning("Price check for %s failed; will try again later", user.UserId, exc_info=True)
        return []
    except RetailConfigError as exc:  # a missing API key must not break page loads
        log.warning("Price check skipped: %s", exc)
        return []
    created += _cheaper_alternatives(user.UserId, items)
    return created


def _price_drops(user, items: list[ListItem], provider) -> list[Notification]:
    from app.services import shopping

    threshold = Decimal(str(current_app.config["NOTIFICATION_PRICE_DROP"]))
    center = shopping.student_center(user)
    radius = current_app.config["SEARCH_MAX_RADIUS_KM"]
    created = []
    for item in items:
        unit = to_decimal(item.UnitCost)
        if not item.BarCode or not item.StoreName or unit <= 0:
            continue
        offer = next(
            (
                o
                for o in provider.get_offers_by_barcode(item.BarCode, center["lat"], center["lng"], radius)
                if o.store_name == item.StoreName and o.in_stock
            ),
            None,
        )
        if offer is None:
            continue
        now_price = money(offer.price)
        drop = (unit - now_price) / unit
        if drop < threshold:
            continue
        row = notify(
            user.UserId,
            PRICE_DROP,
            f"Price drop: {item.ItemName}"[:120],
            f"{item.ItemName} is now {format_zar(now_price)} at {item.StoreName}, down from "
            f"{format_zar(unit)} ({drop * 100:.0f}% cheaper). The price on your list has not changed; "
            "remove the item and add it again to use the new price.",
            dedupe_key=f"drop:{item.ListItemId}:{now_price}",
            url=url_for("shopping.index"),
        )
        if row is not None:
            created.append(row)
    return created


def _cheaper_alternatives(user_id: str, items: list[ListItem]) -> list[Notification]:
    from app.services import shopping

    created = []
    limit = current_app.config["NOTIFICATION_MAX_NEW_ALTERNATIVES"]
    for item in items:
        alt = shopping.alternative_of(item)
        if not alt:
            continue
        saving = to_decimal(alt.get("saving"))
        if saving <= 0:
            continue
        row = notify(
            user_id,
            CHEAPER_ALTERNATIVE,
            f"Cheaper option for {item.ItemName}"[:120],
            f"{alt.get('name') or item.ItemName} is {format_zar(to_decimal(alt.get('price')))} at "
            f"{alt.get('store_name')}, {format_zar(saving)} less than the {format_zar(to_decimal(item.UnitCost))} "
            f"you would pay at {item.StoreName}.",
            dedupe_key=f"alt:{item.ListItemId}:{alt.get('store_name')}:{alt.get('price')}",
            url=url_for("shopping.index", alternatives=1),
        )
        if row is not None:
            created.append(row)
            if len(created) >= limit:
                break
    return created


# ------------------------------------------------------------------------------------------ evaluating
def evaluate(user, prices: bool = False, provider=None, now: datetime | None = None) -> list[Notification]:
    """Run the rules for ``user`` and commit. ``prices`` also runs the price rules (throttled per student)."""
    created = check_budget(user.UserId, now)
    if prices and _price_check_due(user.UserId):
        created += check_prices(user, provider)
    if created:
        db.session.commit()
    return created


def _price_check_due(user_id: str) -> bool:
    """True at most once per ``NOTIFICATION_PRICE_CHECK_TTL`` seconds per student."""
    key = f"notif-price-check:{user_id}"
    if cache.get(key):
        return False
    cache.set(key, 1, timeout=current_app.config["NOTIFICATION_PRICE_CHECK_TTL"])
    return True


def evaluate_everyone(provider=None) -> int:
    """For ``flask notify``: run every rule for every active student. Returns how many notifications were created."""
    from app.models import User

    total = 0
    for user in db.session.scalars(select(User).where(User.IsActive.is_(True))):
        try:
            total += len(evaluate(user, prices=True, provider=provider))
        except Exception:  # one student's failure must not stop the rest
            db.session.rollback()
            log.exception("Notification check failed for %s", user.UserId)
    return total


# ------------------------------------------------------------------------------------------- reading
def _unread_first():
    return (Notification.IsRead.asc(), Notification.CreatedOn.desc(), Notification.NotificationId.desc())


def list_for_user(user_id: str, limit: int = 20, unread_only: bool = False) -> list[Notification]:
    """Unread first, newest first inside each group."""
    query = select(Notification).where(Notification.UserId == user_id)
    if unread_only:
        query = query.where(Notification.IsRead.is_(False))
    return list(db.session.scalars(query.order_by(*_unread_first()).limit(limit)))


def unread_count(user_id: str) -> int:
    return (
        db.session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.UserId == user_id, Notification.IsRead.is_(False))
        )
        or 0
    )


def get_owned(user_id: str, notification_id: str) -> Notification | None:
    """The notification, but only when it belongs to ``user_id`` (other students' ids look like missing ones)."""
    row = db.session.get(Notification, notification_id)
    return row if row is not None and row.UserId == user_id else None


def mark_read(user_id: str, notification_id: str) -> Notification | None:
    row = get_owned(user_id, notification_id)
    if row is not None and not row.IsRead:
        row.IsRead = True
        db.session.commit()
    return row


def mark_all_read(user_id: str) -> int:
    result = db.session.execute(
        update(Notification).where(Notification.UserId == user_id, Notification.IsRead.is_(False)).values(IsRead=True)
    )
    db.session.commit()
    return result.rowcount or 0


def time_ago(when: datetime | None, now: datetime | None = None) -> str:
    """ "just now", "5 min ago", "3 h ago", "2 days ago", then the date."""
    when = as_utc(when)
    if when is None:
        return ""
    seconds = max(0, int(((now or utcnow()) - when).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    if seconds < 7 * 86400:
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"
    return format_date(when)


def to_dict(row: Notification, now: datetime | None = None) -> dict:
    return {
        "id": row.NotificationId,
        "title": row.Title,
        "message": row.Message,
        "type": row.Type,
        "level": row.level,
        "icon": row.icon,
        "is_read": bool(row.IsRead),
        "url": row.Url,
        "created_on": as_utc(row.CreatedOn).isoformat() if row.CreatedOn else None,
        "ago": time_ago(row.CreatedOn, now),
    }


def payload(user_id: str, limit: int = 20, unread_only: bool = False) -> dict:
    return {
        "unread": unread_count(user_id),
        "notifications": [to_dict(row) for row in list_for_user(user_id, limit, unread_only)],
    }


# --------------------------------------------------------------------------------- the bell in a page
def bell() -> dict:
    """What the bell draws for the signed-in student: ``{"unread": n, "items": [...]}``. Worked out once per request.

    The budget rules run first, so a page loaded straight after adding an item already shows its alert.
    """
    if not has_request_context() or not current_user.is_authenticated:
        return {"unread": 0, "items": []}
    if not hasattr(g, "_bell"):
        user = current_user._get_current_object()
        try:
            evaluate(user)
        except Exception:  # a broken rule must never break the page
            db.session.rollback()
            log.exception("Notification check failed for %s", user.UserId)
        limit = current_app.config["NOTIFICATION_BELL_LIMIT"]
        g._bell = {
            "unread": unread_count(user.UserId),
            "items": [to_dict(row) for row in list_for_user(user.UserId, limit)],
        }
    return g._bell


# ------------------------------------------------------------------------------- list badge (unchanged)
def list_item_count() -> int:
    """How many items are on the active list (the red badge on the List tab)."""
    if not has_request_context() or not current_user.is_authenticated:
        return 0
    if not hasattr(g, "_list_count"):
        budget = budgets.get_active_budget(current_user.UserId)
        shopping_list = budgets.get_active_list(budget)
        g._list_count = len(budgets.list_items(shopping_list)) if shopping_list else 0
    return g._list_count


def reset_request_cache() -> None:
    """Call after a request changes budget/list data and then renders a page (so the badges are not stale)."""
    if has_request_context():
        for name in ("_bell", "_list_count"):
            g.pop(name, None)
