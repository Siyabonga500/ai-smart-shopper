"""The admin portal (Step 16).

    GET   /admin/                              dashboard: totals and charts
    GET   /admin/users                         ?q=&status=&role=&page=    search and list students
    GET   /admin/users/<id>                    one student (every look is audited)
    GET/POST /admin/users/<id>/edit            name, cellphone, address, role
    POST  /admin/users/<id>/deactivate | reactivate | reset-password
    GET   /admin/categories                    raw API category -> app category mappings (+ POST to add)
    GET/POST /admin/categories/<id>/edit       POST /admin/categories/<id>/delete
    GET   /admin/integrations                  data sources: activate, API keys, Sync now
    POST  /admin/integrations/<source>/activate | key | key/delete | sync
    GET   /admin/integrations/logs             ?service=&outcome=&day=    the API call log
    GET   /admin/stores                        ?q=&brand=&type=&page=   POST /admin/stores/new, /<id>/edit, /<id>/delete
    POST  /admin/stores/seed                   add the known Durban supermarket and clothing branches that are missing
    GET/POST /admin/users/new                  add an account (student or admin)
    GET   /admin/products                      ?q=&category=&store=&page=   products added by hand
    GET/POST /admin/products/new | /<id>/edit  POST /admin/products/<id>/delete
    GET   /admin/messages                      ?page=   Contact us messages   POST /<id>/read | /<id>/delete
    GET   /admin/pictures                      ?q=&page=   paste picture URLs for the built-in products (by barcode)
    POST  /admin/pictures                      barcode + URLs, one per line (an empty list removes them)
    GET   /admin/courses                       the short courses: questions, attempts, pass rate
    GET   /admin/courses/<id>                  one course: every question and answer (+ POST .../questions to add)
    GET   /admin/courses/<id>/attempts         who took the quiz, their scores
    POST  /admin/questions/<id>/delete | /admin/questions/<id>/answers   (add an answer)
    POST  /admin/answers/<id>/correct | /admin/answers/<id>/delete
    GET   /admin/audit                         ?action=&admin=&target=&start=&end=&page=

Access: signed in AND ``User.IsAdmin`` (and not deactivated). The check is one ``before_request`` for the whole
blueprint, so a route added later cannot forget it. Every change is written to the audit log in the same
transaction as the change itself.
"""

from datetime import date

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user

from app.extensions import db, login_manager
from app.forms.admin import AdminNewUserForm, AdminUserForm, ApiKeyForm, CategoryMappingForm, ProductForm, StoreForm
from app.models import Answer, CatalogueProduct, ContactMessage, Course, ProductPicture, Question, Store
from app.models.admin import MAPPED_CATEGORIES
from app.models.catalogue import PRODUCT_CATEGORIES
from app.services import admin_stats, admin_stores, admin_users, audit, catalogue, category_map, integrations
from app.services import courses as course_service
from app.services.photos import PhotoError, save_product_photo
from app.utils.validators import clean_phone_digits

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
def require_admin():
    if not current_user.is_authenticated:
        return login_manager.unauthorized()
    if not current_user.is_admin:
        current_app.logger.warning(
            "Blocked %s from %s %s (not an admin)", current_user.UserId, request.method, request.path
        )
        abort(403)
    return None


@bp.after_request
def never_cache(response):
    """Admin pages show personal details and one-time passwords: browsers and proxies must not keep them."""
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------------------------- helpers
def me():
    return current_user._get_current_object()


def _choice(value: str | None, allowed) -> str | None:
    return value if value in allowed else None


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "").strip()) if value else None
    except ValueError:
        return None


def _done(message: str, level: str = "success") -> None:
    flash(message, level)


def _fail(message: str) -> None:
    flash(message, "danger")


@bp.app_template_global("admin_url")
def admin_url(endpoint: str, **values) -> str:
    """``url_for`` that carries the current filters along (used by the pager)."""
    return url_for(endpoint, **{**{k: v for k, v in request.args.items() if v and k != "page"}, **values})


