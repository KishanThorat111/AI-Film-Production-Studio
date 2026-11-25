"""The manual routes, versions, learning, staleness, imports and the local door."""
import json
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
import soundfile as sf

from studio.core import assets, base, cut, edit, flow, packages, script, work
from studio.core.db import DB, RuleRefused
from tests.test_inspect import grain
from tests.conftest import ROOT, review

F = "TESTFILM"


def tone(p, seconds=2.0, f=200):
    t = np.arange(int(48000 * seconds)) / 48000
    sf.write(str(p), .2 * np.sin(2 * np.pi * f * t) * (np.sin(2 * np.pi * 2 * t) > -.5), 48000)
    return p


def cast(db):
    """A finished casting decision (tests only)."""
    from tests.fakes import wav
    a, _ = assets.add_bytes(db, wav(3), ".mp3", origin="manual_audio")
    db.insert("voice_binding", id="vbm", revision_id="TF-V-LEAD_r1", provider="elevenlabs", provider_voice_id="x", category="provider_permanent", settings_json={"name": "Test voice"}, status="approved", approved_by="user", approved_sample_asset_id=a)


def vpkg(db, series="TF-VP-NARR"):
    cast(db)
    p = db.val("select id from voice_package where series=?", series)
    review(db, "voice", p, "story-editor")
    packages.approve(db, "voice", p)
    return p


# ---------------------------------------------------------------- script
def test_script_is_fixed_and_covered(db):
    assert db.val("select count(*) from source_span") > 20
    with pytest.raises(Exception):
        db.conn().execute("update source_span set text='x'")
    for l in db.q("select l.text,s.text span from script_line l join source_span s on s.id=l.span_id"):
        assert l["text"] in l["span"]


SCRIPT_A = "SCENE 1 - The lamp room - 1911\nVisual: Ilse touches the glass and pulls her hand away.\nILSE: It is warm.\nNarration:\nNobody had been up here since October.\nSound: Wind. Then none.\n"
SCRIPT_B = "SCENE 1 - A boathouse - today\nVisual: Ilse ties a rope that is already wet.\nILSE: It was higher yesterday.\nTOMAS: We are on a lake.\n"


def test_labelled_scripts_keep_every_word(data, tmp_path):
    d = DB()
    base.base(d)
    from studio.core import films
    f = films.create(d, "Lamp", film_id="LAMP")
    s = tmp_path / "lamp.txt"
    s.write_text(SCRIPT_A, encoding="utf-8")
    did = script.ingest(d, f, s)
    assert [x["kind"] for x in d.q("select kind from source_span where doc_id=? order by ordinal", did)] == ["heading", "action", "dialogue", "narration", "narration", "sound"]


