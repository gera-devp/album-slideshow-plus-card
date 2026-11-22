"""Group repeated snaps of the same moment and keep the sharpest.

Pure helpers (Pillow only, no Home Assistant), shared by the coordinator,
the camera and the tests.

Two shots belong to one group when both hold:

1. **Taken close together.** Their capture times are at most the window
   apart (default 30 s), counted shot to shot, so a longer burst still
   forms one group, capped at ``MAX_GROUP_SPAN_FACTOR`` windows overall.
2. **They look the same.** Their visual fingerprints (a 64-bit difference
   hash of a tiny greyscale copy) differ in at most ``MAX_DISTANCE`` bits,
   and their average colours are close (``MAX_COLOUR_DISTANCE``). Sources
   whose photos have no fingerprint fall back to the same orientation and,
   when known, the same pixel size.

Local photos are fingerprinted only when they carry their own capture time
(EXIF). A file's modified time is no evidence of when it was taken: a bulk
copy gives unrelated photos the same time.

Within a group the sharpest shot is kept: the one with the highest
variance of edges (a blurred or shaken shot has soft edges). When
sharpness is unknown, the larger image, then the larger file, wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from PIL import Image, ImageFilter, ImageOps, ImageStat

# Bits (of 64) two fingerprints may differ by and still count as the same
# picture. Repeated snaps of one scene are usually 0-8 apart; different
# scenes are usually 20+ apart.
MAX_DISTANCE = 12
# A chain of shots never spans more than this many windows in total.
MAX_GROUP_SPAN_FACTOR = 4
# Largest distance between average colours (0-255 per channel, Euclidean)
# for two shots of one scene. Re-exposed snaps differ by about 5-25.
MAX_COLOUR_DISTANCE = 48
# Size of the greyscale copy that sharpness is measured on, so photos of
# different resolutions compare fairly.
_SHARPNESS_EDGE = 512


def fingerprint_image(img: Image.Image) -> tuple[str, float]:
    """Return (fingerprint, sharpness) for an image.

    The fingerprint is 22 hex digits: the 64-bit difference hash, then the
    average colour as RRGGBB.
    """
    upright = ImageOps.exif_transpose(img).convert("RGB")
    r, g, b = (int(round(v)) for v in ImageStat.Stat(upright.resize((16, 16), Image.Resampling.BOX)).mean)
    grey = upright.convert("L")
    grey.thumbnail((_SHARPNESS_EDGE, _SHARPNESS_EDGE), Image.Resampling.BILINEAR)
    # Difference hash: is each pixel brighter than its right-hand neighbour?
    small = grey.resize((9, 8), Image.Resampling.BOX)
    pixels = small.tobytes()  # one byte per pixel in mode "L"
    value = 0
    for row in range(8):
        for col in range(8):
            value = (value << 1) | (pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    # Sharpness: variance of the Laplacian (edge strength).
    edges = grey.filter(ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), scale=1, offset=128))
    sharpness = float(ImageStat.Stat(edges).var[0])
    return f"{value:016x}{r:02x}{g:02x}{b:02x}", round(sharpness, 2)


def fingerprint_file(path: Any) -> tuple[str, float] | None:
    """Fingerprint an image file, decoding JPEGs at a reduced size."""
    try:
        with Image.open(path) as img:
            if img.format == "JPEG":
                img.draft("RGB", (_SHARPNESS_EDGE, _SHARPNESS_EDGE))
            img.load()
            return fingerprint_image(img)
    except Exception:  # noqa: BLE001 - unreadable files just aren't grouped
        return None


def distance(a: str, b: str) -> int:
    """Number of differing bits between two fingerprints' difference hashes."""
    return bin(int(a[:16], 16) ^ int(b[:16], 16)).count("1")


def colour_distance(a: str, b: str) -> float | None:
    """Distance between two fingerprints' average colours, if both carry one."""
    if len(a) < 22 or len(b) < 22:
        return None
    ca = [int(a[i:i + 2], 16) for i in (16, 18, 20)]
    cb = [int(b[i:i + 2], 16) for i in (16, 18, 20)]
    return sum((x - y) ** 2 for x, y in zip(ca, cb)) ** 0.5


