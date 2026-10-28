"""Deterministic frame gate for camera stills (built Oct 27, 2026).

The vision classifier (vision.py) judges what a frame shows. This gate
answers a prior question, in plain code the model cannot talk past:
does this frame actually contain a usable subject at all?

Origin: on Oct 27, 2026 the operator was sent camera stills in which
the person was clipped at the frame edge, tiny, or not in the frame at
all. His rule, coded here: a still is only accepted as evidence of a
person when every gate below passes. A frame that fails is rejected;
the caller scrubs the clip for a better frame or reports "no usable
shot". An empty frame is never delivered as if it showed someone.

Method: fixed cameras make background subtraction reliable. The
subject is the largest changed blob versus a reference frame of the
same scene (an empty-scene frame, or the median of several frames,
which removes anything that moved). Exposure drift is normalized by
matching per-channel means before diffing.

Pure stdlib: frames arrive decoded as flat row-major RGB byte lists,
so fixtures can synthesize pixel grids and the live caller decodes
with whatever imaging library it already has.

Gates (all must pass):
  1. A subject blob is detected.
  2. Subject height >= 12% of frame height.
  3. Subject area   >= 0.8% of frame area.
  4. Subject is not clipped by the left or right frame edge.
"""
from dataclasses import dataclass, field

MIN_HEIGHT_SHARE = 0.12
MIN_AREA_SHARE = 0.008
DIFF_THRESHOLD = 25        # max per-channel change (0-255)
MIN_BLOB_PIXELS = 300      # in full-resolution pixels
DOWNSCALE = 2              # blob search grid step for large frames


@dataclass
class GateResult:
    passed: bool
    reason: str = ""
    subject: dict | None = None
    gates: dict = field(default_factory=dict)


def _channel_means(px, w, h):
    n = w * h
    return [sum(px[c::3]) / n for c in range(3)]


def subject_bbox(candidate, reference, w, h):
    """Largest changed blob vs the reference frame, or None."""
    if len(candidate) != w * h * 3 or len(reference) != w * h * 3:
        raise ValueError("frame size mismatch")
    c_means = _channel_means(candidate, w, h)
    r_means = _channel_means(reference, w, h)
    gain = [r_means[c] - c_means[c] for c in range(3)]

    step = DOWNSCALE if max(w, h) > 400 else 1
    gw, gh = w // step, h // step
    mask = [[False] * gw for _ in range(gh)]
    for gy in range(gh):
        base = (gy * step) * w * 3
        for gx in range(gw):
            i = base + gx * step * 3
            d = 0
            for c in range(3):
                diff = abs(candidate[i + c] + gain[c] - reference[i + c])
                if diff > d:
                    d = diff
            mask[gy][gx] = d > DIFF_THRESHOLD

    seen = [[False] * gw for _ in range(gh)]
    best = None
    for y in range(gh):
        for x in range(gw):
            if mask[y][x] and not seen[y][x]:
                cells = []
                stack = [(y, x)]
                seen[y][x] = True
                while stack:
                    cy, cx = stack.pop()
                    cells.append((cy, cx))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < gh and 0 <= nx < gw and mask[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            stack.append((ny, nx))
                if best is None or len(cells) > len(best):
                    best = cells
    if not best or len(best) * step * step < MIN_BLOB_PIXELS:
        return None
    ys = [c[0] * step for c in best]
    xs = [c[1] * step for c in best]
    return {
        "x_min": min(xs), "x_max": max(xs),
        "y_min": min(ys), "y_max": max(ys),
        "pixels": len(best) * step * step,
    }


def gate_frame(candidate, reference, w, h):
    """Apply every acceptance gate. Returns a GateResult."""
    blob = subject_bbox(candidate, reference, w, h)
    gates = {"subject_detected": blob is not None}
    if blob is None:
        return GateResult(False, "no subject detected in frame", None, gates)
    bh = blob["y_max"] - blob["y_min"] + 1
    height_share = bh / h
    area_share = blob["pixels"] / (w * h)
    clipped = blob["x_min"] <= 2 or blob["x_max"] >= w - 3
    gates["height_share>=%.2f" % MIN_HEIGHT_SHARE] = height_share >= MIN_HEIGHT_SHARE
    gates["area_share>=%.3f" % MIN_AREA_SHARE] = area_share >= MIN_AREA_SHARE
    gates["not_clipped_by_edge"] = not clipped
    subject = {
        "bbox": blob,
        "height_share": round(height_share, 3),
        "area_share": round(area_share, 4),
        "center_x_share": round((blob["x_min"] + blob["x_max"]) / 2 / w, 3),
        "center_y_share": round((blob["y_min"] + blob["y_max"]) / 2 / h, 3),
    }
    failed = [k for k, v in gates.items() if not v]
    if failed:
        return GateResult(False, "failed gates: " + ", ".join(failed), subject, gates)
    return GateResult(True, "", subject, gates)


def centered_crop_box(subject, w, h):
    """Crop box (x0, y0, x1, y1) with the subject centered, 1.62:1."""
    b = subject["bbox"]
    cx = (b["x_min"] + b["x_max"]) // 2
    cy = (b["y_min"] + b["y_max"]) // 2
    crop_w = min(w, max(int((b["x_max"] - b["x_min"]) * 3.2), w // 3))
    crop_h = int(crop_w / 1.62)
    if crop_h > h:
        crop_h = h
        crop_w = int(crop_h * 1.62)
    x0 = min(max(cx - crop_w // 2, 0), w - crop_w)
    y0 = min(max(cy - crop_h // 2, 0), h - crop_h)
    return (x0, y0, x0 + crop_w, y0 + crop_h)
