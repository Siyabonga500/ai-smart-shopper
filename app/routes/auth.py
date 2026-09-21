"""Authentication (Step 3): register, login, logout and Microsoft sign-in.

    GET/POST /register        create an account (email + password, or finish a Microsoft sign-up)
    GET/POST /login           email + password
    GET      /logout          (POST works too, and is what the nav button uses)
    GET      /auth/microsoft  start Microsoft sign-in (flask-dance serves /auth/azure/authorized)

Microsoft accounts: signing in with Microsoft for the first time does not create
an account straight away, because registration needs the SA ID, cellphone, race
and terms. The Microsoft profile is parked in the session and the student lands on
``/register`` with their name and email pre-filled.
"""

import os

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.extensions import bcrypt, db, login_limiter
from app.forms import LoginForm, RegistrationForm
from app.models import User
from app.services.accounts import apply_location, build_user_from_registration, utcnow
from app.utils.security import is_safe_redirect_url

bp = Blueprint("auth", __name__)

MS_PENDING_KEY = "ms_pending"
NEXT_KEY = "post_login_next"
GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me"

_dummy_hashes: dict[int, bytes] = {}


# --- helpers --------------------------------------------------------------------------
DEACTIVATED_MESSAGE = "This account has been deactivated. Please contact support if you think this is a mistake."


def _safe_next(target: str | None) -> str | None:
    return target if is_safe_redirect_url(target, request.host) else None


def _after_login_url(target: str | None = None) -> str:
    return _safe_next(target) or url_for("dashboard.index")


def _spend_time_like_a_real_check(password: str) -> None:
    """Unknown e-mail: still run one bcrypt comparison so timing does not reveal it."""
    rounds = current_app.config["BCRYPT_LOG_ROUNDS"]
    if rounds not in _dummy_hashes:
        _dummy_hashes[rounds] = bcrypt.generate_password_hash("not-a-real-password", rounds)
    try:
        bcrypt.check_password_hash(_dummy_hashes[rounds], password)
    except ValueError:
        pass


def _limiter_keys(email: str) -> tuple[str, str]:
    return f"ip:{request.remote_addr or 'unknown'}", f"email:{email}"


def _start_session(user: User, remember: bool = False) -> None:
    user.LastLogin = utcnow()
    db.session.commit()
    login_user(user, remember=remember)


# --- register -------------------------------------------------------------------------------
@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    pending = session.get(MS_PENDING_KEY)
    form = RegistrationForm(microsoft=bool(pending))
    if pending:
        form.Email.data = pending["email"]  # the Microsoft address cannot be changed here
        if request.method == "GET":
            form.FirstName.data = pending.get("first_name") or ""
            form.LastName.data = pending.get("last_name") or ""

    if form.validate_on_submit():
        user = build_user_from_registration(form, microsoft_id=pending["id"] if pending else None)
        apply_location(user, form.coordinates, user.ResidentialAddress)
        try:
            db.session.commit()
        except IntegrityError:
            # Someone registered the same email / ID between validation and commit.
            db.session.rollback()
            current_app.logger.info("Registration lost a uniqueness race for %s", form.Email.data)
            form.Email.errors.append("That email or ID number is already registered.")
        else:
            session.pop(MS_PENDING_KEY, None)
            _start_session(user)
            flash(f"Welcome to {current_app.config['APP_NAME']}, {user.FirstName}!", "success")
            return redirect(url_for("dashboard.index"))

    return render_template("auth/register.html", form=form, microsoft_pending=pending)


# --- login / logout ----------------------------------------------------------------------------------
@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    next_url = _safe_next(request.args.get("next"))
    if next_url:
        session[NEXT_KEY] = next_url  # survives the Microsoft round trip too

    if form.validate_on_submit():
        email = form.Email.data
        limiting = current_app.config["RATELIMIT_ENABLED"]
        keys = _limiter_keys(email)
        if limiting and any(login_limiter.is_blocked(key) for key in keys):
            flash("Too many failed sign-in attempts. Please wait a few minutes and try again.", "danger")
            return render_template("auth/login.html", form=form, next_url=next_url, **_login_context()), 429

        user = db.session.scalar(select(User).where(func.lower(User.Email) == email))
        if user is not None and user.check_password(form.Password.data):
            login_limiter.reset(keys[1])
            if not user.IsActive:  # only said after the right password, so it reveals nothing to a stranger
                flash(DEACTIVATED_MESSAGE, "danger")
                return render_template("auth/login.html", form=form, next_url=next_url, **_login_context()), 403
            _start_session(user, remember=form.Remember.data)
            remembered_next = session.pop(NEXT_KEY, None)
            return redirect(_after_login_url(next_url or remembered_next))

        if user is None or not user.PasswordHash:  # an unknown address and a Microsoft-only account take equally long
            _spend_time_like_a_real_check(form.Password.data)
        if limiting:
            for key in keys:
                login_limiter.record(key)
        flash("Incorrect email or password.", "danger")
        return render_template("auth/login.html", form=form, next_url=next_url, **_login_context()), 401

    return render_template("auth/login.html", form=form, next_url=next_url, **_login_context())


