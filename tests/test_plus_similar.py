"""Album Slideshow Plus: repeated snaps of one moment play once, as the sharpest."""
from __future__ import annotations

import random
import types

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from custom_components.album_slideshow_plus import camera
from custom_components.album_slideshow_plus import similar
from custom_components.album_slideshow_plus.coordinator import MediaItem, _read_local_photo

T0 = 1_700_000_000_000


def _scene(seed: int, size=(800, 600)) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", size, tuple(rnd.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    for _ in range(40):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        w, h = rnd.randrange(20, 260), rnd.randrange(20, 200)
        shape = draw.ellipse if rnd.random() < 0.5 else draw.rectangle
        shape([x, y, x + w, y + h], fill=tuple(rnd.randrange(256) for _ in range(3)))
    return img


def _resnap(img: Image.Image) -> Image.Image:
    """The same scene shot again: framed a touch differently, a bit brighter."""
    w, h = img.size
    moved = img.crop((12, 8, w - 4, h - 6)).resize((w, h))
    return ImageEnhance.Brightness(moved).enhance(1.06)


def _photo(name, *, at=None, fp=None, sharp=None, w=None, h=None, size=None) -> MediaItem:
    return MediaItem(
        url=f"file:///photos/{name}.jpg", width=w, height=h, mime_type="image/jpeg",
        filename=f"{name}.jpg", source_id=f"file:///photos/{name}.jpg",
        captured_at=at, byte_size=size, fingerprint=fp, sharpness=sharp, shot_scanned=fp is not None,
    )


def test_fingerprints_match_a_resnap_but_not_another_scene():
    base = _scene(1)
    fp_base, sharp_base = similar.fingerprint_image(base)
    fp_resnap, _ = similar.fingerprint_image(_resnap(base))
    fp_other, _ = similar.fingerprint_image(_scene(2))
    assert similar.distance(fp_base, fp_resnap) <= similar.MAX_DISTANCE
    assert similar.distance(fp_base, fp_other) > similar.MAX_DISTANCE
    # 22 hex digits (hash + colour), then the 16x16 greyscale copy.
    assert similar.has_detail(fp_base) and len(fp_base.split("~")[0]) == 22 and sharp_base > 0
    strict = similar.LEVEL_THRESHOLDS["strict"][0]
    loose = similar.LEVEL_THRESHOLDS["loose"][0]
    assert similar.detail_score(fp_base, fp_resnap) <= strict
    assert similar.detail_score(fp_base, fp_other) > loose


def test_blurred_shot_scores_less_sharp():
    base = _scene(3)
    _, sharp = similar.fingerprint_image(base)
    _, soft = similar.fingerprint_image(base.filter(ImageFilter.GaussianBlur(3)))
    assert soft < sharp * 0.5


def test_local_scan_records_fingerprint_and_sharpness(tmp_path):
    path = tmp_path / "IMG_1.jpg"
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = "2025:12:31 18:30:02"
    _scene(4).save(path, quality=90, exif=exif)
    info = _read_local_photo(path)
    assert similar.has_detail(info["fingerprint"])
    assert info["sharpness"] > 0
    assert "captured_at" in info


def test_photos_without_their_own_capture_time_are_not_fingerprinted(tmp_path):
    # Only the file's modified time is known (e.g. a WhatsApp copy): a bulk
    # copy would give unrelated photos the same time, so it is never grouped.
    path = tmp_path / "IMG-20251231-WA0001.jpg"
    _scene(4).save(path, quality=90)
    info = _read_local_photo(path)
    assert "fingerprint" not in info and "captured_at" in info


def _card(background):
    img = Image.new("RGB", (800, 600), background)
    draw = ImageDraw.Draw(img)
    for i in range(6):
        draw.rectangle([60 + i * 110, 200, 130 + i * 110, 330], fill="white")
    return img


def test_same_layout_in_different_colours_is_not_the_same_shot():
    # The layout matches, so the greyscale hash alone would call it one shot.
    fp, sharp = similar.fingerprint_image(_card((200, 120, 0)))
    fp2, sharp2 = similar.fingerprint_image(_card((0, 150, 150)))
    assert similar.distance(fp, fp2) <= similar.MAX_DISTANCE
    items = [_photo("a", at=T0, fp=fp, sharp=sharp), _photo("b", at=T0 + 1_000, fp=fp2, sharp=sharp2)]
    assert similar.colour_distance(fp, fp2) > similar.MAX_COLOUR_DISTANCE
    assert similar.group_similar(items, 30).folded == 0