def _key(item: Any) -> str:
    return getattr(item, "photo_id", None) or getattr(item, "url", "")


def _portrait(item: Any) -> bool | None:
    w, h = getattr(item, "width", None), getattr(item, "height", None)
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        return h > w
    return None


def looks_same(a: Any, b: Any, *, require_fingerprint: bool, max_distance: int = MAX_DISTANCE) -> bool:
    """Whether two shots show the same picture."""
    pa, pb = _portrait(a), _portrait(b)
    if pa is not None and pb is not None and pa != pb:
        return False
    fa, fb = getattr(a, "fingerprint", None), getattr(b, "fingerprint", None)
    if fa and fb:
        colour = colour_distance(fa, fb)
        if colour is not None and colour > MAX_COLOUR_DISTANCE:
            return False
        return distance(fa, fb) <= max_distance
    if require_fingerprint:
        return False
    # No fingerprints (remote sources): time plus matching size.
    wa, ha, wb, hb = (getattr(a, "width", None), getattr(a, "height", None),
                      getattr(b, "width", None), getattr(b, "height", None))
    if all(isinstance(v, int) for v in (wa, ha, wb, hb)):
        return (wa, ha) == (wb, hb)
    return pa is not None and pb is not None


def _quality(item: Any, position: int) -> tuple:
    sharp = getattr(item, "sharpness", None)
    w, h = getattr(item, "width", None), getattr(item, "height", None)
    pixels = w * h if isinstance(w, int) and isinstance(h, int) else 0
    size = getattr(item, "byte_size", None) or 0
    # Earlier position wins ties, so the choice is stable.
    return (sharp if isinstance(sharp, (int, float)) else -1.0, pixels, size, -position)


@dataclass
class Grouping:
    """The result of grouping a playlist."""

    kept: list = field(default_factory=list)
    # Groups of two or more shots, best first.
    groups: list[list] = field(default_factory=list)
    # Photo key of every kept shot that stands for a group -> group size.
    group_size: dict[str, int] = field(default_factory=dict)

    @property
    def folded(self) -> int:
        return sum(len(group) - 1 for group in self.groups)


def group_similar(
    items: Sequence[Any],
    window_seconds: float,
    *,
    require_fingerprint: bool,
    max_distance: int = MAX_DISTANCE,
) -> Grouping:
    """Group repeated snaps; keep the sharpest of each group, in playlist order."""
    window_ms = max(0, int(float(window_seconds) * 1000))
    if window_ms <= 0 or len(items) < 2:
        return Grouping(kept=list(items))
    dated = sorted(
        (item.captured_at, index) for index, item in enumerate(items)
        if type(getattr(item, "captured_at", None)) is int
    )
    # Open groups: [indices], first capture time, last capture time.
    open_groups: list[list] = []
    closed: list[list[int]] = []
    for ts, index in dated:
        still_open = []
        for group in open_groups:
            if ts - group[2] > window_ms:
                closed.append(group[0])
            else:
                still_open.append(group)
        open_groups = still_open
        home = None
        for group in open_groups:
            if ts - group[1] > window_ms * MAX_GROUP_SPAN_FACTOR:
                continue
            if any(
                looks_same(items[member], items[index], require_fingerprint=require_fingerprint,
                           max_distance=max_distance)
                for member in group[0]
            ):
                home = group
                break
        if home is None:
            open_groups.append([[index], ts, ts])
        else:
            home[0].append(index)
            home[2] = ts
    closed.extend(group[0] for group in open_groups)

    drop: set[int] = set()
    result = Grouping()
    for members in closed:
        if len(members) < 2:
            continue
        ranked = sorted(members, key=lambda i: _quality(items[i], i), reverse=True)
        best = ranked[0]
        drop.update(ranked[1:])
        result.groups.append([items[i] for i in ranked])
        result.group_size[_key(items[best])] = len(members)
    result.kept = [item for index, item in enumerate(items) if index not in drop]
    return result
