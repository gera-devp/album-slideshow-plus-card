"""Group repeated snaps of the same moment and keep the sharpest.

Pure helpers (Pillow, and numpy when it is installed; no Home Assistant),
shared by the coordinator, the camera and the tests.

Two shots belong to one group when both hold:

1. **Taken close together.** A group never spans more than the window
   (default 30 s) from its first shot.
2. **They look the same**, at the chosen match level (strict, balanced or
   loose), compared with **every** shot already in the group. Matching only
   one member would let a run of slightly different photos chain into one
   long group.

"Look the same" compares a 16x16 greyscale copy of each photo, both
normalised for brightness and contrast, allowing a one-cell shift for a
moved camera: the score is the mean difference per cell (0 = identical).
People who changed pose, turned or moved change many cells; a re-taken
snap of the same pose changes few. The average colour must be close too.
Fingerprints saved before 1.2.0 have no greyscale copy; until they are
read again, those photos fall back to the 64-bit difference hash.

A photo without a fingerprint is never grouped. Time alone is no evidence:
at a family event people take one different shot after another within
seconds. Local photos are fingerprinted only when they carry their own
capture time (EXIF), since a bulk copy gives unrelated files the same
modified time; other sources are fingerprinted from a small preview.

Within a group the sharpest shot is kept: the one with the highest
variance of edges (a blurred or shaken shot has soft edges). When
sharpness is unknown, the larger image, then the larger file, wins.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, Sequence

from PIL import Image, ImageFilter, ImageOps, ImageStat

try:  # numpy ships with Home Assistant (the camera integration needs it).
    import numpy as _np
except ImportError:  # pragma: no cover - exercised by forcing _np = None
    _np = None

MATCH_STRICT = "strict"
MATCH_BALANCED = "balanced"
MATCH_LOOSE = "loose"
MATCH_LEVELS = (MATCH_STRICT, MATCH_BALANCED, MATCH_LOOSE)
DEFAULT_MATCH = MATCH_BALANCED

# Per level: largest detail score (mean difference per normalised cell),
# and, for fingerprints without a greyscale copy, largest difference-hash
# distance (bits of 64). Calibrated on real camera sequences: snaps of one
# pose taken a moment apart against the same scene seconds later.
LEVEL_THRESHOLDS: dict[str, tuple[float, int]] = {
    MATCH_STRICT: (0.20, 5),
    MATCH_BALANCED: (0.26, 8),
    MATCH_LOOSE: (0.33, 11),
}
# The 1.1 rule, kept for callers that pass a bit distance explicitly.
MAX_DISTANCE = 12
# Largest distance between average colours (0-255 per channel, Euclidean)
# for two shots of one scene. Re-exposed snaps differ by about 5-25.
MAX_COLOUR_DISTANCE = 48
# Size of the greyscale copy that sharpness is measured on, so photos of
# different resolutions compare fairly.
_SHARPNESS_EDGE = 512
# The greyscale copy compared between shots, and the crop compared after
# shifting by up to one cell each way.
DETAIL_EDGE = 16
_CROP = DETAIL_EDGE - 2
_SEPARATOR = "~"

# Detail scores already worked out, keyed by the two fingerprints (sorted).
_scores: dict[tuple[str, str], float] = {}
_scores_version = 0
_MAX_SCORES = 400_000


def fingerprint_image(img: Image.Image) -> tuple[str, float]:
    """Return (fingerprint, sharpness) for an image.

    The fingerprint is 22 hex digits (the 64-bit difference hash, then the
    average colour as RRGGBB), then ``~`` and the 16x16 greyscale copy in
    base64.
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
    detail = grey.resize((DETAIL_EDGE, DETAIL_EDGE), Image.Resampling.BOX).tobytes()
    # Sharpness: variance of the Laplacian (edge strength).
    edges = grey.filter(ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), scale=1, offset=128))
    sharpness = float(ImageStat.Stat(edges).var[0])
    code = base64.b64encode(detail).decode("ascii")
    return f"{value:016x}{r:02x}{g:02x}{b:02x}{_SEPARATOR}{code}", round(sharpness, 2)


def fingerprint_bytes(data: bytes) -> tuple[str, float] | None:
    """Fingerprint image bytes (a downloaded preview)."""
    import io

    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format == "JPEG":
                img.draft("RGB", (_SHARPNESS_EDGE, _SHARPNESS_EDGE))
            img.load()
            return fingerprint_image(img)
    except Exception:  # noqa: BLE001 - an unreadable preview just isn't grouped
        return None


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


