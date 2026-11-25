"""Local video inspection. Every frame is measured; stills shown to agents are chosen from the measurements.
Thresholds are provisional until calibrated on real footage: results are 'pass', 'warn' or 'fail', and a warn is never a pass.
"""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import av
import cv2
import numpy as np

VERSION = "v0.1"
WORK_W = 480


def technical(path: Path, probe: dict, expect: dict) -> dict:
    """V0: facts about the file."""
    out, vs = [], [s for s in probe["streams"] if s["codec_type"] == "video"]
    if not vs:
        return {"outcome": "fail", "items": [("no picture stream", "fail")]}
    v = vs[0]
    w, h = int(v["width"]), int(v["height"])
    num, den = (int(x) for x in v["avg_frame_rate"].split("/"))
    fps = num / den if den else 0
    r_num, r_den = (int(x) for x in v["r_frame_rate"].split("/"))
    dur = float(probe["format"].get("duration", 0))
    facts = {"width": w, "height": h, "fps": round(fps, 3), "duration": round(dur, 3), "codec": v["codec_name"], "pix_fmt": v.get("pix_fmt"),
             "audio_streams": sum(s["codec_type"] == "audio" for s in probe["streams"]), "bit_rate": int(probe["format"].get("bit_rate", 0) or 0)}
    ar = w / h
    want = expect.get("aspect", 16 / 9)
    out.append(("aspect ratio", "pass" if abs(ar - want) / want < 0.005 else "fail", f"{w}x{h} ({ar:.4f}), wanted {want:.4f}"))
    out.append(("resolution", "pass" if min(w, h) >= 720 else "warn", f"{w}x{h}"))
    lo, hi = expect.get("duration", (3, 10))
    out.append(("duration", "pass" if lo - .25 <= dur <= hi + .25 else "warn", f"{dur:.2f}s, wanted {lo}-{hi}s"))
    out.append(("constant frame rate", "pass" if abs(fps - r_num / max(r_den, 1)) < .05 else "warn", f"{fps:.3f}"))
    err = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, timeout=300).stderr.strip()
    out.append(("full decode", "fail" if err else "pass", err[:200] or "clean"))
    return {"facts": facts, "items": out}


def load_frames(path: Path, width=WORK_W):
    small, n = [], 0
    with av.open(str(path)) as c:
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        for f in c.decode(s):
            a = f.to_ndarray(format="bgr24")
            h = int(round(a.shape[0] * width / a.shape[1] / 2) * 2)
            small.append(cv2.resize(a, (width, h), interpolation=cv2.INTER_AREA))
            n += 1
    return np.stack(small) if small else np.zeros((0, 2, 2, 3), np.uint8)


def frame_at(path: Path, idx: int) -> np.ndarray:
    """Exact frame by counting; never by seconds."""
    with av.open(str(path)) as c:
        for i, f in enumerate(c.decode(video=0)):
            if i == idx:
                return f.to_ndarray(format="bgr24")
    raise IndexError(idx)


def _runs(mask):
    out, s = [], None
    for i, m in enumerate(list(mask) + [False]):
        if m and s is None:
            s = i
        if not m and s is not None:
            out.append((s, i - 1))
            s = None
    return out


def _mad(x):
    m = np.median(x)
    return m, np.median(np.abs(x - m)) * 1.4826 + 1e-6


def temporal(fr: np.ndarray, camera="locked") -> dict:
    """V1: every frame. Returns series, flagged windows and a camera summary."""
    n = len(fr)
    gray = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in fr]).astype(np.float32)
    H, W = gray.shape[1:]
    flags, S, shape_ok = [], {}, False
    with ThreadPoolExecutor(3) as pool:                  # three long measurements that do not need each other
        later = [pool.submit(motion_residual, gray), pool.submit(camera_path, gray), pool.submit(subject_series, gray) if camera == "locked" and n > 30 else None]
        return _temporal(fr, gray, n, H, W, camera, later, flags, S, shape_ok)