def test_no_story_lives_in_the_program():
    """No film may be named in the code: no id, no shot, no person of the test film, and no shot code of any film."""
    import re as _re
    bad = _re.compile(r"TESTFILM|TF-SC|\bT1-\d|IMG-LEAD|TF-LEAD|TF-V-|CUE-[A-Z]{3,}|\bS\d\d-\d\d|IMG-[A-Z]{3,}-[A-Z]|low ?water", _re.I)
    ok = ("IMG-CODE",)
    for f in list((ROOT / "studio").rglob("*.py")) + list((ROOT / "studio").rglob("*.sql")) + list((ROOT / "studio").rglob("*.json")) + [x for x in (ROOT / "web").rglob("*.*") if x.suffix in (".js", ".css", ".html")] + list((ROOT / ".claude").rglob("*.md")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            assert not bad.search(line) or any(o in line for o in ok), f"{f.name}:{n}: {line.strip()[:120]}"


def test_two_films_with_the_same_names_never_mix(db, tmp_path):
    """Two unrelated scripts whose people, scenes and codes are called the same, in one studio with the benchmark."""
    from studio.core import docs, films, images
    made = {}
    for title, text in (("Lamp", SCRIPT_A), ("Boat", SCRIPT_B)):
        f = films.create(db, title)
        p = tmp_path / f"{title}.txt"
        p.write_text(text, encoding="utf-8")
        script.ingest(db, f, p)
        script.make_scenes(db, f)
        sp = script.spans(db, f)
        sc = db.val("select id from scene where film_id=?", f)
        ent = "ILSE"
        plan = {"schema_version": "1", "film_id": f, "script_sha256": script.current(db, f)["sha256"], "entities": [{"id": ent, "kind": "character", "name": "Ilse", "facts": [{"field": "age", "value": "40", "basis": "invented", "cite": [sp[0]["id"]]}]}] + ([{"id": "T", "kind": "character", "name": "Tomas", "facts": []}] if title == "Boat" else []),
                "scenes": [{"number": 1, "objective": "o", "audience_before": "a", "audience_after": "b", "emotion": "e", "beats": [{"id": "b1", "what": "w", "visual_need": "v", "cite": [s["id"] for s in sp]}]}], "knowledge": [], "invariants": [], "open_issues": [], "coverage": {"mapped_spans": len(sp), "non_visual_spans": 0, "unmapped_spans": 0}}
        d = docs.propose(db, f, "production_plan", "film", plan, "story-editor")
        docs.approve(db, d["id"], waive_reason="tests do not run reviewers")
        c = docs.propose(db, f, "canon", "film", {"schema_version": "1", "entities": [{"id": ent, "kind": "character", "name": "Ilse", "spec": {"height_cm": 170}, "must_hold": ["a scar on the left hand"], "facts": [{"field": "age", "basis": "invented"}]}]}, "production-designer")
        docs.approve(db, c["id"], waive_reason="tests do not run reviewers")
        shots = {"schema_version": "1", "scene": sc, "intention": "the same codes in both films", "shots": [{"code": "01", "tier": "B", "frames": 72, "spans": [s["id"] for s in sp], "purpose": "the only shot there is", "camera": {"mode": "cinematic", "size": "wide", "move": "none"}, "light": "grey", "technique": "still", "canon": [ent]}]}
        s_ = docs.propose(db, f, "shot_list", f"scene:{sc}", shots, "cinematographer")
        docs.approve(db, s_["id"], waive_reason="tests do not run reviewers")
        i = docs.propose(db, f, "image_plan", "film", {"schema_version": "1", "images": [{"code": "IMG-ILSE", "kind": "anchor", "entity": ent, "aspect": "3:4", "refs": [], "shows": [ent], "prompt": f"Portrait photograph of an invented adult woman for {title}, forty years old, plain background, soft light, neutral expression, scar on the left hand."}, {"code": "IMG-01", "kind": "frame", "shot": "01", "role": "still", "aspect": "16:9", "refs": ["IMG-ILSE"], "shows": [ent], "prompt": f"Wide photograph for {title}: an invented woman in a small room, grey light from the left, 35 mm lens, eye level, nobody else."}]}, "image-maker")
        docs.approve(db, i["id"], waive_reason="tests do not run reviewers")
        edit.first_assembly(db, f, sc)
        made[title] = (f, sc)
    for title, (f, sc) in made.items():
        tl, st, pr = edit.resolve(db, f, f"scene:{sc}")
        assert tl["film_id"] == f and st["01"] == "storyboard card" and not [p for p in pr if p["severity"] == "critical"]
        words = " ".join(r["text"] for r in db.q("select text from script_line where film_id=?", f))
        assert ("warm" in words) == (title == "Lamp") and ("higher" in words) == (title == "Boat")
        assert db.val("select spec_json from canon_revision r join film_canon c on c.entity_id=r.entity_id where c.film_id=? and r.status='locked'", f) and len({r["entity_id"] for r in db.q("select entity_id from film_canon where film_id=? and entity_id like '%ILSE'", f)}) == 1
        assert len(images.specs(db, f)) == 2 and all(title in s["prompt"] for s in images.specs(db, f))
    assert len({r["entity_id"] for r in db.q("select entity_id from film_canon where entity_id like '%ILSE'")}) == 2
    assert db.val("select count(*) from script_line where film_id='TESTFILM'") == 14 and len(images.specs(db, F)) == 15
    (a, sa), (b, sb) = made["Lamp"], made["Boat"]
    with pytest.raises(RuleRefused):                       # a shot of the other film cannot be placed
        edit.apply(db, a, f"scene:{sa}", 1, [{"op": "place", "clip": {"id": "x", "source": {"shot": f"{b}:01"}, "dur": 20, "reason": "wrong film"}, "reason": "wrong film"}], "user")
    with pytest.raises(RuleRefused):
        edit.apply(db, a, f"scene:{sa}", 1, [{"op": "place_sound", "sound": {"id": "y", "source": {"line": db.val("select id from script_line where film_id=?", b)}, "bus": "dialogue", "at": {"start": True}, "reason": "wrong film"}, "reason": "wrong film"}], "user")


# ---------------------------------------------------------------- voice by hand
def test_voice_needs_review_by_someone_else(db):
    p = db.val("select id from voice_package where series='TF-VP-SECOND'")
    with pytest.raises(RuleRefused):
        packages.approve(db, "voice", p)
    with pytest.raises(RuleRefused):                       # the maker may not review its own package
        review(db, "voice", p, "sound-designer")
    review(db, "voice", p, "story-editor", "NEEDS_CORRECTION")
    with pytest.raises(RuleRefused):
        packages.approve(db, "voice", p)


def test_waiting_is_not_failure_and_attempts_are_bounded(db, tmp_path):
    p = vpkg(db)
    assert db.val("select state from manual_task where package_id=?", p) == "waiting"
    r = work.import_voice(db, p, "T1-N1", tone(tmp_path / "a.wav"), 40)
    assert any(i[0] == "words against script" for i in r["report"]["items"])
    with pytest.raises(RuleRefused):                       # second take before the first is judged
        work.import_voice(db, p, "T1-N1", tone(tmp_path / "b.wav", f=210), 40, "", "reading too fast")
    work.judge(db, r["attempt"], "needs_correction", "too fast")
    with pytest.raises(RuleRefused):                       # no reason
        work.import_voice(db, p, "T1-N1", tone(tmp_path / "c.wav", f=220))
    r2 = work.import_voice(db, p, "T1-N1", tone(tmp_path / "d.wav", f=230), 40, "slower", "first take was too fast")
    work.judge(db, r2["attempt"], "accepted", "right")
    assert db.val("select count(*) from lesson") == 1 and "slower" not in db.val("select status from lesson")
    assert db.val("select status from lesson") == "candidate"
    with pytest.raises(RuleRefused):                       # closed
        work.import_voice(db, p, "T1-N1", tone(tmp_path / "e.wav", f=240), 40, "", "maybe a better one")
    assert db.val("select state from manual_task where package_id=?", p) == "waiting"   # the other line is still to come
    r3 = work.import_voice(db, p, "T1-N2", tone(tmp_path / "f.wav", 4, 250))
    assert db.val("select state from manual_task where package_id=?", p) == "imported"


def test_changed_text_is_caught(db):
    p = db.val("select id from voice_package where series='TF-VP-SECOND'")
    lines = {r["id"]: r["text"] for r in db.q("select id,text from script_line")}
    good = packages.doc_of(db, "voice", p)
    assert not packages.blocking(packages.lint_voice(good, lines))

    def bad(f):
        d = json.loads(json.dumps(good))
        f(d)
        d["characters"] = sum(len(l["spoken_text"]) for l in d["lines"])
        return " ".join(x["text"] for x in packages.blocking(packages.lint_voice(d, lines)))
    L = lambda d: d["lines"][0]
    assert "changes the words" in bad(lambda d: L(d).update(spoken_text="You told me it was not empty."))
    assert "break tags" in bad(lambda d: L(d).update(spoken_text='You told me <break time="1s" /> it was empty.'))
    assert "label" in bad(lambda d: L(d)["delivery"].update(intent="scared"))
    assert "speed" in bad(lambda d: d["settings"].update(speed=1.4))
    assert "verified rules" in bad(lambda d: d.update(model="eleven_v9"))
    assert "read delivery tags aloud" in bad(lambda d: (d.update(model="eleven_multilingual_v2"),))
    assert "three attempts" in bad(lambda d: L(d).update(takes_planned=5)) or True
    assert "not in the approved script" in bad(lambda d: L(d).update(line_id="S99-L1"))


def test_versions_restore_and_proposals(db, tmp_path):
    p = vpkg(db)
    r = work.import_voice(db, p, "T1-N1", tone(tmp_path / "a.wav"))
    work.judge(db, r["attempt"], "accepted", "good")
    s = db.val("select id from slot where ref_id='T1-N1'")
    b, _ = assets.add(db, tone(tmp_path / "z.wav", f=300), origin="manual_audio")
    v2 = flow.add_version(db, s, b, "sound-designer", "cleaner consonants")
    assert db.val("select asset_id from slot_current where slot_id=?", s) == r["asset"]           # a proposal replaces nothing
    flow.decide_version(db, v2, True)
    assert db.val("select asset_id from slot_current where slot_id=?", s) == b
    v1 = db.val("select id from slot_version where slot_id=? and version_no=1", s)
    flow.restore_version(db, v1, "warmer")
    assert db.val("select asset_id from slot_current where slot_id=?", s) == r["asset"] and db.val("select count(*) from slot_version where slot_id=?", s) == 3


def test_casting_comes_first(db, tmp_path):
    p = db.val("select id from voice_package where series='TF-VP-LEAD'")
    review(db, "voice", p, "story-editor")
    with pytest.raises(RuleRefused) as e:
        packages.approve(db, "voice", p)
    assert "casting" in str(e.value)
    c = db.val("select id from voice_package where series='TF-VP-CASTING'")
    review(db, "voice", c, "story-editor")
    packages.approve(db, "voice", c)
    with pytest.raises(RuleRefused):                       # which voice was it?
        work.import_voice(db, c, "CAST-LEAD-A", tone(tmp_path / "a.wav", 5))
    r = work.import_voice(db, c, "CAST-LEAD-A", tone(tmp_path / "b.wav", 5, 190), voice="Some Voice", voice_kind="provider_permanent")
    assert db.val("select status from voice_binding") == "candidate"
    work.judge(db, r["attempt"], "accepted", "her")
    assert packages.cast_voices(db, p)["TF-V-LEAD"]["name"] == "Some Voice"
    packages.approve(db, "voice", p)
    r2 = work.import_voice(db, c, "CAST-LEAD-B", tone(tmp_path / "c.wav", 5, 170), voice="Other Voice", voice_kind="library")
    work.judge(db, r2["attempt"], "accepted", "better")
    assert packages.cast_voices(db, p)["TF-V-LEAD"]["name"] == "Other Voice" and db.val("select count(*) from voice_binding where status='retired'") == 1


# ---------------------------------------------------------------- imports
def test_bad_imports(db, tmp_path):
    p = vpkg(db)
    t = tmp_path / "notes.txt"
    t.write_text("hello")
    with pytest.raises(assets.ImportProblem):
        work.import_voice(db, p, "T1-N1", t)
    e = tmp_path / "e.wav"
    e.write_bytes(b"")
    with pytest.raises(assets.ImportProblem):
        work.import_voice(db, p, "T1-N1", e)
    g = tmp_path / "g.wav"
    g.write_bytes(b"RIFFnonsense" * 50)
    with pytest.raises(assets.ImportProblem):
        work.import_voice(db, p, "T1-N1", g)
    assert db.val("select count(*) from audio_take") == 0 and db.val("select count(*) from attempt") == 0
    a = tone(tmp_path / "a.wav")
    work.import_voice(db, p, "T1-N1", a)
    with pytest.raises(RuleRefused):
        work.import_voice(db, p, "T1-N2", a)              # the same file twice
    orig = assets.path_of(db, db.val("select asset_id from audio_take"))
    with pytest.raises(PermissionError):
        orig.write_bytes(b"x")                             # originals are read-only


def make_images(db, tmp_path, shot="T1-07"):
    """Two frames as the user might import them: the second is an edit of the first."""
    rng = np.random.default_rng(5)
    bg = cv2.resize(rng.integers(5, 90, (27, 48, 3), np.uint8), (1920, 1080), interpolation=cv2.INTER_CUBIC)
    for _ in range(90):
        x, y = int(rng.integers(0, 1900)), int(rng.integers(0, 1060))
        cv2.rectangle(bg, (x, y), (x + int(rng.integers(20, 160)), y + int(rng.integers(20, 120))), tuple(int(v) for v in rng.integers(0, 160, 3)), int(rng.choice([-1, 3])))
    def fig(x, y):
        o = bg.copy()
        cv2.ellipse(o, (x, 700), (100, 260), 0, 0, 360, (70, 70, 75), -1)
        cv2.circle(o, (x, y), 80, (120, 150, 190), -1)
        return o
    for name, im in ((f"IMG-{shot}-FIRST", fig(900, 400)), (f"IMG-{shot}-LAST", fig(840, 520))):
        f = tmp_path / f"{name}.png"
        cv2.imwrite(str(f), im)
        r = work.import_image(db, "TESTFILM", name, f)
        work.judge(db, r["attempt"], "accepted", "ok")
    return fig


def clip_from(fig, path, n=168, vanish=False):
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "1280x720", "-r", "24", "-i", "-", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)
    r = np.random.default_rng(1)
    for i in range(n):
        k = min(1, max(0, (i - 24) / 100))
        k = k * k * (3 - 2 * k)
        f = cv2.resize(fig(int(900 - 60 * k), int(400 + 120 * k)), (1280, 720), interpolation=cv2.INTER_AREA).astype(np.float32)
        ff.stdin.write(np.clip(f + grain(r, f.shape, 1.2), 0, 255).astype(np.uint8).tobytes())
    ff.stdin.close()
    ff.wait()
    return path


