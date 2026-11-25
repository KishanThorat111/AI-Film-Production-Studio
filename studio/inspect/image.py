"""Still-image checks and first/last-frame pair checks (before any generation is spent)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

VERSION = "i0.1"


def read(path: Path) -> np.ndarray:
    a = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if a is None:
        raise ValueError("not a readable image")
    return a


def conform(img: np.ndarray, w=1920, h=1080) -> tuple[np.ndarray, dict]:
    """Exact target size. Crops the minimum; never stretches. Reports how much was lost."""
    H, W = img.shape[:2]
    s = max(w / W, h / H)
    r = cv2.resize(img, (int(np.ceil(W * s)), int(np.ceil(H * s))), interpolation=cv2.INTER_LANCZOS4 if s > 1 else cv2.INTER_AREA)
    y, x = (r.shape[0] - h) // 2, (r.shape[1] - w) // 2
    return r[y:y + h, x:x + w], {"scale": round(s, 4), "cropped_px": [int(r.shape[1] - w), int(r.shape[0] - h)]}


def inspect(path: Path, expect: dict) -> dict:
    a = read(path)
    H, W = a.shape[:2]
    g = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    items = []
    want = expect.get("aspect", 16 / 9)
    dev = abs(W / H - want) / want
    items.append(("aspect ratio", "pass" if dev < .001 else "warn" if dev < .02 else "fail", f"{W}x{H}; wanted {want:.4f}" + ("; will be cropped to fit exactly" if 0.001 <= dev < .02 else "")))
    items.append(("resolution", "pass" if H >= 1080 else "warn" if H >= 720 else "fail", f"{W}x{H}"))
    blk, wht = float((g < 3).mean()), float((g > 252).mean())
    items.append(("clipping", "warn" if wht > .02 or blk > .35 else "pass", f"{100 * blk:.1f}% crushed black, {100 * wht:.1f}% blown white"))
    # sharpness where there is detail, not in flat darkness
    t = 64
    lap = cv2.Laplacian(g, cv2.CV_32F)
    tiles = [lap[y:y + t, x:x + t].var() for y in range(0, H - t, t) for x in range(0, W - t, t)]
    sh = float(np.percentile(tiles, 95))
    items.append(("sharpness", "pass" if sh > 60 else "warn", f"{sh:.0f} in the sharpest areas"))
    edge = np.concatenate([a[:4].reshape(-1, 3), a[-4:].reshape(-1, 3), a[:, :4].reshape(-1, 3), a[:, -4:].reshape(-1, 3)])
    rows = [np.abs(a[0].astype(int) - a[1]).mean(), a[:6].std(), a[-6:].std()]
    bars = a[: H // 20].std() < 1.0 and a[-H // 20:].std() < 1.0 and g.std() > 8
    items.append(("borders", "fail" if bars else "pass", "flat bars at top and bottom" if bars else "none"))
    o = [i[1] for i in items]
    return {"version": VERSION, "outcome": "fail" if "fail" in o else "warn" if "warn" in o else "pass", "items": items, "facts": {"width": W, "height": H, "mean": round(float(g.mean()), 1), "sharp": round(sh, 1)},
            "examined": ["the whole image at full size"], "not_examined": ["identity (needs an approved identity pack)", "meaning, anatomy and composition (agents and you)"]}


def _affine(g0, g1, mask):
    sf = cv2.SIFT_create(1800)
    k0, d0 = sf.detectAndCompute(g0, mask)
    k1, d1 = sf.detectAndCompute(g1, mask)
    if d0 is None or d1 is None:
        return None, 0
    ms = [p for p, *q in cv2.BFMatcher().knnMatch(d0, d1, k=2) if q and p.distance < .7 * q[0].distance]
    if len(ms) < 12:
        return None, len(ms)
    M, inl = cv2.estimateAffinePartial2D(np.float32([k0[p.queryIdx].pt for p in ms]), np.float32([k1[p.trainIdx].pt for p in ms]), ransacReprojThreshold=2)
    return (M, int(inl.sum())) if M is not None else (None, len(ms))


def pair(first: Path, last: Path, camera="locked", allowed: list[list[float]] | None = None, seconds=6.0) -> dict:
    """allowed: regions (x0,y0,x1,y1 in 0-1) where change is intended."""
    a, b = read(first), read(last)
    items = []
    if a.shape != b.shape:
        return {"version": VERSION, "outcome": "fail", "items": [("same size", "fail", f"{a.shape[1]}x{a.shape[0]} and {b.shape[1]}x{b.shape[0]}")], "facts": {}}
    s = 960 / a.shape[1]
    a, b = (cv2.resize(x, (960, int(a.shape[0] * s)), interpolation=cv2.INTER_AREA) for x in (a, b))
    H, W = a.shape[:2]
    m = np.full((H, W), 255, np.uint8)
    for x0, y0, x1, y1 in allowed or []:
        m[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)] = 0
    if abs(a.astype(int) - b).mean() < .4:
        items.append(("frames differ", "fail", "the two frames are practically identical"))
    g0, g1 = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    M, n = _affine(g0, g1, m)
    facts = {}
    moved = None
    if M is None:
        items.append(("camera", "warn", f"could not be measured ({n} matching points outside the moving area)"))
    else:
        c = np.float32([[0, 0], [W, 0], [0, H], [W, H]])
        moved = float(np.abs(c @ M[:, :2].T + M[:, 2] - c).max()) * 1920 / W
        zoom = float(np.hypot(M[0, 0], M[1, 0]))
        facts.update(camera_px=round(moved, 1), zoom=round(zoom, 4), matches=n)
        if camera == "locked":
            items.append(("camera locked", "pass" if moved < 4 else "warn" if moved < 8 else "fail", f"{moved:.1f}px at 1920, zoom {zoom:.4f}"))
        else:
            rate = moved / 1920 / seconds
            items.append(("camera move reachable", "pass" if rate < .12 else "warn" if rate < .25 else "fail", f"{100 * rate:.1f}% of the width per second"))
    la, lb = (cv2.cvtColor(cv2.GaussianBlur(x, (0, 0), 2), cv2.COLOR_BGR2LAB).astype(np.float32) for x in (a, b))
    out = m > 0
    d = la - lb
    glob = float(np.linalg.norm(np.median(d[out], axis=0))) if out.any() else 0
    items.append(("light and colour", "pass" if glob < 3 else "warn" if glob < 6 else "fail", f"overall shift {glob:.1f}"))
    de = np.linalg.norm(d - np.median(d[out], axis=0) if out.any() else d, axis=2)
    facts.update(colour_shift=round(glob, 2))
    if camera == "locked" and (moved is None or moved < 8):
        loc = ((de > 18) & out).astype(np.uint8)
        k, _, st, _ = cv2.connectedComponentsWithStats(loc)
        st = sorted([st[i] for i in range(1, k)], key=lambda z: -z[4])
        big = int(st[0][4]) if st else 0
        frac = big / (H * W)
        where = f" at {st[0][0] * 100 // W}%,{st[0][1] * 100 // H}%" if st else ""
        items.append(("background unchanged", "pass" if frac < .0008 else "warn" if frac < .004 else "fail", f"largest change outside the intended area: {100 * frac:.2f}% of the frame{where if frac >= .0008 else ''}"))
    new = float((de > 25).mean())
    facts["changed_fraction"] = round(new, 4)
    items.append(("amount to invent", "pass" if new < .25 else "warn" if new < .45 else "fail", f"{100 * new:.0f}% of the picture must change in {seconds:.0f}s"))
    o = [i[1] for i in items]
    return {"version": VERSION, "outcome": "fail" if "fail" in o else "warn" if "warn" in o else "pass", "items": items, "facts": facts}