def test_snaps_of_one_moment_fold_into_the_sharpest():
    base = _scene(5)
    fp, sharp = similar.fingerprint_image(base)
    fp_blur, soft = similar.fingerprint_image(base.filter(ImageFilter.GaussianBlur(2)))
    fp_re, sharp_re = similar.fingerprint_image(_resnap(base))
    fp_other, sharp_other = similar.fingerprint_image(_scene(6))
    items = [
        _photo("blurred", at=T0, fp=fp_blur, sharp=soft),
        _photo("sharp", at=T0 + 3_000, fp=fp, sharp=sharp),
        _photo("resnap", at=T0 + 7_000, fp=fp_re, sharp=min(sharp_re, sharp) - 1),
        _photo("other_scene", at=T0 + 9_000, fp=fp_other, sharp=sharp_other),
        _photo("same_scene_next_day", at=T0 + 86_400_000, fp=fp, sharp=sharp),
    ]
    result = similar.group_similar(items, 30)
    assert [item.filename for item in result.kept] == ["sharp.jpg", "other_scene.jpg", "same_scene_next_day.jpg"]
    assert len(result.groups) == 1 and result.folded == 2
    assert [item.filename for item in result.groups[0]][0] == "sharp.jpg"
    assert result.group_size[similar._key(items[1])] == 3


def test_shots_further_apart_than_the_window_stay_separate():
    base = _scene(7)
    fp, sharp = similar.fingerprint_image(base)
    items = [_photo("a", at=T0, fp=fp, sharp=sharp), _photo("b", at=T0 + 45_000, fp=fp, sharp=sharp)]
    assert similar.group_similar(items, 30).folded == 0
    assert similar.group_similar(items, 60).folded == 1


def test_interleaved_subjects_still_group_correctly():
    a, b = _scene(8), _scene(9)
    fa, sa = similar.fingerprint_image(a)
    fb, sb = similar.fingerprint_image(b)
    fa2, _ = similar.fingerprint_image(_resnap(a))
    fb2, _ = similar.fingerprint_image(_resnap(b))
    items = [
        _photo("a1", at=T0, fp=fa, sharp=sa), _photo("b1", at=T0 + 2_000, fp=fb, sharp=sb),
        _photo("a2", at=T0 + 4_000, fp=fa2, sharp=1), _photo("b2", at=T0 + 6_000, fp=fb2, sharp=1),
    ]
    result = similar.group_similar(items, 30)
    assert sorted(item.filename for item in result.kept) == ["a1.jpg", "b1.jpg"]


def test_photos_without_a_fingerprint_are_never_grouped():
    # Same time, same size, same phone: at a family event that is usually a
    # different picture, so nothing is grouped until both have been checked.
    items = [
        _photo("a", at=T0, w=4000, h=3000, size=3_000_000),
        _photo("b", at=T0 + 2_000, w=4000, h=3000, size=3_400_000),
        _photo("c", at=T0 + 4_000, w=4000, h=3000, size=3_100_000),
    ]
    assert similar.group_similar(items, 30).folded == 0
    fp, sharp = similar.fingerprint_image(_scene(12))
    items[0].fingerprint, items[0].sharpness = fp, sharp
    assert similar.group_similar(items, 30).folded == 0


def test_window_zero_or_tiny_albums_change_nothing():
    items = [_photo("a", at=T0, fp="0" * 22, sharp=1)]
    assert similar.group_similar(items, 30).kept == items
    two = items + [_photo("b", at=T0, fp="0" * 22, sharp=2)]
    assert similar.group_similar(two, 0).kept == two


def _cam(items, *, group=True):
    cam = camera.AlbumSlideshowCamera.__new__(camera.AlbumSlideshowCamera)
    cam.store = types.SimpleNamespace(
        date_filter="off", custom_lookback_days=365, missing_date_mode="use_uploaded_at",
        order_mode="album_order", hidden_revision=0, hidden_photo_ids=frozenset(),
        group_similar_shots=group, similar_window_seconds=30,
    )
    cam.coordinator = types.SimpleNamespace(data={"items": items}, provider="local_folder")
    cam._effective_cache = None
    return cam


def test_the_playlist_holds_one_photo_per_moment():
    base = _scene(10)
    fp, sharp = similar.fingerprint_image(base)
    fp_soft, soft = similar.fingerprint_image(base.filter(ImageFilter.GaussianBlur(2)))
    items = [
        _photo("snap1_soft", at=T0, fp=fp_soft, sharp=soft),
        _photo("snap2_sharp", at=T0 + 2_000, fp=fp, sharp=sharp),
        _photo("elsewhere", at=T0 + 10 * 86_400_000, fp="f" * 22, sharp=10),
    ]
    cam = _cam(items)
    assert [item.filename for item in cam._effective_items()] == ["snap2_sharp.jpg", "elsewhere.jpg"]
    assert cam._grouping.folded == 1
    off = _cam(items, group=False)
    assert len(off._effective_items()) == 3 and off._grouping is None