@bp.app_template_filter("safe_http_url")
def safe_http_url(value):
    """Only ``http(s)://`` addresses are ever put in an href (a stored ``javascript:`` link would be an XSS)."""
    return value if isinstance(value, str) and value.lower().startswith(("http://", "https://")) else None


@bp.app_template_filter("safe_image_url")
def safe_image_url(value):
    """A product picture that may go in ``src``: our own ``/static/`` files or an ``http(s)://`` address."""
    if isinstance(value, str) and value.startswith("/static/") and ".." not in value:
        return value
    return safe_http_url(value)


# ------------------------------------------------------------------------------------------- dashboard
@bp.get("/")
def index():
    data = admin_stats.dashboard()
    return render_template("admin/dashboard.html", data=data, charts=admin_stats.chart_payload(data))


# ---------------------------------------------------------------------------------------------- users
@bp.get("/users")
def users():
    status = _choice(request.args.get("status"), admin_users.STATUSES)
    role = _choice(request.args.get("role"), admin_users.ROLES)
    query = (request.args.get("q") or "").strip()[:100]
    page = admin_users.search(query, status, role, request.args.get("page"))
    return render_template(
        "admin/users.html",
        page=page,
        q=query,
        status=status,
        role=role,
        roles=admin_users.ROLES,
        statuses=admin_users.STATUSES,
    )


@bp.route("/users/new", methods=["GET", "POST"])
def user_new():
    form = AdminNewUserForm()
    if request.method == "POST" and form.validate_on_submit():
        try:
            user = admin_users.create(
                first_name=form.FirstName.data,
                last_name=form.LastName.data,
                email=form.Email.data,
                cellphone=clean_phone_digits(form.CellphoneNumber.data) if form.CellphoneNumber.data else None,
                password=form.Password.data,
                role=form.Role.data,
            )
        except admin_users.AdminUserError as exc:
            db.session.rollback()
            form.Email.errors = [str(exc)]
        else:
            db.session.flush()
            audit.record(me(), "user.create", f"user:{user.UserId}", {"email": user.Email, "role": form.Role.data})
            db.session.commit()
            _done(f"Added {user.FullName}. They can sign in with the password you chose.")
            return redirect(url_for("admin.user_detail", user_id=user.UserId))
    return render_template("admin/user_new.html", form=form), 400 if request.method == "POST" else 200


def _user_or_404(user_id: str):
    user = admin_users.get(user_id)
    if user is None:
        abort(404)
    return user


@bp.get("/users/<user_id>")
def user_detail(user_id):
    user = _user_or_404(user_id)
    audit.record(me(), "user.view", f"user:{user.UserId}")
    db.session.commit()
    return render_template(
        "admin/user_detail.html",
        u=user,
        stats=admin_users.activity(user),
        role=admin_users.role_of(user),
        is_self=user.UserId == me().UserId,
    )


@bp.route("/users/<user_id>/edit", methods=["GET", "POST"])
def user_edit(user_id):
    user = _user_or_404(user_id)
    form = AdminUserForm(obj=user) if request.method == "GET" else AdminUserForm()
    if request.method == "GET":
        form.Role.data = admin_users.role_of(user)
        form.CellphoneNumber.data = user.CellphoneNumber
    if request.method == "POST" and form.validate_on_submit():
        before = admin_users.snapshot(user)
        try:
            admin_users.update(
                me(),
                user,
                first_name=form.FirstName.data,
                last_name=form.LastName.data,
                cellphone=clean_phone_digits(form.CellphoneNumber.data),
                address=form.ResidentialAddress.data,
                role=form.Role.data,
            )
        except admin_users.AdminUserError as exc:
            db.session.rollback()
            _fail(str(exc))
            return render_template("admin/user_form.html", form=form, u=user, is_self=user.UserId == me().UserId), 400
        after = admin_users.snapshot(user)
        audit.record(me(), "user.update", f"user:{user.UserId}", {"changes": audit.changes(before, after)})
        db.session.commit()
        _done(f"Saved {user.FullName}.")
        return redirect(url_for("admin.user_detail", user_id=user.UserId))
    status = 400 if request.method == "POST" else 200
    return render_template("admin/user_form.html", form=form, u=user, is_self=user.UserId == me().UserId), status


