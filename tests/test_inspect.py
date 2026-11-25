"""Defect-injection bench. Synthetic, but with texture, noise and real H.264 compression. Counts, not percentages."""
import subprocess

import cv2
import numpy as np
import pytest

from studio.core.assets import probe
from studio.inspect import audio, image, video

W, H, N = 1280, 720, 144


def background(seed=3):
    r = np.random.default_rng(seed)
    bg = cv2.resize(r.integers(15, 120, (36, 64, 3), np.uint8), (W, H), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    for _ in range(70):
        x, y = int(r.integers(0, W)), int(r.integers(0, H))
        cv2.rectangle(bg, (x, y), (x + int(r.integers(20, 140)), y + int(r.integers(20, 120))), tuple(float(v) for v in r.integers(10, 170, 3)), int(r.choice([-1, 2, 3])))
    return cv2.GaussianBlur(bg, (0, 0), .8)


def figure(im, x, sx=1.0, face=True):
    c = (int(x), 400)
    cv2.ellipse(im, c, (int(62 * sx), 190), 0, 0, 360, (45, 60, 150), -1)
    cv2.rectangle(im, (c[0] - int(40 * sx), 340), (c[0] + int(40 * sx), 360), (30, 160, 160), -1)
    cv2.circle(im, (c[0], 190), int(46 * sx), (140, 165, 200), -1)
    if face:
        for dx in (-16, 16):
            cv2.circle(im, (c[0] + int(dx * sx), 182), 6, (20, 20, 25), -1)
        cv2.line(im, (c[0] - 14, 212), (c[0] + 14, 212), (40, 40, 110), 4)


_grain = {}


def grain(r, shape, sigma):
    """Noise that differs in every frame: a few frames of it, shifted at random. Making it anew for each frame took longer than the inspection."""
    if shape not in _grain:
        _grain[shape] = np.random.default_rng(7).standard_normal((6, *shape), np.float32)
    g = _grain[shape][int(r.integers(6))]
    return np.roll(g, (int(r.integers(shape[0])), int(r.integers(shape[1]))), (0, 1)) * np.float32(sigma)


def clip(tmp, defect=None, seed=3, light=True):
    r = np.random.default_rng(seed + 100)
    bg = background(seed)
    p = tmp / f"{defect}_{seed}.mp4"
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", "24", "-i", "-", "-c:v", "libx264", "-crf", "21", "-pix_fmt", "yuv420p", str(p)], stdin=subprocess.PIPE)
    frozen = None
    for i in range(N):
        f = bg.copy()
        x = 380 + 260 * (1 - np.cos(np.pi * min(1, max(0, (i - 12) / 110)))) / 2
        sx, show = 1.0, True
        if defect == "vanish" and 70 <= i < 74:
            show = False
        if defect == "morph" and 60 <= i < 84:
            sx = 1 + .75 * np.sin((i - 60) / 24 * np.pi)
        if defect == "creep":
            f[90:230, 900:1060] = np.roll(bg[90:230, 900:1060], int(i / 9), 1)
        if defect == "appear" and i > 80:
            cv2.rectangle(f, (1010, 420), (1110, 600), (15, 15, 18), -1)
        if show:
            figure(f, x, sx, face=not (defect == "faceless" and 60 <= i < 80))
        if light:
            f *= 1 + .02 * np.sin(i / 23)
        if defect == "flicker" and 40 <= i < 80:
            f *= 1 + .05 * (-1) ** i
        if defect == "drift":
            f = cv2.warpAffine(f, np.float32([[1, 0, i * .12], [0, 1, 0]]), (W, H), borderMode=cv2.BORDER_REFLECT)
        if defect == "zoom":
            s = 1 + .0004 * i
            f = cv2.warpAffine(f, np.float32([[s, 0, W / 2 * (1 - s)], [0, s, H / 2 * (1 - s)]]), (W, H), borderMode=cv2.BORDER_REFLECT)
        if defect == "cut" and i >= 90:
            f = cv2.flip(background(seed + 9), 1)
            figure(f, x)
        if defect == "black" and 50 <= i < 53:
            f[:] = 0
        if defect == "freeze":
            if i == 60:
                frozen = f.copy()
            if 60 <= i < 90:
                f = frozen.copy()
        if defect == "glitch" and i == 66:
            f = np.roll(f, 40, 1)
        if not (defect == "freeze" and 60 < i < 90):
            f = f + grain(r, f.shape, 1.6)
            if defect == "freeze" and i == 60:
                frozen = f.copy()
        ff.stdin.write(np.clip(f, 0, 255).astype(np.uint8).tobytes())
    ff.stdin.close()
    ff.wait()
    return p


DEFECTS = {"vanish": {"subject vanishes", "short glitch"}, "morph": {"subject changes shape"}, "creep": {"static area changed"}, "appear": {"static area changed"}, "flicker": {"luma flicker"},
           "drift": {"camera moved"}, "zoom": {"camera moved"}, "cut": {"unplanned cut"}, "black": {"black frames"}, "freeze": {"frozen"}, "glitch": {"short glitch", "unplanned cut"}}
EXPECT = {"aspect": 16 / 9, "duration": (3, 10), "camera": "locked"}


@pytest.mark.parametrize("defect", list(DEFECTS))
def test_injected_defect_is_found(tmp_path, defect):
    p = clip(tmp_path, defect)
    rep = video.inspect(p, probe(p), EXPECT, tmp_path / "v")
    got = {f["class"] for f in rep["flags"]}
    assert got & DEFECTS[defect], (defect, rep["flags"])
    assert rep["outcome"] != "pass"


@pytest.mark.parametrize("seed", [3, 11, 29, 47])
def test_clean_clip_has_no_serious_flag(tmp_path, seed):
    p = clip(tmp_path, None, seed)
    rep = video.inspect(p, probe(p), EXPECT, tmp_path / "v")
    assert [f for f in rep["flags"] if f["severity"] in ("critical", "major")] == []


def test_known_blind_spot_is_recorded(tmp_path):
    """A face that loses its features for 20 frames: local measurement is not expected to see it. This documents the gap."""
    p = clip(tmp_path, "faceless")
    rep = video.inspect(p, probe(p), EXPECT, tmp_path / "v")
    assert "identity (needs approved references)" in rep["not_examined"]


def test_adherence(tmp_path):
    p = clip(tmp_path, None)
    a, b = video.frame_at(p, 0), video.frame_at(p, N - 1)
    good = video.inspect(p, probe(p), EXPECT, tmp_path / "v", a, b)["adherence"]
    swapped = video.inspect(p, probe(p), EXPECT, tmp_path / "v", b, a)["adherence"]
    assert good["first"]["outcome"] == good["last"]["outcome"] == "pass"
    assert swapped["first"]["at_edge"]["score"] < good["first"]["at_edge"]["score"] - .02
    other = np.clip(cv2.flip(background(77), 0), 0, 255).astype(np.uint8)
    assert video.inspect(p, probe(p), EXPECT, tmp_path / "v", other, b)["adherence"]["first"]["outcome"] != "pass"


def _img(tmp, name, f):
    p = tmp / name
    cv2.imwrite(str(p), np.clip(f, 0, 255).astype(np.uint8))
    return p


def test_frame_pairs(tmp_path):
    bg = background()
    def at(x, b=None):
        f = (bg if b is None else b).copy()
        figure(f, x)
        return f
    a = _img(tmp_path, "a.png", at(400))
    zone = [[.2, .1, .62, .95]]
    sh = lambda f, dx: cv2.warpAffine(f, np.float32([[1, 0, dx], [0, 1, 0]]), (W, H), borderMode=cv2.BORDER_REFLECT)
    door = bg.copy()
    cv2.rectangle(door, (1000, 200), (1100, 420), (8, 8, 8), -1)
    good = image.pair(a, _img(tmp_path, "g.png", at(600)), "locked", zone)
    assert good["outcome"] == "pass", good
    for name, f in {"moved": at(600, sh(bg, 8)), "object": at(600, door), "light": at(600, bg * np.array([.8, 1, 1.18]))}.items():
        assert image.pair(a, _img(tmp_path, name + ".png", f), "locked", zone)["outcome"] == "fail", name
    assert image.pair(a, a, "locked", zone)["outcome"] == "fail"


def test_conform_is_exact():
    out, info = image.conform(np.zeros((1536, 2752, 3), np.uint8))
    assert out.shape == (1080, 1920, 3) and info["cropped_px"][0] < 20


def test_word_comparison():
    assert audio.compare_words("[quietly] Two nights ago, 2 officers.", "two nights ago two officers") == []
    d = audio.compare_words("I counted eleven steps.", "i counted seven steps")
    assert d == [{"op": "replace", "script": ["eleven"], "heard": ["seven"]}]


def test_audio_measurements(tmp_path):
    import soundfile as sf
    t = np.arange(48000 * 3) / 48000
    clean = .2 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 1.5 * t) > 0)
    sf.write(tmp_path / "c.wav", clean, 48000)
    sf.write(tmp_path / "x.wav", np.clip(clean * 9, -1, 1), 48000)
    sf.write(tmp_path / "n.wav", clean + np.random.default_rng(1).normal(0, .02, len(t)), 48000)
    r = lambda n: audio.measure(tmp_path / n, probe(tmp_path / n))
    assert dict((i[0], i[1]) for i in r("c.wav")["items"])["clipping"] == "pass"
    assert dict((i[0], i[1]) for i in r("x.wav")["items"])["clipping"] == "fail"
    assert dict((i[0], i[1]) for i in r("n.wav")["items"])["noise floor"] != "pass"