def _login_context() -> dict:
    return {"microsoft_enabled": "azure" in current_app.blueprints}


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    was_signed_in = current_user.is_authenticated
    logout_user()
    session.pop(MS_PENDING_KEY, None)
    session.pop(NEXT_KEY, None)
    if was_signed_in:
        flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


# --- Microsoft ---------------------------------------------------------------------------------------
@bp.get("/auth/microsoft")
def microsoft():
    """Entry point for the "Sign in with Microsoft" buttons."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if "azure" not in current_app.blueprints:
        flash("Microsoft sign-in is not set up on this server yet. Please use your email and password.", "warning")
        return redirect(url_for("auth.login"))
    return redirect(url_for("azure.login"))


def complete_microsoft_login(profile: dict):
    """Turn a Microsoft Graph ``/me`` profile into a session (or a pending registration).

    Returns a Flask response. Kept separate from the flask-dance signal so it is easy to test.
    """
    ms_id = (profile or {}).get("id")
    email = ((profile or {}).get("mail") or (profile or {}).get("userPrincipalName") or "").strip().lower()
    if not ms_id or not email:
        flash("Microsoft did not share an email address with us, so we could not sign you in.", "danger")
        return redirect(url_for("auth.login"))

    user = db.session.scalar(select(User).where(User.MicrosoftId == ms_id))
    if user is not None:
        if not user.IsActive:
            flash(DEACTIVATED_MESSAGE, "danger")
            return redirect(url_for("auth.login"))
        _start_session(user)
        return redirect(_after_login_url(session.pop(NEXT_KEY, None)))

    if db.session.scalar(select(User.UserId).where(func.lower(User.Email) == email)):
        # Never attach a Microsoft identity to an existing account just because the email matches:
        # the address on a work/school Microsoft account is not proof of ownership.
        flash("An account with this email already exists. Sign in with your email and password instead.", "warning")
        return redirect(url_for("auth.login"))

    session[MS_PENDING_KEY] = {
        "id": ms_id,
        "email": email,
        "first_name": profile.get("givenName") or "",
        "last_name": profile.get("surname") or "",
    }
    flash("Almost there - complete your details to finish creating your account.", "info")
    return redirect(url_for("auth.register"))


def _on_microsoft_authorized(blueprint, token):
    """flask-dance signal receiver. Returning a response also stops flask-dance storing the token."""
    if not token:
        flash("Microsoft sign-in was cancelled or failed. Please try again.", "danger")
        return redirect(url_for("auth.login"))
    try:
        response = blueprint.session.get(GRAPH_ME_URL, timeout=10)
        profile = response.json() if response.ok else None
    except Exception:  # noqa: BLE001 - network / JSON errors: treat as a failed sign-in
        current_app.logger.exception("Could not read the Microsoft profile")
        profile = None
    if not profile:
        flash("We could not read your Microsoft profile. Please try again.", "danger")
        return redirect(url_for("auth.login"))
    return complete_microsoft_login(profile)


def _on_microsoft_error(blueprint, error=None, error_description=None, error_uri=None):
    current_app.logger.warning("Microsoft OAuth error: %s (%s)", error, error_description)
    flash("Microsoft sign-in failed. Please try again.", "danger")
    return redirect(url_for("auth.login"))


def build_microsoft_blueprint(app):
    """Return the flask-dance Azure blueprint, or ``None`` when no credentials are configured."""
    client_id = app.config.get("MICROSOFT_CLIENT_ID")
    client_secret = app.config.get("MICROSOFT_CLIENT_SECRET")
    if not (client_id and client_secret):
        app.logger.info("Microsoft OAuth not configured (set MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET).")
        return None

    from flask_dance.consumer import oauth_authorized, oauth_error
    from flask_dance.contrib.azure import make_azure_blueprint

    # Microsoft returns scopes in a different form than requested.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    if app.debug:
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # allow http://localhost callbacks

    blueprint = make_azure_blueprint(
        client_id=client_id,
        client_secret=client_secret,
        tenant=app.config.get("MICROSOFT_TENANT", "common"),
        scope=["openid", "email", "profile", "User.Read"],
        redirect_to="dashboard.index",
    )
    # Module-level receivers (blinker keeps only weak references).
    oauth_authorized.connect(_on_microsoft_authorized, sender=blueprint)
    oauth_error.connect(_on_microsoft_error, sender=blueprint)
    return blueprint