def _user_action(user_id, action, verb, work):
    user = _user_or_404(user_id)
    try:
        work(user)
    except admin_users.AdminUserError as exc:
        db.session.rollback()
        _fail(str(exc))
    else:
        audit.record(me(), action, f"user:{user.UserId}", {"email": user.Email})
        db.session.commit()
        _done(f"{user.FullName} {verb}.")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@bp.post("/users/<user_id>/deactivate")
def user_deactivate(user_id):
    return _user_action(
        user_id,
        "user.deactivate",
        "was deactivated and can no longer sign in",
        lambda user: admin_users.deactivate(me(), user),
    )


@bp.post("/users/<user_id>/reactivate")
def user_reactivate(user_id):
    return _user_action(user_id, "user.reactivate", "was reactivated", admin_users.reactivate)


@bp.post("/users/<user_id>/reset-password")
def user_reset_password(user_id):
    user = _user_or_404(user_id)
    try:
        password = admin_users.reset_password(user)
    except admin_users.AdminUserError as exc:
        db.session.rollback()
        _fail(str(exc))
        return redirect(url_for("admin.user_detail", user_id=user_id))
    audit.record(me(), "user.reset_password", f"user:{user.UserId}", {"email": user.Email})  # never the password itself
    db.session.commit()
    if user.UserId == me().UserId:
        login_user(user)  # an admin resetting their own password stays signed in on this browser
    return render_template("admin/password_reset.html", u=user, temp_password=password)


# ------------------------------------------------------------------------------------------ categories
@bp.route("/categories", methods=["GET", "POST"])
def categories():
    form = CategoryMappingForm()
    if request.method == "POST":
        if form.validate_on_submit():
            try:
                row = category_map.create(form.RawCategory.data, form.MappedCategory.data)
                audit.record(
                    me(), "category.create", f"mapping:{row.RawKey}", {"raw": row.RawCategory, "to": row.MappedCategory}
                )
                category_map.commit_and_refresh()
            except category_map.MappingError as exc:
                db.session.rollback()
                form.RawCategory.errors = [str(exc)]
            else:
                _done(f"Mapped “{row.RawCategory}” to {row.MappedCategory}.")
                return redirect(url_for("admin.categories"))
        status = 400
    else:
        status = 200
    category = _choice(request.args.get("category"), MAPPED_CATEGORIES)
    page = category_map.search(request.args.get("q"), category, request.args.get("page"))
    return (
        render_template(
            "admin/categories.html",
            form=form,
            page=page,
            q=request.args.get("q", ""),
            category=category,
            counts=category_map.counts(),
            targets=MAPPED_CATEGORIES,
        ),
        status,
    )


@bp.route("/categories/<mapping_id>/edit", methods=["GET", "POST"])
def category_edit(mapping_id):
    row = category_map.get(mapping_id) or abort(404)
    form = CategoryMappingForm(obj=row) if request.method == "GET" else CategoryMappingForm()
    if request.method == "GET":
        form.RawCategory.data, form.MappedCategory.data = row.RawCategory, row.MappedCategory
    if request.method == "POST" and form.validate_on_submit():
        before = {"raw": row.RawCategory, "to": row.MappedCategory}
        try:
            category_map.update(row, form.RawCategory.data, form.MappedCategory.data)
            audit.record(
                me(),
                "category.update",
                f"mapping:{row.RawKey}",
                {"changes": audit.changes(before, {"raw": row.RawCategory, "to": row.MappedCategory})},
            )
            category_map.commit_and_refresh()
        except category_map.MappingError as exc:
            db.session.rollback()
            form.RawCategory.errors = [str(exc)]
        else:
            _done("Mapping saved.")
            return redirect(url_for("admin.categories"))
    return render_template("admin/category_form.html", form=form, row=row), 400 if request.method == "POST" else 200


@bp.post("/categories/<mapping_id>/delete")
def category_delete(mapping_id):
    row = category_map.get(mapping_id) or abort(404)
    audit.record(me(), "category.delete", f"mapping:{row.RawKey}", {"raw": row.RawCategory, "to": row.MappedCategory})
    db.session.delete(row)
    category_map.commit_and_refresh()
    _done(f"Removed the mapping for “{row.RawCategory}”.")
    return redirect(url_for("admin.categories"))


