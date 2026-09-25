"""Flask CLI commands.

flask seed-stores [--geocode] [--dry-run] [--file extra_stores.json]
flask retail-search "full cream milk" [--lat --lng --radius --category]
flask healthcheck          # JSON health report; exit code 1 when the database is down
flask seed-demo [--reset]  # 3 demo students with budgets, lists and six months of history (development only)
flask seed-admin [--keep-password]  # the built-in admin account (DEFAULT_ADMIN_EMAIL / DEFAULT_ADMIN_PASSWORD)
"""

import json
from pathlib import Path

import click
from sqlalchemy import select

from app.data.durban_stores import ALL_STORES, DURBAN_STORES, FINDERS, StoreSeed, unconfirmed_requests
from app.extensions import db
from app.models import Store
from app.utils.geo import distance_km

# A better location source never gets replaced by a worse one when the seeder is run again.
_RANK = {"approximate": 1, "geocoded": 2, "official": 3}
_MAX_GEOCODE_DRIFT_KM = 5.0  # a lookup further than this from our anchor is probably the wrong place


def _seeds_from_file(path: str) -> list[StoreSeed]:
    """JSON list of objects with: slug, name, brand, suburb, address, lat, lng and optional
    source, phone, hours, link and kind (grocery, clothing or both)."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    seeds = []
    for row in rows:
        try:
            seeds.append(
                StoreSeed(
                    slug=row["slug"],
                    name=row["name"],
                    brand=row["brand"],
                    suburb=row.get("suburb", ""),
                    address=row["address"],
                    lat=float(row["lat"]),
                    lng=float(row["lng"]),
                    source=row.get("source", "approximate"),
                    phone=row.get("phone"),
                    hours=row.get("hours"),
                    link=row.get("link"),
                    kind=row.get("kind", "grocery"),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise click.ClickException(f"Bad store entry {row!r}: {exc}") from exc
    return seeds


def upsert_stores(seeds) -> tuple[int, int]:
    """Insert or update stores by slug. Returns ``(created, updated)``."""
    created = updated = 0
    for seed in seeds:
        store = db.session.scalar(select(Store).where(Store.Slug == seed.slug))
        if store is None:
            store = Store(Slug=seed.slug)
            db.session.add(store)
            created += 1
        else:
            updated += 1
        store.Name, store.Brand, store.Suburb, store.Address = seed.name, seed.brand, seed.suburb, seed.address
        store.Phone, store.OpeningHours = seed.phone, seed.hours
        store.StoreType = seed.kind
        if store.Latitude is None or _RANK.get(seed.source, 1) >= _RANK.get(store.LocationSource or "approximate", 1):
            store.Latitude, store.Longitude, store.LocationSource = seed.lat, seed.lng, seed.source
    db.session.commit()
    return created, updated


def add_missing_stores(seeds) -> int:
    """Insert the seeds whose slug is not in the database yet and touch nothing else, so an admin's edits survive."""
    have = set(db.session.scalars(select(Store.Slug)))
    added = upsert_stores([seed for seed in seeds if seed.slug not in have])[0]
    return added


def geocode_approximate_stores(echo=click.echo) -> int:
    """Replace ``approximate`` coordinates with an OpenStreetMap lookup of the address."""
    from app.services.geocoding import GeocodingError, geocode

    improved = 0
    for store in db.session.scalars(select(Store).where(Store.LocationSource == "approximate")):
        for query in (f"{store.Address}, South Africa", f"{store.Name}, {store.Suburb}, Durban"):
            try:
                results = geocode(query, limit=1)
            except GeocodingError as exc:
                echo(f"  ! {store.Name}: geocoder unavailable ({exc})")
                return improved
            if (
                results
                and distance_km(store.Latitude, store.Longitude, results[0].lat, results[0].lng)
                <= _MAX_GEOCODE_DRIFT_KM
            ):
                store.Latitude, store.Longitude, store.LocationSource = results[0].lat, results[0].lng, "geocoded"
                improved += 1
                echo(f"  + {store.Name}: {results[0].lat:.5f}, {results[0].lng:.5f}")
                break
        else:
            echo(f"  - {store.Name}: no confident match, keeping the approximate position")
    db.session.commit()
    return improved


