"""Admin portal tables (Step 16).

``CategoryMapping``   raw category names from a retail API -> the app's budget categories.
``IntegrationSetting`` one row per data source (Apify, Parse.bot, Mock): is it the active one, its encrypted API
                      key, and when it was last synced.
``AuditLog``          one row per admin action. Rows are only ever added: there is no edit or delete anywhere in the app.
"""

from sqlalchemy import Index
from sqlalchemy import false as sa_false

from app.extensions import db
from app.utils.dates import utcnow
from app.utils.ids import AUDIT_PREFIX, MAPPING_PREFIX, generate_id

MAPPED_CATEGORIES = ("Grocery", "Toiletries", "Clothes", "Electronics", "Combined")
SOURCES = ("loyaltyhub", "buyly", "apify", "parsebot", "mock")


class CategoryMapping(db.Model):
    __tablename__ = "category_mappings"

    MappingId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(MAPPING_PREFIX))
    RawCategory = db.Column(db.String(150), nullable=False)  # as the admin typed it
    RawKey = db.Column(db.String(150), nullable=False, unique=True)  # lower-cased, single spaces: what is matched
    MappedCategory = db.Column(db.String(30), nullable=False)
    CreatedOn = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    UpdatedOn = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class IntegrationSetting(db.Model):
    __tablename__ = "integration_settings"

    Source = db.Column(db.String(20), primary_key=True)  # "loyaltyhub" | "buyly" | "apify" | "parsebot" | "mock"
    IsActive = db.Column(db.Boolean, nullable=False, default=False, server_default=sa_false())
    ApiKeyEncrypted = db.Column(db.Text, nullable=True)
    LastSyncOn = db.Column(db.DateTime(timezone=True), nullable=True)
    LastSyncStatus = db.Column(db.String(10), nullable=True)  # "ok" | "error"
    LastSyncMessage = db.Column(db.String(300), nullable=True)
    UpdatedOn = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_created", "CreatedOn"), Index("ix_audit_logs_admin", "AdminId", "CreatedOn"))

    AuditId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(AUDIT_PREFIX))
    # No foreign key on purpose: the trail must survive the admin's account being deleted, so the id and e-mail
    # are copied here.
    AdminId = db.Column(db.String(30), nullable=False)
    AdminEmail = db.Column(db.String(100), nullable=False)
    Action = db.Column(db.String(60), nullable=False)  # "user.deactivate", "store.update", ...
    Target = db.Column(db.String(200), nullable=True)  # "user:USR-...", "store:STR-..."
    Detail = db.Column(db.JSON, nullable=True)  # what changed; never a password or API key
    CreatedOn = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