# ----------------------------------------------------------------------------------------- integrations
@bp.get("/integrations")
def integrations_page():
    return render_template(
        "admin/integrations.html",
        sources=integrations.overview(),
        key_form=ApiKeyForm(),
        recent=integrations.recent_calls(limit=10),
    )


def _integration_action(source, action, work, message):
    try:
        work()
    except integrations.IntegrationError as exc:
        db.session.rollback()
        _fail(str(exc))
    else:
        audit.record(me(), action, f"integration:{source}")  # nothing secret ever goes in the audit detail
        integrations.commit_and_refresh()
        _done(message)
    return redirect(url_for("admin.integrations_page"))


@bp.post("/integrations/<source>/activate")
def integration_activate(source):
    if source not in integrations.SOURCE_INFO:
        abort(404)
    holder = {}
    try:
        holder["previous"] = integrations.activate(source)
    except integrations.IntegrationError as exc:
        db.session.rollback()
        _fail(str(exc))
    else:
        audit.record(me(), "integration.activate", f"integration:{source}", {"previous": holder["previous"]})
        integrations.commit_and_refresh()
        _done(f"{integrations.SOURCE_INFO[source].label} is now the active data source.")
    return redirect(url_for("admin.integrations_page"))


@bp.post("/integrations/<source>/key")
def integration_key(source):
    if source not in integrations.SOURCE_INFO:
        abort(404)
    form = ApiKeyForm()
    if not form.validate_on_submit():
        _fail(next(iter(sum(form.errors.values(), [])), "Paste the key first."))
        return redirect(url_for("admin.integrations_page"))
    return _integration_action(
        source,
        "integration.set_key",
        lambda: integrations.set_key(source, form.ApiKey.data),
        f"Saved the {integrations.SOURCE_INFO[source].key_label.lower()} for {integrations.SOURCE_INFO[source].label}.",
    )


@bp.post("/integrations/<source>/key/delete")
def integration_key_delete(source):
    if source not in integrations.SOURCE_INFO:
        abort(404)
    return _integration_action(
        source,
        "integration.remove_key",
        lambda: integrations.remove_key(source),
        f"Removed the saved key for {integrations.SOURCE_INFO[source].label}.",
    )


@bp.post("/integrations/<source>/sync")
def integration_sync(source):
    if source not in integrations.SOURCE_INFO:
        abort(404)
    row = integrations.sync_now(source)
    audit.record(
        me(),
        "integration.sync",
        f"integration:{source}",
        {"status": row.LastSyncStatus, "message": row.LastSyncMessage},
    )
    db.session.commit()
    _done(row.LastSyncMessage, "success" if row.LastSyncStatus == "ok" else "danger")
    return redirect(url_for("admin.integrations_page"))


@bp.get("/integrations/logs")
def api_logs():
    service = request.args.get("service") or None
    outcome = _choice(request.args.get("outcome"), ("ok", "error"))
    day = _date(request.args.get("day"))
    calls = integrations.recent_calls(limit=200, service=service, outcome=outcome, day=day)
    return render_template(
        "admin/api_logs.html",
        calls=calls,
        services=integrations.services_in_log(),
        service=service or "",
        outcome=outcome or "",
        day=day,
    )


# ---------------------------------------------------------------------------------------------- stores
@bp.get("/stores")
def stores():
    brand = request.args.get("brand") or None
    store_type = _choice(request.args.get("type"), ("grocery", "clothing", "both"))
    page = admin_stores.search(request.args.get("q"), brand, request.args.get("page"), store_type=store_type)
    return render_template(
        "admin/stores.html",
        page=page,
        q=request.args.get("q", ""),
        brand=brand or "",
        store_type=store_type or "",
        brands=admin_stores.brands(),
        missing=admin_stores.missing_seeds(),
    )


