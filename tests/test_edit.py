"""The edit and the engine: what is accepted is done, what cannot be done is refused."""
import copy
import subprocess

import cv2
import numpy as np
import pytest
import soundfile as sf

from studio.core import edit, films, flow
from studio.core.db import RuleRefused
from studio.engine import render, sound

F, SC = "TESTFILM", "scene:TF-SC"


# ---------------------------------------------------------------- engine: every field changes the result or is refused
@pytest.fixture(scope="module")
def m(tmp_path_factory):
    d = tmp_path_factory.mktemp("e")

    def vid(n, fn, fps=24, frames=150):
        ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "320x180", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(d / n)], stdin=subprocess.PIPE)
        for i in range(frames):
            ff.stdin.write(fn(i).tobytes())
        ff.stdin.close()
        ff.wait()

    def moving(i):
        f = np.full((180, 320, 3), 25, np.uint8)
        f[:, :2 * i + 2] = 200
        return f
    vid("v24.mp4", moving)
    vid("v30.mp4", moving, 30)
    vid("v60.mp4", moving, 60)
    vid("g.mp4", lambda i: np.full((180, 320, 3), (30, 160, 30), np.uint8))
    half = np.zeros((180, 320, 3), np.uint8)
    half[:, 160:] = 255
    cv2.imwrite(str(d / "half.png"), half)
    cv2.imwrite(str(d / "black.png"), np.zeros((180, 320, 3), np.uint8))
    cv2.imwrite(str(d / "noise.png"), np.random.default_rng(1).integers(0, 255, (180, 320, 3), np.uint8))
    cv2.imwrite(str(d / "depth.png"), np.tile(np.linspace(0, 255, 320, dtype=np.uint8), (180, 1)))
    return lambda a: {"v": d / "v24.mp4", "v30": d / "v30.mp4", "v60": d / "v60.mp4", "g": d / "g.mp4", "half": d / "half.png", "black": d / "black.png", "noise": d / "noise.png", "depth": d / "depth.png"}.get(a)


def tl(clips, frames=80, **k):
    return {"schema_version": "1", "film_id": "x", "fps": [24, 1], "size": [320, 180], "sample_rate": 48000, "frames": frames, "video": [{"track": 0, "clips": clips}], "audio": [], "markers": [], **k}


def clip(**k):
    return {"id": "a", "kind": "video", "asset": "v", "at": 0, "dur": 80, "reason": "r", **k}


def frame(t, res, f=40):
    render.validate(t)
    return np.frombuffer(render.Renderer(t, res).frame(f), np.uint16).reshape(180, 320, 4).astype(int)


def edge(t, res, f=48):
    g = frame(t, res, f)[90, :, 0]
    return int((g > g.max() / 2).sum()) / 2 - 1


@pytest.mark.parametrize("asset,fps", [("v", 24), ("v30", 30), ("v60", 60)])
def test_every_source_plays_at_its_true_speed(m, asset, fps):
    assert abs(edge(tl([clip(asset=asset)]), m, 48) - 2 * fps) <= 1


def test_retime_and_hold(m):
    assert abs(edge(tl([clip(speed=2)]), m, 40) - 80) <= 1
    assert abs(edge(tl([clip(speed=.5)]), m, 40) - 20) <= 1
    ramp = [{"f": 0, "v": 1}, {"f": 40, "v": 3}]
    assert abs(edge(tl([clip(speed=ramp)]), m, 40) - 79) <= 2
    long = tl([clip(speed=4)], 80)
    assert any("needs frame" in x["text"] for x in render.lint(long, m))
    assert not any(x["severity"] == "critical" for x in render.lint(tl([clip(speed=4, hold_last=True)]), m))
    assert abs(edge(tl([clip(speed=4, hold_last=True)]), m, 79) - 149) <= 1


def test_a_mask_limits_an_effect(m):
    f = lambda **k: frame(tl([{**clip(asset="g"), "effects": [{"op": "fade", "version": "1", "params": {"level": .2}, **k}]}]), m)[90, :, 1]
    plain, masked, whole = frame(tl([clip(asset="g")]), m)[90, :, 1], f(mask="half"), f()
    assert abs(masked[40] - plain[40]) < 900 and abs(masked[280] - whole[280]) < 900 and plain[40] - whole[40] > 5000