def has_detail(fingerprint: str | None) -> bool:
    """Whether a fingerprint carries the greyscale copy (1.2.0 and later)."""
    return bool(fingerprint) and _SEPARATOR in fingerprint


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


def _detail_bytes(fingerprint: str) -> bytes | None:
    try:
        raw = base64.b64decode(fingerprint.split(_SEPARATOR, 1)[1])
    except (IndexError, ValueError):
        return None
    return raw if len(raw) == DETAIL_EDGE * DETAIL_EDGE else None


def _crops_np(raw: bytes):
    """The nine shifted crops of a greyscale copy, each normalised (9, 196)."""
    grid = _np.frombuffer(raw, dtype=_np.uint8).astype(_np.float32).reshape(DETAIL_EDGE, DETAIL_EDGE)
    crops = _np.lib.stride_tricks.sliding_window_view(grid, (_CROP, _CROP)).reshape(9, _CROP * _CROP)
    crops = crops - crops.mean(axis=1, keepdims=True)
    return crops / (crops.std(axis=1, keepdims=True) + 1e-6)


def _crops_py(raw: bytes) -> list[list[float]]:
    out = []
    for dy in range(3):
        for dx in range(3):
            cells = [
                float(raw[(dy + y) * DETAIL_EDGE + dx + x])
                for y in range(_CROP) for x in range(_CROP)
            ]
            mean = sum(cells) / len(cells)
            std = (sum((v - mean) ** 2 for v in cells) / len(cells)) ** 0.5 + 1e-6
            out.append([(v - mean) / std for v in cells])
    return out


def _score_from_crops(ca: Any, cb: Any) -> float:
    """Best match between the centre of each copy and the other's shifts."""
    if _np is not None and not isinstance(ca, list):
        one = _np.abs(cb - ca[4]).mean(axis=1).min()
        two = _np.abs(ca - cb[4]).mean(axis=1).min()
        return float(min(one, two))
    best = float("inf")
    for centre, shifted in ((ca[4], cb), (cb[4], ca)):
        for crop in shifted:
            best = min(best, sum(abs(x - y) for x, y in zip(centre, crop)) / len(centre))
    return best


def _crops(raw: bytes) -> Any:
    return _crops_np(raw) if _np is not None else _crops_py(raw)


def detail_score(a: str, b: str) -> float | None:
    """Mean difference per normalised cell between two fingerprints (0 = same).

    ``None`` when either has no greyscale copy. Results are remembered.
    """
    key = (a, b) if a <= b else (b, a)
    known = _scores.get(key)
    if known is not None:
        return known
    ra, rb = _detail_bytes(a), _detail_bytes(b)
    if ra is None or rb is None:
        return None
    score = _score_from_crops(_crops(ra), _crops(rb))
    _remember(key, score)
    return score


def _remember(key: tuple[str, str], score: float) -> None:
    if len(_scores) >= _MAX_SCORES:
        _scores.clear()
    _scores[key] = round(score, 4)


def scores_version() -> int:
    """Changes whenever ``warm_scores`` adds results."""
    return _scores_version


def clear_scores() -> None:
    """Forget remembered detail scores (tests)."""
    global _scores_version
    _scores.clear()
    _scores_version += 1


def _key(item: Any) -> str:
    return getattr(item, "photo_id", None) or getattr(item, "url", "")


def _portrait(item: Any) -> bool | None:
    w, h = getattr(item, "width", None), getattr(item, "height", None)
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        return h > w
    return None


def _could_match(a: Any, b: Any) -> bool:
    """Cheap checks before comparing detail: fingerprints, orientation, colour."""
    fa, fb = getattr(a, "fingerprint", None), getattr(b, "fingerprint", None)
    if not fa or not fb:
        return False
    pa, pb = _portrait(a), _portrait(b)
    if pa is not None and pb is not None and pa != pb:
        return False
    colour = colour_distance(fa, fb)
    return colour is None or colour <= MAX_COLOUR_DISTANCE


class _Budget:
    """How many uncached detail scores a grouping may still work out."""

    def __init__(self, limit: int | None) -> None:
        self.left = limit
        self.short = False

    def allow(self) -> bool:
        if self.left is None:
            return True
        if self.left <= 0:
            self.short = True
            return False
        self.left -= 1
        return True