def test_only_photos_with_a_neighbour_in_the_window_are_fingerprinted():
    from custom_components.album_slideshow_plus.coordinator import AlbumCoordinator

    coord = AlbumCoordinator.__new__(AlbumCoordinator)
    coord.provider = "local_folder"
    coord.store = types.SimpleNamespace(group_similar_shots=True, similar_window_seconds=30)
    items = [
        _photo("burst_a", at=T0), _photo("burst_b", at=T0 + 4_000),
        _photo("alone", at=T0 + 3_600_000), _photo("undated"),
        _photo("burst_c_done", at=T0 + 8_000), _photo("date_not_read_yet", at=T0 + 9_000),
    ]
    for item in items[:5]:
        item.exif_scanned = True
    items[4].shot_scanned = True
    # Local photos wait for their own capture time (the date scan).
    assert [item.filename for item in coord._shot_candidates(items)] == ["burst_a.jpg", "burst_b.jpg"]
    coord.store.group_similar_shots = False
    assert coord._shot_candidates(items) == []
    coord.store.group_similar_shots = True
    # Remote sources need the camera's preview fetcher.
    coord.provider = "google_shared"
    coord.shot_fetcher = None
    assert coord._shot_candidates(items) == []
    coord.shot_fetcher = lambda item: None
    assert [item.filename for item in coord._shot_candidates(items)] == [
        "burst_a.jpg", "burst_b.jpg", "date_not_read_yet.jpg",
    ]


class _FakeHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _remote(name, at):
    return MediaItem(
        url=f"https://photos.example/{name}=w4000-h3000", width=4000, height=3000,
        mime_type="image/jpeg", filename=f"{name}.jpg", source_id=name, captured_at=at,
    )


def _jpeg(img) -> bytes:
    import io

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def test_remote_photos_are_fingerprinted_from_a_preview_and_remembered():
    import asyncio

    from custom_components.album_slideshow_plus.coordinator import AlbumCoordinator

    base = _scene(13)
    previews = {
        "sharp": _jpeg(base), "shaky": _jpeg(base.filter(ImageFilter.GaussianBlur(3))),
        "next_shot": _jpeg(_scene(14)), "unreachable": None,
    }

    async def fetcher(item):
        return previews[item.source_id]

    saved = []
    coord = AlbumCoordinator.__new__(AlbumCoordinator)
    coord.hass = _FakeHass()
    coord.provider = "google_shared"
    coord.store = types.SimpleNamespace(group_similar_shots=True, similar_window_seconds=30)
    coord.shot_fetcher = fetcher
    coord._shots, coord._shots_loaded = {}, True
    coord._shots_store = types.SimpleNamespace(async_delay_save=lambda fn, delay: saved.append(fn()))
    coord.async_update_listeners = lambda: None
    items = [_remote("shaky", T0), _remote("sharp", T0 + 2_000), _remote("next_shot", T0 + 5_000),
             _remote("unreachable", T0 + 7_000)]

    async def run():
        real_sleep = asyncio.sleep

        async def no_wait(_):
            await real_sleep(0)
        asyncio.sleep = no_wait
        try:
            await coord._shot_pass(coord._shot_candidates(items))
        finally:
            asyncio.sleep = real_sleep
    asyncio.run(run())

    assert [item.shot_scanned for item in items] == [True, True, True, False]
    result = similar.group_similar(items, 30)
    # The shaky and sharp snaps are one moment; the next shot is a different picture.
    assert [item.source_id for item in result.kept] == ["sharp", "next_shot", "unreachable"]
    assert saved and len(saved[-1]["shots"]) == 3

    # After a refresh the album is listed afresh; stored fingerprints come back.
    fresh = [_remote("shaky", T0), _remote("sharp", T0 + 2_000)]
    asyncio.run(coord._apply_known_shots(fresh))
    assert all(item.shot_scanned and item.fingerprint for item in fresh)


