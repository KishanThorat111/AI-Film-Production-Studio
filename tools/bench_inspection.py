"""Planted defects in rendered footage: how many does the clip inspection find, and how often does it cry wolf?

    python tools/bench_inspection.py <folder with frame sequences> <output.json> [seconds]

Every sequence folder (0000.png, 0001.png, ...) is used clean and with each defect planted at a known place. The plan is fixed and hashed before
anything is measured. This is footage with real perspective, light and camera movement, but it is rendered, not generated: the numbers say what the
measurements can do, not how they will do on Gemini clips. Those need the user's blind verdicts (Agents page).
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.core import assets            # noqa: E402
from studio.inspect import video as iv    # noqa: E402

W, H, FPS = 960, 540, 24


def load(folder: Path, n):
    fr = [cv2.resize(cv2.imread(str(p)), (W, H), interpolation=cv2.INTER_AREA) for p in sorted(folder.glob("[0-9][0-9][0-9][0-9].png"))[:n]]
    return np.stack(fr)


def region(fr, r, size=.22):
    """A place with something to see: the most textured of some random boxes."""
    g = cv2.cvtColor(fr[len(fr) // 2], cv2.COLOR_BGR2GRAY)
    best = None
    for _ in range(40):
        w, h = int(W * size * r.uniform(.6, 1)), int(H * size * r.uniform(.8, 1.4))
        x, y = r.integers(10, W - w - 10), r.integers(10, H - h - 10)
        s = g[y:y + h, x:x + w].std()
        if best is None or s > best[0]:
            best = (s, x, y, w, h)
    return best[1:]


def soft(w, h):
    m = np.zeros((h, w), np.float32)
    cv2.ellipse(m, (w // 2, h // 2), (int(w * .42), int(h * .42)), 0, 0, 360, 1, -1)
    return cv2.GaussianBlur(m, (0, 0), max(2, w / 18))[..., None]


def vanish(fr, r):
    f, (x, y, w, h), n = fr.copy(), region(fr, r), len(fr)
    a, m = n // 2, soft(w, h)
    for i in range(a, n):
        bg = cv2.GaussianBlur(cv2.inpaint(f[i], cv2.copyMakeBorder((m[..., 0] > .1).astype(np.uint8) * 255, y, H - y - h, x, W - x - w, cv2.BORDER_CONSTANT), 5, cv2.INPAINT_TELEA), (0, 0), 1)[y:y + h, x:x + w]
        f[i][y:y + h, x:x + w] = (f[i][y:y + h, x:x + w] * (1 - m) + bg * m).astype(np.uint8)
    return f, (a - 2, a + 4)


def appear(fr, r):
    f, n = fr.copy(), len(fr)
    (x, y, w, h), a = region(fr, r, .16), n // 2
    src = region(fr, np.random.default_rng(int(r.integers(1e6))), .16)
    m = soft(w, h)
    for i in range(a, n):
        p = cv2.resize(f[i][src[1]:src[1] + src[3], src[0]:src[0] + src[2]], (w, h))
        p = np.clip(p.astype(np.float32) * 1.5 + 35, 0, 255)
        f[i][y:y + h, x:x + w] = (f[i][y:y + h, x:x + w] * (1 - m) + p * m).astype(np.uint8)
    return f, (a - 2, a + 4)


def morph(fr, r):
    f, n = fr.copy(), len(fr)
    x, y, w, h = region(fr, r, .26)
    a, b = n // 3, 2 * n // 3
    gy, gx = np.mgrid[0:h, 0:w].astype(np.float32)
    env = soft(w, h)[..., 0]
    for i in range(a, b):
        k = np.sin((i - a) / (b - a) * np.pi) * w * .12
        mx = gx + env * k * np.sin(gy / h * 6.3 + i * .35)
        my = gy + env * k * np.cos(gx / w * 6.3 + i * .27)
        f[i][y:y + h, x:x + w] = cv2.remap(f[i][y:y + h, x:x + w], mx.astype(np.float32), my.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return f, (a, b)


def flicker(fr, r):
    f, n = fr.astype(np.float32), len(fr)
    a, b = n // 3, 2 * n // 3
    for i in range(a, b):
        f[i] *= 1 + (.16 if i % 2 else -.16)
    return np.clip(f, 0, 255).astype(np.uint8), (a, b)


def freeze(fr, r):
    f, a = fr.copy(), len(fr) // 2
    f[a:a + 12] = f[a]
    return f, (a, a + 12)


def cutjump(fr, r):
    f, a = fr.copy(), len(fr) // 2
    f[a:] = f[a:, :, ::-1]
    return f, (a - 1, a + 1)


def jerk(fr, r):
    f, a = fr.copy(), len(fr) // 2
    for i, d in enumerate((14, -18, 22, -12)):
        f[a + i] = cv2.warpAffine(f[a + i], np.float32([[1, 0, d], [0, 1, d * .6]]), (W, H), borderMode=cv2.BORDER_REFLECT)
    return f, (a, a + 4)


def colour(fr, r):
    f, n = fr.astype(np.float32), len(fr)
    a = n // 2
    for i in range(a, n):
        f[i] *= np.array([.8, 1.0, 1.22], np.float32)
    return np.clip(f, 0, 255).astype(np.uint8), (a - 1, a + 2)


def softness(fr, r):
    f, n = fr.copy(), len(fr)
    a, b = n // 2, n // 2 + 16
    for i in range(a, min(n, b)):
        f[i] = cv2.GaussianBlur(f[i], (0, 0), 5)
    return f, (a, b)


DEFECTS = {"object vanishes": vanish, "object appears": appear, "shape melts and returns": morph, "brightness pumps": flicker, "picture freezes": freeze, "hidden cut": cutjump, "camera jerk": jerk, "colour shifts": colour, "goes soft": softness}


def encode(fr, p):
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "21", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(p)], stdin=subprocess.PIPE)
    for f in fr:
        ff.stdin.write(f.tobytes())
    ff.stdin.close()
    ff.wait()


def moving(fr):
    g = [cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (240, 135)) for f in (fr[0], fr[len(fr) // 2], fr[-1])]
    return "moving" if max(np.abs(g[0].astype(int) - g[1]).mean(), np.abs(g[1].astype(int) - g[2]).mean()) > 2.2 else "locked"


def main(src, out, seconds=4.0):
    seqs = [d for d in sorted(Path(src).iterdir()) if d.is_dir() and len(list(d.glob("[0-9][0-9][0-9][0-9].png"))) >= 48]
    n = int(seconds * FPS)
    plan = {"sequences": [d.name for d in seqs], "defects": list(DEFECTS), "frames": n, "size": [W, H], "rule": "found = a critical or major flag that touches the planted frames (4 frames tolerance); false alarm = a critical or major flag on a clean clip", "seed": 7}
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    rows, t0 = [], time.time()
    with tempfile.TemporaryDirectory() as td:
        for d in seqs:
            fr = load(d, n)
            cam = moving(fr)
            for name, fn in [("clean", None)] + list(DEFECTS.items()):
                r = np.random.default_rng(int(hashlib.sha256(f"{d.name}{name}7".encode()).hexdigest()[:8], 16))
                f, at = fn(fr, r) if fn else (fr, None)
                p = Path(td) / f"{d.name}_{len(rows)}.mp4"
                encode(f, p)
                rep = iv.inspect(p, assets.probe(p), {"aspect": 16 / 9, "duration": (1, 20), "camera": cam, "fps": FPS}, Path(td) / "v")
                flags = [{k: x[k] for k in ("class", "severity", "from", "to")} for x in rep["flags"] if x["severity"] in ("critical", "major")]
                hit = [x for x in flags if at and x["to"] >= at[0] - 4 and x["from"] <= at[1] + 4]
                rows.append({"sequence": d.name, "camera": cam, "planted": name, "at": at, "found": bool(hit) if at else None, "classes": sorted({x["class"] for x in hit}), "flags_elsewhere": len(flags) - len(hit), "false_alarm": bool(flags) if not at else None})
                print(f"{d.name:8} {cam:7} {name:24} {'FOUND ' + ', '.join(rows[-1]['classes']) if rows[-1]['found'] else 'missed' if at else 'false alarm: ' + ', '.join(sorted({x['class'] for x in flags})) if flags else 'clean'}", flush=True)
    by = {k: [r for r in rows if r["planted"] == k] for k in DEFECTS}
    res = {"plan": plan, "plan_hash": plan_hash, "made": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - t0), "recall": {k: f"{sum(r['found'] for r in v)}/{len(v)}" for k, v in by.items()}, "recall_total": f"{sum(r['found'] for v in by.values() for r in v)}/{sum(len(v) for v in by.values())}",
           "false_alarms": f"{sum(bool(r['false_alarm']) for r in rows if r['planted'] == 'clean')}/{len(seqs)}", "rows": rows, "limits": "rendered footage, planted defects; not generated footage"}
    Path(out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("recall", "recall_total", "false_alarms")}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 4.0)
