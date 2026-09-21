"""JSON endpoints for the bell (Step 17).

    GET   /api/notifications                  ?limit=&unread=1   unread first, newest first; also runs the checks
    POST  /api/notifications/<id>/read        mark one as read
    POST  /api/notifications/read-all         mark every notification as read

Both need a signed-in student. Another student's notification id answers 404, exactly like an id that does not exist.
"""

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.extensions import db
from app.services import notifications

bp = Blueprint("notifications_api", __name__, url_prefix="/api/notifications")

MAX_LIMIT = 100


def _limit(default: int = 20) -> int:
    try:
        value = int(request.args.get("limit", default))
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_LIMIT))


@bp.get("")
@login_required
def list_notifications():
    user = current_user._get_current_object()
    try:
        notifications.evaluate(user, prices=True)  # new events first, so the list is up to date
    except Exception:  # a failing rule must not hide the notifications already there
        db.session.rollback()
        notifications.log.exception("Notification check failed for %s", user.UserId)
    unread_only = request.args.get("unread", "").lower() in {"1", "true", "yes"}
    return jsonify(notifications.payload(user.UserId, _limit(), unread_only))


@bp.post("/read-all")
@login_required
def read_all():
    changed = notifications.mark_all_read(current_user.UserId)
    return jsonify(changed=changed, unread=0)


@bp.post("/<notification_id>/read")
@login_required
def read_one(notification_id: str):
    row = notifications.mark_read(current_user.UserId, notification_id)
    if row is None:
        response = jsonify(error="Notification not found.")
        response.status_code = 404
        return response
    return jsonify(notification=notifications.to_dict(row), unread=notifications.unread_count(current_user.UserId))