def test_google_previews_are_requested_small():
    import asyncio

    asked = []
    cam = camera.AlbumSlideshowCamera.__new__(camera.AlbumSlideshowCamera)
    cam.coordinator = types.SimpleNamespace(provider="google_shared")

    async def fake_fetch(url):
        asked.append(url)
        return b"jpeg"

    cam._fetch_remote = fake_fetch
    item = _remote("abc", T0)
    item.url = "https://lh3.googleusercontent.com/pw/AP1GczExample=w4000-h3000"
    assert asyncio.run(cam._shot_preview_bytes(item)) == b"jpeg"
    assert asked == ["https://lh3.googleusercontent.com/pw/AP1GczExample=w512-h512"]
    # Other web sources use the photo's own link.
    cam.coordinator = types.SimpleNamespace(provider="nextcloud")
    item.url = "https://cloud.example/remote.php/dav/files/me/IMG_1.jpg"
    asyncio.run(cam._shot_preview_bytes(item))
    assert asked[-1] == item.url
    # Immich: its own preview, even when the slides use the original.
    cam.coordinator = types.SimpleNamespace(provider="immich")
    item.url = "https://immich.local/api/assets/1a2b/original"
    asyncio.run(cam._shot_preview_bytes(item))
    assert asked[-1] == "https://immich.local/api/assets/1a2b/thumbnail?size=preview"


# ---------------------------------------------------------------------------
# 1.2.0: match levels, no chaining, background comparisons
# ---------------------------------------------------------------------------


def _with_person(img: Image.Image, x: int) -> Image.Image:
    """The same backdrop with a person-sized shape at ``x``."""
    out = img.copy()
    draw = ImageDraw.Draw(out)
    draw.rectangle([x, 150, x + 220, 560], fill=(30, 30, 30))
    draw.ellipse([x + 50, 60, x + 170, 180], fill=(200, 170, 140))
    return out


def _bits(count: int) -> str:
    """A pre-1.2.0 fingerprint whose hash has ``count`` bits set, grey colour."""
    return f"{(1 << count) - 1:016x}808080"


def test_someone_who_moved_is_a_different_picture_at_every_level():
    backdrop = _scene(20)
    here, _ = similar.fingerprint_image(_with_person(backdrop, 60))
    again, _ = similar.fingerprint_image(_resnap(_with_person(backdrop, 60)))
    moved, _ = similar.fingerprint_image(_with_person(backdrop, 480))
    for level in similar.MATCH_LEVELS:
        items = [_photo("a", at=T0, fp=here, sharp=5), _photo("b", at=T0 + 3_000, fp=again, sharp=4),
                 _photo("c", at=T0 + 6_000, fp=moved, sharp=9)]
        result = similar.group_similar(items, 30, level=level)
        assert [item.filename for item in result.kept] == ["a.jpg", "c.jpg"], level


def test_levels_differ_only_in_how_alike_shots_must_be():
    a, b = _photo("a", at=T0, fp=_bits(0), sharp=2), _photo("b", at=T0 + 1_000, fp=_bits(7), sharp=1)
    # Old fingerprints fall back to the hash: 7 bits apart.
    assert similar.group_similar([a, b], 30, level="strict").folded == 0
    assert similar.group_similar([a, b], 30, level="balanced").folded == 1
    assert similar.group_similar([a, b], 30, level="loose").folded == 1
    strict, balanced, loose = (similar.LEVEL_THRESHOLDS[level] for level in similar.MATCH_LEVELS)
    assert strict < balanced < loose
    # An unknown level behaves as the default.
    assert similar.group_similar([a, b], 30, level="bogus").folded == 1


def test_a_run_of_slowly_changing_shots_does_not_chain_into_one_group():
    # a~b and b~c, but a and c differ: a shot must match the whole group.
    items = [
        _photo("a", at=T0, fp=_bits(0), sharp=3),
        _photo("b", at=T0 + 2_000, fp=_bits(7), sharp=2),
        _photo("c", at=T0 + 4_000, fp=_bits(14), sharp=1),
    ]
    result = similar.group_similar(items, 30, level="balanced")
    assert result.folded == 1
    assert [item.filename for item in result.kept] == ["a.jpg", "c.jpg"]


def test_a_group_lasts_one_window_from_its_first_shot():
    fp, sharp = similar.fingerprint_image(_scene(21))
    items = [_photo(name, at=T0 + offset, fp=fp, sharp=sharp)
             for name, offset in (("a", 0), ("b", 20_000), ("c", 40_000))]
    result = similar.group_similar(items, 30)
    # a and b are one moment; c is 40 s after a, so it starts a new group.
    assert result.folded == 1 and len(result.kept) == 2


def _burst(prefix: str, start: int, seed: int) -> list[MediaItem]:
    base = _scene(seed)
    out = []
    for n, img in enumerate((base, _resnap(base), base.filter(ImageFilter.GaussianBlur(2)))):
        fp, sharp = similar.fingerprint_image(img)
        out.append(_photo(f"{prefix}{n}", at=start + n * 2_000, fp=fp, sharp=sharp))
    return out