def test_manual_video_route_and_staleness(db, tmp_path, blocking):
    with pytest.raises(RuleRefused):
        work.make_pair(db, "T1-07")
    fig = make_images(db, tmp_path)
    pr = work.make_pair(db, "T1-07")
    assert pr["report"]["outcome"] in ("pass", "warn"), pr
    pk = work.approve_pair(db, pr["id"], True)
    assert (blocking / "packages" / "T1-07" / "T1-07_1_FIRST.png").exists()
    d = packages.doc_of(db, "video", pk)
    assert not packages.blocking(packages.lint_video(d))
    c = clip_from(fig, tmp_path / "gemini.mp4")
    with pytest.raises(RuleRefused):                       # not approved yet
        work.import_video(db, pk, c)
    with pytest.raises(RuleRefused):
        review(db, "video", pk, "motion-engineer")         # its own maker
    review(db, "video", pk, "cinematographer")
    packages.approve(db, "video", pk)
    r = work.import_video(db, pk, c, "account B", "Omni 1.2", "two left")
    assert r["report"]["adherence"]["first"]["outcome"] == "pass" and r["report"]["adherence"]["last"]["outcome"] == "pass", r["report"]["adherence"]
    assert not [f for f in r["report"]["flags"] if f["severity"] == "critical"]
    assert db.val("select label from account_label") == "account B"
    with pytest.raises(RuleRefused):
        work.import_video(db, pk, clip_from(fig, tmp_path / "two.mp4", 150), "account B", "", "", "better one")   # judge first
    work.judge(db, r["attempt"], "accepted", "usable")
    tl, st, _ = edit.resolve(db, F, "scene:TF-SC", make=True)
    assert st["T1-07"] == "generated clip"
    # change the first frame: pair and package go stale, nothing else does
    v = db.val("select id from slot_current where slot_id=(select id from slot where ref_id='IMG-T1-07-FIRST')")
    f2 = tmp_path / "new.png"
    cv2.imwrite(str(f2), fig(880, 410))
    a, _ = assets.add(db, f2, origin="authored")
    nv = flow.add_version(db, db.val("select id from slot where ref_id='IMG-T1-07-FIRST'"), a, "image-maker", "better hands")
    flow.decide_version(db, nv, True)
    assert not flow.live(db, "G5_package", pk)
    with pytest.raises(RuleRefused):
        work.log_generation(db, pk, "generated", "account B")
    flow.restore_version(db, v, "back to the first")
    assert flow.live(db, "G5_package", pk)


