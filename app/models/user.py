"""User model - Budget Shopper Views Plan PDF, pages 1 (fields) and 18 (code).

Differences from the PDF listing, all forced by the framework or requested:
  * ``db`` comes from ``app.extensions`` instead of being created here.
  * ``UserId`` gets a Python-side default (``USR-...``, see ``app.utils.ids``)
    and ``get_id`` returns it (plus a password fingerprint), because Flask-Login otherwise looks for an
    attribute called ``id``.
  * Deleting a User cascades to everything they own (Budgets -> SubBudgets /
    ShoppingLists -> ListItems, and Preferences).

Step 3 (registration with e-mail + password) needed more than the PDF has:
  * ``MicrosoftId`` is now optional (the PDF has it NOT NULL). It stays unique,
    so an account can be created with a password, with Microsoft, or both.
  * New columns: PasswordHash, SAIdHash, SAIdLast4, DateOfBirth, Gender, Race,
    TermsAcceptedOn.
  * Step 4 adds Latitude / Longitude: the cached map position of ResidentialAddress
    (used to sort stores by distance). Durban is the fallback when they are empty.

The full SA ID number is never stored: ``SAIdHash`` is an HMAC fingerprint used
to stop the same ID registering twice, ``SAIdLast4`` is for masked display.
"""

import hashlib
import hmac

from flask import current_app
from flask_login import UserMixin
from sqlalchemy import false as sa_false
from sqlalchemy import func
from sqlalchemy import true as sa_true

from app.extensions import bcrypt, db, login_manager
from app.utils.ids import USER_PREFIX, generate_id
from app.utils.sa_id import mask_sa_id


class User(UserMixin, db.Model):
    __tablename__ = "users"

    UserId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(USER_PREFIX))
    FirstName = db.Column(db.String(50), nullable=False)
    LastName = db.Column(db.String(50), nullable=False)
    Email = db.Column(db.String(100), unique=True, nullable=False)
    MicrosoftId = db.Column(db.String(100), unique=True, nullable=True)
    ResidentialAddress = db.Column(db.Text, nullable=True)
    ProfilePic = db.Column(db.String(255), nullable=True)
    CellphoneNumber = db.Column(db.String(20), nullable=True)
    CreatedOn = db.Column(db.DateTime(timezone=True), server_default=func.now())
    LastLogin = db.Column(db.DateTime(timezone=True), server_default=func.now())

    # --- Step 3: sign-up details ---------------------------------------------
    PasswordHash = db.Column(db.String(128), nullable=True)  # bcrypt; NULL for Microsoft-only accounts
    SAIdHash = db.Column(db.String(64), nullable=True)  # HMAC-SHA256 fingerprint of the SA ID
    SAIdLast4 = db.Column(db.String(4), nullable=True)
    DateOfBirth = db.Column(db.Date, nullable=True)  # derived from the SA ID
    Gender = db.Column(db.String(20), nullable=True)  # pre-filled from the SA ID, editable
    Race = db.Column(db.String(30), nullable=True)
    TermsAcceptedOn = db.Column(db.DateTime(timezone=True), nullable=True)

    # --- Step 4: cached map position of ResidentialAddress ----------------------------
    Latitude = db.Column(db.Float, nullable=True)
    Longitude = db.Column(db.Float, nullable=True)

    # --- Step 16: roles and account status --------------------------------------------
    IsAdmin = db.Column(db.Boolean, nullable=False, default=False, server_default=sa_false())
    IsActive = db.Column(
        db.Boolean, nullable=False, default=True, server_default=sa_true()
    )  # False = deactivated by an admin

    __table_args__ = (db.UniqueConstraint("SAIdHash", name="uq_users_SAIdHash"),)

    # Relationships - deleting a user deletes everything below them.
    budgets = db.relationship("Budget", backref="user", lazy=True, cascade="save-update, merge, delete")
    sub_budgets = db.relationship("SubBudget", backref="user", lazy=True, cascade="save-update, merge, delete")
    shopping_lists = db.relationship("ShoppingList", backref="user", lazy=True, cascade="save-update, merge, delete")
    list_items = db.relationship("ListItem", backref="user", lazy=True, cascade="save-update, merge, delete")
    preferences = db.relationship("Preference", backref="user", lazy=True, cascade="save-update, merge, delete")
    notifications = db.relationship("Notification", backref="user", lazy=True, cascade="save-update, merge, delete")

    def _session_stamp(self) -> str:
        """A short fingerprint of the current password hash (nothing that could be used to guess the password)."""
        return hashlib.sha256((self.PasswordHash or "no-password").encode()).hexdigest()[:16]

    def get_id(self) -> str:
        """What Flask-Login keeps in the session and the remember-me cookie: ``<UserId>:<password fingerprint>``.

        Because the fingerprint changes with the password, changing or resetting a password signs the account out
        everywhere else, remember-me cookies included (see :func:`load_user`).
        """
        return f"{self.UserId}:{self._session_stamp()}"

    @property
    def is_active(self) -> bool:  # Flask-Login: a deactivated account cannot sign in
        return bool(self.IsActive)

    @property
    def is_admin(self) -> bool:
        return bool(self.IsAdmin) and self.is_active

    # --- passwords (bcrypt) -----------------------------------------------------
    @property
    def has_password(self) -> bool:
        return bool(self.PasswordHash)

    def set_password(self, password: str) -> None:
        self.PasswordHash = bcrypt.generate_password_hash(password, current_app.config["BCRYPT_LOG_ROUNDS"]).decode(
            "utf-8"
        )

    def check_password(self, password: str) -> bool:
        if not self.PasswordHash:
            return False
        try:
            return bcrypt.check_password_hash(self.PasswordHash, password)
        except ValueError:  # malformed hash
            return False

    @property
    def has_location(self) -> bool:
        return self.Latitude is not None and self.Longitude is not None

    @property
    def masked_sa_id(self) -> str:
        return mask_sa_id(self.SAIdLast4)

    @property
    def FullName(self) -> str:
        return f"{self.FirstName} {self.LastName}".strip()


@login_manager.user_loader
def load_user(session_id: str):
    """The signed-in user, or ``None`` (signed out) when the account was deactivated or its password has changed since."""
    user_id, _, stamp = (session_id or "").partition(":")
    user = db.session.get(User, user_id) if user_id else None
    if user is None or not user.IsActive or not hmac.compare_digest(stamp, user._session_stamp()):
        return None
    return user