def test_comparisons_are_worked_out_in_the_background_when_the_budget_runs_out():
    similar.clear_scores()
    items = _burst("x", T0, 30) + _burst("y", T0 + 600_000, 31)
    short = similar.group_similar(items, 30, budget=0)
    assert short.folded == 0 and short.incomplete
    version = similar.scores_version()
    assert similar.warm_scores(items, 30) == 6  # three pairs in each burst
    assert similar.scores_version() > version
    assert similar.warm_scores(items, 30) == 0
    done = similar.group_similar(items, 30, budget=0)
    assert done.folded == 4 and not done.incomplete
    # Unlimited (the default) works them out inline.
    similar.clear_scores()
    assert similar.group_similar(items, 30).folded == 4


def test_scores_without_numpy_match_numpy(monkeypatch):
    pytest = __import__("pytest")
    numpy = pytest.importorskip("numpy")
    assert numpy is not None
    base = _scene(40)
    pairs = [
        (base, _resnap(base)), (base, _scene(41)),
        (_with_person(base, 60), _with_person(base, 480)),
    ]
    fingerprints = [(similar.fingerprint_image(a)[0], similar.fingerprint_image(b)[0]) for a, b in pairs]
    similar.clear_scores()
    with_numpy = [similar.detail_score(a, b) for a, b in fingerprints]
    similar.clear_scores()
    monkeypatch.setattr(similar, "_np", None)
    without = [similar.detail_score(a, b) for a, b in fingerprints]
    assert all(abs(x - y) < 1e-3 for x, y in zip(with_numpy, without))
    # warm_scores takes the same path.
    similar.clear_scores()
    items = [_photo("a", at=T0, fp=fingerprints[0][0]), _photo("b", at=T0 + 1_000, fp=fingerprints[0][1])]
    assert similar.warm_scores(items, 30) == 1
    similar.clear_scores()


def test_fingerprints_from_before_1_2_are_read_again():
    from custom_components.album_slideshow_plus.coordinator import AlbumCoordinator

    coord = AlbumCoordinator.__new__(AlbumCoordinator)
    coord.provider = "local_folder"
    coord.store = types.SimpleNamespace(group_similar_shots=True, similar_window_seconds=30)
    fp_new, sharp = similar.fingerprint_image(_scene(50))
    items = [
        _photo("old", at=T0, fp=_bits(3), sharp=1),
        _photo("new", at=T0 + 2_000, fp=fp_new, sharp=sharp),
        _photo("unreadable", at=T0 + 3_000),
    ]
    for item in items:
        item.exif_scanned = True
    items[2].shot_scanned = True
    assert [item.filename for item in coord._shot_candidates(items)] == ["old.jpg"]


def test_the_playlist_uses_the_chosen_match_level():
    items = [_photo("a", at=T0, fp=_bits(0), sharp=2), _photo("b", at=T0 + 1_000, fp=_bits(7), sharp=1)]
    cam = _cam(items)
    cam.store.similar_match = "strict"
    assert len(cam._effective_items()) == 2
    cam.store.similar_match = "balanced"
    assert [item.filename for item in cam._effective_items()] == ["a.jpg"]
    assert cam._similar_input == items


def test_sensor_reports_what_each_level_would_skip():
    from custom_components.album_slideshow_plus import sensor
    from custom_components.album_slideshow_plus.const import DOMAIN

    items = [
        _photo("a", at=T0, fp=_bits(0), sharp=3), _photo("b", at=T0 + 1_000, fp=_bits(4), sharp=2),
        _photo("c", at=T0 + 600_000, fp=_bits(0), sharp=3), _photo("d", at=T0 + 601_000, fp=_bits(10), sharp=1),
    ]
    cam = _cam(items)
    cam.store.similar_match = "balanced"
    ent = sensor.SimilarShotsSensor.__new__(sensor.SimilarShotsSensor)
    ent.entry = types.SimpleNamespace(entry_id="e1")
    ent.hass = types.SimpleNamespace(data={DOMAIN: {"e1": {"camera": cam}}})
    ent.coordinator = types.SimpleNamespace(store=cam.store, data={"items": items})
    attrs = ent.extra_state_attributes
    assert ent.native_value == 1
    assert attrs["match"] == "balanced"
    assert attrs["skipped_if"] == {"strict": 1, "balanced": 1, "loose": 2}
    assert attrs["photos_rechecking"] == 4
