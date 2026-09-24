"""Profile views (Step 5; PDF 'Other Views' 7-11).

GET       /profile                         details, account stats, preferences
GET/POST  /profile/edit                    name, cellphone, address (with map), profile picture
POST      /profile/password                change password
POST      /profile/preferences             add a preference
POST      /profile/preferences/<id>/delete remove one
"""

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user
from sqlalchemy import func, select

from app.extensions import db, login_limiter
from app.forms import ChangePasswordForm, PreferenceForm, ProfileForm
from app.models import Preference
from app.services.account_stats import account_stats
from app.services.accounts import apply_location
from app.services.photos import PhotoError, delete_profile_photo, save_profile_photo
from app.utils.validators import validate_sa_phone

bp = Blueprint("profile", __name__, url_prefix="/profile")

_SAME_PLACE = 1e-5  # degrees (about a metre): pins closer than this count as "not moved"


def _same_coordinates(a, b) -> bool:
    return bool(a and b) and abs(a[0] - b[0]) < _SAME_PLACE and abs(a[1] - b[1]) < _SAME_PLACE


def _preferences_by_type(user_id: str) -> dict[str, list[Preference]]:
    rows = db.session.scalars(
        select(Preference).where(Preference.UserId == user_id).order_by(Preference.PreferenceValue)
    ).all()
    grouped = {kind: [] for kind in current_app.config["PREFERENCE_TYPES"]}
    for row in rows:
        grouped.setdefault(row.PreferenceType, []).append(row)  # keeps rows with older/unknown types visible
    return grouped


def _render_profile(preference_form=None, status=200):
    preferences = _preferences_by_type(current_user.UserId)
    return (
        render_template(
            "profile/view.html",
            stats=account_stats(current_user.UserId),
            preferences=preferences,
            preference_total=sum(len(rows) for rows in preferences.values()),
            preference_form=preference_form or PreferenceForm(),
        ),
        status,
    )


@bp.get("", strict_slashes=False)
@login_required
def index():
    return _render_profile()


# --- edit -------------------------------------------------------------------------------------------
@bp.route("/edit", methods=["GET", "POST"])
@login_required
def edit():
    user = current_user._get_current_object()
    form = ProfileForm(obj=user)
    password_form = ChangePasswordForm(prefix="pw", require_current=user.has_password)
    if request.method == "GET":
        form.select_residence_for(user.ResidentialAddress)

    if request.method == "POST" and form.validate_on_submit():
        old_address = (user.ResidentialAddress or "").strip()
        old_position = (user.Latitude, user.Longitude) if user.has_location else None
        submitted_position = form.coordinates
        address_changed = form.ResidentialAddress.data != old_address

        new_photo = None
        upload = form.ProfilePic.data
        try:
            if upload is not None and getattr(upload, "filename", ""):
                new_photo = save_profile_photo(upload)
        except PhotoError as exc:
            form.ProfilePic.errors.append(str(exc))

        if not form.ProfilePic.errors:
            user.FirstName = form.FirstName.data
            user.LastName = form.LastName.data
            user.CellphoneNumber = validate_sa_phone(form.CellphoneNumber.data)
            user.ResidentialAddress = form.ResidentialAddress.data

            # A pin sent back unchanged next to an edited address is stale (JavaScript was off).
            if address_changed and _same_coordinates(submitted_position, old_position):
                submitted_position = None
            apply_location(user, submitted_position, user.ResidentialAddress, address_changed=address_changed)

            old_photo = user.ProfilePic
            if new_photo:
                user.ProfilePic = new_photo
            elif form.RemovePhoto.data:
                user.ProfilePic = None
            db.session.commit()
            if user.ProfilePic != old_photo:
                delete_profile_photo(old_photo)

            flash("Your profile has been updated.", "success")
            return redirect(url_for("profile.index"))

    return render_template(
        "profile/edit.html",
        form=form,
        password_form=password_form,
    ), (400 if request.method == "POST" and form.errors else 200)


# --- password -----------------------------------------------------------------------------------------------
@bp.post("/password")
@login_required
def change_password():
    user = current_user._get_current_object()
    form = ChangePasswordForm(prefix="pw", require_current=user.has_password)
    limiting = current_app.config["RATELIMIT_ENABLED"]
    limiter_key = f"pwchange:{user.UserId}"

    if form.validate_on_submit():
        problem = None
        if user.has_password:
            if limiting and login_limiter.is_blocked(limiter_key):
                problem = "Too many attempts. Please wait a few minutes and try again."
            elif not user.check_password(form.CurrentPassword.data or ""):
                if limiting:
                    login_limiter.record(limiter_key)
                problem = "Your current password is incorrect."
                form.CurrentPassword.errors = [problem]
            elif form.CurrentPassword.data == form.NewPassword.data:
                problem = "Choose a password you have not used just now."
                form.NewPassword.errors = [problem]
        if problem is None:
            user.set_password(form.NewPassword.data)
            db.session.commit()
            login_limiter.reset(limiter_key)
            # The new password ended every session, this one included. Sign this browser straight back in, and keep
            # "remember me" only if it was on. Every other device has to sign in again.
            login_user(
                user,
                remember=bool(request.cookies.get(current_app.config.get("REMEMBER_COOKIE_NAME", "remember_token"))),
            )
            flash(
                "Your password has been changed. Any other device you were signed in on has been signed out.", "success"
            )
            return redirect(url_for("profile.index"))
        if not form.errors:
            flash(problem, "danger")

    profile_form = ProfileForm(obj=user, formdata=None)
    return render_template("profile/edit.html", form=profile_form, password_form=form), 400


# --- preferences --------------------------------------------------------------------------------------------------
@bp.post("/preferences")
@login_required
def add_preference():
    form = PreferenceForm()
    if form.validate_on_submit():
        kind, value = form.PreferenceType.data, form.PreferenceValue.data
        count = db.session.scalar(
            select(func.count()).select_from(Preference).where(Preference.UserId == current_user.UserId)
        )
        exists = db.session.scalar(
            select(Preference.PreferenceId).where(
                Preference.UserId == current_user.UserId,
                Preference.PreferenceType == kind,
                func.lower(Preference.PreferenceValue) == value.lower(),
            )
        )
        if count >= current_app.config["MAX_PREFERENCES_PER_USER"]:
            form.PreferenceValue.errors.append(
                f"You can save up to {current_app.config['MAX_PREFERENCES_PER_USER']} preferences. Remove one first."
            )
        elif exists:
            form.PreferenceValue.errors.append(f"“{value}” is already in your {kind} preferences.")
        else:
            db.session.add(Preference(UserId=current_user.UserId, PreferenceType=kind, PreferenceValue=value))
            db.session.commit()
            flash(f"Added “{value}” to your {kind} preferences.", "success")
            return redirect(url_for("profile.index") + "#preferences")
    return _render_profile(preference_form=form, status=400)


@bp.post("/preferences/<preference_id>/delete")
@login_required
def delete_preference(preference_id):
    preference = db.session.scalar(
        select(Preference).where(Preference.PreferenceId == preference_id, Preference.UserId == current_user.UserId)
    )
    if preference is None:
        flash("That preference no longer exists.", "warning")
    else:
        db.session.delete(preference)
        db.session.commit()
        flash("Preference removed.", "info")
    return redirect(url_for("profile.index") + "#preferences")