@pytest.mark.parametrize("t", sorted(render.TRANSITIONS - {"cut"}))
def test_every_transition_does_something(m, t):
    ov = 10 if t in render.OVERLAP else 0
    a, b = {"id": "a", "kind": "video", "asset": "g", "at": 0, "dur": 40 + ov, "reason": "r"}, {"id": "b", "kind": "video", "asset": "v", "at": 40, "dur": 40, "reason": "r"}
    with_ = tl([a, {**b, "transition_in": {"type": t, "dur": 10}}])
    without = tl([{**a, "dur": 40}, b])
    assert not [x for x in render.lint(with_, m) if x["severity"] in ("critical", "major")]
    assert np.abs(frame(with_, m, 44) - frame(without, m, 44)).mean() > 300
    assert np.abs(frame(with_, m, 60) - frame(without, m, 60)).mean() < 60


def test_a_dip_goes_down_before_the_cut(m):
    a, b = {"id": "a", "kind": "video", "asset": "g", "at": 0, "dur": 40, "reason": "r"}, {"id": "b", "kind": "video", "asset": "g", "at": 40, "dur": 40, "reason": "r", "transition_in": {"type": "dip", "dur": 10}}
    assert frame(tl([a, b]), m, 38)[..., :3].mean() < .6 * frame(tl([a, b]), m, 20)[..., :3].mean()


@pytest.mark.parametrize("op", sorted(set(render.OPS) - {"place", "over", "matte"}))
def test_every_effect_changes_the_picture(m, op):
    strong = {"grade": {"exposure": 1}, "blur": {"radius": 8}, "lens_blur": {"radius": 14, "focus": 0}, "depth_parallax": {"dx": .05}, "flicker": {"amount": .6}, "shimmer": {"amount": .03, "rx": .6, "ry": .6}, "lens_distort": {"k1": .3}, "fade": {"level": .3}, "wipe": {"progress": .5}, "key": {"r": .5, "g": .5, "b": .5, "tolerance": .6}, "luma_key": {"low": .3, "high": .6},
              "corner_pin": {"x0": .2, "y0": .2, "x1": .8, "y1": .3, "x2": .9, "y2": .9, "x3": .1, "y3": .7}, "letterbox": {"ratio": 3}, "bloom": {"threshold": .2, "gain": 1}, "halation": {"threshold": .1, "gain": 1}, "dust": {"gain": 3, "density": 1, "lit": .05}, "film_curve": {"shoulder": .9, "toe": .03}, "chromatic": {"amount": .03}, "rain": {"gain": 2}, "fog": {"density": .9}}.get(op, {})
    p = {**strong, **({"aux": "depth"} if op in ("lens_blur", "depth_parallax") else {})}
    base = frame(tl([{"id": "a", "kind": "image", "asset": "noise", "at": 0, "dur": 80, "reason": "r"}]), m)
    g = frame(tl([{"id": "a", "kind": "image", "asset": "noise", "at": 0, "dur": 80, "reason": "r", "effects": [{"op": op, "version": "1", "params": p}]}]), m)
    assert np.abs(g - base).mean() > 40, op


def test_corner_pin_puts_the_corners_where_it_says(m):
    g = frame(tl([{"id": "a", "kind": "image", "asset": "noise", "at": 0, "dur": 80, "reason": "r", "effects": [{"op": "grade", "version": "1", "params": {"exposure": 6}}, {"op": "corner_pin", "version": "1", "params": {"x0": .25, "y0": .25, "x1": .75, "y1": .25, "x2": .75, "y2": .75, "x3": .25, "y3": .75}}]}]), m)[..., :3].max(2)
    ys, xs = np.where(g > 20000)
    assert abs(xs.min() - 80) <= 2 and abs(xs.max() - 239) <= 2 and abs(ys.min() - 45) <= 2 and abs(ys.max() - 134) <= 2


@pytest.mark.parametrize("bad", [{"kind": "nested"}, {"kind": "scene3d"}, {"blend": "overlay"}, {"transition_in": {"type": "morph_matte", "dur": 4}}, {"effects": [{"op": "stabilise", "version": "1", "params": {}}]}, {"unknown_field": 1}])
def test_what_the_engine_cannot_do_is_refused_not_ignored(bad):
    with pytest.raises(render.TimelineProblem):
        render.validate(tl([clip(**bad)]))