@bp.post("/stores/seed")
def stores_seed():
    added = admin_stores.add_known_stores()
    audit.record(me(), "store.seed", "stores", {"added": added})
    db.session.commit()
    _done(
        f"Added {added} Durban store{'' if added == 1 else 's'}."
        if added
        else "Every known Durban store is already listed."
    )
    return redirect(url_for("admin.product_new") if request.form.get("back") == "product" else url_for("admin.stores"))


def _store_data(form) -> dict:
    return {
        "name": form.Name.data,
        "brand": form.Brand.data,
        "suburb": form.Suburb.data,
        "address": form.Address.data,
        "lat": float(form.Latitude.data),
        "lng": float(form.Longitude.data),
        "hours": form.OpeningHours.data,
        "phone": form.Phone.data,
        "location_source": form.LocationSource.data,
        "store_type": form.StoreType.data,
    }


@bp.route("/stores/new", methods=["GET", "POST"])
def store_new():
    form = StoreForm()
    if request.method == "GET":
        form.LocationSource.data = "official"
        form.StoreType.data = "grocery"
    if request.method == "POST" and form.validate_on_submit():
        store = Store()
        admin_stores.apply(store, _store_data(form))
        db.session.add(store)
        db.session.flush()
        audit.record(me(), "store.create", f"store:{store.StoreId}", admin_stores.snapshot(store))
        db.session.commit()
        _done(f"Added {store.Name}.")
        return redirect(url_for("admin.stores"))
    return render_template("admin/store_form.html", form=form, store=None), 400 if request.method == "POST" else 200


@bp.route("/stores/<store_id>/edit", methods=["GET", "POST"])
def store_edit(store_id):
    store = admin_stores.get(store_id) or abort(404)
    form = (
        StoreForm()
        if request.method == "POST"
        else StoreForm(
            data={
                "Name": store.Name,
                "Brand": store.Brand,
                "Suburb": store.Suburb,
                "Address": store.Address,
                "Latitude": store.Latitude,
                "Longitude": store.Longitude,
                "LocationSource": store.LocationSource,
                "OpeningHours": store.OpeningHours,
                "Phone": store.Phone,
                "StoreType": store.StoreType,
            }
        )
    )
    if request.method == "POST" and form.validate_on_submit():
        before = admin_stores.snapshot(store)
        admin_stores.apply(store, _store_data(form))
        audit.record(
            me(),
            "store.update",
            f"store:{store.StoreId}",
            {"changes": audit.changes(before, admin_stores.snapshot(store))},
        )
        db.session.commit()
        _done(f"Saved {store.Name}.")
        return redirect(url_for("admin.stores"))
    return render_template("admin/store_form.html", form=form, store=store), 400 if request.method == "POST" else 200


@bp.post("/stores/<store_id>/delete")
def store_delete(store_id):
    store = admin_stores.get(store_id) or abort(404)
    audit.record(me(), "store.delete", f"store:{store.StoreId}", admin_stores.snapshot(store))
    db.session.delete(store)
    db.session.commit()
    _done(f"Deleted {store.Name}.")
    return redirect(url_for("admin.stores"))


# -------------------------------------------------------------------------------------------- products
@bp.get("/products")
def products():
    category = _choice(request.args.get("category"), PRODUCT_CATEGORIES)
    store_id = request.args.get("store") or None
    query = (request.args.get("q") or "").strip()[:100]
    page = catalogue.search(query, category, store_id, request.args.get("page"))
    return render_template(
        "admin/products.html",
        page=page,
        q=query,
        category=category or "",
        store_id=store_id or "",
        categories=PRODUCT_CATEGORIES,
        counts=catalogue.counts(),
        stores=catalogue.store_options(),
    )


def _save_photos(files) -> list[str]:
    saved = []
    for storage in files or []:
        if storage and storage.filename:
            saved.append(save_product_photo(storage))
    return saved


