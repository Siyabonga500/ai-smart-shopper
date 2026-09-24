"""User management for the admin portal (Step 16): find, edit, deactivate/reactivate and reset the password of a student.

Safety rules, so an admin cannot lock everyone out by accident:
  * you cannot deactivate your own account or take the admin role from yourself;
  * the last active admin cannot be deactivated or demoted.
Callers commit (together with the audit row).
"""

from __future__ import annotations

import secrets
import string

from sqlalchemy import func, or_, select

from app.extensions import db
from app.models import Budget, ListItem, ShoppingList, User
from app.utils.pagination import Page, like_pattern, paginate
from app.utils.passwords import password_problems

ROLES = ("Student", "Admin")
STATUSES = ("active", "deactivated")


class AdminUserError(ValueError):
    """An admin action that is refused; the message is safe to show."""


def role_of(user: User) -> str:
    return "Admin" if user.IsAdmin else "Student"


def get(user_id: str) -> User | None:
    return db.session.get(User, user_id)


def search(
    query: str | None = None, status: str | None = None, role: str | None = None, page=1, per_page: int = 20
) -> Page:
    """Students matching ``query`` (name, e-mail, cellphone or id), newest first."""
    statement = select(User)
    text = (query or "").strip()
    if text:
        pattern = like_pattern(text)
        full_name = User.FirstName + " " + User.LastName
        statement = statement.where(
            or_(
                full_name.ilike(pattern, escape="\\"),
                User.Email.ilike(pattern, escape="\\"),
                User.CellphoneNumber.ilike(pattern, escape="\\"),
                User.UserId == text,
            )
        )
    if status == "active":
        statement = statement.where(User.IsActive.is_(True))
    elif status == "deactivated":
        statement = statement.where(User.IsActive.is_(False))
    if role == "Admin":
        statement = statement.where(User.IsAdmin.is_(True))
    elif role == "Student":
        statement = statement.where(User.IsAdmin.is_(False))
    return paginate(statement.order_by(User.CreatedOn.desc(), User.UserId), page, per_page)


def activity(user: User) -> dict:
    """Counts shown on the user's page."""

    def count(model, *conditions):
        return (
            db.session.scalar(select(func.count()).select_from(model).where(model.UserId == user.UserId, *conditions))
            or 0
        )

    return {
        "budgets": count(Budget),
        "active_budgets": count(Budget, Budget.IsActive.is_(True)),
        "lists": count(ShoppingList),
        "active_lists": count(ShoppingList, ShoppingList.IsActive.is_(True)),
        "items_bought": count(ListItem, ListItem.IsPurchased.is_(True)),
    }


def snapshot(user: User) -> dict:
    """The editable fields, for the audit "from -> to"."""
    return {
        "first_name": user.FirstName,
        "last_name": user.LastName,
        "cellphone": user.CellphoneNumber,
        "address": user.ResidentialAddress,
        "role": role_of(user),
    }


def _active_admin_count(excluding: str | None = None) -> int:
    query = select(func.count()).select_from(User).where(User.IsAdmin.is_(True), User.IsActive.is_(True))
    if excluding:
        query = query.where(User.UserId != excluding)
    return db.session.scalar(query) or 0


def update(
    admin: User, user: User, *, first_name: str, last_name: str, cellphone: str, address: str | None, role: str
) -> None:
    if role not in ROLES:
        raise AdminUserError("Choose a role: Student or Admin.")
    make_admin = role == "Admin"
    if user.IsAdmin and not make_admin:
        if user.UserId == admin.UserId:
            raise AdminUserError("You cannot remove your own admin role. Ask another admin to do it.")
        if user.IsActive and _active_admin_count(excluding=user.UserId) == 0:
            raise AdminUserError("This is the only active admin. Make someone else an admin first.")
    if make_admin and not user.IsAdmin and not user.IsActive:
        raise AdminUserError("Reactivate this account before making it an admin.")
    user.FirstName, user.LastName = first_name, last_name
    user.CellphoneNumber = cellphone
    user.ResidentialAddress = address or None
    user.IsAdmin = make_admin


def deactivate(admin: User, user: User) -> None:
    if user.UserId == admin.UserId:
        raise AdminUserError("You cannot deactivate your own account.")
    if not user.IsActive:
        raise AdminUserError("This account is already deactivated.")
    if user.IsAdmin and _active_admin_count(excluding=user.UserId) == 0:
        raise AdminUserError("This is the only active admin, so it cannot be deactivated.")
    user.IsActive = False


def reactivate(user: User) -> None:
    if user.IsActive:
        raise AdminUserError("This account is already active.")
    user.IsActive = True


def temporary_password(length: int = 14) -> str:
    """A random password that satisfies the password policy (letters of both cases, a digit and a symbol)."""
    pools = [string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#$%&*?"]
    letters = string.ascii_letters + string.digits + "!@#$%&*?"
    for _ in range(100):
        chars = [secrets.choice(pool) for pool in pools] + [secrets.choice(letters) for _ in range(length - len(pools))]
        secrets.SystemRandom().shuffle(chars)
        candidate = "".join(chars)
        if not password_problems(candidate):
            return candidate
    raise RuntimeError("Could not generate a password")  # unreachable in practice


def reset_password(user: User) -> str:
    """Give ``user`` a new random password and return it (shown once to the admin, never stored or logged)."""
    if not user.has_password:
        raise AdminUserError("This student signs in with Microsoft, so there is no password to reset.")
    if not user.IsActive:
        raise AdminUserError("Reactivate this account before resetting its password.")
    password = temporary_password()
    user.set_password(password)
    return password


def find_by_email(email: str) -> User | None:
    return db.session.scalar(select(User).where(func.lower(User.Email) == (email or "").strip().lower()))


def create(*, first_name: str, last_name: str, email: str, cellphone: str | None, password: str, role: str) -> User:
    """A new account made by an admin (Admin > Users > Add user). It can sign in at once with ``password``."""
    if role not in ROLES:
        raise AdminUserError("Choose a role: Student or Admin.")
    if find_by_email(email) is not None:
        raise AdminUserError("An account with that email address already exists.")
    user = User(
        FirstName=first_name,
        LastName=last_name,
        Email=email.strip().lower(),
        CellphoneNumber=cellphone or None,
        IsAdmin=role == "Admin",
        IsActive=True,
    )
    user.set_password(password)
    db.session.add(user)
    return user


def ensure_default_admin(email: str, password: str, *, reset_password: bool = False) -> tuple[User, bool]:
    """The built-in admin account (``DEFAULT_ADMIN_EMAIL``). Returns ``(user, created)``; the caller commits.

    An existing account with that address is made an active admin; its password is only replaced when
    ``reset_password`` is set (``flask seed-admin`` does that, the first sign-in does not).
    """
    user = find_by_email(email)
    if user is None:
        user = User(FirstName="Siyabonga", LastName="Admin", Email=email.strip().lower(), IsAdmin=True, IsActive=True)
        user.set_password(password)
        db.session.add(user)
        return user, True
    user.IsAdmin, user.IsActive = True, True
    if reset_password or not user.has_password:
        user.set_password(password)
    return user, False