def test_a_small_change_renders_only_what_changed(m, tmp_path):
    t = tl([{**clip(), "dur": 100}, {"id": "b", "kind": "video", "asset": "g", "at": 100, "dur": 120, "reason": "r"}, {"id": "c", "kind": "video", "asset": "v30", "at": 220, "dur": 100, "reason": "r"}], 320)
    r = render.Renderer(t, m)
    r.render(tmp_path)
    assert r.rendered == 320
    t2 = copy.deepcopy(t)
    t2["video"][0]["clips"][2]["effects"] = [{"op": "fade", "version": "1", "params": {"level": .5}}]
    r2 = render.Renderer(t2, m)
    r2.render(tmp_path)
    assert r2.rendered == 320 - 192


# ---------------------------------------------------------------- sound
def snd(items, frames=240, **k):
    return {**tl([clip()], frames), "audio": items, **k}


@pytest.fixture
def wavs(tmp_path):
    t = np.arange(48000 * 8) / 48000
    sf.write(str(tmp_path / "tone.wav"), .2 * np.sin(2 * np.pi * 300 * t), 48000)
    sf.write(str(tmp_path / "noise.wav"), .1 * np.random.default_rng(0).normal(0, 1, 48000 * 8), 48000)
    sf.write(str(tmp_path / "short.wav"), .2 * np.sin(2 * np.pi * 200 * t[:48000]), 48000)
    return lambda a: tmp_path / f"{a}.wav"


def rms(x):
    return float(np.sqrt((np.asarray(x) ** 2).mean()))


def test_gain_keyframes_count_film_frames(wavs):
    b = sound.mix(snd([{"id": "t", "asset": "tone", "bus": "music", "at_sample": 0, "reason": "r", "fade_in": 0, "fade_out": 0, "gain_db": [{"f": 0, "v": 0}, {"f": 96, "v": -40}]}]), wavs)["bus"]["music"]
    assert rms(b[:2400]) > 5 * rms(b[48000 * 3:48000 * 3 + 4800]) > 0         # three seconds in, not six milliseconds


def test_a_source_can_walk_away(wavs):
    k = {"id": "t", "asset": "noise", "bus": "foley", "at_sample": 0, "reason": "r"}
    b = np.asarray(sound.mix(snd([{**k, "distance": [{"f": 0, "v": 0}, {"f": 180, "v": 4}], "occlusion": [{"f": 0, "v": 0}, {"f": 180, "v": .9}]}]), wavs)["bus"]["foley"])
    near, far = b[4800:24000], b[48000 * 7:48000 * 7 + 19200]
    bright = lambda x: rms(np.diff(x, axis=0)) / rms(x)
    assert rms(far) < .25 * rms(near) and bright(far) < .4 * bright(near)
    assert np.abs(np.diff(b[:48000 * 7, 0])).max() < 1.0                       # nothing clicks


def test_ducking_rules_are_followed(wavs):
    items = [{"id": "m", "asset": "tone", "bus": "music", "at_sample": 0, "reason": "r"}, {"id": "v", "asset": "noise", "bus": "dialogue", "at_sample": 0, "reason": "r"}]
    lev = lambda **k: rms(sound.mix(snd(items, **k), wavs)["bus"]["music"][48000 * 2:48000 * 5])
    assert lev(ducking=[]) > 1.8 * lev(ducking=[{"key": "voice", "target": "music", "db": -12}]) and lev() < lev(ducking=[])


def test_ambience_can_last_longer_than_its_recording(wavs):
    a = {"id": "x", "asset": "short", "bus": "ambience", "at_sample": 0, "dur_samples": 48000 * 6, "reason": "r"}
    assert rms(sound.mix(snd([a]), wavs)["bus"]["ambience"][48000 * 4:48000 * 5]) == 0
    assert rms(sound.mix(snd([{**a, "loop": True}]), wavs)["bus"]["ambience"][48000 * 4:48000 * 5]) > .05


def test_loudness_agrees_with_the_reference(wavs):
    import pyloudnorm
    r = np.random.default_rng(5)
    x = np.concatenate([.05 * r.normal(0, 1, (48000 * 6, 2)), .3 * r.normal(0, 1, (48000 * 5, 2)), .002 * r.normal(0, 1, (48000 * 9, 2))]).astype(np.float32)
    assert abs(sound.loudness(x) - pyloudnorm.Meter(48000).integrated_loudness(x)) < .12


def test_true_peak_sees_between_the_samples():
    t = np.arange(48000) / 48000
    x = np.repeat((.5 * np.sin(2 * np.pi * 12000 * t + np.pi / 4))[:, None], 2, 1)
    assert 20 * np.log10(np.abs(x).max()) < -8.9 and abs(sound.true_peak_db(x) - -6.02) < .3


