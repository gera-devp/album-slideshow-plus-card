"""Spotlight, memories, story and sleep helpers for Album Slideshow Plus.

Pure functions with no Home Assistant imports, so the camera and the tests
share one implementation. Items are ``MediaItem``-like objects exposing
``photo_id`` / ``url`` / ``captured_at`` (epoch ms or ``None``).

Terms used here:

- **New photo**: a photo whose *added time* falls inside the chosen window
  (today since local midnight, or the last 24 h / 3 days / 7 days). The added
  time is the provider's "added to album" date when it has one, otherwise
  the moment this slideshow first saw the photo (see ``first_seen``).
- **Spotlight**: a slide inserted into the normal rotation every N slides
  (or every N minutes) that shows a new photo, round-robin, so every new
  photo gets its turn before any repeats.
- **Memory**: a photo taken on today's calendar date in an earlier year,
  inserted the same way.
- **Story**: after a random photo, the next few photos taken shortly after
  it, shown in the order they were taken.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta, timezone, tzinfo
import math
from typing import Any, Iterable, Sequence

from .const import (
    NEW_DETECT_FILE_TIME,
    NEW_DETECT_FIRST_SEEN,
    NEW_WINDOW_3D,
    NEW_WINDOW_7D,
    NEW_WINDOW_24H,
    SPOTLIGHT_EVERY_MINUTES,
    SPOTLIGHT_EVERY_SLIDES,
    SPOTLIGHT_OFF,
)

# Photos the slideshow already had when it first ran are recorded with this
# first-seen value, so installing the integration doesn't flag a whole
# library as "new today".
BASELINE = 0

# Round-robin memory: how many spotlighted photo keys to remember.
SHOWN_MEMORY = 500

# Memories widen to nearby days (nearest first) when no photo was taken on
# today's exact date in an earlier year.
MEMORY_WIDEN_DAYS = 3

_DAYS_IN_RING = 365


def photo_key(item: Any) -> str:
    """Stable identity of a photo (matches the camera's shuffle key)."""
    return getattr(item, "photo_id", None) or getattr(item, "url", "")


# ---------------------------------------------------------------------------
# First-seen tracking
# ---------------------------------------------------------------------------

def update_first_seen(
    seen: dict[str, int],
    keys: Iterable[str],
    now_ms: int,
    *,
    baseline: bool,
    prune_to: int | None = None,
) -> tuple[dict[str, int], list[str]]:
    """Record first-seen times for ``keys``; return the map and the new keys.

    ``baseline`` marks every unknown key with ``BASELINE`` instead of
    ``now_ms`` (the first run after install). New keys are returned in the
    order given, and only when ``baseline`` is False.

    When ``prune_to`` is given and the map has grown past twice that many
    entries, keys not in ``keys`` are dropped, oldest first, so a long-lived
    slideshow over a changing album can't grow the map without bound.
    """
    current = list(dict.fromkeys(k for k in keys if k))
    added: list[str] = []
    for key in current:
        if key in seen:
            continue
        seen[key] = BASELINE if baseline else now_ms
        if not baseline:
            added.append(key)
    if prune_to is not None and len(seen) > max(2 * prune_to, 100):
        keep = set(current)
        stale = sorted((v, k) for k, v in seen.items() if k not in keep)
        excess = len(seen) - max(2 * prune_to, 100)
        for _, key in stale[:excess]:
            del seen[key]
    return seen, added


def added_at(
    item: Any,
    detection: str,
    first_seen: dict[str, int],
    file_mtimes: dict[str, int] | None = None,
) -> int | None:
    """When a photo was added, in epoch ms, per the detection mode.

    - ``auto``: the provider's added-to-album date (``uploaded_at``) when it
      has one, otherwise first seen.
    - ``first_seen``: when this slideshow first saw the photo.
    - ``file_modified``: the file's modified time (local folders), otherwise
      the ``auto`` rule.

    ``None`` (or ``BASELINE``) means "already there", never new.
    """
    key = photo_key(item)
    seen = first_seen.get(key)
    if detection == NEW_DETECT_FIRST_SEEN:
        return seen if seen else None
    if detection == NEW_DETECT_FILE_TIME and file_mtimes:
        mtime = file_mtimes.get(key)
        if type(mtime) is int and mtime > 0:
            return mtime
    uploaded = getattr(item, "uploaded_at", None)
    if type(uploaded) is int and uploaded > 0:
        return uploaded
    return seen if seen else None


def window_cutoff_ms(window: str, now: datetime) -> int:
    """Earliest added time (epoch ms) that still counts as new.

    ``now`` must be timezone-aware in Home Assistant's time zone, so
    ``today`` starts at local midnight.
    """
    if window == NEW_WINDOW_24H:
        start = now - timedelta(hours=24)
    elif window == NEW_WINDOW_3D:
        start = now - timedelta(days=3)
    elif window == NEW_WINDOW_7D:
        start = now - timedelta(days=7)
    else:  # NEW_WINDOW_TODAY and anything unknown
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp() * 1000)


