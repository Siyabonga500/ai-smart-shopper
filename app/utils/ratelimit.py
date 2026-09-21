"""Tiny in-memory sliding-window limiter.

It is per process: with several gunicorn workers each one keeps its own
counts, so treat it as a speed bump (login guessing, geocoder abuse), not as a
hard guarantee. Swap for Redis-backed limiting if the app ever needs that.
"""

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, clock=time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._events: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        events = self._events[key]
        while events and now - events[0] >= self.window:
            events.popleft()
        return events

    def hit(self, key: str) -> bool:
        """Record one event; ``True`` while within the limit, ``False`` once exceeded."""
        with self._lock:
            now = self._clock()
            events = self._prune(key, now)
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, self._clock())) >= self.limit

    def record(self, key: str) -> None:
        """Record an event without checking (used for failed logins)."""
        with self._lock:
            now = self._clock()
            self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
