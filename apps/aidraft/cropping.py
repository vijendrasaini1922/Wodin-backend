"""Subject-aware cropping, shared by the planner (to choose which photo goes in which slot) and the page renderer (to crop it).

A photo is cropped to fill its slot ("cover" fit). Plain cover-fit centres the picture, which is how a portrait of a person
placed in a wide slot ends up as a torso and legs with no face. The AI describes where the subject is as a box (fractions of
the picture, covering the head AND the body of every person). This module only does arithmetic with that box:

* `window` finds the crop that keeps the subject in frame, giving up the feet before the head;
* `loss` says how much of the subject a slot would cut off, so the planner can prefer a better slot or photo.

All coordinates are fractions (0..1) of the source picture. No AI and no image data are involved here.
"""

Box = tuple[float, float, float, float]  # x0, y0, x1, y1


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def window_size(src_ratio: float, slot_ratio: float) -> tuple[float, float]:
    """Fraction of the source (width, height) that a cover-fit crop shows."""
    if src_ratio >= slot_ratio:
        return slot_ratio / src_ratio, 1.0
    return 1.0, src_ratio / slot_ratio


def window(src_ratio: float, slot_ratio: float, focus: tuple[float, float] = (0.5, 0.5), subject: Box | None = None) -> tuple[float, float, float, float]:
    """The crop as (x0, y0, width, height) in source fractions."""
    wf, hf = window_size(src_ratio, slot_ratio)
    if subject is None:  # same as CSS object-position: the focus point keeps its relative place in the frame
        return (1 - wf) * _clamp(focus[0], 0, 1), (1 - hf) * _clamp(focus[1], 0, 1), wf, hf
    sx0, sy0, sx1, sy1 = subject
    x0 = _clamp((sx0 + sx1) / 2 - wf / 2, 0, 1 - wf)
    if sy1 - sy0 > hf:  # the subject is taller than the frame: keep the head (top), lose the lowest part first
        y0 = _clamp(sy0 - 0.03, 0, 1 - hf)
    else:
        y0 = _clamp((sy0 + sy1) / 2 - hf / 2, 0, 1 - hf)
    return x0, y0, wf, hf


def loss(src_ratio: float, slot_ratio: float, focus: tuple[float, float] = (0.5, 0.5), subject: Box | None = None) -> float:
    """0 = the whole subject is inside the crop; higher = more of it is cut off. A cut-off head counts double."""
    if subject is None:
        return 0.0
    x0, y0, wf, hf = window(src_ratio, slot_ratio, focus, subject)
    sx0, sy0, sx1, sy1 = subject
    area = max((sx1 - sx0) * (sy1 - sy0), 1e-6)
    ix = max(0.0, min(sx1, x0 + wf) - max(sx0, x0))
    iy = max(0.0, min(sy1, y0 + hf) - max(sy0, y0))
    missing = 1.0 - (ix * iy) / area
    head_cut = _clamp((y0 - sy0) / max(sy1 - sy0, 1e-6), 0, 1)
    return round(missing + head_cut, 4)
