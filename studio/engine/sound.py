"""Sound mixer: 48 kHz float, sample-addressed. Distance, walls, rooms, moving sources, ducking, planned silences, stems.

Buses live in files and are worked through in blocks, so a 15-minute film needs hardly more memory than a short scene.
Automation keyframes count film frames from the start of the sound.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, fftconvolve, sosfilt, sosfilt_zi

from ..inspect.audio import decode
from .render import ev

SR = 48000
BUSES = ("narration", "dialogue", "foley", "hard_fx", "ambience", "music", "reverb")
VOICE = ("narration", "dialogue")
SEND = {"narration": 0.0, "dialogue": .10, "foley": .12, "hard_fx": .16, "ambience": 0.0, "music": .04, "reverb": 0}
DUCK = [{"key": "voice", "target": "ambience", "db": -3.5}, {"key": "voice", "target": "music", "db": -7.0}]
BLOCK = SR * 20
CTRL = 480                       # 10 ms control rate, interpolated per sample


def _lp(x, hz):
    return sosfilt(butter(2, min(hz, SR * .45), "low", fs=SR, output="sos"), x, axis=0) if hz < 19000 else x


def _hp(x, hz):
    return sosfilt(butter(2, hz, "high", fs=SR, output="sos"), x, axis=0)


def room(seconds=.5, damp=3500, seed=1, pre=.008):
    """A synthetic room response: decorrelated, darkening tail. Made here, so it is free of rights."""
    r = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    ir = r.normal(0, 1, (n, 2)) * np.exp(-6.9 * t / seconds)[:, None]
    ir = _lp(ir, damp)
    for k in range(7):
        i = int((pre + .004 + .006 * k * (1 + .4 * r.random())) * SR)
        if i < n:
            ir[i] += r.choice([-1, 1], 2) * (2.6 / (k + 1.5))
    ir[:int(pre * SR)] = 0
    return (ir / np.sqrt((ir ** 2).sum(0)))[:, :]


def curve(k, n, default, spf):
    """Automation per sample. spf: samples per film frame, because keyframes count frames."""
    if k is None or isinstance(k, (int, float)):
        return np.full(n, default if k is None else float(k), np.float32)
    g = np.arange(0, n + CTRL, CTRL)
    return np.interp(np.arange(n), g, [ev(k, i / spf) for i in g]).astype(np.float32)


def moving(k):
    return isinstance(k, list) and len({p["v"] for p in k}) > 1


def _lp_hz(occ, dist):
    hz = 24000.0
    if occ > 0:
        hz = min(hz, 6500 * (1 - occ) ** 2 + 260)
    if dist > 0:
        hz = min(hz, 16000 / (1 + 1.8 * dist))
    return hz


def _travel(st, occ, dist):
    """A source that moves behind a wall or away: the filter follows in 10 ms steps, its memory carries over, so nothing clicks."""
    out, zi = np.empty_like(st), None
    for i in range(0, len(st), CTRL):
        hz = min(_lp_hz(float(occ[i]), float(dist[i])), SR * .45)
        sos = butter(2, hz, "low", fs=SR, output="sos")
        if zi is None:
            zi = np.repeat((sosfilt_zi(sos) * 0)[:, :, None], st.shape[1], 2)
        out[i:i + CTRL], zi = sosfilt(sos, st[i:i + CTRL], axis=0, zi=zi)
    return out * ((1 - .45 * occ) / (1 + 1.1 * dist))[:, None]


class _Store:
    """Arrays in files; small ones stay in memory."""

    def __init__(self, n):
        self.n, self.dir, self.k = n, None, 0

    def make(self):
        if self.n < SR * 90:
            return np.zeros((self.n, 2), np.float32)
        if self.dir is None:
            from ..core.db import data_dir
            base = data_dir() / "tmp"
            base.mkdir(parents=True, exist_ok=True)
            self.dir = tempfile.TemporaryDirectory(prefix="mix_", dir=base, ignore_cleanup_errors=True)
        self.k += 1
        return np.memmap(Path(self.dir.name) / f"{self.k}.f32", np.float32, "w+", shape=(self.n, 2))


def blocks(n, size=BLOCK):
    for a in range(0, n, size):
        yield a, min(n, a + size)


def _silent(x):
    return not any(np.abs(x[a:b]).max() > 0 for a, b in blocks(len(x)))


def _source(a, resolve, spf, n):
    src = decode(resolve(a["asset"]))
    i0 = a.get("src_in", 0)
    L = len(src) - i0
    if a.get("loop") and a.get("dur_samples", 0) > L > 0:                # the same air for longer, joined with a crossfade
        need, x = a["dur_samples"], src[i0:]
        fade = min(SR, L // 4)
        w = np.linspace(0, 1, fade)[:, None]
        parts, have = [x[:L - fade]], L - fade
        while have < need:
            parts.append(np.concatenate([x[L - fade:] * np.sqrt(1 - w) + x[:fade] * np.sqrt(w), x[fade:L - fade]]))
            have += L - fade
        src, i0 = np.concatenate(parts), 0
        L = len(src)
    L = min(a.get("dur_samples", L), L)
    return src, i0, L


def mix(tl: dict, resolve, rooms: dict[str, dict] | None = None) -> dict:
    n = int(round(tl["frames"] * tl["fps"][1] / tl["fps"][0] * SR))
    spf = SR * tl["fps"][1] / tl["fps"][0]
    rooms = {**tl.get("rooms", {}), **(rooms or {})}
    store = _Store(n)
    bus = {b: store.make() for b in BUSES}
    rms = {"room": room(**rooms.get("room", {}))}
    for k, v in rooms.items():
        rms[k] = room(**v)
    send = {}
    for a in tl["audio"]:
        src, i0, L = _source(a, resolve, spf, n)
        if L <= 0:
            continue
        mono = src.shape[1] == 1 or a["bus"] in VOICE + ("foley", "hard_fx")
        st = np.repeat(src.mean(1)[i0:i0 + L, None], 2, 1) if mono else src[i0:i0 + L, :2]
        st = st.astype(np.float32).copy()
        del src
        fi, fo = a.get("fade_in", 16 if a["bus"] in ("foley", "hard_fx") else 96), a.get("fade_out", 96)
        if fi:
            st[:fi] *= np.linspace(0, 1, min(fi, L))[:, None][: len(st[:fi])]
        if fo:
            st[-fo:] *= np.linspace(1, 0, min(fo, L))[:, None][-len(st[-fo:]):]
        mv = moving(a.get("occlusion")) or moving(a.get("distance"))
        occ, dist = ev(a.get("occlusion"), 0, 0.0), ev(a.get("distance"), 0, 0.0)
        if mv:
            o, d = curve(a.get("occlusion"), L, 0.0, spf), curve(a.get("distance"), L, 0.0, spf)
            st = _travel(st, o, d).astype(np.float32)
            occ, dist = float(o.mean()), float(d.mean())
        else:
            if occ > 0:                       # through a wall or ceiling: dull and quieter
                st = _lp(st, 6500 * (1 - occ) ** 2 + 260).astype(np.float32) * (1 - .45 * occ)
            if dist > 0:                      # air and level
                st = _lp(st, 16000 / (1 + 1.8 * dist)).astype(np.float32) / (1 + 1.1 * dist)
        for e in a.get("effects", []):
            if e["op"] == "phone":
                st = (_lp(_hp(st, 320), 3300) * 1.25).astype(np.float32)
            elif e["op"] == "highpass":
                st = _hp(st, e["hz"]).astype(np.float32)
            elif e["op"] == "lowpass":
                st = _lp(st, e["hz"]).astype(np.float32)
            elif e["op"] == "room":
                pass                          # chooses the reverb send below
            else:
                raise ValueError(f"sound effect '{e['op']}' is not built")
        p, q = a["at_sample"], min(n, a["at_sample"] + L)
        if p >= n or q <= 0:
            continue
        g = 10 ** (curve(a.get("gain_db"), L, 0.0, spf) / 20)
        th = (curve(a.get("pan"), L, 0.0, spf) + 1) * np.pi / 4
        st[:, 0] *= g * np.cos(th) * 1.4142
        st[:, 1] *= g * np.sin(th) * 1.4142
        seg = st[max(0, -p):q - p]
        bus[a["bus"]][max(0, p):q] += seg
        amt = SEND[a["bus"]] + .12 * min(dist, 3) + .06 * occ
        rn = next((e["name"] for e in a.get("effects", []) if e.get("op") == "room"), "room")
        rn = rn if rn in rms else "room"
        if amt:
            if rn not in send:
                send[rn] = store.make()
            send[rn][max(0, p):q] += seg * amt
        del st, seg
    for k, s in send.items():
        _reverb(s, rms[k], bus["reverb"])
    send.clear()
    _duck(bus, n, tl.get("ducking", DUCK))
    _silences(bus, n, tl)
    return {"bus": bus, "n": n, "store": store}


def _reverb(s, ir, out):
    n, m = len(s), len(ir)
    for a, b in blocks(n):
        x = np.asarray(s[a:b])
        if not np.abs(x).max() > 0:
            continue
        e = min(n, b + m - 1)
        for c in range(2):
            y = fftconvolve(x[:, c], ir[:, c])[: e - a]
            out[a:e, c] += y.astype(np.float32) * .6


def _duck(bus, n, rules):
    """What gives way to what. The key is the smoothed level of the named buses."""
    env_sos, smooth = butter(1, 10, "low", fs=SR, output="sos"), butter(1, 1.6, "low", fs=SR, output="sos")
    for key in sorted({r["key"] for r in rules}):
        src = VOICE if key == "voice" else (key,)
        vals, z = [], np.zeros((1, 2))
        for a, b in blocks(n):                                   # first pass: how loud the key usually is
            x = sum(np.asarray(bus[s][a:b]) for s in src).mean(1)
            e, z = sosfilt(env_sos, x ** 2, zi=z)
            e = np.sqrt(np.maximum(e, 0))[::CTRL]
            vals.append(e[e > 1e-4])
        vals = np.concatenate(vals) if vals else np.zeros(0)
        if not len(vals):
            continue
        ref, z, z2 = float(np.percentile(vals, 80)), np.zeros((1, 2)), np.zeros((1, 2))
        for a, b in blocks(n):
            x = sum(np.asarray(bus[s][a:b]) for s in src).mean(1)
            e, z = sosfilt(env_sos, x ** 2, zi=z)
            k, z2 = sosfilt(smooth, np.clip(np.sqrt(np.maximum(e, 0)) / ref, 0, 1), zi=z2)
            for r in rules:
                if r["key"] == key:
                    bus[r["target"]][a:b] *= (10 ** (r["db"] * np.clip(k, 0, 1) / 20)).astype(np.float32)[:, None]


def _silences(bus, n, tl):
    """Planned silences are enforced, with short musical edges."""
    fps = tl["fps"][0] / tl["fps"][1]
    ed = int(.02 * SR)
    for s in tl.get("silences", []):
        a, b = int(s["from_frame"] / fps * SR), int((s["from_frame"] + s["frames"]) / fps * SR)
        a0, b1 = max(0, a - ed), min(n, b + ed)
        w = np.full(b1 - a0, 10 ** (-80 / 20), np.float32)
        w[:a - a0] = np.linspace(1, 0, a - a0)
        w[b - a0:] = np.linspace(0, 1, ed)[: b1 - b]
        for x in bus.values():
            x[a0:b1] *= w[:, None]


# ------------------------------------------------------------ level
_PRE = np.array([[1.53512485958697, -2.69169618940638, 1.19839281085285, 1.0, -1.69065929318241, 0.73248077421585], [1.0, -2.0, 1.0, 1.0, -1.99004745483398, 0.99007225036621]])


class Loudness:
    """BS.1770 integrated loudness, fed in blocks (48 kHz stereo)."""

    def __init__(self):
        self.z, self.e, self.rest = np.zeros((2, 2, 2)), [], np.zeros((0, 2))

    def feed(self, x):
        y, self.z = sosfilt(_PRE, np.asarray(x, np.float64), axis=0, zi=self.z)
        y = np.concatenate([self.rest, y])
        k = len(y) // 4800
        if k:
            self.e += list((y[:k * 4800].reshape(k, 4800, 2) ** 2).mean(1).sum(1))
        self.rest = y[k * 4800:]

    def value(self) -> float:
        e = np.array(self.e)
        if len(e) < 4:
            return float("-inf")
        b = np.convolve(e, np.ones(4) / 4, "valid")
        with np.errstate(divide="ignore"):
            l = -0.691 + 10 * np.log10(b)
        g = b[l > -70]
        if not len(g):
            return float("-inf")
        rel = -0.691 + 10 * np.log10(g.mean()) - 10
        g = b[(l > -70) & (l > rel)]
        return float(-0.691 + 10 * np.log10(g.mean())) if len(g) else float("-inf")


def loudness(x) -> float:
    m = Loudness()
    for a, b in blocks(len(x)):
        m.feed(x[a:b])
    return m.value()


def master(mixed: dict, target=-16.0, ceiling_db=-2.2) -> tuple[np.ndarray, dict]:
    """Level and limiter. The level is found again after limiting, so a peaky film still reaches its target where that is possible."""
    from scipy.ndimage import minimum_filter1d, uniform_filter1d
    n, bus = mixed["n"], mixed["bus"]
    live = [x for x in bus.values() if not _silent(x)]
    out = mixed["store"].make() if "store" in mixed else np.zeros((n, 2), np.float32)
    total = lambda a, b: sum((np.asarray(x[a:b]) for x in live), np.zeros((b - a, 2), np.float32))
    m = Loudness()
    for a, b in blocks(n):
        m.feed(total(a, b))
    l0 = m.value()
    c, look = 10 ** (ceiling_db / 20), int(.004 * SR)
    gain = 10 ** ((target - l0) / 20) if np.isfinite(l0) else 1.0
    w = 2 * look + 1
    for attempt in range(3):
        lm, least, work = Loudness(), 1.0, 0
        for a, b in blocks(n):
            p, q = max(0, a - 2 * w), min(n, b + 2 * w)
            y = total(p, q) * gain
            need = np.minimum(1.0, c / np.maximum(np.abs(y).max(1), 1e-9))
            gr = uniform_filter1d(minimum_filter1d(need, w), w)[a - p:b - p]
            y = np.clip(y[a - p:b - p] * gr[:, None], -c, c)
            least = min(least, float(gr.min()))
            work += int((gr < .999).sum())
            out[a:b] = y
            lm.feed(y)
        l1 = lm.value()
        # chase the target only while the limiter works rarely; otherwise dynamics matter more than the number
        if not np.isfinite(l1) or abs(target - l1) < .3 or work > n * .08 or l1 > target or 20 * np.log10(least + 1e-9) < -8:
            break
        gain *= 10 ** (min(target - l1, 2.0) / 20)
    r = lambda v, k=1: round(float(v), k) if np.isfinite(v) else None
    return out, {"before_lufs": r(l0), "lufs": r(l1), "target": target, "gain_db": r(20 * np.log10(gain)), "limited_db": r(20 * np.log10(least + 1e-9)), "limited_share": round(work / max(1, n), 4)}


def true_peak_db(x) -> float:
    """4x oversampled peak over the whole signal."""
    from scipy.signal import resample_poly
    pk = 0.0
    for a, b in blocks(len(x), SR * 10):
        p = max(0, a - 64)
        seg = np.asarray(x[p:min(len(x), b + 64)], np.float64)
        pk = max(pk, float(np.abs(resample_poly(seg, 4, 1, axis=0)).max()) if len(seg) else 0.0)
    return 20 * np.log10(pk + 1e-9)


def write(y, path: Path):
    with sf.SoundFile(str(path), "w", SR, 2, "PCM_24") as f:
        for a, b in blocks(len(y)):
            f.write(np.asarray(y[a:b]))
    return path


def stems(mixed, folder: Path, gain=1.0):
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for b, x in mixed["bus"].items():
        if not _silent(x):
            p = folder / f"stem_{b}.wav"
            with sf.SoundFile(str(p), "w", SR, 2, "PCM_24") as f:
                for i, j in blocks(len(x)):
                    f.write(np.asarray(x[i:j]) * gain)
            out[b] = str(p)
    return out
