"""A small paginator for the admin lists (the app needs nothing bigger)."""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select

from app.extensions import db

DEFAULT_PER_PAGE = 20


@dataclass(frozen=True)
class Page:
    items: list = field(default_factory=list)
    total: int = 0
    page: int = 1
    per_page: int = DEFAULT_PER_PAGE

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def first(self) -> int:
        return 0 if not self.total else (self.page - 1) * self.per_page + 1

    @property
    def last(self) -> int:
        return min(self.total, self.page * self.per_page)

    def window(self, size: int = 2) -> list[int | None]:
        """Page numbers to show, with ``None`` where pages are skipped: 1 ... 4 5 [6] 7 8 ... 20."""
        wanted = sorted({1, self.pages, *range(max(1, self.page - size), min(self.pages, self.page + size) + 1)})
        result: list[int | None] = []
        for number in wanted:
            if result and number - (result[-1] or number) > 1:
                result.append(None)
            result.append(number)
        return result


def parse_page(value: Any, default: int = 1) -> int:
    try:
        return max(1, min(int(value), 100_000))
    except (TypeError, ValueError):
        return default


def paginate(statement, page: Any = 1, per_page: int = DEFAULT_PER_PAGE) -> Page:
    """Run ``statement`` (a ``select`` of one entity, already filtered and ordered) one page at a time.

    A page number past the end shows the last page instead of an empty one.
    """
    number = parse_page(page)
    total = db.session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    last = max(1, -(-total // per_page))
    number = min(number, last)
    rows = list(db.session.scalars(statement.limit(per_page).offset((number - 1) * per_page)))
    return Page(rows, total, number, per_page)


def like_pattern(text: str) -> str:
    """``%text%`` for LIKE with the user's own ``%``, ``_`` and ``\\`` taken literally (use ``escape="\\\\"``)."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