def new_photo_indices(
    items: Sequence[Any],
    *,
    detection: str,
    first_seen: dict[str, int],
    file_mtimes: dict[str, int] | None,
    cutoff_ms: int,
    now_ms: int | None = None,
) -> list[int]:
    """Indices of new photos, most recently added first."""
    found: list[tuple[int, int]] = []
    for index, item in enumerate(items):
        ts = added_at(item, detection, first_seen, file_mtimes)
        if ts is None or ts < cutoff_ms:
            continue
        if now_ms is not None and ts > now_ms + 86_400_000:
            # A clock-skewed "future" file time shouldn't pin a photo as new forever.
            continue
        found.append((ts, index))
    found.sort(key=lambda pair: (-pair[0], pair[1]))
    return [index for _, index in found]


# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------

def _ring_day(local: date) -> int:
    day = 28 if (local.month, local.day) == (2, 29) else local.day
    return date(2001, local.month, day).timetuple().tm_yday


def memory_indices(
    items: Sequence[Any],
    today: date,
    tz: tzinfo | None,
    *,
    widen_days: int = MEMORY_WIDEN_DAYS,
) -> list[int]:
    """Photos taken on today's date in earlier years, nearest day first.

    Falls back to up to ``widen_days`` either side when nothing matches the
    exact day. February 29 counts as February 28.
    """
    today_ring = _ring_day(today)
    by_distance: dict[int, list[tuple[int, int]]] = {}
    for index, item in enumerate(items):
        ts = getattr(item, "captured_at", None)
        if type(ts) is not int:
            continue
        try:
            local = datetime.fromtimestamp(ts / 1000, tz or timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            continue
        if local.year >= today.year:
            continue
        diff = abs(_ring_day(local) - today_ring)
        distance = min(diff, _DAYS_IN_RING - diff)
        if distance > widen_days:
            continue
        # Older memories first within a day keeps the rotation varied.
        by_distance.setdefault(distance, []).append((local.year, index))
    for distance in sorted(by_distance):
        if by_distance[distance]:
            return [index for _, index in sorted(by_distance[distance])]
    return []


def years_ago(captured_at: int | None, today: date, tz: tzinfo | None) -> int | None:
    """Whole years between a capture time and today, or None."""
    if type(captured_at) is not int:
        return None
    try:
        local = datetime.fromtimestamp(captured_at / 1000, tz or timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return None
    years = today.year - local.year
    if (today.month, today.day) < (local.month, local.day):
        years -= 1
    return max(0, years)


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def slides_between(mode: str, every: int, slide_interval: float) -> int | None:
    """Spotlight period in slides, or None when the mode is off.

    ``every_n_minutes`` converts minutes to slides with the current slide
    interval (rounded, at least 1), so a 10 minute spotlight at a 60 s
    interval is every 10th slide.
    """
    if mode == SPOTLIGHT_OFF or mode not in (SPOTLIGHT_EVERY_SLIDES, SPOTLIGHT_EVERY_MINUTES):
        return None
    try:
        every = int(every)
    except (TypeError, ValueError):
        return None
    if every < 1:
        return None
    if mode == SPOTLIGHT_EVERY_SLIDES:
        return every
    interval = float(slide_interval) if slide_interval else 60.0
    if not math.isfinite(interval) or interval <= 0:
        interval = 60.0
    return max(1, round(every * 60 / interval))


def is_due(since: int, period: int | None) -> bool:
    """Whether the next slide is a spotlight.

    ``period`` N means one spotlight per N slides: after N - 1 ordinary
    slides. N = 1 makes every slide a spotlight (only new photos play).
    """
    return period is not None and since >= period - 1


def pick_round_robin(
    candidates: Sequence[str],
    shown: Sequence[str],
    *,
    avoid: str | None = None,
) -> str | None:
    """Pick the candidate whose turn it is.

    Candidates never spotlighted come first, in the given order (newest
    added first for new photos). After that, the one spotlighted longest
    ago. ``avoid`` (the photo on screen) is never picked: when it is the
    only candidate, there is no pick, and the inserted slide waits a slide
    rather than showing the same photo twice in a row.
    """
    pool = [key for key in candidates if key != avoid]
    if not pool:
        return None
    position = {key: index for index, key in enumerate(shown)}
    unseen = [key for key in pool if key not in position]
    if unseen:
        return unseen[0]
    return min(pool, key=lambda key: position[key])


def remember_shown(shown: Sequence[str], keys: Iterable[str]) -> tuple[str, ...]:
    """Move ``keys`` to the most-recent end of the round-robin memory."""
    keys = [key for key in keys if key]
    if not keys:
        return tuple(shown)
    drop = set(keys)
    updated = [key for key in shown if key not in drop] + list(dict.fromkeys(keys))
    return tuple(updated[-SHOWN_MEMORY:])


# ---------------------------------------------------------------------------
# Story mode
# ---------------------------------------------------------------------------

def story_followers(
    items: Sequence[Any],
    anchor: int,
    length: int,
    window_hours: float,
    *,
    chronological: Sequence[int] | None = None,
) -> list[int]:
    """Up to ``length - 1`` photos taken within ``window_hours`` after the anchor.

    Returned in the order they were taken. ``chronological`` is an optional
    precomputed list of indices with a capture date, sorted by capture time
    (ties by index), to avoid re-sorting a large album on every slide.
    """
    if length <= 1 or not (0 <= anchor < len(items)):
        return []
    start = getattr(items[anchor], "captured_at", None)
    if type(start) is not int:
        return []
    limit = start + int(max(0.0, float(window_hours)) * 3_600_000)
    order = chronological if chronological is not None else chronological_order(items)
    # Binary search for the anchor's position in the chronological order.
    lo, hi = 0, len(order)
    while lo < hi:
        mid = (lo + hi) // 2
        ts = items[order[mid]].captured_at
        if (ts, order[mid]) < (start, anchor):
            lo = mid + 1
        else:
            hi = mid
    followers: list[int] = []
    anchor_key = photo_key(items[anchor])
    seen_keys = {anchor_key}
    for index in order[lo:]:
        if index == anchor:
            continue
        ts = items[index].captured_at
        if ts > limit:
            break
        key = photo_key(items[index])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        followers.append(index)
        if len(followers) >= length - 1:
            break
    return followers


def chronological_order(items: Sequence[Any]) -> list[int]:
    """Indices of dated items sorted by capture time, then index."""
    dated = [
        (item.captured_at, index)
        for index, item in enumerate(items)
        if type(getattr(item, "captured_at", None)) is int
    ]
    dated.sort()
    return [index for _, index in dated]


# ---------------------------------------------------------------------------
# Sleep schedule
# ---------------------------------------------------------------------------

def parse_time(value: Any, default: dtime) -> dtime:
    """Accept a ``time`` or an ``HH:MM[:SS]`` string."""
    if isinstance(value, dtime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        parts = value.strip().split(":")
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            return default
        if 2 <= len(numbers) <= 3:
            numbers += [0] * (3 - len(numbers))
            hour, minute, second = numbers
            if 0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60:
                return dtime(hour, minute, second)
    return default


def is_sleep_time(now: dtime, start: dtime, end: dtime) -> bool:
    """Whether ``now`` is inside the sleep window; handles overnight windows.

    Equal start and end means no sleep window.
    """
    now = now.replace(tzinfo=None, microsecond=0)
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end