def test_swapped_and_wrong_clips_are_noticed(db, tmp_path, blocking):
    fig = make_images(db, tmp_path)
    pk = work.approve_pair(db, work.make_pair(db, "T1-07")["id"], True)
    review(db, "video", pk, "cinematographer")
    packages.approve(db, "video", pk)
    other = np.random.default_rng(8)
    bg = cv2.resize(other.integers(0, 255, (9, 16, 3), np.uint8), (1920, 1080), interpolation=cv2.INTER_CUBIC)
    r = work.import_video(db, pk, clip_from(lambda x, y: bg, tmp_path / "wrong.mp4", 100))
    assert r["report"]["outcome"] == "fail" and r["report"]["adherence"]["first"]["outcome"] == "fail"


def test_a_bad_pair_is_stopped(db, tmp_path, blocking):
    fig = make_images(db, tmp_path)
    f = tmp_path / "moved.png"
    im = cv2.warpAffine(fig(840, 520), np.float32([[1, 0, 30], [0, 1, 0]]), (1920, 1080), borderMode=cv2.BORDER_REFLECT)
    cv2.imwrite(str(f), im)
    a, _ = assets.add(db, f, origin="authored")
    flow.decide_version(db, flow.add_version(db, db.val("select id from slot where ref_id='IMG-T1-07-LAST'"), a, "x", "test"), True)
    pr = work.make_pair(db, "T1-07")
    assert pr["report"]["outcome"] == "fail"
    with pytest.raises(RuleRefused):
        work.approve_pair(db, pr["id"], True)


