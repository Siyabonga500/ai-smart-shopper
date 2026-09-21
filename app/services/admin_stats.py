"""The admin dashboard numbers and chart series (Step 16)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.extensions import db
from app.models import Budget, ShoppingList, User
from app.services import integrations
from app.utils.dates import MONTHS_SHORT, sast_day_bounds, to_sast, today_sast

WEEKDAYS_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # not strftime: it follows the server's locale


def _count(model, *conditions) -> int:
    return db.session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def totals() -> dict:
    return {
        "users": _count(User),
        "active_users": _count(User, User.IsActive.is_(True)),
        "deactivated_users": _count(User, User.IsActive.is_(False)),
        "admins": _count(User, User.IsAdmin.is_(True), User.IsActive.is_(True)),
        "active_budgets": _count(Budget, Budget.IsActive.is_(True)),
        "active_lists": _count(ShoppingList, ShoppingList.IsActive.is_(True)),
        "api_calls_today": integrations.calls_today(),
    }


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())  # Monday


def registrations_by_week(weeks: int = 8, today: date | None = None) -> list[dict]:
    """New students per week (Monday to Sunday, SAST) for the last ``weeks`` weeks, oldest first."""
    today = today or today_sast()
    first = week_start(today) - timedelta(weeks=weeks - 1)
    lower, _ = sast_day_bounds(first, None)
    tally = {first + timedelta(weeks=n): 0 for n in range(weeks)}
    for created in db.session.scalars(select(User.CreatedOn).where(User.CreatedOn >= lower)):
        local = to_sast(created)
        if isinstance(local, datetime):
            start = week_start(local.date())
            if start in tally:
                tally[start] += 1
    return [{"week_start": start, "count": count} for start, count in tally.items()]


def dashboard(today: date | None = None) -> dict:
    api = integrations.calls_per_day(7, today)
    return {
        "totals": totals(),
        "registrations": registrations_by_week(8, today),
        "api_calls": api,
        "active_source": integrations.active_source(),
        "active_source_label": integrations.SOURCE_INFO.get(
            integrations.active_source(), integrations.SOURCE_INFO["mock"]
        ).label,
    }


def chart_payload(data: dict) -> dict:
    """The dashboard data as plain JSON for Chart.js."""
    return {
        "registrations": {
            "labels": [
                f'{row["week_start"].day} {MONTHS_SHORT[row["week_start"].month - 1]}' for row in data["registrations"]
            ],
            "values": [row["count"] for row in data["registrations"]],
        },
        "apiCalls": {
            "labels": [f'{WEEKDAYS_SHORT[row["date"].weekday()]} {row["date"].day}' for row in data["api_calls"]],
            "ok": [row["ok"] for row in data["api_calls"]],
            "error": [row["error"] for row in data["api_calls"]],
        },
    }
