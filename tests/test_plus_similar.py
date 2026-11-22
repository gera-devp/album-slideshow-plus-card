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
    assert len(fp_base) == 22 and sharp_base > 0


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
    assert len(info["fingerprint"]) == 22
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
    assert similar.group_similar(items, 30, require_fingerprint=True).folded == 0


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
    result = similar.group_similar(items, 30, require_fingerprint=True)
    assert [item.filename for item in result.kept] == ["sharp.jpg", "other_scene.jpg", "same_scene_next_day.jpg"]
    assert len(result.groups) == 1 and result.folded == 2
    assert [item.filename for item in result.groups[0]][0] == "sharp.jpg"
    assert result.group_size[similar._key(items[1])] == 3


def test_shots_further_apart_than_the_window_stay_separate():
    base = _scene(7)
    fp, sharp = similar.fingerprint_image(base)
    items = [_photo("a", at=T0, fp=fp, sharp=sharp), _photo("b", at=T0 + 45_000, fp=fp, sharp=sharp)]
    assert similar.group_similar(items, 30, require_fingerprint=True).folded == 0
    assert similar.group_similar(items, 60, require_fingerprint=True).folded == 1


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
    result = similar.group_similar(items, 30, require_fingerprint=True)
    assert sorted(item.filename for item in result.kept) == ["a1.jpg", "b1.jpg"]


def test_local_photos_wait_for_their_fingerprint():
    items = [_photo("a", at=T0), _photo("b", at=T0 + 1_000)]
    assert similar.group_similar(items, 30, require_fingerprint=True).folded == 0


def test_sources_without_fingerprints_group_by_time_and_size():
    items = [
        _photo("a", at=T0, w=4000, h=3000, size=3_000_000),
        _photo("b", at=T0 + 2_000, w=4000, h=3000, size=3_400_000),
        _photo("portrait", at=T0 + 4_000, w=3000, h=4000),
    ]
    result = similar.group_similar(items, 30, require_fingerprint=False)
    # The bigger file (more detail) is kept; a different orientation is not the same shot.
    assert [item.filename for item in result.kept] == ["b.jpg", "portrait.jpg"]


def test_window_zero_or_tiny_albums_change_nothing():
    items = [_photo("a", at=T0, fp="0" * 22, sharp=1)]
    assert similar.group_similar(items, 30, require_fingerprint=True).kept == items
    two = items + [_photo("b", at=T0, fp="0" * 22, sharp=2)]
    assert similar.group_similar(two, 0, require_fingerprint=True).kept == two


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
        _photo("burst_c_done", at=T0 + 8_000),
    ]
    items[4].shot_scanned = True
    assert [item.filename for item in coord._shot_candidates(items)] == ["burst_a.jpg", "burst_b.jpg"]
    coord.store.group_similar_shots = False
    assert coord._shot_candidates(items) == []
    coord.store.group_similar_shots = True
    coord.provider = "immich"
    assert coord._shot_candidates(items) == []