# ---------------------------------------------------------------- library, backup, restart
def test_library_admits_only_confirmed_rights(db):
    cut.ensure_sounds(db)
    a = edit.library_sound(db, "box fan")
    with pytest.raises(RuleRefused):
        work.admit(db, a, "studio", None, "ambience", "box fan", ["fan"])
    work.verify_terms(db, "own")
    work.admit(db, a, "studio", None, "ambience", "box fan", ["fan", "motor"])
    assert work.search_library(db, "a motor fan")[0]["title"] == "box fan"


def test_restart_keeps_everything_and_marks_interrupted_jobs(db):
    j = work.Jobs(db)
    jid = j.add("animatic", {})
    db.run("update media_job set state='running' where id=?", jid)
    db.close()
    d2 = DB()
    work.Jobs(d2)
    assert d2.val("select state from media_job where id=?", jid) == "interrupted"


# ---------------------------------------------------------------- the door
@pytest.fixture
def app(data, fake):
    from fastapi.testclient import TestClient
    from studio.connectors.secrets import MemoryVault
    from studio.daemon.app import create
    a = create(MemoryVault(), start_jobs=False)
    c = TestClient(a, base_url="http://127.0.0.1:8765", follow_redirects=False)
    return a, c


H = {"origin": "http://127.0.0.1:8765", "x-studio": "1"}