def test_a_quarter_of_an_hour_needs_little_memory(data, tmp_path):
    """The audit's D-11: 5.2 MB per second before. A 15-minute film must fit beside everything else."""
    import os
    import sys
    code = ("import sys,threading,time,psutil,os\nsys.path.insert(0,'.')\nimport numpy as np,soundfile as sf\nfrom studio.engine import sound\n"
            f"p=r'{tmp_path}'\nt=np.arange(48000*20)/48000\nsf.write(p+'/a.wav',.2*np.sin(2*np.pi*220*t)*np.random.default_rng(1).normal(1,.3,len(t)),48000)\n"
            "pk=[0];run=[True];b0=psutil.Process().memory_info().rss\n"
            "def w():\n  while run[0]:\n    m=psutil.Process().memory_info();pk[0]=max(pk[0],getattr(m,'private',m.rss));time.sleep(.02)\n"
            "T=threading.Thread(target=w);T.start()\ns=900\n"
            "tl={'frames':24*s,'fps':[24,1],'audio':[{'id':str(i),'asset':'a','bus':('dialogue','music','ambience','foley')[i%4],'at_sample':48000*10*i,'distance':.4,'effects':[{'op':'room','name':'r'}]} for i in range(88)],'silences':[{'from_frame':4000,'frames':48}],'rooms':{'r':{'seconds':.6}}}\n"
            "mx=sound.mix(tl,lambda a:p+'/a.wav')\ny,i=sound.master(mx)\nsound.write(y,p+'/o.wav')\nrun[0]=False;T.join()\nprint(pk[0]//2**20,i)\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "STUDIO_DATA": str(data)}, cwd=str(render.ROOT))
    assert r.returncode == 0, r.stderr[-800:]
    mb = int(r.stdout.split()[0])
    assert mb < 1500, mb
    assert abs(sf.info(str(tmp_path / "o.wav")).duration - 900) < .01


# ---------------------------------------------------------------- edit commands
def go(db, *cmds, base=None, **k):
    return edit.apply(db, F, SC, base if base is not None else edit.latest(db, F, SC)["rev"], list(cmds), "film-editor", **k)


def C(op, **k):
    return {"op": op, "reason": "because the scene wants it", **k}


def test_commands_do_what_they_say(db, blocking):
    a = edit.resolve(db, F, SC)[0]
    r = go(db, C("trim", id="T1-02", dur=60), C("move", id="T1-03", before="T1-02"), C("add_effect", id="T1-07", effect={"op": "fade", "version": "1", "params": {"level": .5}}), C("set_sound", id="T1-N1", fields={"gain_db": -3, "pan": .2}), C("plan_silence", silence={"id": "SIL2", "at": {"clip": "T1-05", "frame": 10}, "frames": 12, "max_dbfs": -40, "reason": "a held breath"}))
    d = edit.latest(db, F, SC)["doc"]
    assert [c["id"] for c in d["clips"]][1:3] == ["T1-03", "T1-02"] and next(c for c in d["clips"] if c["id"] == "T1-02")["dur"] == 60
    assert next(s for s in d["audio"] if s["id"] == "T1-N1")["gain_db"] == -3 and len(d["silences"]) == 2
    assert any("T1-02" in x and "84 to 60" in x for x in r["changes"]) and any("order" in x for x in r["changes"])
    b = edit.resolve(db, F, SC)[0]
    assert b["frames"] == a["frames"] - 24


def test_an_edit_is_all_or_nothing(db, blocking):
    n = db.val("select count(*) from cut_rev")
    with pytest.raises(edit.EditRefused, match="command 2"):
        go(db, C("trim", id="T1-02", dur=60), C("trim", id="NOT-THERE", dur=5))
    assert db.val("select count(*) from cut_rev") == n


@pytest.mark.parametrize("cmd,word", [
    ({"op": "explode", "reason": "boom"}, "no command"), ({"op": "trim", "id": "T1-02", "dur": 60}, "reason"), (C("trim", id="T1-02", dur=0), "minimum"), (C("place", clip={"id": "Z", "source": {"shot": "S99"}, "dur": 24, "reason": "nothing"}), "no shot"),
    (C("place", clip={"id": "T1-02", "source": {"solid": "black"}, "dur": 24, "reason": "twice"}), "twice"), (C("add_effect", id="T1-02", effect={"op": "stabilise", "version": "1", "params": {}}), "not"), (C("add_effect", id="T1-02", effect={"op": "grain", "version": "1", "params": {"wow": 1}}), "no parameters"),
    (C("set_sound", id="T1-N1", fields={"reverb_send": 3}), "no"), (C("place_sound", sound={"id": "Q", "source": {"line": "nobody"}, "bus": "dialogue", "at": {"start": True}, "reason": "no such line"}), "no line"), (C("set_transition", id="T1-03", value={"type": "star_wipe", "dur": 10}), "star_wipe"),
    (C("place_sound", sound={"id": "Q", "source": {"sound": "box fan"}, "bus": "ambience", "at": {"clip": "GONE"}, "reason": "lost anchor"}), "GONE"), (C("remove", id="nothing"), "nothing called"), (C("set_finish", value=[{"op": "lut", "version": "1", "params": {}}]), "lut")])
def test_refusals_name_the_rule(db, blocking, cmd, word):
    with pytest.raises(edit.EditRefused) as e:
        go(db, cmd)
    assert word.lower() in str(e.value).lower(), e.value


def test_commands_for_an_old_revision_are_refused(db, blocking):
    r = edit.latest(db, F, SC)["rev"]
    go(db, C("trim", id="T1-02", dur=70))
    with pytest.raises(edit.EditRefused, match="newest revision"):
        go(db, C("trim", id="T1-02", dur=50), base=r)


def test_removing_a_clip_that_sounds_hang_on_is_refused(db, blocking):
    with pytest.raises(edit.EditRefused):
        go(db, C("remove", id="T1-08"))


def test_rules_notice_edit_mistakes(db, blocking):
    cut_ = lambda *c: " ".join(p["text"] for p in go(db, *c, dry=True)["problems"])
    assert "line is not in the edit" in cut_(C("remove", id="T1-L2"))
    assert "script order" in cut_(C("set_sound", id="T1-L1", fields={"at": {"clip": "T1-07", "frame": 0}}))
    assert "quarter second" in cut_(C("trim", id="T1-06B", dur=4))
    assert "not used" in cut_(C("remove", id="T1-02"), C("set_sound", id="T1-L1", fields={"at": {"clip": "T1-03", "frame": 0}}))
    with pytest.raises(edit.EditRefused, match="silence"):
        go(db, C("set_sound", id="T1-L4", fields={"at": {"silence": "SIL", "frame": 10}}))


def test_looking_never_writes(db, blocking):
    before = {t: db.val(f"select count(*) from {t}") for t in ("asset", "audio_take", "timeline_rev", "event", "cut_rev")}
    for _ in range(2):
        tl_, st, pr = edit.resolve(db, F, SC)
    from studio.core import cut, gates
    cut.state(db, F, SC)
    gates.shots(db, F)
    assert before == {t: db.val(f"select count(*) from {t}") for t in before}
    assert st["voice T1-N1"] == "not made yet"


def test_no_windows_voice_is_a_named_gap(db, blocking, monkeypatch):
    real = subprocess.run

    def no(*a, **k):
        if a and a[0] and a[0][0] == "powershell":
            raise FileNotFoundError("powershell")
        return real(*a, **k)
    monkeypatch.setattr(subprocess, "run", no)
    from studio.core import cut
    cut.ensure_sounds(db)
    tl_, st, pr = edit.resolve(db, F, SC, make=True)
    assert st["voice T1-N1"] == "not made yet" and tl_["frames"] > 900 and not [p for p in pr if p["severity"] == "critical"]


def test_a_film_is_its_scenes_in_order(db, blocking):
    r = edit.apply(db, F, "film", 0, [{"op": "set_parts", "value": [{"scene": "TF-SC"}], "reason": "one scene"}], "user")
    from studio.core import cut
    cut.ensure_sounds(db)
    one, two = edit.resolve(db, F, SC, make=True)[0], edit.resolve(db, F, "film")[0]
    assert two["frames"] == one["frames"] and [c["at"] for c in two["video"][0]["clips"]] == [c["at"] for c in one["video"][0]["clips"]] and two["audio"][0]["at_sample"] == one["audio"][0]["at_sample"]
    assert r["rev"] == 1


def test_first_assembly_of_a_designed_scene(db, tmp_path):
    db.run("delete from cut_note") if False else None
    f = films.create(db, "Plain")
    assert flow.mode(db, f, "voice") == "manual"
    with pytest.raises(RuleRefused):
        edit.first_assembly(db, f, "nothing")
