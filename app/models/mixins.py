"""Shared model behaviour."""

from datetime import datetime, timezone


class ActivatableMixin:
    """``activate()`` / ``deactivate()`` for models with ``IsActive`` and a closed timestamp.

    Subclasses name their closed-timestamp column in ``closed_field``
    (``ClosedDate`` on Budget, ``DateClosed`` on ShoppingList).

    The helpers only change the object; the caller commits the session, so
    several changes (for example closing a budget and its shopping list when
    the student presses "Done") stay in one transaction.
    """

    closed_field = "ClosedDate"

    def activate(self) -> None:
        """Mark active again and clear the closed timestamp."""
        self.IsActive = True
        setattr(self, self.closed_field, None)

    def deactivate(self, when: datetime | None = None) -> None:
        """Mark inactive and stamp the closed timestamp (UTC now unless ``when`` is given).

        Calling it again on something already closed keeps the original
        timestamp, unless ``when`` is passed explicitly.
        """
        self.IsActive = False
        if getattr(self, self.closed_field) is None or when is not None:
            setattr(self, self.closed_field, when or datetime.now(timezone.utc))