def register_cli(app) -> None:
    @app.cli.command("seed-stores")
    @click.option(
        "--geocode",
        "do_geocode",
        is_flag=True,
        help="Refine approximate coordinates through OpenStreetMap Nominatim (about 1 second per store).",
    )
    @click.option(
        "--file",
        "extra_file",
        type=click.Path(exists=True, dir_okay=False),
        help="JSON file with extra branches to add or update.",
    )
    @click.option("--dry-run", is_flag=True, help="Show what would be seeded without touching the database.")
    @click.option(
        "--missing-only", is_flag=True, help="Only add branches that are not in the database (keeps admin edits)."
    )
    def seed_stores(do_geocode, extra_file, dry_run, missing_only):
        """Seed Durban supermarket and clothing branches (safe to run repeatedly)."""
        seeds = list(ALL_STORES) + (_seeds_from_file(extra_file) if extra_file else [])
        if dry_run:
            for seed in seeds:
                click.echo(f"  {seed.name:<40} {seed.suburb:<16} {seed.kind:<9} {seed.source}")
            click.echo(f"{len(seeds)} stores would be seeded.")
        elif missing_only:
            click.echo(f"Stores: {add_missing_stores(seeds)} added ({len(seeds)} known).")
        else:
            created, updated = upsert_stores(seeds)
            click.echo(f"Stores: {created} added, {updated} updated ({len(seeds)} total).")
            if do_geocode:
                click.echo("Looking up addresses on OpenStreetMap (max 1 request per second)...")
                click.echo(f"{geocode_approximate_stores()} store position(s) refined.")

        missing = unconfirmed_requests(DURBAN_STORES)
        if missing:
            click.echo("")
            click.echo(
                f"Not seeded ({len(missing)}): no branch could be confirmed for these requested areas. "
                "Check the retailer's store finder and add them with --file:"
            )
            for brand, area in missing:
                click.echo(f"  - {brand} {area}")
            click.echo("Store finders: " + "; ".join(f"{brand}: {url}" for brand, url in FINDERS.items()))

    @app.cli.command("seed-demo")
    @click.option("--reset", is_flag=True, help="Delete the three demo students first and build them again.")
    @click.option(
        "--allow-production", is_flag=True, help="Seed even when APP_ENV=production (the demo password is public)."
    )
    def seed_demo_command(reset, allow_production):
        """Create three demo students, each with a budget, a shopping list and six months of history."""
        from app.services import demo

        if app.config.get("CONFIG_NAME") == "production" and not allow_production:
            raise click.ClickException(
                "Refusing to create demo accounts with a public password on a production site. "
                "Use --allow-production if you really mean it."
            )
        add_missing_stores(ALL_STORES)  # the store filter, map and route need the branches; admin edits stay
        try:
            result = demo.seed_demo(reset=reset)
        except demo.DemoError as exc:
            raise click.ClickException(str(exc)) from exc
        for email in result.skipped:
            click.echo(f"  {email} already exists (use --reset to rebuild it)")
        if result.created:
            click.echo(
                f"Created {len(result.created)} demo student(s): {result.trips} past trips, {result.items} list items, "
                f"{result.notifications} notification(s)."
            )
            click.echo(f"\nSign in with any of these (password: {demo.DEMO_PASSWORD}):")
            for user in result.created:
                click.echo(f"  {user.Email:<28} {user.FirstName} {user.LastName}")
            click.echo("\nTo see the admin portal, run: flask make-admin thabo.demo@example.com")

    @app.cli.command("seed-admin")
    @click.option("--keep-password", is_flag=True, help="Do not reset the password of an existing account.")
    def seed_admin(keep_password):
        """Create the built-in admin account (DEFAULT_ADMIN_EMAIL / DEFAULT_ADMIN_PASSWORD), or make it an admin again."""
        from app.services.admin_users import ensure_default_admin

        email, password = app.config["DEFAULT_ADMIN_EMAIL"], app.config["DEFAULT_ADMIN_PASSWORD"]
        user, created = ensure_default_admin(email, password, reset_password=not keep_password)
        db.session.commit()
        click.echo(f"{'Created' if created else 'Updated'} the admin account {user.Email}. Sign in at /login.")

    @app.cli.command("healthcheck")
    def healthcheck():
        """Print the health report as JSON; exit 1 when the database is down (used by the Docker HEALTHCHECK)."""
        import sys

        from app.services import health

        payload, healthy = health.report()
        click.echo(json.dumps(payload, indent=2))
        if not healthy:
            sys.exit(1)

    @app.cli.command("make-admin")
    @click.argument("email")
    @click.option("--remove", is_flag=True, help="Take the admin role away instead of giving it.")
    def make_admin(email, remove):
        """Give (or with --remove, take away) the admin role of the student with this email address."""
        from sqlalchemy import func, select

        from app.extensions import db
        from app.models import AuditLog, User
        from app.utils.dates import utcnow

        user = db.session.scalar(select(User).where(func.lower(User.Email) == email.strip().lower()))
        if user is None:
            raise click.ClickException(f"No student has the email address {email}.")
        if not remove and not user.IsActive:
            raise click.ClickException("That account is deactivated. Reactivate it first.")
        if remove and user.IsAdmin:
            others = (
                db.session.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(User.IsAdmin.is_(True), User.IsActive.is_(True), User.UserId != user.UserId)
                )
                or 0
            )
            if others == 0:
                raise click.ClickException("That is the only active admin. Make another admin first.")
        if bool(user.IsAdmin) == (not remove):
            click.echo(f"{user.Email} is already {'not ' if remove else ''}an admin. Nothing changed.")
            return
        user.IsAdmin = not remove
        # The audit trail also records changes made from the command line (there is no signed-in admin, so the row says so).
        db.session.add(
            AuditLog(
                AdminId="cli",
                AdminEmail="command line",
                Action="user.remove_admin" if remove else "user.make_admin",
                Target=f"user:{user.UserId}",
                Detail={"email": user.Email},
                CreatedOn=utcnow(),
            )
        )
        db.session.commit()
        click.echo(f"{user.Email} is {'no longer' if remove else 'now'} an admin.")

    @app.cli.command("notify")
    def notify_everyone():
        """Run every notification rule for every active student (schedule this, for example every 15 minutes)."""
        from app.services.notifications import evaluate_everyone

        click.echo(f"{evaluate_everyone()} notification(s) created.")

    @app.cli.command("retail-search")
    @click.argument("query")
    @click.option("--lat", type=float, help="Latitude (default: Durban centre).")
    @click.option("--lng", type=float, help="Longitude (default: Durban centre).")
    @click.option("--radius", type=float, default=None, help="Kilometres (default: RETAIL_DEFAULT_RADIUS_KM).")
    @click.option("--category", help="Grocery, Toiletries or Clothes.")
    def retail_search(query, lat, lng, radius, category):
        """Try the configured retail provider (RETAIL_PROVIDER) from the command line."""
        from app.services.http import ExternalAPIError
        from app.services.retail_api import RetailConfigError, get_retail_provider, group_by_barcode
        from app.utils.geo import default_map_center

        centre = default_map_center()
        lat = centre["lat"] if lat is None else lat
        lng = centre["lng"] if lng is None else lng
        radius = radius or app.config["RETAIL_DEFAULT_RADIUS_KM"]
        try:
            provider = get_retail_provider()
            products = provider.search_products(query, lat, lng, radius, category)
        except (RetailConfigError, ExternalAPIError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Provider: {provider.name}  |  {len(products)} offers for {query!r} within {radius:g} km")
        for group in group_by_barcode(products)[:15]:
            click.echo(f"\n{group.name}  [{group.barcode or 'no barcode'}]")
            for offer in group.offers:
                stock = "" if offer.in_stock else "  (out of stock)"
                km = f"{offer.distance_km:.1f} km" if offer.distance_km is not None else "-"
                click.echo(f"    R{offer.price:>7}  {offer.store_name:<36} {km}{stock}")
        click.echo("\nCalls are logged in app/logs/api_calls.log")