def _apply_product(product: CatalogueProduct, form: ProductForm) -> None:
    """Copy the validated form onto ``product``, storing any uploaded pictures. Raises ``PhotoError``."""
    limit = current_app.config["MAX_PRODUCT_PHOTOS"]
    clothing = form.is_clothing
    product.Category = form.Category.data
    product.StoreId = form.StoreId.data
    product.Name = form.Name.data
    product.Brand = form.Brand.data
    product.Price = form.Price.data
    product.SalePrice = form.SalePrice.data
    product.StockStatus = form.StockStatus.data
    product.StockQuantity = form.StockQuantity.data
    # Every category can have several pictures: the image address, the photo addresses and any uploads, in order.
    new = [u for u in [form.ImageUrl.data or None, *form.photo_urls] if u]
    new += _save_photos(form.ImageFile.data) + _save_photos(form.Photos.data)
    if product.ProductId and not form.RemovePictures.data:
        new += [p for p in product.photos if not p.startswith("http")]  # uploads stay unless removed on purpose
    photos = list(dict.fromkeys(new))  # no duplicates, order kept
    if len(photos) > limit:
        raise PhotoError(f"A product can have at most {limit} pictures.")
    product.Photos = photos or None
    product.ImageUrl = photos[0] if photos else None
    product.Barcode = form.Barcode.data
    if clothing:
        product.Sku, product.Size, product.Colour = form.Sku.data, form.Size.data, form.Colour.data
        if form.StockQuantity.data == 0:
            product.StockStatus = "out_of_stock"
    else:
        product.Sku = product.Size = product.Colour = None


def _product_form_page(form, product, status=200):
    return (
        render_template(
            "admin/product_form.html",
            form=form,
            product=product,
            stores=catalogue.store_options(),
            missing=admin_stores.missing_seeds(),
        ),
        status,
    )


@bp.route("/products/new", methods=["GET", "POST"])
def product_new():
    form = ProductForm(stores=catalogue.store_options())
    if request.method == "GET":
        form.Category.data = (
            request.args.get("category") if request.args.get("category") in PRODUCT_CATEGORIES else "Grocery"
        )
        form.StockStatus.data = "in_stock"
    if request.method == "POST" and form.validate_on_submit():
        product = CatalogueProduct(CreatedBy=me().Email)
        try:
            _apply_product(product, form)
        except PhotoError as exc:
            db.session.rollback()
            _fail(str(exc))
            return _product_form_page(form, None, 400)
        db.session.add(product)
        db.session.flush()
        audit.record(me(), "product.create", f"product:{product.ProductId}", catalogue.snapshot(product))
        db.session.commit()
        _done(f"Added {product.Name}. Students can find it in search now.")
        return redirect(url_for("admin.products"))
    return _product_form_page(form, None, 400 if request.method == "POST" else 200)


@bp.route("/products/<product_id>/edit", methods=["GET", "POST"])
def product_edit(product_id):
    product = catalogue.get(product_id) or abort(404)
    stores = catalogue.store_options()
    if request.method == "GET":
        form = ProductForm(
            stores=stores,
            data={
                "Category": product.Category,
                "StoreId": product.StoreId,
                "Name": product.Name,
                "Brand": product.Brand,
                "Barcode": product.Barcode,
                "Sku": product.Sku,
                "Price": product.Price,
                "SalePrice": product.SalePrice,
                "PhotoUrls": "\n".join(p for p in product.photos if p.startswith("http")),
                "Size": product.Size,
                "Colour": product.Colour,
                "StockStatus": product.StockStatus,
                "StockQuantity": product.StockQuantity,
            },
        )
        return _product_form_page(form, product)
    form = ProductForm(stores=stores)
    if form.validate_on_submit():
        before = catalogue.snapshot(product)
        try:
            _apply_product(product, form)
        except PhotoError as exc:
            db.session.rollback()
            _fail(str(exc))
            return _product_form_page(form, product, 400)
        audit.record(
            me(),
            "product.update",
            f"product:{product.ProductId}",
            {"changes": audit.changes(before, catalogue.snapshot(product))},
        )
        db.session.commit()
        _done(f"Saved {product.Name}.")
        return redirect(url_for("admin.products"))
    return _product_form_page(form, product, 400)


@bp.post("/products/<product_id>/delete")
def product_delete(product_id):
    product = catalogue.get(product_id) or abort(404)
    audit.record(me(), "product.delete", f"product:{product.ProductId}", catalogue.snapshot(product))
    db.session.delete(product)
    db.session.commit()
    _done(f"Deleted {product.Name}.")
    return redirect(url_for("admin.products"))


