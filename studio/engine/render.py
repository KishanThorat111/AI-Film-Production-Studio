"""Timeline document -> picture, sound, master, delivery. Picture in frames, sound in samples; seconds are never stored."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import av
import cv2
import jsonschema
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..core.db import ROOT, sha
from .compositor import ENGINE_VERSION, OPS, Compositor, to_linear

SCHEMA = json.loads((ROOT / "studio/schema/timeline.schema.json").read_text(encoding="utf-8"))
BLEND = {"normal": 0, "add": 1, "screen": 2, "multiply": 3}
TRANSITIONS = {"cut", "dissolve", "dip", "wipe", "flash"}
OVERLAP = {"dissolve", "wipe"}                    # these need both pictures at the same time
TEXT_OPS = {"timestamp", "caption"}
SOUND_OPS = {"room", "phone", "highpass", "lowpass"}
SEG = 96


class TimelineProblem(Exception):
    pass


# ------------------------------------------------------------------ keyframes
def ev(k, f, default=None):
    if k is None:
        return default
    if isinstance(k, (int, float)):
        return float(k)
    if k and not isinstance(k[0], dict):
        return [float(x) for x in k]
    if f <= k[0]["f"]:
        return k[0]["v"]
    for a, b in zip(k, k[1:]):
        if a["f"] <= f <= b["f"]:
            t = (f - a["f"]) / max(1, b["f"] - a["f"])
            e = b.get("ease", "linear")
            t = 0.0 if e == "hold" and f < b["f"] else t * t * (3 - 2 * t) if e == "smooth" else t * t if e == "in" else 1 - (1 - t) ** 2 if e == "out" else _bez(t, b["bz"]) if e == "bezier" and "bz" in b else t
            va, vb = a["v"], b["v"]
            return [x + (y - x) * t for x, y in zip(va, vb)] if isinstance(va, list) else va + (vb - va) * t
    return k[-1]["v"]


def _bez(x, bz):
    x1, y1, x2, y2 = bz
    lo, hi = 0.0, 1.0
    for _ in range(24):
        t = (lo + hi) / 2
        (lo, hi) = (t, hi) if 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3 < x else (lo, t)
    return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3


# ------------------------------------------------------------------ lint
def validate(tl):
    e = list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(tl))
    if e:
        raise TimelineProblem("; ".join(f"{'/'.join(map(str, x.path))}: {x.message}" for x in e[:4]))


def lint(tl, resolve, approved=lambda asset: True) -> list[dict]:
    """Problems found before any rendering. 'resolve' maps an asset id to a path or None."""
    out = []
    add = lambda sev, where, text: out.append({"severity": sev, "where": where, "text": text})
    for tr in tl["video"]:
        clips = sorted(tr["clips"], key=lambda c: c["at"])
        for a, b in zip(clips, clips[1:]):
            ov = a["at"] + a["dur"] - b["at"]
            ti = b.get("transition_in", {})
            want = ti.get("dur", 0) if ti.get("type") in OVERLAP else 0
            if ov > want:
                add("critical", b["id"], f"overlaps {a['id']} by {ov} frames")
            elif ov < want and tr["track"] == 0:
                add("major", b["id"], f"its {ti['type']} has only {max(0, ov)} of {want} frames to work with")
            elif ov < 0 and tr["track"] == 0:
                add("major", b["id"], f"{-ov} empty frames before it")
        for c in clips:
            if c["at"] + c["dur"] > tl["frames"]:
                add("critical", c["id"], "runs past the end")
            for fx in c.get("effects", []):
                for ref in ([fx["mask"]] if fx.get("mask") else []) + ([fx["params"]["aux"]] if isinstance(fx["params"].get("aux"), str) else []):
                    if resolve(ref) is None or not Path(resolve(ref)).exists():
                        add("critical", c["id"], f"{fx['op']}: the picture it needs is missing")
                if fx["op"] in ("lens_blur", "depth_parallax") and "aux" not in fx["params"]:
                    add("critical", c["id"], f"{fx['op']} needs a depth picture")
                if fx["op"] in OPS:
                    bad = set(fx["params"]) - {n for n, _ in OPS[fx["op"]][0]} - {"aux"}
                    if bad:
                        add("critical", c["id"], f"{fx['op']}: unknown parameters {sorted(bad)}")
            if c.get("card") and c["kind"] != "solid":
                add("critical", c["id"], "a card is text on black, not on another picture")
            if c.get("matte") and (resolve(c["matte"]) is None or not Path(resolve(c["matte"])).exists()):
                add("critical", c["id"], "its matte is missing")
            if (c.get("speed") is not None or c.get("hold_last")) and c["kind"] != "video":
                add("major", c["id"], "speed and hold only mean something for a moving picture")
            if c["kind"] in ("video", "image"):
                p = resolve(c.get("asset"))
                if p is None or not Path(p).exists():
                    add("critical", c["id"], "its file is missing")
                    continue
                if not approved(c["asset"]):
                    add("major", c["id"], "uses an asset without a live approval")
                if c["kind"] == "video":
                    n = count_frames(p)
                    last = max(source_frame(c, f, p, tl["fps"]) for f in ((0, c["dur"] - 1) if not isinstance(c.get("speed"), list) else range(c["dur"])))
                    if last >= n and not c.get("hold_last"):
                        add("critical", c["id"], f"needs frame {last}, the clip has {n}")
                    fps = source_fps(p)
                    film = tl["fps"][0] / tl["fps"][1]
                    if abs(fps - film) > .01:
                        add("info", c["id"], f"its source has {fps:.3f} frames per second; it is shown at its true speed on {film:.3f}")
    moves = []
    for c in sorted([c for tr in tl["video"] if tr["track"] == 0 for c in tr["clips"]], key=lambda c: c["at"]):
        s = c.get("transform", {}).get("scale")
        moves.append("zoom" if isinstance(s, list) and s and isinstance(s[0], dict) and abs(s[-1]["v"] - s[0]["v"]) > .01 else "other")
        if moves[-3:] == ["zoom"] * 3:
            add("moderate", c["id"], "third slow zoom in a row")
    for a in tl["audio"]:
        if a.get("asset") and (resolve(a["asset"]) is None or not Path(resolve(a["asset"])).exists()):
            add("critical", a["id"], "its sound file is missing")
    return out


_fps, _counts = {}, {}


def source_fps(p) -> float:
    k = (str(p), Path(p).stat().st_mtime_ns)
    if k not in _fps:
        with av.open(str(p)) as c:
            _fps[k] = float(c.streams.video[0].average_rate)
    return _fps[k]


def source_frame(c, f, path, fps) -> int:
    """Which source frame belongs to film frame f of this clip: true speed whatever the source rate, times the retime curve."""
    step = source_fps(path) / (fps[0] / fps[1])
    if abs(step - 1) < .0005:
        step = 1.0
    sp = c.get("speed")
    if sp is None or isinstance(sp, (int, float)):
        t = f * float(1 if sp is None else sp)
    else:                                         # keyframes: distance is the sum of the speeds so far
        t = sum(ev(sp, k) for k in range(f))
    return max(0, c.get("src_in", 0) + int(t * step + 1e-6))


def count_frames(p) -> int:
    k = (str(p), Path(p).stat().st_mtime_ns)
    if k not in _counts:
        with av.open(str(p)) as c:
            _counts[k] = sum(1 for _ in c.decode(video=0))
    return _counts[k]


# ------------------------------------------------------------------ sources
class VideoSource:
    """Frame-exact: seek by timestamp to the keyframe before, then count forward."""

    def __init__(self, path):
        self.c = av.open(str(path))
        self.s = self.c.streams.video[0]
        self.s.thread_type = "AUTO"
        self.it, self.pos, self.last = None, -1, None
        self.fps = float(self.s.average_rate)

    def get(self, n):
        if n == self.pos:
            return self.last
        if self.it is None or n < self.pos or n > self.pos + 48:
            pts = int(round(n / self.fps / self.s.time_base)) + (self.s.start_time or 0)
            self.c.seek(max(0, pts), stream=self.s, backward=True)
            self.it, self.pos = self.c.decode(self.s), None
        for f in self.it:
            i = int(round(float((f.pts - (self.s.start_time or 0)) * self.s.time_base) * self.fps))
            if i >= n:
                self.pos = n
                self.last = f.to_ndarray(format="rgb48le") if f.format.components[0].bits > 8 else f.to_ndarray(format="rgb24")
                return self.last
        raise IndexError(n)

    def close(self):
        self.c.close()


def read_image(path) -> np.ndarray:
    a = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_UNCHANGED)
    if a.ndim == 2:
        a = cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
    return a[..., [2, 1, 0] + ([3] if a.shape[-1] == 4 else [])]


def font(px):
    for n in ("consola.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(n, px)
        except OSError:
            pass
    return ImageFont.load_default()


def text_layer(w, h, text, size=34, x=.04, y=.9, color=(255, 255, 255, 255), box=False):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = font(int(size * h / 1080))
    bb = d.textbbox((0, 0), text, font=f)
    px, py = x * w, y * h
    if box:
        d.rectangle((px - 8, py - 6, px + bb[2] + 8, py + bb[3] + 8), fill=(0, 0, 0, 170))
    d.text((px, py), text, font=f, fill=color)
    return np.asarray(im)


def card_layer(w, h, text):
    """A storyboard or title card: first line large, the rest wrapped below."""
    im = Image.new("RGB", (w, h), (14, 15, 17))
    d = ImageDraw.Draw(im)
    head, _, rest = text.partition("\n")
    f1, f2, y = font(int(h * .075)), font(int(h * .038)), h * .34
    tw = d.textlength(head, font=f1)
    d.text(((w - tw) / 2, y), head, font=f1, fill=(236, 233, 226))
    y, line = y + h * .13, ""
    for word in rest.replace("\n", " ").split() + [None]:
        if word is None or d.textlength(line + " " + word, font=f2) > w * .7:
            d.text(((w - d.textlength(line, font=f2)) / 2, y), line, font=f2, fill=(168, 166, 160))
            y, line = y + h * .055, word or ""
        else:
            line = (line + " " + word).strip()
    return np.asarray(im)


# ------------------------------------------------------------------ picture
class Renderer:
    def __init__(self, tl, resolve, scale=1.0, burn=False):
        validate(tl)
        self.tl, self.resolve, self.burn = tl, resolve, burn
        self.w, self.h = int(tl["size"][0] * scale) // 2 * 2, int(tl["size"][1] * scale) // 2 * 2
        self.scale = scale
        self.g = Compositor(self.w, self.h)
        self.tex, self.vid, self.aux, self.eight, self.cur8 = {}, {}, {}, {}, False
        self.tracks = sorted(tl["video"], key=lambda t: t["track"])

    def key(self):
        assets = sorted({c.get("asset") for t in self.tl["video"] for c in t["clips"] if c.get("asset")})
        return sha({"tl": {k: self.tl[k] for k in ("fps", "size", "frames", "video")}, "e": ENGINE_VERSION, "g": self.g.key, "s": self.scale, "b": self.burn, "a": [(a, Path(self.resolve(a)).name) for a in assets]})

    def segment_key(self, s, e):
        """A segment is the same if nothing that can be seen in it changed."""
        cl = []
        for t in self.tracks:
            for c in t["clips"]:
                if c["at"] < e and c["at"] + c["dur"] > s:
                    cl.append([t["track"], {k: v for k, v in c.items() if k not in ("reason", "shot")}, Path(self.resolve(c["asset"])).name if c.get("asset") else None])
                elif c["at"] == e and c.get("transition_in", {}).get("type") == "dip":
                    cl.append([t["track"], "dip", c["transition_in"]["dur"]])
        return sha([self.tl["fps"], self.tl["size"], s, e, cl, ENGINE_VERSION, self.g.key, self.scale, self.burn, self.tl.get("film_id")])

    def _source(self, c, f):
        k = c["kind"]
        if k == "video":
            p = self.resolve(c["asset"])
            if c["id"] not in self.vid:
                self.vid[c["id"]] = VideoSource(p)
            n = source_frame(c, f, p, self.tl["fps"])
            if c.get("hold_last"):
                n = min(n, count_frames(p) - 1)
            return self.vid[c["id"]].get(n), True
        return (None if c["asset"] in self.tex else read_image(self.resolve(c["asset"]))), False

    def _texture(self, c, f):
        if c["kind"] == "solid":
            key = "solid:" + c["id"]
            if key not in self.tex:
                self.eight[key] = False
                if c.get("card"):
                    self.tex[key] = (self.g.upload(to_linear(card_layer(self.w, self.h, c["card"]))), self.w, self.h)
                else:
                    self.tex[key] = (self.g.upload(np.tile(np.array([[[0, 0, 0, 1]]], np.float16), (4, 4, 1))), 4, 4)
            self.cur8 = False
            return self.tex[key]
        lin, moving = self._source(c, f)
        key = c["id"] if moving else c["asset"]
        if lin is not None:
            h, w = lin.shape[:2]
            eight = lin.dtype == np.uint8
            if not eight:
                lin = to_linear(lin)
            self.eight[key] = eight
            if key in self.tex and self.tex[key][1:] == (w, h):
                (self.g.update8 if eight else self.g.update)(self.tex[key][0], lin)
            else:
                self.tex[key] = ((self.g.upload8 if eight else self.g.upload)(lin), w, h)
        self.cur8 = self.eight.get(key, False)
        return self.tex[key]

    def _aux(self, asset):
        if asset not in self.aux:
            self.aux[asset] = self.g.upload(to_linear(read_image(self.resolve(asset))))
        return self.aux[asset]

    def _clip(self, c, f, gf):
        g = self.g
        tex, sw, sh = self._texture(c, f)
        tr = c.get("transform", {})
        pos, anc = ev(tr.get("pos"), f, [0, 0]), tr.get("anchor", [0, 0])
        sa, da = sw / sh, self.w / self.h
        fit = (1.0, da and 1.0)
        fx, fy = (sa / da, 1.0) if sa < da else (1.0, da / sa)       # fill the frame, keep proportions
        fx, fy = (1.0, sa / da) if sa < da else (da / sa, 1.0)
        if c["kind"] == "solid":
            fx = fy = 1.0
        cur = g.run("place", tex, {"x": pos[0], "y": pos[1], "scale": ev(tr.get("scale"), f, 1.0), "rot": ev(tr.get("rot"), f, 0.0), "opacity": 1.0, "fit_x": fx, "fit_y": fy, "ax": anc[0], "ay": anc[1], "lin": 1 if self.cur8 else 0}, gf)
        for e in c.get("effects", []):
            if e.get("enabled") is False or e["op"] in ("timestamp", "caption"):
                continue
            pr = {k: ev(v, f) for k, v in e["params"].items() if k != "aux"}
            for rk in ("radius", "size"):       # pixel sizes are authored at 1080 lines
                if rk in pr and e["op"] in ("blur", "lens_blur", "bloom", "grain", "dust"):
                    pr[rk] = pr[rk] * self.h / 1080
            nx = g.run(e["op"], cur, pr, gf, self._aux(e["params"]["aux"]) if "aux" in e["params"] else None)
            if e.get("mask"):                       # only where the mask is white
                part = g.run("matte", nx, {}, gf, self._aux(e["mask"]))
                g.free(nx)
                nx = g.run("over", cur, {"mode": 0, "opacity": 1}, gf, part)
                g.free(part)
            g.free(cur)
            cur = nx
        if c.get("matte"):
            nx = g.run("matte", cur, {}, gf, self._aux(c["matte"]))
            g.free(cur)
            cur = nx
        return cur, float(ev(tr.get("opacity"), f, 1.0))

    def frame(self, n) -> bytes:
        g = self.g
        g.begin()
        acc = g.run("fade", self.g.white, {"level": 0}, n)
        for t in self.tracks:
            for c in sorted(t["clips"], key=lambda c: c["at"]):
                if not c["at"] <= n < c["at"] + c["dur"]:
                    continue
                f = n - c["at"]
                lay, op = self._clip(c, f, n)
                ti = c.get("transition_in", {})
                if ti.get("type") == "dissolve" and f < ti["dur"]:
                    op *= (f + 1) / (ti["dur"] + 1)
                lv = self._dip(t, c, n)
                if lv < 1:
                    d = g.run("fade", lay, {"level": lv}, n)
                    g.free(lay)
                    lay = d
                if ti.get("type") == "wipe" and f < ti["dur"]:
                    d = g.run("wipe", lay, {"progress": (f + 1) / (ti["dur"] + 1), "angle": ti.get("angle", 0)}, n)
                    g.free(lay)
                    lay = d
                if ti.get("type") == "flash" and f < ti["dur"]:
                    d = g.run("over", lay, {"mode": 0, "opacity": (1 - f / ti["dur"]) ** 2}, n, g.white)
                    g.free(lay)
                    lay = d
                nx = g.run("over", acc, {"mode": BLEND[c.get("blend", "normal")], "opacity": op}, n, lay)
                g.free(acc)
                g.free(lay)
                acc = nx
                for e in c.get("effects", []):
                    if e["op"] in ("timestamp", "caption") and e.get("enabled", True):
                        pr = e["params"]
                        tx = pr["text"]
                        if e["op"] == "timestamp":
                            s = int(pr.get("start_s", 0) + f * self.tl["fps"][1] / self.tl["fps"][0])
                            tx = f"{tx} {s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"
                        acc = self._text(acc, n, (tx, pr.get("size", 34), pr.get("x", .04), pr.get("y", .9), bool(pr.get("box"))))
        if self.burn:
            acc = self._text(acc, n, (f"{n:05d}", 30, .01, .01, True))
        d = g.finish(acc, n)
        g.free(acc)
        return d

    @staticmethod
    def _dip(t, c, n):
        """Through black: the old picture goes down before the cut, the new one comes up after it."""
        f, lv, ti = n - c["at"], 1.0, c.get("transition_in", {})
        if ti.get("type") == "dip" and f < ti["dur"]:
            lv = (f + 1) / (ti["dur"] + 1)
        end = c["at"] + c["dur"]
        for o in t["clips"]:
            x = o.get("transition_in", {})
            if o["at"] == end and x.get("type") == "dip" and end - n <= x["dur"]:
                lv = min(lv, (end - n) / (x["dur"] + 1))
        return lv

    def _text(self, acc, n, spec):
        k = ("text",) + spec
        if k not in self.tex:
            if len(self.tex) > 400:
                self.tex = {a: b for a, b in self.tex.items() if a[0] != "text"}
            self.tex[k] = (self.g.upload(to_linear(text_layer(self.w, self.h, spec[0], spec[1], spec[2], spec[3], box=spec[4]))), self.w, self.h)
        nx = self.g.run("over", acc, {"mode": 0, "opacity": 1}, n, self.tex[k][0])
        self.g.free(acc)
        return nx

    def png(self, n, path):
        a = np.frombuffer(self.frame(n), np.uint16).reshape(self.h, self.w, 4)
        cv2.imwrite(str(path), (a[..., [2, 1, 0]] >> 8).astype(np.uint8))

    def render(self, work: Path, progress=None, stop=lambda: False) -> list[Path]:
        """Independent segments. A finished segment is never rendered twice; a broken one is replaced."""
        work = Path(work)
        work.mkdir(parents=True, exist_ok=True)
        fps = f"{self.tl['fps'][0]}/{self.tl['fps'][1]}"
        out, self.rendered, self.render_s = [], 0, 0.0
        for s in range(0, self.tl["frames"], SEG):
            e = min(s + SEG, self.tl["frames"])
            p = work / f"{self.segment_key(s, e)[:24]}.mkv"
            out.append(p)
            if p.exists() and count_frames(p) == e - s:
                p.touch()
                continue
            tmp = p.with_suffix(".part.mkv")
            t0 = time.time()
            ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba64le", "-s", f"{self.w}x{self.h}", "-r", fps, "-i", "-", "-vf", "scale=out_color_matrix=bt709:out_range=tv,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
                                   "-c:v", "hevc_nvenc", "-preset", "p5" if self.scale == 1 else "p2", "-rc", "vbr", "-cq", "14" if self.scale == 1 else "24", "-b:v", "0", "-bf", "2", "-g", "48", "-pix_fmt", "p010le", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv", str(tmp)],
                                  stdin=subprocess.PIPE)
            try:
                for n in range(s, e):
                    if stop():
                        raise KeyboardInterrupt
                    ff.stdin.write(self.frame(n))
                    if progress and n % 12 == 0:
                        progress(n, self.tl["frames"])
                ff.stdin.close()
                if ff.wait() != 0:
                    raise RuntimeError("encoder failed")
            except BaseException:
                ff.kill()
                ff.wait()
                tmp.unlink(missing_ok=True)
                raise
            tmp.replace(p)
            self.rendered += e - s
            ends = {c["id"]: c["at"] + c["dur"] for tr in self.tracks for c in tr["clips"]}
            for cid in [k for k in self.vid if ends[k] <= e]:          # finished clips give their memory back
                self.vid.pop(cid).close()
                self.tex.pop(cid, None)
            later = {c.get("asset") for tr in self.tracks for c in tr["clips"] if c["at"] + c["dur"] > e} | {"solid:" + c["id"] for tr in self.tracks for c in tr["clips"] if c["at"] + c["dur"] > e} | set(self.vid)
            for k in [k for k in self.tex if not isinstance(k, tuple) and k not in later]:
                self.tex.pop(k)
            need = {r for tr in self.tracks for c in tr["clips"] if c["at"] + c["dur"] > e for r in [c.get("matte")] + [x.get("mask") for x in c.get("effects", [])] + [x["params"].get("aux") for x in c.get("effects", [])]}
            for k in [k for k in self.aux if k not in need]:
                self.aux.pop(k)
            self.render_s += time.time() - t0
        for v in self.vid.values():
            v.close()
        self.vid = {}
        return out


def join(segs: list[Path], out: Path):
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in segs), encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], check=True)
    lst.unlink()
    return out


def deliver(master: Path, wav: Path, out: Path, fps, kbps=16000) -> dict:
    """YouTube: H.264 High, 2 B-frames, closed GOP of half the frame rate, no edit lists, AAC 48 kHz. Sync is measured and corrected."""
    gop = max(1, round(fps[0] / fps[1] / 2))
    v = out.with_suffix(".v.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(master), "-an", "-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv", "-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0", "-c:v", "h264_nvenc", "-preset", "p6", "-profile:v", "high", "-rc", "vbr", "-cq", "18", "-b:v", f"{kbps}k", "-maxrate", f"{kbps * 2}k", "-bufsize", f"{kbps * 3}k",
                    "-bf", "2", "-g", str(gop), "-strict_gop", "1", "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv", str(v)], check=True)

    def mux(pad):
        af = ["-af", f"adelay={pad}S:all=1"] if pad > 0 else []
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(v), "-i", str(wav), *af, "-c:v", "copy", "-c:a", "aac", "-b:a", "384k", "-ar", "48000", "-movflags", "+faststart", "-use_editlist", "0", str(out)], check=True)
    mux(0)
    pad = max(0, round(-sync_offset(out, wav) * 48000))
    mux(pad)
    v.unlink()
    return {"pad_samples": pad, "offset_ms": round(sync_offset(out, wav) * 1000, 2)}


def _pcm(path: Path, channels=2, start=None, seconds=None) -> np.ndarray:
    cut = (["-ss", f"{start:.6f}"] if start else []) + (["-t", f"{seconds:.6f}"] if seconds else [])
    r = subprocess.run(["ffmpeg", "-v", "error", *cut, "-i", str(path), "-ac", str(channels), "-ar", "48000", "-f", "f32le", "-"], capture_output=True)
    x = np.frombuffer(r.stdout, np.float32)
    return x.reshape(-1, channels) if channels > 1 else x


def pcm_blocks(path: Path, seconds=20):
    """Decoded sound of a file, piece by piece, so that the length of a film does not matter."""
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "2", "-ar", "48000", "-f", "f32le", "-"], stdout=subprocess.PIPE)
    try:
        while b := p.stdout.read(48000 * 8 * seconds):
            yield np.frombuffer(b[: len(b) // 8 * 8], np.float32).reshape(-1, 2)
    finally:
        p.stdout.close()
        p.wait()


def sync_offset(path: Path, wav: Path, at: float = 0.0) -> float:
    """Seconds by which sound is late (+) or early (-) against picture, measured in a window that starts near 'at'."""
    with av.open(str(path)) as c:
        v0 = next(float(f.pts * f.time_base) for f in c.decode(video=0))
    x, t0, need = [], None, 40.0
    with av.open(str(path)) as c:
        st = c.streams.audio[0]
        if at > 0:
            c.seek(int(at / st.time_base), stream=st, backward=True)
        got = 0
        for f in c.decode(audio=0):
            t0 = float(f.pts * f.time_base) if t0 is None else t0
            x.append(f.to_ndarray()[0] if f.format.is_planar else f.to_ndarray().reshape(-1, f.layout.nb_channels)[:, 0])
            got += len(x[-1])
            if got > 48000 * need:
                break
    x = np.concatenate(x).astype(np.float32)
    start = max(0.0, t0 - v0 - .5) if at > 0 else 0.0
    r = _pcm(wav, 1, start, need + 2)
    from scipy.signal import correlate
    n = min(len(x), len(r), int(48000 * need))
    if n < 4800 or np.abs(r[:n]).max() < 1e-4:
        return 0.0
    cc = correlate(x[:n], r[:n], mode="full", method="fft")
    mid, w = n - 1, 48000
    lo = max(0, mid - w)
    lag = int(np.argmax(cc[lo:mid + w])) - (mid - lo)
    return t0 + lag / 48000 - v0 - start


def qa(path: Path, tl: dict, wav: Path | None = None) -> dict:
    """What is checked on the finished file, over its whole length, before it is registered."""
    from . import sound
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True).stdout)
    v = next(s for s in pr["streams"] if s["codec_type"] == "video")
    a = [s for s in pr["streams"] if s["codec_type"] == "audio"]
    items, fps = [], tl["fps"][0] / tl["fps"][1]
    n = count_frames(path)
    items.append(("frame count", "pass" if n == tl["frames"] else "fail", f"{n} of {tl['frames']}"))
    items.append(("size", "pass" if [v["width"], v["height"]] == list(tl["size"]) or v["width"] < tl["size"][0] else "fail", f"{v['width']}x{v['height']}"))
    num = lambda s: (lambda p: int(p[0]) / max(1, int(p[1])))(s.split("/"))
    items.append(("constant frame rate", "pass" if abs(num(v["avg_frame_rate"]) - fps) < .01 and abs(num(v["r_frame_rate"]) - fps) < .01 else "fail", f"{num(v['avg_frame_rate']):.3f}"))
    items.append(("decodes cleanly", "pass" if not subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True).stderr.strip() else "fail", ""))
    items.append(("colour tags", "pass" if all(v.get(k) == "bt709" for k in ("color_primaries", "color_transfer", "color_space")) and v.get("color_range") == "tv" else "fail", f"{v.get('color_primaries')}/{v.get('color_transfer')}/{v.get('color_space')}/{v.get('color_range')}"))
    if a and wav:
        length = tl["frames"] / fps
        for name, at in [("sync", 0.0)] + ([("sync near the end", max(0.0, length - 45))] if length > 90 else []):
            off = sync_offset(path, wav, at) * 1000
            items.append((name, "pass" if abs(off) <= 5 else "fail", f"{off:+.2f} ms"))
    if a:
        items.append(("sound format", "pass" if a[0].get("sample_rate") == "48000" and a[0].get("channels") == 2 else "fail", f"{a[0].get('codec_name')} {a[0].get('sample_rate')} Hz {a[0].get('channels')} channels"))
        m, peak, quiet = sound.Loudness(), 0.0, {}
        pad = 0.0
        with av.open(str(path)) as c:
            pad = next(float(f.pts * f.time_base) for f in c.decode(video=0))
        want = [(s, int((s["from_frame"] / fps + pad) * 48000) + 480, int((s["from_frame"] / fps + pad) * 48000) + int(s["frames"] / fps * 48000) - 480) for s in tl.get("silences", [])]
        acc = {i: [0.0, 0] for i in range(len(want))}
        pos, tail = 0, np.zeros((0, 2), np.float32)
        from scipy.signal import resample_poly
        for x in pcm_blocks(path):
            m.feed(x)
            y = np.concatenate([tail, x])
            peak = max(peak, float(np.abs(resample_poly(y, 4, 1, axis=0)).max()))
            tail = x[-64:]
            for i, (_, p0, p1) in enumerate(want):
                s0, s1 = max(p0, pos), min(p1, pos + len(x))
                if s1 > s0:
                    acc[i][0] += float((x[s0 - pos:s1 - pos] ** 2).sum())
                    acc[i][1] += 2 * (s1 - s0)
            pos += len(x)
        l, target = m.value(), tl.get("loudness", -16)
        items.append(("loudness", "pass" if abs(l - target) <= 2 else "warn", f"{l:.1f} LUFS, wanted {target:.0f}"))
        tp = 20 * np.log10(peak + 1e-9)
        items.append(("true peak", "pass" if tp <= -1 else "warn" if tp <= -.3 else "fail", f"{tp:.1f} dB"))
        for i, (s, _, _) in enumerate(want):
            e, k = acc[i]
            lv = 20 * np.log10((e / k) ** .5 + 1e-9) if k else -120
            items.append((f"silence at frame {s['from_frame']}", "pass" if lv <= s["max_dbfs"] else "fail", f"{lv:.0f} dB, wanted {s['max_dbfs']:.0f} or lower"))
    o = [i[1] for i in items]
    return {"outcome": "fail" if "fail" in o else "warn" if "warn" in o else "pass", "items": items}
