"""Notification model (Step 17): what the bell shows.

One row per event that happened to a student ("you have used 75% of your budget", "Full Cream Milk dropped 12%").

``DedupeKey`` is what stops the same event being announced twice. It is unique per student, so writing a second
notification with the same key is refused by the database itself. A key is cleared (set to NULL, which the unique
index ignores) when the condition that caused the notification stops being true, so it can announce again if the
condition comes back; see ``services/notifications.py``.
"""

from sqlalchemy import Index, UniqueConstraint
from sqlalchemy import false as sa_false

from app.extensions import db
from app.utils.dates import utcnow
from app.utils.ids import NOTIFICATION_PREFIX, generate_id

# Notification types (the ``Type`` column) and how the bell draws them.
BUDGET_THRESHOLD = "budget_threshold"  # 50 / 75 / 90 / 100 % of the budget used
OVER_BUDGET = "over_budget"  # the list costs more than the budget
PRICE_DROP = "price_drop"  # an item on the list got at least 10% cheaper
NSFAS_REFERENCE = "nsfas_reference"  # this month's planned spending is above the R1 750 reference
CHEAPER_ALTERNATIVE = "cheaper_alternative"  # a cheaper way to buy a list item was found

TYPE_STYLE = {  # type -> (level, Bootstrap icon)
    BUDGET_THRESHOLD: ("warning", "pie-chart"),
    OVER_BUDGET: ("danger", "exclamation-triangle"),
    PRICE_DROP: ("success", "graph-down-arrow"),
    NSFAS_REFERENCE: ("warning", "info-circle"),
    CHEAPER_ALTERNATIVE: ("info", "tag"),
}
NOTIFICATION_TYPES = tuple(TYPE_STYLE)


class Notification(db.Model):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("UserId", "DedupeKey", name="uq_notifications_user_dedupe"),
        Index("ix_notifications_user_read_created", "UserId", "IsRead", "CreatedOn"),
    )

    NotificationId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(NOTIFICATION_PREFIX))
    UserId = db.Column(db.String(30), db.ForeignKey("users.UserId", ondelete="CASCADE"), nullable=False)
    Title = db.Column(db.String(120), nullable=False)
    Message = db.Column(db.Text, nullable=False)
    Type = db.Column(db.String(40), nullable=False)
    IsRead = db.Column(db.Boolean, nullable=False, default=False, server_default=sa_false())
    CreatedOn = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    # Beyond the brief: the de-duplication key and the page the notification opens.
    DedupeKey = db.Column(db.String(150), nullable=True)
    Url = db.Column(db.String(255), nullable=True)

    @property
    def level(self) -> str:
        return TYPE_STYLE.get(self.Type, ("info", "bell"))[0]

    @property
    def icon(self) -> str:
        return TYPE_STYLE.get(self.Type, ("info", "bell"))[1]

    def __repr__(self) -> str:
        return f"<Notification {self.NotificationId} {self.Type} read={self.IsRead}>"
