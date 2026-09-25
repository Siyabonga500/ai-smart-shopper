"""Demo data for ``flask seed-demo``: three students with a live budget and list and six months of shopping history.

The prices come from the built-in :class:`~app.services.retail_api.MockProvider` (the same offline catalogue the app
uses when ``RETAIL_PROVIDER=mock``), so the demo needs no API keys and the same seed always gives the same data.

The three students show the three situations the app is built around:

* Thabo: a normal month. Individual category budgets, a list that is comfortably inside them.
* Nomvula: one Combined Budget and a list close to the limit (the amber "almost there" state).
* Sipho: a budget of the full R1 750 NSFAS allowance and a list that is over budget, so "Proceed to Summary" is
  disabled while adding items is still allowed.

Every demo account signs in with :data:`DEMO_PASSWORD`. Never seed them into a public production site.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.data.dut_residences import get_residence
from app.data.mock_products import CATALOGUE
from app.extensions import db
from app.models import ListItem, ShoppingList, SubBudget, User
from app.services import budgets
from app.services.budgets import COMBINED
from app.services.retail_api import MockProvider, Product, RetailProvider
from app.utils.dates import MONTHS_LONG, previous_months, today_sast, utcnow
from app.utils.money import money

DEMO_PASSWORD = "Demo!Pass123"
HISTORY_MONTHS = 6
SAST = timezone(timedelta(hours=2))
D = Decimal


@dataclass(frozen=True)
class Persona:
    key: str
    first: str
    last: str
    email: str
    cell: str
    residence: str  # a key from app.data.dut_residences
    lat: float  # approximate position of the residence
    lng: float
    monthly: tuple[tuple[str, int], ...]  # the budget each past month
    current: tuple[tuple[str, int], ...]  # this month's budget
    list_fill: float  # this month's list as a share of the budget (above 1 = over budget)
    prefers: str = "cheapest"  # "cheapest", "nearest" or "mixed" (see _pick_offer)
    spend: tuple[float, float] = (0.80, 0.97)  # how much of a past month's budget was spent
    seed: int = 1


PERSONAS: tuple[Persona, ...] = (
    Persona(
        "thabo",
        "Thabo",
        "Mokoena",
        "thabo.demo@example.com",
        "0710000001",
        "berea",
        -29.8530,
        31.0020,
        monthly=(("Grocery", 900), ("Toiletries", 300)),
        current=(("Grocery", 900), ("Toiletries", 300)),
        list_fill=0.60,
        prefers="cheapest",
        seed=11,
    ),
    Persona(
        "nomvula",
        "Nomvula",
        "Dlamini",
        "nomvula.demo@example.com",
        "0710000002",
        "campbell-hall",
        -29.8690,
        31.0035,
        monthly=((COMBINED, 1500),),
        current=((COMBINED, 1500),),
        list_fill=0.88,
        prefers="mixed",
        spend=(0.85, 0.99),
        seed=22,
    ),
    Persona(
        "sipho",
        "Sipho",
        "Ndlovu",
        "sipho.demo@example.com",
        "0710000003",
        "alpine-road",
        -29.8410,
        31.0010,
        monthly=(("Grocery", 1000), ("Toiletries", 350), ("Clothes", 250)),
        current=(("Grocery", 1100), ("Toiletries", 400), ("Clothes", 250)),  # R1 750: the whole NSFAS allowance
        list_fill=1.06,
        prefers="cheapest",
        spend=(0.75, 0.95),
        seed=33,
    ),
)


@dataclass
class DemoResult:
    created: list[User] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # emails that already existed
    trips: int = 0
    items: int = 0
    notifications: int = 0


class DemoError(RuntimeError):
    """Seeding was refused or failed; the message says why."""


def demo_emails() -> list[str]:
    return [persona.email for persona in PERSONAS]


def remove_demo_users() -> int:
    """Delete the demo students and everything they own (budgets, lists, items, notifications). Returns how many."""
    demo_users = db.session.scalars(select(User).where(User.Email.in_(demo_emails()))).all()
    if any(user.IsAdmin and user.IsActive for user in demo_users):
        other_admins = db.session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.IsAdmin.is_(True), User.IsActive.is_(True), User.Email.notin_(demo_emails()))
        )
        if not other_admins:
            raise DemoError(
                "A demo student is the only active admin, so removing them would lock everyone out of /admin. "
                "Make your own account an admin first (flask make-admin you@example.com)."
            )
    for user in demo_users:
        db.session.delete(user)
    db.session.commit()
    return len(demo_users)


# ------------------------------------------------------------------------------------------------------ helpers
def _sast(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, 0, tzinfo=SAST).astimezone(timezone.utc)


def _pool(category: str, cap: Decimal) -> list:
    """Everyday products of one category, none costing more than ``cap`` (a student buys basics, not a TV)."""
    return sorted((c for c in CATALOGUE if c.category == category and D(c.price) <= cap), key=lambda c: c.barcode)


def _pick_offer(offers: list[Product], prefers: str, rng: random.Random) -> Product | None:
    """``cheapest``: the lowest price. ``nearest``: the closest branch. ``mixed``: one of the two cheapest (a student
    who does not always walk to the same shop), so a trip visits more than one store."""
    stocked = sorted((o for o in offers if o.in_stock), key=lambda o: (o.price, o.distance_km or 999, o.store_name))
    if not stocked:
        return None
    if prefers == "nearest":
        return min(stocked, key=lambda o: (o.distance_km if o.distance_km is not None else 999, o.price, o.store_name))
    if prefers == "mixed":
        return rng.choice(stocked[:2])
    return stocked[0]


def _add_item(
    user: User,
    shopping_list: ShoppingList,
    sub: SubBudget | None,
    offer: Product,
    category: str,
    quantity: int,
    when: datetime | None = None,
) -> ListItem:
    item = ListItem(
        UserId=user.UserId,
        ShoppingListId=shopping_list.ShoppingListId,
        SubBudgetId=sub.SubBudgetId if sub else None,
        ItemName=offer.name,
        BarCode=offer.barcode,
        UnitCost=money(offer.price),
        ItemQuantity=quantity,
        Category=category,
        StoreName=offer.store_name,
        StoreAddress=offer.store_address,
        ItemImage=offer.image_url,
        StoreLatitude=offer.store_lat,
        StoreLongitude=offer.store_lng,
    )
    if when is not None:
        item.DateCreated = when
    db.session.add(item)
    return item


def _fill_list(
    user: User,
    persona: Persona,
    budget,
    shopping_list: ShoppingList,
    provider: RetailProvider,
    rng: random.Random,
    fraction: float,
    created: datetime | None = None,
) -> int:
    """Put items on ``shopping_list`` until it reaches ``fraction`` of the budget.

    Up to 100% no category goes past its own allowance. Above 100% the list is finished with one more item that
    tips the whole budget over, which is exactly the situation the over-budget rules are for.
    """
    subs = {
        sub.Category: sub for sub in db.session.scalars(select(SubBudget).where(SubBudget.BudgetId == budget.BudgetId))
    }
    total = money(budget.TotalAmount)
    target = min(total, money(total * D(str(fraction))))
    combined = COMBINED in subs
    if combined:
        weights = {"Grocery": 6, "Toiletries": 3, "Clothes": 1}
    else:
        weights = {category: float(sub.AllocatedAmount) for category, sub in subs.items()}
    spent_total = D("0")
    spent_cat: dict[str, Decimal] = {category: D("0") for category in weights}
    used_codes: set[str] = set()
    added = 0

    for _ in range(400):
        if spent_total >= target * D("0.97"):
            break
        category = rng.choices(list(weights), weights=list(weights.values()))[0]
        room = target - spent_total
        if not combined:
            room = min(room, money(subs[category].AllocatedAmount) - spent_cat[category])
        if room < D("5"):
            continue
        pool = _pool(category, min(room, D("300")))
        if not pool:
            continue
        product = rng.choice(pool)
        if product.barcode in used_codes:
            continue
        offer = _pick_offer(
            provider.get_offers_by_barcode(product.barcode, persona.lat, persona.lng, 15), persona.prefers, rng
        )
        if offer is None:
            continue
        quantity = rng.choice([1, 1, 1, 2, 2, 3])
        while quantity > 1 and money(offer.price) * quantity > room:
            quantity -= 1
        cost = money(offer.price) * quantity
        if cost > room:
            continue
        _add_item(
            user,
            shopping_list,
            subs.get(COMBINED) if combined else subs.get(category),
            offer,
            category,
            quantity,
            created,
        )
        used_codes.add(product.barcode)
        spent_total += cost
        spent_cat[category] += cost
        added += 1

    if fraction > 1:
        added += _overshoot(
            user, persona, shopping_list, subs, provider, spent_total, total, used_codes, weights, created, rng
        )
    db.session.flush()
    return added


def _overshoot(
    user, persona, shopping_list, subs, provider, spent_total, total, used_codes, weights, created, rng
) -> int:
    """One last item that takes the list past the budget total."""
    need = total - spent_total
    for category in weights:
        for product in _pool(category, D("2000")):
            if product.barcode in used_codes or D(product.price) <= need:
                continue
            offer = _pick_offer(
                provider.get_offers_by_barcode(product.barcode, persona.lat, persona.lng, 15), persona.prefers, rng
            )
            if offer is None or money(offer.price) <= need:
                continue
            sub = subs.get(COMBINED) or subs.get(category)
            _add_item(user, shopping_list, sub, offer, category, 1, created)
            return 1
    return 0


def _rewrite_dates(budget, shopping_list, created: datetime, shopped: datetime) -> None:
    """A past month must look like one: everything created on the 1st and closed on the shopping day."""
    budget.DateCreated, budget.ClosedDate = created, shopped
    shopping_list.DateCreated, shopping_list.DateClosed = created, shopped
    for sub in db.session.scalars(select(SubBudget).where(SubBudget.BudgetId == budget.BudgetId)):
        sub.DateCreated, sub.ClosedDate = created, shopped
    for item in db.session.scalars(select(ListItem).where(ListItem.ShoppingListId == shopping_list.ShoppingListId)):
        item.DateCreated, item.PurchasedDate = created + timedelta(hours=3), shopped


def _past_month(
    user: User, persona: Persona, provider: RetailProvider, rng: random.Random, year: int, month: int
) -> int:
    budget = budgets.create_budget(
        user.UserId, f"{MONTHS_LONG[month - 1]} Budget", [(c, D(a)) for c, a in persona.monthly]
    )
    shopping_list = budgets.get_active_list(budget)
    created = _sast(year, month, 1, 8)
    shopped = _sast(year, month, rng.randint(2, 8), rng.choice([10, 14, 16]))
    fraction = rng.uniform(*persona.spend)
    added = _fill_list(user, persona, budget, shopping_list, provider, rng, fraction, created + timedelta(hours=3))
    db.session.commit()
    budgets.recalculate(budget)
    db.session.commit()
    budgets.complete_purchase(user.UserId)
    _rewrite_dates(budget, shopping_list, created, shopped)
    db.session.commit()
    return added


# ------------------------------------------------------------------------------------------------------ entry point
def seed_demo(
    *, reset: bool = False, today: date | None = None, provider: RetailProvider | None = None, notify: bool = True
) -> DemoResult:
    """Create the demo students. Safe to repeat: a student who exists is left alone unless ``reset`` is true."""
    provider = provider or MockProvider()
    today = today or today_sast()
    result = DemoResult()
    if reset:
        remove_demo_users()

    past = previous_months(today, HISTORY_MONTHS + 1)[:-1]  # the six months before this one
    for persona in PERSONAS:
        if db.session.scalar(select(User).where(User.Email == persona.email)) is not None:
            result.skipped.append(persona.email)
            continue
        residence = get_residence(persona.residence)
        user = User(
            FirstName=persona.first,
            LastName=persona.last,
            Email=persona.email,
            CellphoneNumber=persona.cell,
            ResidentialAddress=residence.full_address if residence else None,
            Latitude=persona.lat,
            Longitude=persona.lng,
            TermsAcceptedOn=utcnow(),
        )
        user.set_password(DEMO_PASSWORD)
        db.session.add(user)
        db.session.commit()
        rng = random.Random(persona.seed)

        try:
            items = 0
            for year, month in past:
                items += _past_month(user, persona, provider, rng, year, month)
            budget = budgets.create_budget(
                user.UserId, f"{MONTHS_LONG[today.month - 1]} Budget", [(c, D(a)) for c, a in persona.current]
            )
            items += _fill_list(
                user, persona, budget, budgets.get_active_list(budget), provider, rng, persona.list_fill
            )
            db.session.commit()
            budgets.recalculate(budget)
            db.session.commit()
        except Exception as exc:
            # Never leave a half-built student behind: the next run would see the address and skip them.
            db.session.rollback()
            half_built = db.session.scalar(select(User).where(User.Email == persona.email))
            if half_built is not None:
                db.session.delete(half_built)
                db.session.commit()
            raise DemoError(f"Could not build {persona.first}: {exc}. Nothing was left half-made.") from exc
        result.items += items
        result.trips += len(past)
        result.created.append(user)

    if notify and result.created:
        from app.services.notifications import evaluate

        for user in result.created:
            result.notifications += len(evaluate(user, prices=True, provider=provider))
    return result