# -------------------------------------------------------------------------------------------- messages
@bp.get("/messages")
def messages():
    from sqlalchemy import select

    from app.utils.pagination import paginate

    page = paginate(select(ContactMessage).order_by(ContactMessage.CreatedOn.desc()), request.args.get("page"), 20)
    unread = db.session.query(ContactMessage).filter(ContactMessage.IsRead.is_(False)).count()
    return render_template("admin/messages.html", page=page, unread=unread)


@bp.post("/messages/<message_id>/read")
def message_read(message_id):
    message = db.session.get(ContactMessage, message_id) or abort(404)
    message.IsRead = not message.IsRead
    audit.record(me(), "message.read" if message.IsRead else "message.unread", f"message:{message.MessageId}")
    db.session.commit()
    return redirect(url_for("admin.messages", page=request.args.get("page")))


@bp.post("/messages/<message_id>/delete")
def message_delete(message_id):
    message = db.session.get(ContactMessage, message_id) or abort(404)
    audit.record(
        me(), "message.delete", f"message:{message.MessageId}", {"from": message.Email, "subject": message.Subject}
    )
    db.session.delete(message)
    db.session.commit()
    _done("Message deleted.")
    return redirect(url_for("admin.messages"))


# -------------------------------------------------------------------------------------------- pictures
@bp.route("/pictures", methods=["GET", "POST"])
def pictures():
    from app.data.mock_products import CATALOGUE
    from app.services.retail_api import clean_barcode, clean_image_url

    if request.method == "POST":
        barcode = clean_barcode(request.form.get("barcode"))
        lines = [line.strip() for line in (request.form.get("urls") or "").splitlines() if line.strip()]
        urls = [clean_image_url(line) for line in lines]
        back = url_for("admin.pictures", q=request.form.get("q") or None, page=request.form.get("page") or None)
        if not barcode:
            _fail("Enter the product's barcode (8 to 14 digits).")
            return redirect(back)
        if any(u is None or not u.startswith("https://") for u in urls):
            _fail("Every line must be a picture address starting with https://")
            return redirect(back)
        if len(urls) > current_app.config["MAX_PRODUCT_PHOTOS"]:
            _fail(f"At most {current_app.config['MAX_PRODUCT_PHOTOS']} pictures per product.")
            return redirect(back)
        saved = catalogue.set_pictures(barcode, urls)
        audit.record(me(), "picture.set", f"barcode:{barcode}", {"pictures": len(saved)})
        db.session.commit()
        _done(
            f"Saved {len(saved)} picture{'' if len(saved) == 1 else 's'} for {barcode}."
            if saved
            else f"Removed the pictures of {barcode}."
        )
        return redirect(back + f"#p-{barcode}")

    query = (request.args.get("q") or "").strip()[:100]
    words = query.casefold().split()
    items = [i for i in CATALOGUE if all(w in f"{i.name} {i.brand} {i.barcode} {i.category}".casefold() for w in words)]
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        page = 1
    per_page = 20
    pages = max(1, -(-len(items) // per_page))
    page = min(page, pages)
    shown = items[(page - 1) * per_page : page * per_page]
    return render_template(
        "admin/pictures.html",
        items=shown,
        pictures=catalogue.pictures_for({i.barcode for i in shown}),
        q=query,
        page=page,
        pages=pages,
        total=len(items),
        with_pictures=db.session.query(ProductPicture.Barcode).distinct().count(),
    )


# --------------------------------------------------------------------------------------------- courses
@bp.get("/courses")
def courses():
    course_service.ensure_courses()
    rows = [(c, course_service.attempt_stats(c)) for c in course_service.all_courses()]
    return render_template("admin/courses.html", rows=rows)


def _course_or_404(course_id):
    return db.session.get(Course, course_id) or abort(404)


def _course_page(course, status=200, form=None):
    return (
        render_template(
            "admin/course_detail.html",
            course=course,
            stats=course_service.attempt_stats(course),
            form=form or {},
            max_answers=course_service.MAX_ANSWERS,
        ),
        status,
    )


@bp.get("/courses/<course_id>")
def course_detail(course_id):
    return _course_page(_course_or_404(course_id))


@bp.post("/courses/<course_id>/questions")
def question_add(course_id):
    course = _course_or_404(course_id)
    answers = [request.form.get(f"answer_{i}", "") for i in range(4)]
    try:
        correct = int(request.form.get("correct", ""))
    except ValueError:
        correct = None
    try:
        question = course_service.add_question(
            course, request.form.get("text"), answers, correct, request.form.get("explanation")
        )
    except course_service.CourseError as exc:
        db.session.rollback()
        _fail(str(exc))
        return _course_page(course, 400, request.form)
    db.session.flush()
    audit.record(
        me(), "question.create", f"question:{question.QuestionId}", {"course": course.Slug, "text": question.Text}
    )
    db.session.commit()
    _done("Question added.")
    return redirect(url_for("admin.course_detail", course_id=course.CourseId) + f"#q-{question.QuestionId}")


@bp.post("/questions/<question_id>/delete")
def question_delete(question_id):
    question = db.session.get(Question, question_id) or abort(404)
    course_id, text = question.CourseId, question.Text
    course_service.delete_question(question)
    audit.record(me(), "question.delete", f"question:{question_id}", {"text": text})
    db.session.commit()
    _done("Question removed.")
    return redirect(url_for("admin.course_detail", course_id=course_id))


@bp.post("/questions/<question_id>/answers")
def answer_add(question_id):
    question = db.session.get(Question, question_id) or abort(404)
    try:
        answer = course_service.add_answer(question, request.form.get("text"), request.form.get("correct") == "1")
    except course_service.CourseError as exc:
        db.session.rollback()
        _fail(str(exc))
    else:
        db.session.flush()
        audit.record(me(), "answer.create", f"answer:{answer.AnswerId}", {"question": question_id, "text": answer.Text})
        db.session.commit()
        _done("Answer added.")
    return redirect(url_for("admin.course_detail", course_id=question.CourseId) + f"#q-{question_id}")


@bp.post("/answers/<answer_id>/correct")
def answer_correct(answer_id):
    answer = db.session.get(Answer, answer_id) or abort(404)
    course_service.mark_correct(answer)
    audit.record(me(), "answer.mark_correct", f"answer:{answer_id}", {"text": answer.Text})
    db.session.commit()
    _done("Correct answer changed.")
    return redirect(url_for("admin.course_detail", course_id=answer.question.CourseId) + f"#q-{answer.QuestionId}")


@bp.post("/answers/<answer_id>/delete")
def answer_delete(answer_id):
    answer = db.session.get(Answer, answer_id) or abort(404)
    question = answer.question
    try:
        course_service.delete_answer(answer)
    except course_service.CourseError as exc:
        db.session.rollback()
        _fail(str(exc))
    else:
        audit.record(me(), "answer.delete", f"answer:{answer_id}", {"text": answer.Text})
        db.session.commit()
        _done("Answer removed.")
    return redirect(url_for("admin.course_detail", course_id=question.CourseId) + f"#q-{question.QuestionId}")


@bp.get("/courses/<course_id>/attempts")
def course_attempts(course_id):
    course = _course_or_404(course_id)
    return render_template(
        "admin/course_attempts.html",
        course=course,
        attempts=course_service.attempts_of(course),
        stats=course_service.attempt_stats(course),
    )


# ------------------------------------------------------------------------------------------------ audit
@bp.get("/audit")
def audit_log():
    args = request.args
    page = audit.search(
        action=args.get("action") or None,
        admin=(args.get("admin") or "").strip()[:100] or None,
        target=(args.get("target") or "").strip()[:100] or None,
        start=_date(args.get("start")),
        end=_date(args.get("end")),
        page=args.get("page"),
    )
    return render_template(
        "admin/audit.html",
        page=page,
        actions=audit.actions(),
        action=args.get("action", ""),
        admin=args.get("admin", ""),
        target=args.get("target", ""),
        start=args.get("start", ""),
        end=args.get("end", ""),
    )