def looks_same(
    a: Any,
    b: Any,
    *,
    level: str = DEFAULT_MATCH,
    max_distance: int | None = None,
    budget: _Budget | None = None,
) -> bool:
    """Whether two shots show the same picture. Both need a fingerprint."""
    if not _could_match(a, b):
        return False
    fa, fb = a.fingerprint, b.fingerprint
    limit, bits = LEVEL_THRESHOLDS.get(level, LEVEL_THRESHOLDS[DEFAULT_MATCH])
    if max_distance is not None:
        return distance(fa, fb) <= max_distance
    if has_detail(fa) and has_detail(fb):
        key = (fa, fb) if fa <= fb else (fb, fa)
        score = _scores.get(key)
        if score is None:
            if budget is not None and not budget.allow():
                return False
            score = detail_score(fa, fb)
        return score is not None and score <= limit
    return distance(fa, fb) <= bits


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
    # True when some comparisons were left for later (see ``warm_scores``).
    incomplete: bool = False

    @property
    def folded(self) -> int:
        return sum(len(group) - 1 for group in self.groups)


def _dated(items: Sequence[Any]) -> list[tuple[int, int]]:
    return sorted(
        (item.captured_at, index) for index, item in enumerate(items)
        if type(getattr(item, "captured_at", None)) is int and getattr(item, "fingerprint", None)
    )


def group_similar(
    items: Sequence[Any],
    window_seconds: float,
    *,
    level: str = DEFAULT_MATCH,
    max_distance: int | None = None,
    budget: int | None = None,
) -> Grouping:
    """Group repeated snaps; keep the sharpest of each group, in playlist order.

    ``budget`` caps how many detail scores not worked out yet may be
    computed here (the rest count as different for now), so a large album
    never stalls the caller; ``warm_scores`` fills them in the background.
    """
    window_ms = max(0, int(float(window_seconds) * 1000))
    if window_ms <= 0 or len(items) < 2:
        return Grouping(kept=list(items))
    allowance = _Budget(budget)
    # Open groups: [indices], first capture time.
    open_groups: list[list] = []
    closed: list[list[int]] = []
    for ts, index in _dated(items):
        still_open = []
        for group in open_groups:
            if ts - group[1] > window_ms:
                closed.append(group[0])
            else:
                still_open.append(group)
        open_groups = still_open
        home = None
        for group in open_groups:
            if all(
                looks_same(
                    items[member], items[index],
                    level=level, max_distance=max_distance, budget=allowance,
                )
                for member in group[0]
            ):
                home = group
                break
        if home is None:
            open_groups.append([[index], ts])
        else:
            home[0].append(index)
    closed.extend(group[0] for group in open_groups)

    drop: set[int] = set()
    result = Grouping(incomplete=allowance.short)
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


def warm_scores(items: Sequence[Any], window_seconds: float, *, batch: int = 1024) -> int:
    """Work out every detail score a grouping could need; return how many were new.

    Blocking: run it in an executor. Pairs are shots taken within the window
    of each other that pass the cheap checks.
    """
    global _scores_version
    window_ms = max(0, int(float(window_seconds) * 1000))
    dated = [
        (ts, items[index]) for ts, index in _dated(items)
        if has_detail(getattr(items[index], "fingerprint", None))
    ]
    pairs: list[tuple[str, str]] = []
    wanted: set[tuple[str, str]] = set()
    for i, (ts, item) in enumerate(dated):
        for j in range(i + 1, len(dated)):
            other_ts, other = dated[j]
            if other_ts - ts > window_ms:
                break
            if not _could_match(item, other):
                continue
            fa, fb = item.fingerprint, other.fingerprint
            key = (fa, fb) if fa <= fb else (fb, fa)
            if key in _scores or key in wanted:
                continue
            wanted.add(key)
            pairs.append(key)
    if not pairs:
        return 0
    crops: dict[str, Any] = {}

    def crops_of(fingerprint: str) -> Any:
        known = crops.get(fingerprint)
        if known is None:
            raw = _detail_bytes(fingerprint)
            known = _crops(raw) if raw is not None else False
            crops[fingerprint] = known
        return known

    added = 0
    for start in range(0, len(pairs), batch):
        chunk = [
            (key, crops_of(key[0]), crops_of(key[1]))
            for key in pairs[start:start + batch]
        ]
        chunk = [entry for entry in chunk if entry[1] is not False and entry[2] is not False]
        if not chunk:
            continue
        if _np is not None:
            left = _np.stack([entry[1] for entry in chunk])
            right = _np.stack([entry[2] for entry in chunk])
            one = _np.abs(right - left[:, 4:5, :]).mean(axis=2).min(axis=1)
            two = _np.abs(left - right[:, 4:5, :]).mean(axis=2).min(axis=1)
            values = _np.minimum(one, two).tolist()
        else:
            values = [_score_from_crops(entry[1], entry[2]) for entry in chunk]
        for (key, _a, _b), value in zip(chunk, values):
            _remember(key, float(value))
            added += 1
    if added:
        _scores_version += 1
    return added
