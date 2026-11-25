import copy
import subprocess

import cv2
import numpy as np
import pytest
import soundfile as sf

from studio.engine import render, sound


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("m")
    r = np.random.default_rng(2)
    im = cv2.resize(r.integers(0, 255, (27, 48, 3), np.uint8), (1920, 1080), interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(str(d / "im.png"), im)
    # each frame carries its number as 10 black/white blocks
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1280x720", "-r", "24", "-i", "-", "-c:v", "libx264", "-bf", "3", "-g", "250", "-crf", "18", "-pix_fmt", "yuv420p", str(d / "v.mp4")], stdin=subprocess.PIPE)
    for n in range(200):
        f = np.full((720, 1280, 3), 90, np.uint8)
        for b in range(10):
            f[200:400, 40 + b * 120:130 + b * 120] = 235 if n >> b & 1 else 20
        ff.stdin.write(f.tobytes())
    ff.stdin.close()
    ff.wait()
    t = np.arange(48000) / 48000
    x = np.zeros(48000 * 2)
    x[:48] = .9
    sf.write(str(d / "click.wav"), x, 48000)
    sf.write(str(d / "tone.wav"), .2 * np.sin(2 * np.pi * 300 * np.arange(48000 * 10) / 48000), 48000)
    return d


def base(frames=192, size=(1280, 720)):
    return {"schema_version": "1", "film_id": "t", "fps": [24, 1], "size": list(size), "sample_rate": 48000, "frames": frames,
            "video": [{"track": 0, "clips": [{"id": "a", "kind": "image", "asset": "im", "at": 0, "dur": 96, "reason": "open", "transform": {"scale": [{"f": 0, "v": 1.0}, {"f": 95, "v": 1.06, "ease": "smooth"}]},
                                              "effects": [{"op": "grade", "version": "1", "params": {"exposure": -.4}}, {"op": "grain", "version": "1", "params": {"amount": .02}}]},
                                             {"id": "b", "kind": "video", "asset": "v", "at": 96, "dur": frames - 96, "src_in": 40, "reason": "reveal"}]}],
            "audio": [], "markers": []}


def res(d):
    m = {"im": d / "im.png", "v": d / "v.mp4", "click": d / "click.wav", "tone": d / "tone.wav"}
    return lambda a: m.get(a)


def number(raw, w, h):
    a = np.frombuffer(raw, np.uint16).reshape(h, w, 4)[..., 0]
    s = w / 1280
    return sum((a[int(240 * s):int(360 * s), int((60 + b * 120) * s):int((110 + b * 120) * s)].mean() > 33000) << b for b in range(10))


def test_every_effect_compiles_and_runs():
    from studio.engine.compositor import OPS, Compositor, to_linear
    g = Compositor(320, 180)
    t = g.upload(to_linear(np.full((180, 320, 3), 120, np.uint8)))
    for op in OPS:
        g.begin()
        out = np.frombuffer(g.finish(g.run(op, t, {}, 7, t), 7), np.uint16)
        assert out.max() > 0, op


def test_video_frames_are_exact(media):
    r = render.Renderer(base(), res(media))
    for n in (96, 97, 150, 191, 100):
        assert number(r.frame(n), 1280, 720) == 40 + n - 96


def test_render_is_repeatable_and_resumes(media, tmp_path):
    tl = base()
    a = render.Renderer(tl, res(media)).frame(50)
    assert a == render.Renderer(tl, res(media)).frame(50)
    calls = []
    with pytest.raises(KeyboardInterrupt):                # stopped in the second segment
        render.Renderer(tl, res(media)).render(tmp_path, stop=lambda: calls.append(1) or len(calls) > 130)
    first = [p for p in tmp_path.rglob("*.mkv") if ".part" not in p.name][0]
    import hashlib
    stamp = hashlib.sha256(first.read_bytes()).hexdigest()
    assert not list(tmp_path.rglob("*.part.mkv"))
    segs = render.Renderer(tl, res(media)).render(tmp_path)
    r = render.Renderer(tl, res(media))
    r.render(tmp_path)
    assert r.rendered == 0 and hashlib.sha256(first.read_bytes()).hexdigest() == stamp              # finished work was kept
    m = render.join(segs, tmp_path / "m.mkv")
    assert render.count_frames(m) == 192
    tl2 = copy.deepcopy(tl)
    tl2["video"][0]["clips"][0]["effects"][0]["params"]["exposure"] = 0
    assert render.Renderer(tl2, res(media)).key() != render.Renderer(tl, res(media)).key()


def test_lint_finds_problems(media):
    tl = base()
    assert [x for x in render.lint(tl, res(media)) if x["severity"] == "critical"] == []
    bad = copy.deepcopy(tl)
    bad["video"][0]["clips"][1]["at"] = 90
    bad["video"][0]["clips"][1]["src_in"] = 180
    bad["video"][0]["clips"][0]["asset"] = "gone"
    bad["video"][0]["clips"][0]["effects"][0]["params"]["nonsense"] = 1
    txt = " ".join(x["text"] for x in render.lint(bad, res(media)))
    for w in ("overlaps", "needs frame", "missing", "unknown parameters"):
        assert w in txt
    with pytest.raises(render.TimelineProblem):
        render.validate({**tl, "video": [{"track": 0, "clips": [{"id": "x", "kind": "image", "at": 1.5, "dur": 4, "reason": "r"}]}]})


def test_sound_placement_silence_delivery(media, tmp_path):
    tl = base()
    tl["audio"] = [{"id": "c", "asset": "click", "bus": "hard_fx", "at_sample": 96000, "fade_in": 0, "reason": "sync mark"}, {"id": "t", "asset": "tone", "bus": "ambience", "at_sample": 0, "gain_db": -6, "reason": "bed"}]
    tl["silences"] = [{"from_frame": 120, "frames": 48, "max_dbfs": -50, "reason": "held breath"}]
    mx = sound.mix(tl, res(media))
    c = np.abs(mx["bus"]["hard_fx"]).max(1)
    assert abs(int(np.argmax(c > .2)) - 96000) <= 2
    y, info = sound.master(mx)
    assert -16.6 < info["lufs"] < -15 and np.abs(y).max() <= 10 ** (-2.1 / 20)
    wav = sound.write(y, tmp_path / "mix.wav")
    m = render.join(render.Renderer(tl, res(media)).render(tmp_path), tmp_path / "m.mkv")
    d = render.deliver(m, wav, tmp_path / "out.mp4", tl["fps"])
    assert abs(d["offset_ms"]) < 5, d
    q = render.qa(tmp_path / "out.mp4", tl, wav)
    assert {i[0]: i[1] for i in q["items"]}["sync"] == "pass" and q["outcome"] == "pass", q
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,profile,has_b_frames", "-of", "csv", str(tmp_path / "out.mp4")], capture_output=True, text=True).stdout
    assert "h264" in info and "High" in info and "aac" in info


def test_occlusion_and_distance_change_the_sound(media):
    def level(**k):
        tl = base()
        tl["audio"] = [{"id": "t", "asset": "click", "bus": "foley", "at_sample": 0, "reason": "x", **k}]
        b = sound.mix(tl, res(media))["bus"]["foley"]
        return float((b ** 2).sum())
    assert level(occlusion=.8) < .5 * level()
    assert level(distance=2) < .4 * level()
