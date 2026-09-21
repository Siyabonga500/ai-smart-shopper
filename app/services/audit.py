"""The admin audit trail (Step 16): who did what, to what, and when.

Every admin action that changes something (and every look at a student's personal details) adds one row.
``record`` only adds the row to the session: the route commits it together with the change itself, so an action
and its audit row are saved, or rolled back, together. Nothing edits or deletes audit rows.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select

from app.extensions import db
from app.models import AuditLog
from app.utils.dates import sast_day_bounds, utcnow
from app.utils.pagination import Page, like_pattern, paginate

_SECRET_WORDS = ("password", "secret", "token", "apikey", "api_key")


def _is_secret_name(name: str) -> bool:
    name = name.lower()
    return any(word in name for word in _SECRET_WORDS) or name == "key" or name.endswith("_key")


def scrub(detail: Any) -> Any:
    """A copy of ``detail`` that is safe to store: values under secret-looking names are replaced, text is capped."""
    if isinstance(detail, dict):
        return {str(k): ("[hidden]" if _is_secret_name(str(k)) else scrub(v)) for k, v in detail.items()}
    if isinstance(detail, (list, tuple)):
        return [scrub(v) for v in detail][:50]
    if isinstance(detail, (datetime, date)):
        return detail.isoformat()
    if isinstance(detail, str):
        return detail[:300]
    if isinstance(detail, (int, float, bool)) or detail is None:
        return detail
    return str(detail)[:300]


def record(admin, action: str, target: str | None = None, detail: dict | None = None) -> AuditLog:
    row = AuditLog(
        AdminId=admin.UserId,
        AdminEmail=(admin.Email or "")[:100],
        Action=action[:60],
        Target=target[:200] if target else None,
        Detail=scrub(detail) if detail else None,
        CreatedOn=utcnow(),
    )
    db.session.add(row)
    return row


def changes(before: dict, after: dict) -> dict:
    """``{field: {"from": old, "to": new}}`` for the fields that differ."""
    return {key: {"from": before.get(key), "to": after[key]} for key in after if before.get(key) != after[key]}


def actions() -> list[str]:
    return list(db.session.scalars(select(AuditLog.Action).distinct().order_by(AuditLog.Action)))


def search(
    action: str | None = None,
    admin: str | None = None,
    target: str | None = None,
    start: date | None = None,
    end: date | None = None,
    page=1,
    per_page: int = 25,
) -> Page:
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.Action == action)
    if admin:
        query = query.where(AuditLog.AdminEmail.ilike(like_pattern(admin), escape="\\") | (AuditLog.AdminId == admin))
    if target:
        query = query.where(AuditLog.Target.ilike(like_pattern(target), escape="\\"))
    lower, upper = sast_day_bounds(start, end)
    if lower is not None:
        query = query.where(AuditLog.CreatedOn >= lower)
    if upper is not None:
        query = query.where(AuditLog.CreatedOn < upper)
    return paginate(query.order_by(AuditLog.CreatedOn.desc(), AuditLog.AuditId.desc()), page, per_page)


def count() -> int:
    return db.session.scalar(select(func.count()).select_from(AuditLog)) or 0