def _temporal(fr, gray, n, H, W, camera, later, flags, S, shape_ok):
    luma = gray.reshape(n, -1).mean(1)
    S["luma"] = luma
    S["sharp"] = np.array([cv2.Laplacian(g, cv2.CV_32F).var() for g in gray])
    diff = np.array([0] + [float(np.abs(gray[i] - gray[i - 1]).mean()) for i in range(1, n)])
    S["diff"] = diff
    # black / frozen / cut
    for a, b in _runs((luma < 6) & (gray.reshape(n, -1).std(1) < 4)):
        flags.append(("black frames", a, b, "critical", f"{b - a + 1} black frames"))
    moves = np.array([float(np.median(diff[max(1, i - 10):i + 11])) for i in range(n)])
    for a, b in _runs(diff[1:] < 0.02):
        if b - a + 1 >= 5:
            before, after = diff[max(1, a - 8):a + 1], diff[b + 2:b + 10]
            stopped = len(before) >= 4 and len(after) >= 4 and np.median(before) > .12 and np.median(after) > .12      # movement on both sides: it stuck
            if stopped or b - a + 1 >= max(6, n // 12):
                flags.append(("frozen", a + 1, b + 1, "major" if stopped else "moderate", f"{b - a + 1} identical frames" + ("" if stopped else "; this may be intended stillness")))
    med, mad = _mad(diff[1:]) if n > 2 else (0, 1)
    hist = [cv2.calcHist([f], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3).ravel() / (H * W) for f in fr]
    hd = np.array([0] + [float(np.abs(hist[i] - hist[i - 1]).sum() / 2) for i in range(1, n)])
    S["hist"] = hd
    tiny = np.stack([cv2.resize(cv2.GaussianBlur(g, (0, 0), 2), (96, 54), interpolation=cv2.INTER_AREA) for g in gray])
    corr = np.array([1.0] + [float(np.corrcoef(tiny[i].ravel(), tiny[i - 1].ravel())[0, 1]) if min(tiny[i].std(), tiny[i - 1].std()) > 3 else 1.0 for i in range(1, n)])
    S["corr"] = corr
    for i in range(1, n):
        if diff[i] > max(med + 10 * mad, 5) and (hd[i] > .35 or corr[i] < .55):
            flags.append(("unplanned cut", i, i, "critical", f"picture changes abruptly at frame {i}"))
    # single-frame glitches: far from both neighbours while the neighbours agree with each other
    for k in (1, 2, 3):
        for i in range(k, n - k):
            around = float(np.abs(gray[i - k] - gray[i + k]).mean())
            here = min(float(np.abs(gray[i] - gray[i - k]).mean()), float(np.abs(gray[i] - gray[i + k]).mean()))
            if here > 2.2 * around + 1.2 and here > 3 * med + 1.0:
                flags.append(("short glitch", i, i, "major", f"frame {i} differs from frames {i - k} and {i + k}, which agree"))
    # luma flicker: alternating second difference, robust to fades
    if n > 8:
        hf = luma[1:-1] - (luma[:-2] + luma[2:]) / 2
        win = np.convolve(np.abs(hf), np.ones(8) / 8, "same")
        flips = np.convolve((np.sign(hf[1:]) != np.sign(hf[:-1])).astype(float), np.ones(8) / 8, "same")
        for a, b in _runs(win > 0.9):
            if b - a >= 4:
                rel = float(win[a:b + 1].max() / max(8.0, luma[a + 1:b + 2].mean()))
                steady = flips[min(a, len(flips) - 1):b + 1].mean() > .75                    # up, down, up, down: a model's pumping; a flame or a lamp wanders
                flags.append(("luma flicker", a + 1, b + 1, "major" if steady and rel > .03 else "moderate", f"brightness alternates by about {2 * float(win[a:b + 1].max()):.1f} levels"))
    flags += colour_jumps(fr, n) + soft_runs(S["sharp"], n)
    mo = later[0].result()
    S["unexplained"] = mo["frac"]
    flags += mo["flags"]
    # camera path from background features
    cam = later[1].result()
    S["cam_shift"], S["cam_zoom"] = cam["shift"], cam["zoom"]
    if camera == "locked":
        worst = float(np.nanmax(cam["shift"])) if n > 1 else 0
        z = float(np.nanmax(np.abs(cam["zoom"] - 1))) if n > 1 else 0
        if worst * 1920 / W > 6 or z > .01:
            flags.append(("camera moved", int(np.nanargmax(cam["shift"])), n - 1, "major", f"up to {worst * 1920 / W:.1f}px (at 1920) and {100 * z:.1f}% zoom in a locked shot"))
    # slow change in a static region (object creeps, texture boils): compare start and end medians outside moving areas
    if camera == "locked" and n > 24:
        a, b = np.median(gray[:8], 0), np.median(gray[-8:], 0)
        act = np.percentile(np.abs(np.diff(gray[::2], axis=0)), 95, axis=0) if n > 4 else np.zeros_like(a)
        quiet = (cv2.blur((act < 6).astype(np.float32), (9, 9)) > .95) & (subject_path(gray) == 0)
        ch = ((np.abs(cv2.GaussianBlur(a - b, (0, 0), 1.5)) > 9) & quiet).astype(np.uint8)
        k, lab, st, _ = cv2.connectedComponentsWithStats(ch)
        big = [st[i] for i in range(1, k) if st[i, 4] > .0012 * H * W]
        if big:
            x, y, w, h = max(big, key=lambda s: s[4])[:4]
            flags.append(("static area changed", 0, n - 1, "moderate", f"region {x * 100 // W}%,{y * 100 // H}% changed slowly without visible motion", [x / W, y / H, (x + w) / W, (y + h) / H]))
    # subject mask area and self-similarity (vanish, morph) for locked cameras
    if camera == "locked" and n > 30:
        subj = later[2].result()
        S["subj_area"] = subj["area"]
        S["subj_sim"] = subj["sim"]
        shape_ok = subj["reliable"]
        flags += subj["flags"]
    return {"series": S, "flags": merge(flags), "n": n, "shape_ok": shape_ok}


def colour_jumps(fr, n, k=5):
    """The balance of colours changes from one frame to the next and stays changed. Light that moves changes it slowly."""
    c = np.array([[float(x.mean()) + 1.0 for x in cv2.split(f)] for f in fr])
    r = np.stack([np.log(c[:, 2] / c[:, 1]), np.log(c[:, 0] / c[:, 1])], 1)
    out = []
    for i in range(k, n - k):
        step = np.median(r[i:i + k], 0) - np.median(r[i - k:i], 0)
        j = int(np.argmax(np.abs(step)))
        before, after = r[i - k:i, j], r[i:i + k, j]
        calm = max(np.ptp(before), np.ptp(after))
        now = abs(r[i, j] - r[i - 1, j])
        if abs(step[j]) > .07 and now > .6 * abs(step[j]) and calm < .45 * abs(step[j]):
            out.append(("colour jump", i - 1, i, "major", f"the colour balance changes by {100 * (np.exp(abs(step[j])) - 1):.0f}% at frame {i} and stays"))
    return out


def soft_runs(sharp, n):
    """Detail disappears for a while and comes back, or never was there after a frame."""
    s = np.log(np.maximum(sharp, 1e-3))
    out = []
    for i in range(6, n - 4):
        drop = np.median(s[max(0, i - 8):i]) - np.median(s[i:i + 6])
        if drop > 1.1 and s[i - 1] - s[i] > .5 * drop and np.ptp(s[max(0, i - 6):i]) < .5 * drop:
            e = i
            base = np.median(s[max(0, i - 8):i])
            while e < n - 1 and s[e + 1] < base - .5 * drop:
                e += 1
            out.append(("goes soft", i, e, "major", f"fine detail falls to {100 * np.exp(-drop):.0f}% at frame {i}"))
    return out


def motion_residual(gray):
    """What movement cannot explain. Every frame is predicted from its neighbours along the optical flow; what remains in one place, suddenly or for a
    while, is something that appeared, vanished or changed its shape. Moving cameras and people are followed, so they do not count."""
    n, (H, W) = len(gray), gray.shape[1:]
    frac, flags = np.zeros(n), []
    if n < 12:
        return {"frac": frac, "flags": flags}
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
    gx, gy = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    g8 = [np.clip(cv2.GaussianBlur(g, (0, 0), .8), 0, 255).astype(np.uint8) for g in gray]
    by, bx = 15, 27
    blocks, both, back = (np.zeros((n, by, bx), np.float32) for _ in range(3))
    dark = np.array([float(np.percentile(g, 99)) < 14 for g in gray])

    def left(a, b):
        f = dis.calc(b, a, None)                      # where each pixel of b was in a
        w = cv2.remap(a, gx + f[..., 0], gy + f[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        e = cv2.absdiff(b, w).astype(np.float32)
        m = np.maximum(np.abs(f[..., 0]), np.abs(f[..., 1]))
        edge = (gx + f[..., 0] < 1) | (gx + f[..., 0] > W - 2) | (gy + f[..., 1] < 1) | (gy + f[..., 1] > H - 2)
        e[edge] = 0                                   # came from outside the picture
        gain = float(np.median(b[::4, ::4])) - float(np.median(w[::4, ::4]))
        return np.maximum(e - abs(gain), 0), m
    grad = [cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)) / 8 for g in g8]
    for i in range(1, n):
        e1, m = left(g8[i - 1], g8[i])
        if i + 1 < n:                                 # seen from both sides: what was only covered before is no surprise
            e2, _ = left(g8[i + 1], g8[i])
            pair = np.minimum(e1, e2)
        else:
            pair = e1
        div = 4 + .35 * cv2.blur(grad[i], (7, 7)) + .25 * np.minimum(m, 12)
        blocks[i] = cv2.resize(e1 / div, (bx, by), interpolation=cv2.INTER_AREA)
        back[i] = cv2.resize((e2 if i + 1 < n else e1 * 0) / div, (bx, by), interpolation=cv2.INTER_AREA)
        both[i] = cv2.resize(pair / div, (bx, by), interpolation=cv2.INTER_AREA)
        frac[i] = float((e1 > 18).mean())
    med = np.median(blocks[1:], 0)
    mad = np.median(np.abs(blocks[1:] - med), 0) * 1.4826
    z = (blocks - med) / (mad + .12 + .25 * med)
    hot = (z > 6) & (blocks > .75)
    mb = np.median(back[1:-1], 0)
    db = np.median(np.abs(back[1:-1] - mb), 0) * 1.4826
    lost = (back - mb) / (db + .12 + .25 * mb) > 4          # what this frame shows here is not in the next one
    lost = np.concatenate([lost[:1] * False, lost[:-1]])     # compared with the frame that follows it
    grown = np.stack([cv2.dilate(x.astype(np.uint8), np.ones((3, 3), np.uint8)) for x in lost]).astype(bool)
    swap = hot & grown                                       # a new thing here AND the old thing gone: not something that was merely uncovered
    per = hot.reshape(n, -1).mean(1)
    fm, fd = _mad(frac[1:])
    for i in range(2, n):
        if dark[max(0, i - 3):i].any():
            continue                                  # out of the dark, everything is new
        if frac[i] > max(.16, fm + 12 * fd) and frac[i] > 4 * np.median(frac[max(1, i - 6):i]) and per[i] > .16:
            flags.append(("unplanned cut", i, i, "critical", f"{100 * frac[i]:.0f}% of the picture at frame {i} cannot come from the frame before"))
    cuts = {f[1] for f in flags}

    def spots(mask, least):
        out = [[]]
        for i in range(1, n):
            part = mask[i].mean()
            if part == 0 or part > .35 or dark[max(0, i - 3):i].any() or any(abs(i - c) <= 1 for c in cuts):
                out.append([])
                continue
            k, lab, st, _ = cv2.connectedComponentsWithStats(mask[i].astype(np.uint8), connectivity=8)
            out.append([tuple(st[j][:4]) for j in range(1, k) if st[j, 4] >= least])
        return out
    single = spots(swap, 2)
    m2 = np.median(both[1:], 0)
    d2 = np.median(np.abs(both[1:] - m2), 0) * 1.4826
    lasting = spots(((both - m2) / (d2 + .12 + .25 * m2) > 6) & (both > .9), 3)
    # sudden: one frame, then calm again
    for i in range(2, n - 2):
        for x, y, w, h in single[i]:
            near = blocks[i - 2:i + 3, y:y + h, x:x + w].reshape(5, -1).mean(1)
            if near[2] > 2.5 * max(near[0], near[1] * .8, near[3] * .8, near[4], .25):
                flags.append(("sudden local change", i - 1, i, "major", f"something at {100 * x // bx}%,{100 * y // by}% appears, vanishes or jumps at frame {i}", [x / bx, y / by, (x + w) / bx, (y + h) / by]))
    # lasting: the same place stays unexplained
    run, start, box = 0, None, None
    act = [bool(s) for s in lasting]
    for i in range(1, n + 1):
        ov = False
        if i < n and act[i]:
            for s in lasting[i]:
                if box is None or (s[0] < box[0] + box[2] and box[0] < s[0] + s[2] and s[1] < box[1] + box[3] and box[1] < s[1] + s[3]):
                    ov = True
                    box = s if box is None else (min(box[0], s[0]), min(box[1], s[1]), max(box[0] + box[2], s[0] + s[2]) - min(box[0], s[0]), max(box[1] + box[3], s[1] + s[3]) - min(box[1], s[1]))
                    break
        if ov:
            start = i if start is None else start
            run += 1
            gap = 0
        elif start is not None:
            gap += 1
            if gap > 3 or i == n:
                if run >= 6 and run >= .5 * (i - gap - start + 1):
                    flags.append(("shape unstable", start, i - gap, "major", f"an area at {100 * box[0] // bx}%,{100 * box[1] // by}% keeps changing in a way that movement does not explain", [box[0] / bx, box[1] / by, min(1, (box[0] + box[2]) / bx), min(1, (box[1] + box[3]) / by)]))
                run, start, box, gap = 0, None, None, 0
    return {"frac": frac, "flags": flags}


def camera_path(gray):
    n = len(gray)
    shift, zoom = np.zeros(n), np.ones(n)
    orb = cv2.ORB_create(600)
    k0, d0 = orb.detectAndCompute(gray[0].astype(np.uint8), None)
    if d0 is None or len(k0) < 20:
        return {"shift": np.full(n, np.nan) if n > 1 else shift, "zoom": zoom}
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    H, W = gray.shape[1:]
    c = np.float32([[0, 0], [W, 0], [0, H], [W, H]])
    for i in range(1, n):
        k, d = orb.detectAndCompute(gray[i].astype(np.uint8), None)
        if d is None:
            shift[i] = np.nan
            continue
        m = [p for p, *q in bf.knnMatch(d0, d, k=2) if q and p.distance < .75 * q[0].distance]
        if len(m) < 15:
            shift[i] = np.nan
            continue
        M, inl = cv2.estimateAffinePartial2D(np.float32([k0[p.queryIdx].pt for p in m]), np.float32([k[p.trainIdx].pt for p in m]), ransacReprojThreshold=1.5)
        if M is None or inl.sum() < 12:
            shift[i] = np.nan
            continue
        shift[i] = float(np.abs(c @ M[:, :2].T + M[:, 2] - c).max())
        zoom[i] = float(np.hypot(M[0, 0], M[1, 0]))
    if np.isnan(shift[1:]).all():
        shift[:] = np.nan
    return {"shift": shift, "zoom": zoom}


def _masks(gray):
    med = np.median(gray[:: max(1, len(gray) // 60)], 0)
    for g in gray:
        m = (cv2.GaussianBlur(np.abs(g - med), (5, 5), 0) > 12).astype(np.uint8)
        yield cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))


def subject_path(gray):
    """Everywhere a MOVING thing has been. Something that stays put (an object that appeared) is not part of it."""
    H, W = gray.shape[1:]
    path, seen = np.zeros((H, W), np.uint8), []
    for m in list(_masks(gray))[:: max(1, len(gray) // 48)]:
        k, lab, st, ce = cv2.connectedComponentsWithStats(m)
        for j in range(1, k):
            if st[j, 4] > .002 * H * W:
                seen.append((ce[j], lab == j))
    for c, m in seen:
        near = [x for x, _ in seen if np.hypot(*(x - c)) < .015 * W]
        if len(near) < .3 * max(1, len(gray[:: max(1, len(gray) // 48)])):      # not parked here for a third of the clip
            path |= m.astype(np.uint8)
    return cv2.dilate(path, np.ones((13, 13), np.uint8))


def subject_series(gray):
    n = len(gray)
    H, W = gray.shape[1:]
    med = np.median(gray[:: max(1, n // 60)], 0)
    area, patch = np.zeros(n), []
    width = np.zeros(n)
    for i, m in enumerate(_masks(gray)):
        k, lab, st, _ = cv2.connectedComponentsWithStats(m)
        if k > 1:
            j = 1 + int(np.argmax(st[1:, 4]))
            rows = (lab == j).sum(1)
            width[i] = float(np.median(rows[rows > 0]))
    for i, g in enumerate(gray):
        m = cv2.GaussianBlur(np.abs(g - med), (5, 5), 0) > 14
        m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3)))
        area[i] = m.sum()
        ys, xs = np.where(m)
        patch.append(cv2.resize(g[ys.min():ys.max() + 1, xs.min():xs.max() + 1], (32, 64)) if len(xs) > .002 * H * W else None)
    sim = np.full(n, np.nan)
    for i in range(12, n):
        if patch[i] is not None and patch[i - 12] is not None and patch[i].std() > 2:
            sim[i] = float(np.corrcoef(patch[i].ravel(), patch[i - 12].ravel())[0, 1])
    flags = []
    typical = np.median(area)
    if typical > .004 * H * W:
        sm = cv2.medianBlur(area.astype(np.float32).reshape(-1, 1), 5).ravel() if n >= 5 else area
        d1 = np.array([0] + [float(np.abs(gray[i] - gray[i - 1]).mean()) for i in range(1, n)])
        dm = np.median(d1[1:])
        sudden = lambda i: d1[max(0, i - 2):i + 3].max() > 2.5 * dm + .4
        dips = [(a, b) for a, b in _runs(sm < .5 * typical) if a > 4 and b < n - 5]
        if any(not (sudden(a) and sudden(b + 1)) and b - a > 3 for a, b in dips):
            # the subject stays near one place, so it became part of the 'background': its shape cannot be followed this way
            return {"area": area, "sim": sim, "flags": [], "reliable": False}
        for a, b in _runs(sm < .35 * typical):
            if a > 4 and b < n - 5 and b - a < n // 3 and sudden(a) and sudden(b + 1):
                flags.append(("subject vanishes", a, b, "critical", f"moving subject area falls to {100 * sm[a:b + 1].min() / typical:.0f}% for {b - a + 1} frames"))
        # body width against its own neighbourhood in time: catches stretching that size-normalised comparison cannot
        pad = np.pad(width, 30, mode="edge")
        local = np.array([np.median(pad[i:i + 61]) for i in range(n)])
        dev = np.where(local > 6, width / np.maximum(local, 1) - 1, 0)
        for a, b in _runs(np.abs(cv2.medianBlur(dev.astype(np.float32).reshape(-1, 1), 5).ravel()) > .28):
            if 3 <= b - a + 1 <= 45 and a > 5 and b < n - 6:
                flags.append(("subject changes shape", a, b, "major", f"body width is {100 * dev[a:b + 1][np.argmax(np.abs(dev[a:b + 1]))]:+.0f}% against the frames around it"))
        m, d = _mad(sim[~np.isnan(sim)]) if (~np.isnan(sim)).sum() > 20 else (1, .05)
        for a, b in _runs(np.nan_to_num(sim, nan=1.0) < min(m - 5 * d, .78)):
            if b - a >= 3:
                flags.append(("subject changes shape", max(0, a - 12), b, "major", f"self-similarity falls to {np.nanmin(sim[a:b + 1]):.2f} (typical {m:.2f})"))
    return {"area": area, "sim": sim, "flags": flags, "reliable": typical > .004 * H * W}


def merge(flags, gap=3):
    out = []
    for f in sorted(flags, key=lambda x: (x[0], x[1])):
        f = list(f) + [None] * (6 - len(f))
        if out and out[-1][0] == f[0] and f[1] - out[-1][2] <= gap:
            out[-1][2] = max(out[-1][2], f[2])
        else:
            out.append(f)
    return [{"class": f[0], "from": int(f[1]), "to": int(f[2]), "severity": f[3], "text": f[4], "region": f[5]} for f in out]


def adherence(fr: np.ndarray, first: np.ndarray | None, last: np.ndarray | None) -> dict:
    """V3: how well the clip's ends match the approved frames, and where settled motion begins/ends."""
    out = {}
    H, W = fr.shape[1:3]
    for name, ref, rng in (("first", first, range(0, min(8, len(fr)))), ("last", last, range(max(0, len(fr) - 8), len(fr)))):
        if ref is None:
            continue
        r = cv2.resize(ref, (W, H), interpolation=cv2.INTER_AREA)
        sc = [similarity(r, fr[i]) for i in rng]
        k = int(np.argmax([s["score"] for s in sc]))
        edge = sc[0] if name == "first" else sc[-1]
        best = sc[k]
        out[name] = {"at_edge": edge, "best": best, "best_frame": list(rng)[k], "outcome": "pass" if edge["score"] > .82 else "warn" if edge["score"] > .6 else "fail"}
    return out


def similarity(a, b) -> dict:
    ga, gb = (cv2.cvtColor(x, cv2.COLOR_BGR2GRAY).astype(np.float32) for x in (a, b))
    c1, c2 = 6.5, 58.5
    ma, mb = cv2.GaussianBlur(ga, (0, 0), 1.5), cv2.GaussianBlur(gb, (0, 0), 1.5)
    va, vb = cv2.GaussianBlur(ga * ga, (0, 0), 1.5) - ma * ma, cv2.GaussianBlur(gb * gb, (0, 0), 1.5) - mb * mb
    cov = cv2.GaussianBlur(ga * gb, (0, 0), 1.5) - ma * mb
    ssim = float((((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))).mean())
    la, lb = (cv2.cvtColor(x, cv2.COLOR_BGR2LAB).astype(np.float32).reshape(-1, 3).mean(0) for x in (a, b))
    return {"score": round(ssim, 3), "colour": round(float(np.linalg.norm(la - lb)), 2)}


# ---------------------------------------------------------------- evidence views
def contact_sheet(fr, idx, cols=6, w=320):
    h = int(fr.shape[1] * w / fr.shape[2])
    rows = (len(idx) + cols - 1) // cols
    sheet = np.full((rows * (h + 4), cols * (w + 4), 3), 24, np.uint8)
    for k, i in enumerate(idx):
        t = cv2.resize(fr[i], (w, h), interpolation=cv2.INTER_AREA)
        cv2.rectangle(t, (0, 0), (52, 20), (0, 0, 0), -1)
        cv2.putText(t, str(i), (4, 15), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1, cv2.LINE_AA)
        y, x = (k // cols) * (h + 4), (k % cols) * (w + 4)
        sheet[y:y + h, x:x + w] = t
    return sheet


def pick_frames(t, n=18):
    d = np.nan_to_num(t["series"]["diff"]) + .15
    c = np.cumsum(d) / d.sum()
    idx = {0, t["n"] - 1} | {int(np.searchsorted(c, q)) for q in np.linspace(0, 1, n - 2, endpoint=False)[1:]}
    for f in t["flags"]:
        if f["to"] - f["from"] < t["n"] - 2:
            idx |= {max(0, f["from"] - 1), f["from"], (f["from"] + f["to"]) // 2, min(t["n"] - 1, f["to"] + 1)}
    return sorted(i for i in idx if 0 <= i < t["n"])[:30]


def slit_scans(fr, lines=(.3, .5, .7), height=200):
    out = []
    for q in lines:
        s = fr[:, int(q * (fr.shape[1] - 1)), :, :]
        out.append(cv2.resize(s, (fr.shape[2], height), interpolation=cv2.INTER_NEAREST))
        out.append(np.full((6, fr.shape[2], 3), 90, np.uint8))
    v = fr[:, :, int(.5 * (fr.shape[2] - 1)), :]
    out.append(cv2.resize(np.transpose(v, (0, 1, 2)), (fr.shape[2], height), interpolation=cv2.INTER_NEAREST))
    return np.vstack(out)


def heatmap(fr):
    g = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in fr[:: max(1, len(fr) // 80)]]).astype(np.float32)
    s = g.std(0)
    s = np.clip(s / max(8, np.percentile(s, 99)) * 255, 0, 255).astype(np.uint8)
    return cv2.addWeighted(cv2.applyColorMap(s, cv2.COLORMAP_INFERNO), .75, fr[len(fr) // 2], .25, 0)


def usable_ranges(t, min_len=12):
    bad = np.zeros(t["n"], bool)
    for f in t["flags"]:
        if f["class"] in ("static area changed",) or f["severity"] not in ("critical", "major"):
            continue
        if f["class"] == "camera moved":
            continue
        bad[max(0, f["from"] - 1):f["to"] + 2] = True
    return [(a, b) for a, b in _runs(~bad) if b - a + 1 >= min_len]


def inspect(path: Path, probe: dict, expect: dict, outdir: Path, first=None, last=None) -> dict:
    with ThreadPoolExecutor(1) as pool:
        tech = pool.submit(technical, path, probe, expect)
        fr = load_frames(path)
        tech = tech.result()
    if len(fr) < 2:
        return {"version": VERSION, "technical": tech, "outcome": "fail", "flags": [], "views": {}}
    t = temporal(fr, expect.get("camera", "locked"))
    ad = adherence(fr, first, last)
    outdir.mkdir(parents=True, exist_ok=True)
    views = {}
    for name, img in (("contact", contact_sheet(fr, pick_frames(t))), ("slits", slit_scans(fr)), ("change", heatmap(fr))):
        p = outdir / f"{name}.png"
        cv2.imwrite(str(p), img)
        views[name] = str(p)
    for k, f in enumerate([f for f in t["flags"] if f["to"] - f["from"] < len(fr) // 2][:6]):
        a, b = max(0, f["from"] - 2), min(len(fr) - 1, f["to"] + 2)
        idx = list(range(a, b + 1))[:: max(1, (b - a + 1) // 12)]
        p = outdir / f"flag{k}.png"
        cv2.imwrite(str(p), contact_sheet(fr, idx))
        views[f"flag{k}"] = str(p)
    sev = [f["severity"] for f in t["flags"]]
    items = [i[1] for i in tech["items"]]
    outcome = "fail" if "critical" in sev or "fail" in items or any(v["outcome"] == "fail" for v in ad.values()) else "warn" if sev or "warn" in items or any(v["outcome"] == "warn" for v in ad.values()) else "pass"
    return {"version": VERSION, "outcome": outcome, "technical": tech, "flags": t["flags"], "adherence": ad, "frames": t["n"], "usable": usable_ranges(t), "views": views,
            "series": {k: [None if np.isnan(x) else round(float(x), 3) for x in v] for k, v in t["series"].items()},
            "examined": ["every frame, downscaled to 480 wide", "first and last frames against the approved frames" if ad else "no approved frames to compare"],
            "not_examined": ([] if t["shape_ok"] else ["changes in the subject's shape (it could not be separated from the background)"]) + ["performance and feeling (you)", "identity (needs approved references)", "full-resolution detail beyond the evidence views"]}


def describe(rep: dict) -> str:
    return json.dumps({k: rep[k] for k in ("outcome", "flags", "usable", "frames")}, indent=1)
