"""Album Slideshow Plus: new-photo spotlight, memories, story mode, sleep."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time as dtime, timedelta, timezone
import random
import types

import pytest

from custom_components.album_slideshow_plus import camera
from custom_components.album_slideshow_plus import spotlight as sp
from custom_components.album_slideshow_plus.const import (
    NEW_DETECT_AUTO,
    NEW_DETECT_FILE_TIME,
    NEW_DETECT_FIRST_SEEN,
    NEW_WINDOW_24H,
    NEW_WINDOW_7D,
    NEW_WINDOW_TODAY,
    ORDER_ALBUM,
    ORDER_RANDOM,
    SPOTLIGHT_EVERY_MINUTES,
    SPOTLIGHT_EVERY_SLIDES,
    SPOTLIGHT_OFF,
)
from custom_components.album_slideshow_plus.coordinator import AlbumCoordinator, MediaItem

DAY = 86_400_000
HOUR = 3_600_000


def _photo(key, *, captured=None, uploaded=None) -> MediaItem:
    return MediaItem(
        url=f"https://example.test/{key}.jpg", width=200, height=100,
        mime_type="image/jpeg", filename=f"{key}.jpg", source_id=str(key),
        captured_at=captured, uploaded_at=uploaded,
    )


def K(item_or_name) -> str:
    """The photo key the slideshow uses for an item (or a photo name)."""
    item = _photo(item_or_name) if isinstance(item_or_name, str) else item_or_name
    return sp.photo_key(item)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_first_run_records_a_baseline_and_later_photos_are_new():
    seen: dict[str, int] = {}
    _, added = sp.update_first_seen(seen, ["a", "b"], 1000, baseline=True)
    assert added == [] and seen == {"a": sp.BASELINE, "b": sp.BASELINE}
    _, added = sp.update_first_seen(seen, ["a", "b", "c"], 2000, baseline=False)
    assert added == ["c"] and seen["c"] == 2000
    # Seeing it again doesn't make it new twice.
    _, added = sp.update_first_seen(seen, ["a", "b", "c"], 3000, baseline=False)
    assert added == [] and seen["c"] == 2000


def test_first_seen_map_is_pruned_when_the_album_shrinks():
    seen = {f"old{i}": i + 1 for i in range(500)}
    sp.update_first_seen(seen, ["x", "y"], 10_000, baseline=False, prune_to=2)
    assert len(seen) <= 100 and {"x", "y"} <= set(seen)


def test_added_at_modes():
    item = _photo("a", uploaded=5000)
    seen = {K("a"): 7000}
    mtimes = {K("a"): 9000}
    assert sp.added_at(item, NEW_DETECT_AUTO, seen, mtimes) == 5000
    assert sp.added_at(item, NEW_DETECT_FIRST_SEEN, seen, mtimes) == 7000
    assert sp.added_at(item, NEW_DETECT_FILE_TIME, seen, mtimes) == 9000
    local = _photo("b")
    assert sp.added_at(local, NEW_DETECT_AUTO, {K("b"): 7000}, {}) == 7000
    # Baseline photos are never new.
    assert sp.added_at(local, NEW_DETECT_AUTO, {K("b"): sp.BASELINE}, {}) is None
    assert sp.added_at(local, NEW_DETECT_FIRST_SEEN, {}, {}) is None


def test_today_window_starts_at_local_midnight():
    tz = timezone(timedelta(hours=5, minutes=30))
    now = datetime(2026, 11, 22, 12, 44, tzinfo=tz)
    midnight = datetime(2026, 11, 22, tzinfo=tz)
    assert sp.window_cutoff_ms(NEW_WINDOW_TODAY, now) == int(midnight.timestamp() * 1000)
    assert sp.window_cutoff_ms(NEW_WINDOW_24H, now) == int((now - timedelta(hours=24)).timestamp() * 1000)
    assert sp.window_cutoff_ms(NEW_WINDOW_7D, now) == int((now - timedelta(days=7)).timestamp() * 1000)


def test_new_photos_are_listed_newest_first_and_old_ones_left_out():
    items = [_photo("old"), _photo("mid"), _photo("newest"), _photo("future")]
    seen = {K("old"): 100, K("mid"): 2000, K("newest"): 3000, K("future"): 10 * DAY}
    found = sp.new_photo_indices(
        items, detection=NEW_DETECT_FIRST_SEEN, first_seen=seen, file_mtimes={},
        cutoff_ms=1000, now_ms=4000,
    )
    assert [items[i].source_id for i in found] == ["newest", "mid"]


def test_slides_between_converts_minutes_with_the_interval():
    assert sp.slides_between(SPOTLIGHT_OFF, 5, 60) is None
    assert sp.slides_between(SPOTLIGHT_EVERY_SLIDES, 5, 60) == 5
    assert sp.slides_between(SPOTLIGHT_EVERY_MINUTES, 10, 60) == 10
    assert sp.slides_between(SPOTLIGHT_EVERY_MINUTES, 1, 30) == 2
    assert sp.slides_between(SPOTLIGHT_EVERY_MINUTES, 1, 600) == 1


def test_one_spotlight_per_period():
    assert not sp.is_due(0, 3) and not sp.is_due(1, 3) and sp.is_due(2, 3)
    assert sp.is_due(0, 1)
    assert not sp.is_due(100, None)


def test_round_robin_gives_every_candidate_a_turn():
    candidates = ["a", "b", "c"]
    shown: tuple[str, ...] = ()
    order = []
    for _ in range(6):
        key = sp.pick_round_robin(candidates, shown)
        order.append(key)
        shown = sp.remember_shown(shown, [key])
    assert order == ["a", "b", "c", "a", "b", "c"]
    # The photo on screen is skipped when another is available.
    assert sp.pick_round_robin(["a", "b"], (), avoid="a") == "b"
    # The only candidate is on screen: no pick, so it can't repeat back to back.
    assert sp.pick_round_robin(["a"], (), avoid="a") is None


def test_memories_match_today_in_earlier_years_then_nearby_days():
    tz = timezone.utc
    def at(y, m, d):
        return int(datetime(y, m, d, 12, tzinfo=tz).timestamp() * 1000)
    items = [
        _photo("this_year", captured=at(2026, 11, 22)),
        _photo("2020", captured=at(2020, 11, 22)),
        _photo("2018", captured=at(2018, 11, 22)),
        _photo("near", captured=at(2019, 11, 24)),
        _photo("undated"),
    ]
    found = sp.memory_indices(items, date(2026, 11, 22), tz)
    assert [items[i].source_id for i in found] == ["2018", "2020"]
    found = sp.memory_indices(items[3:], date(2026, 11, 22), tz)
    assert [items[3:][i].source_id for i in found] == ["near"]
    assert sp.memory_indices(items[3:], date(2026, 6, 1), tz) == []
    assert sp.years_ago(at(2018, 11, 22), date(2026, 11, 22), tz) == 8
    assert sp.years_ago(at(2018, 11, 23), date(2026, 11, 22), tz) == 7


def test_story_followers_are_the_next_shots_in_the_window():
    base = 1_700_000_000_000
    items = [
        _photo("later_trip", captured=base + 30 * HOUR),
        _photo("anchor", captured=base),
        _photo("plus2h", captured=base + 2 * HOUR),
        _photo("plus1h", captured=base + HOUR),
        _photo("before", captured=base - HOUR),
        _photo("plus5h", captured=base + 5 * HOUR),
    ]
    followers = sp.story_followers(items, 1, 3, 6)
    assert [items[i].source_id for i in followers] == ["plus1h", "plus2h"]
    followers = sp.story_followers(items, 1, 8, 6)
    assert [items[i].source_id for i in followers] == ["plus1h", "plus2h", "plus5h"]
    assert sp.story_followers(items, 1, 1, 6) == []
    assert sp.story_followers([_photo("undated")], 0, 3, 6) == []


@pytest.mark.parametrize(("now", "start", "end", "asleep"), [
    (dtime(23, 0), dtime(22, 0), dtime(7, 0), True),
    (dtime(6, 59), dtime(22, 0), dtime(7, 0), True),
    (dtime(7, 0), dtime(22, 0), dtime(7, 0), False),
    (dtime(12, 0), dtime(22, 0), dtime(7, 0), False),
    (dtime(13, 0), dtime(12, 30), dtime(14, 0), True),
    (dtime(14, 0), dtime(12, 30), dtime(14, 0), False),
    (dtime(14, 0), dtime(9, 0), dtime(9, 0), False),
])
def test_sleep_window(now, start, end, asleep):
    assert sp.is_sleep_time(now, start, end) is asleep


def test_parse_time_accepts_strings_and_times():
    assert sp.parse_time("22:30", None) == dtime(22, 30)
    assert sp.parse_time("07:05:09", None) == dtime(7, 5, 9)
    assert sp.parse_time(dtime(1, 2), None) == dtime(1, 2)
    assert sp.parse_time("25:00", dtime(3, 0)) == dtime(3, 0)
    assert sp.parse_time("nonsense", None) is None


# ---------------------------------------------------------------------------
# Camera integration of the inserted slides
# ---------------------------------------------------------------------------

def _store(**overrides):
    values = dict(
        order_mode=ORDER_ALBUM, shuffle_age_bias=0, slide_interval=60,
        spotlight_mode=SPOTLIGHT_EVERY_SLIDES, spotlight_every=3,
        new_photo_window=NEW_WINDOW_TODAY, new_photo_detection=NEW_DETECT_AUTO,
        memories_mode=SPOTLIGHT_OFF, memories_every=10,
        story_length=1, story_window_hours=6,
        sleep_enabled=False, sleep_start="22:00:00", sleep_end="07:00:00",
    )
    values.update(overrides)
    return types.SimpleNamespace(**values)


def _coordinator(first_seen: dict[str, int]):
    coordinator = AlbumCoordinator.__new__(AlbumCoordinator)
    coordinator._first_seen = first_seen
    coordinator._file_mtimes = {}
    coordinator._acknowledged_before = None
    coordinator.first_seen_revision = 0
    return coordinator


def _cam(store, coordinator, seed: int = 1):
    cam = camera.AlbumSlideshowCamera.__new__(camera.AlbumSlideshowCamera)
    cam.store = store
    cam.coordinator = coordinator
    cam._index = 0
    cam._random_order = []
    cam._random_pos = 0
    cam._recent_urls = []
    cam._rng = random.Random(seed)
    return cam


def _play(cam, items, slides):
    shown = []
    for _ in range(slides):
        cam._preferred_partner = None
        cam._advance_slide(len(items), items)
        shown.append((items[cam._index].source_id, cam._spotlight_kind))
        cam._finish_inserted_slide(items, {"photo_ids": [items[cam._index].photo_id]})
    return shown


def _album_with_new(n_old=8, new=("n1", "n2")):
    now = _now_ms()
    items = [_photo(f"p{i}") for i in range(n_old)] + [_photo(key) for key in new]
    seen = {K(f"p{i}"): sp.BASELINE for i in range(n_old)}
    seen.update({K(key): now - 60_000 * (index + 1) for index, key in enumerate(new)})
    return items, seen


def test_spotlight_inserts_new_photos_and_the_album_order_carries_on():
    items, seen = _album_with_new()
    cam = _cam(_store(), _coordinator(seen))
    shown = _play(cam, items, 9)
    assert shown == [
        ("p1", None), ("p2", None), ("n1", "new"),
        ("p3", None), ("p4", None), ("n2", "new"),
        ("p5", None), ("p6", None), ("n1", "new"),
    ]


def test_every_slide_is_new_when_the_period_is_one():
    items, seen = _album_with_new()
    cam = _cam(_store(spotlight_every=1), _coordinator(seen))
    assert [key for key, _ in _play(cam, items, 4)] == ["n1", "n2", "n1", "n2"]


def test_no_spotlight_without_new_photos_then_straight_away_when_one_arrives():
    items, seen = _album_with_new(new=())
    coordinator = _coordinator(seen)
    cam = _cam(_store(), coordinator)
    assert all(kind is None for _, kind in _play(cam, items, 6))
    items = items + [_photo("fresh")]
    seen[K("fresh")] = _now_ms()
    coordinator.first_seen_revision += 1
    assert _play(cam, items, 1) == [("fresh", "new")]


def test_photos_marked_as_seen_stop_being_spotlighted():
    items, seen = _album_with_new()
    coordinator = _coordinator(seen)
    cam = _cam(_store(spotlight_every=1), coordinator)
    coordinator._acknowledged_before = _now_ms()
    coordinator.first_seen_revision += 1
    assert all(kind is None for _, kind in _play(cam, items, 3))


def test_random_order_still_shows_every_photo_once_per_cycle():
    items, seen = _album_with_new(n_old=10)
    cam = _cam(_store(order_mode=ORDER_RANDOM), _coordinator(seen))
    shown = _play(cam, items, 18)
    ordinary_old = [key for key, kind in shown if kind is None and key.startswith("p")]
    # Each old photo plays once before any old photo repeats, spotlights or not.
    assert sorted(ordinary_old[:10]) == sorted(f"p{i}" for i in range(10))
    assert sum(1 for _, kind in shown if kind == "new") == 6
    # Nothing plays twice in a row.
    keys = [key for key, _ in shown]
    assert all(a != b for a, b in zip(keys, keys[1:]))


def test_album_order_never_repeats_the_spotlighted_photo_straight_after():
    # New photos at the front of the album, as a NAS folder sorted by date has them.
    items, seen = _album_with_new()
    items = items[-2:] + items[:-2]
    cam = _cam(_store(spotlight_every=2), _coordinator(seen))
    keys = [key for key, _ in _play(cam, items, 12)]
    assert all(a != b for a, b in zip(keys, keys[1:]))


def test_memories_are_inserted_on_their_own_cadence():
    tz = timezone.utc
    today = datetime.now(tz)
    items = [_photo(f"p{i}") for i in range(6)] + [
        _photo("memory", captured=int(today.replace(year=today.year - 3).timestamp() * 1000)),
    ]
    cam = _cam(
        _store(spotlight_mode=SPOTLIGHT_OFF, memories_mode=SPOTLIGHT_EVERY_SLIDES, memories_every=2),
        _coordinator({}),
    )
    shown = _play(cam, items, 4)
    assert [kind for _, kind in shown] == [None, "memory", None, "memory"]
    assert shown[1][0] == "memory"


def test_new_photo_wins_when_both_are_due_and_the_memory_follows():
    tz = timezone.utc
    today = datetime.now(tz)
    items, seen = _album_with_new(new=("n1",))
    items.append(_photo("memory", captured=int(today.replace(year=today.year - 2).timestamp() * 1000)))
    seen[K("memory")] = sp.BASELINE
    cam = _cam(
        _store(spotlight_every=2, memories_mode=SPOTLIGHT_EVERY_SLIDES, memories_every=2),
        _coordinator(seen),
    )
    kinds = [kind for _, kind in _play(cam, items, 4)]
    assert kinds[:3] == [None, "new", "memory"]


def test_story_plays_photos_from_the_same_outing_in_order():
    base = 1_700_000_000_000
    trip = [_photo(f"t{i}", captured=base + i * HOUR) for i in range(4)]
    others = [_photo(f"o{i}", captured=base + (100 + i) * DAY) for i in range(6)]
    items = others + trip
    cam = _cam(
        _store(order_mode=ORDER_RANDOM, spotlight_mode=SPOTLIGHT_OFF, story_length=3, story_window_hours=6),
        _coordinator({}),
        seed=3,
    )
    keys = []
    for _ in range(30):
        cam._advance_slide(len(items), items)
        keys.append(items[cam._index].source_id)
        meta: dict = {}
        cam._finish_inserted_slide(items, meta)
        if keys[-1] == "t0":
            assert meta.get("story") == [1, 3]
            break
    assert "t0" in keys
    following = []
    for _ in range(2):
        cam._advance_slide(len(items), items)
        following.append(items[cam._index].source_id)
    assert following == ["t1", "t2"]


def test_inserted_slide_state_survives_the_navigation_cursor_round_trip():
    items, seen = _album_with_new()
    cam = _cam(_store(), _coordinator(seen))
    cam._paired_urls = ()
    cam._cycle_ids = ()
    cam._cycle_held = 0
    cam._candidate_cache = None
    cam._chrono_cache = None
    cam.hass = None
    cam.entry = types.SimpleNamespace(entry_id="e1", title="KJ")
    cam._download_cache = None
    _play(cam, items, 3)
    cursor = cam._capture_cursor()
    assert cursor.spotlight_shown == (K("n1"),)
    assert cursor.resume_index == 2
    renderer = cam._make_renderer(cursor)
    renderer._advance_slide(len(items), items)
    assert items[renderer._index].source_id == "p3"


def test_sleep_holds_the_slideshow(monkeypatch):
    store = _store(sleep_enabled=True, sleep_start="00:00:00", sleep_end="23:59:59", paused=False,
                   pause_when_unwatched=False)
    cam = _cam(store, _coordinator({}))
    assert cam._sleeping is True
    assert cam._held is True
    store.sleep_enabled = False
    assert cam._held is False


def test_coordinator_announces_new_photos(monkeypatch):
    fired = []
    coordinator = AlbumCoordinator.__new__(AlbumCoordinator)
    coordinator.hass = types.SimpleNamespace(
        bus=types.SimpleNamespace(async_fire=lambda event, data: fired.append((event, data))),
    )
    coordinator.entry = types.SimpleNamespace(entry_id="e1", title="KJ Photos")
    items = [_photo("a"), _photo("b")]
    asyncio.run(coordinator._track_first_seen(items))
    assert fired == [] and coordinator.first_seen == {K("a"): sp.BASELINE, K("b"): sp.BASELINE}
    asyncio.run(coordinator._track_first_seen(items + [_photo("c")]))
    assert len(fired) == 1
    event, data = fired[0]
    assert event == "album_slideshow_plus_new_photos"
    assert data["count"] == 1 and data["filenames"] == ["c.jpg"]
    assert coordinator.added_at(_photo("c"), NEW_DETECT_AUTO) is not None
    assert coordinator.added_at(_photo("a"), NEW_DETECT_AUTO) is None


def test_mark_all_seen_clears_new_photos():
    coordinator = AlbumCoordinator.__new__(AlbumCoordinator)
    coordinator.hass = types.SimpleNamespace(bus=types.SimpleNamespace(async_fire=lambda *a: None))
    coordinator.entry = types.SimpleNamespace(entry_id="e1", title="KJ")
    coordinator.async_update_listeners = lambda: None
    items = [_photo("a")]
    asyncio.run(coordinator._track_first_seen(items))
    asyncio.run(coordinator._track_first_seen(items + [_photo("b", uploaded=_now_ms())]))
    coordinator.data = {"items": items + [_photo("b", uploaded=_now_ms() - 1000)]}
    assert asyncio.run(coordinator.async_mark_all_seen()) == 1
    assert coordinator.added_at(coordinator.data["items"][1], NEW_DETECT_AUTO) is None


def test_local_exif_reads_date_time_original_from_the_exif_sub_ifd(tmp_path):
    from PIL import Image

    from custom_components.album_slideshow_plus.coordinator import _read_local_exif

    path = tmp_path / "phone.jpg"
    exif = Image.Exif()
    exif[306] = "2024:05:01 18:00:00"  # IFD0 DateTime: when it was edited
    exif.get_ifd(0x8769)[36867] = "2019:11:22 10:30:00"  # when it was taken
    exif.get_ifd(0x8769)[36881] = "+05:30"
    Image.new("RGB", (40, 30), "red").save(path, exif=exif)
    captured = _read_local_exif(path)["captured_at"]
    assert captured == int(datetime(2019, 11, 22, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)


def test_a_lone_memory_never_plays_twice_in_a_row():
    tz = timezone.utc
    today = datetime.now(tz)
    items = [_photo(f"p{i}") for i in range(5)] + [
        _photo("memory", captured=int(today.replace(year=today.year - 4).timestamp() * 1000)),
    ]
    cam = _cam(
        _store(order_mode=ORDER_ALBUM, spotlight_mode=SPOTLIGHT_OFF,
               memories_mode=SPOTLIGHT_EVERY_SLIDES, memories_every=2),
        _coordinator({}),
    )
    keys = [key for key, _ in _play(cam, items, 16)]
    assert keys.count("memory") >= 4
    assert all(a != b for a, b in zip(keys, keys[1:]))