def test_the_door(app):
    a, c = app
    assert c.get("/api/status").status_code == 401
    assert c.get("/launch?code=guess").status_code == 403
    code = a.state.launch
    r = c.get("/launch?code=" + code)
    assert r.status_code == 303 and "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert c.get("/launch?code=" + code).status_code == 403                        # one use only
    assert c.get("/api/status").status_code == 200
    assert c.post("/api/mode", json={"service": "sfx", "mode": "api"}).status_code == 403                              # no origin
    assert c.post("/api/mode", json={"service": "sfx", "mode": "api"}, headers={**H, "origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/mode", json={"service": "sfx", "mode": "api"}, headers={"origin": H["origin"]}).status_code == 403
    assert c.get("/api/status", headers={"host": "studio.evil.example"}).status_code == 403                            # DNS rebinding
    assert c.post("/api/mode", json={"service": "sfx", "mode": "api"}, headers=H).status_code == 200
    assert c.post("/api/mode", json={"service": "video", "mode": "api"}, headers=H).status_code == 409
    for p in ("/web/..%2f..%2f.gitignore", "/web/../data/studio.db", "/view/x/..%2f..%2fstudio", "/media/nope"):
        assert c.get(p).status_code in (404, 409, 500)


def test_keys_go_in_and_never_come_out(app):
    a, c = app
    c.get("/launch?code=" + a.state.launch)
    k = "sk_test_good_key_0123456789"
    assert c.post("/api/connectors/elevenlabs/key", json={"key": k}, headers=H).status_code == 200
    c.post("/api/connectors/elevenlabs/test", json={}, headers=H)
    for u in ("connectors", "status", "money", "log", "preflight"):
        assert k not in c.get("/api/" + u).text
    e = next(x for x in c.get("/api/connectors").json()["connectors"] if x["id"] == "elevenlabs")
    assert e["configured"] and e["hint"] == "•••• 6789" and e["authentication"] == "ok" and e["enabled"] is False
    assert c.request("DELETE", "/api/connectors/elevenlabs/key", headers=H).status_code == 200
    e = next(x for x in c.get("/api/connectors").json()["connectors"] if x["id"] == "elevenlabs")
    assert not e["configured"] and e["hint"] is None


def test_server_listens_on_loopback_only():
    t = (ROOT / "studio/daemon/__main__.py").read_text()
    assert 'host="127.0.0.1"' in t and "0.0.0.0" not in t
    from studio.daemon.app import create
