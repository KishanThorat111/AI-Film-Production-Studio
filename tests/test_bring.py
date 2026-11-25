"""Bringing files in: every kind, the right and the wrong ones, and whether the accepted one really reaches the edit."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import cv2
import numpy as np
import pytest
import soundfile as sf

from studio.core import assets, bring, common, edit, flow, gates, images, motion, packages, voice, work
from studio.core.db import RuleRefused
from tests.conftest import ROOT, review
from tests.test_ui import nav, page, shot, site, toast          # noqa: F401  (fixtures)
from tests.test_workflow import cast, clip_from, make_images, vpkg

FILM = "TESTFILM"


def tone(p, seconds=2.0, f=220):
    t = np.arange(int(48000 * seconds)) / 48000
    sf.write(str(p), (.2 * np.sin(2 * np.pi * f * t) * np.minimum(1, t * 8)).astype(np.float32), 48000)
    return p


def photo(p, seed=1):
    r = np.random.default_rng(seed)
    cv2.imwrite(str(p), cv2.resize(r.integers(0, 255, (45, 80, 3), np.uint8), (1920, 1080), interpolation=cv2.INTER_CUBIC))
    return p


def staged(db, p):
    import shutil
    q = p.with_name("up_" + p.name)
    shutil.copyfile(p, q)
    return bring.stage(db, FILM, q, p.name)


# ------------------------------------------------------------ rules
def test_a_refused_file_can_be_brought_in_again(db, tmp_path):
    """Refused for a missing reason: the file must not be 'already imported' the second time."""
    a, b = photo(tmp_path / "a.png", 1), photo(tmp_path / "b.png", 2)
    code = images.specs(db, FILM)[0]["code"]
    r = work.import_image(db, FILM, code, a)
    with pytest.raises(RuleRefused, match="judge the previous"):
        work.import_image(db, FILM, code, b)
    work.judge(db, r["attempt"], "rejected", "wrong person")
    with pytest.raises(RuleRefused, match="what was wrong"):
        work.import_image(db, FILM, code, b)
    work.import_image(db, FILM, code, b, reason="a different person, same light")
    with pytest.raises(RuleRefused, match="judge the previous"):
        work.import_image(db, FILM, code, a)
    other = next(s["code"] for s in images.specs(db, FILM) if s["code"] != code)
    with pytest.raises(RuleRefused, match="already been imported .*attempt 1"):
        work.import_image(db, FILM, other, a)


def test_unsupported_and_mislabelled_files(db, tmp_path):
    (tmp_path / "notes.pdf").write_bytes(b"%PDF-1.7")
    (tmp_path / "fake.mp4").write_bytes(b"this is not a clip" * 50)
    (tmp_path / "empty.wav").write_bytes(b"")
    with pytest.raises(assets.ImportProblem, match="not supported"):
        staged(db, tmp_path / "notes.pdf")
    with pytest.raises(assets.ImportProblem, match="cannot be read"):
        staged(db, tmp_path / "fake.mp4")
    with pytest.raises(assets.ImportProblem):
        staged(db, tmp_path / "empty.wav")
    silent = tmp_path / "only_sound.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=2", "-c:a", "aac", str(silent)], check=True)
    with pytest.raises(assets.ImportProblem, match="no picture"):
        motion.import_clip(db, FILM, db.val("select id from shot limit 1"), silent, reason="made on a phone as a test")
    assert bring.waiting(db, FILM) == [] and db.val("select count(*) from attempt") == 0


def test_suggestions_by_name_words_and_first_frame(db, tmp_path, blocking, monkeypatch):
    from studio.inspect import audio as ia
    m = staged(db, photo(tmp_path / "img lead.png"))
    code = images.specs(db, FILM)[0]["code"]
    m = staged(db, photo(tmp_path / f"final_{code}_v2.png", 5))
    assert m["to"][0]["id"] == f"image:{code}" and "file name" in m["to"][0]["why"]
    p = vpkg(db)
    lines = packages.doc_of(db, "voice", p)["lines"]
    monkeypatch.setattr(ia, "transcribe", lambda q, *a, **k: {"text": lines[1]["text"], "words": []})
    m = staged(db, tone(tmp_path / "ElevenLabs_2026-11-25.wav"))
    assert m["to"][0]["id"] == f"line:{p}/{lines[1]['line_id']}" and "spoken" in m["to"][0]["why"]
    fig = make_images(db, tmp_path)
    m = staged(db, clip_from(fig, tmp_path / "gemini_generated.mp4", 60))
    best = next(x for x in m["to"] if x["id"].startswith("shotclip:T1-07"))
    assert best["score"] == max(x["score"] for x in m["to"]) and "first frame" in best["why"]
    assert len(bring.waiting(db, FILM)) == 4 and any(n["kind"] == "bring" for n in gates.needs_you(db, FILM))


def test_every_kind_reaches_its_place(db, tmp_path, blocking):
    """Placed from the waiting room with the checks of its own page; a refusal leaves the file waiting; nothing is in the film before."""
    code = next(s["code"] for s in images.specs(db, FILM) if s["kind"] == "anchor")
    f = staged(db, photo(tmp_path / "x.png", 3))
    n = db.val("select count(*) from asset")
    with pytest.raises(RuleRefused, match="credit"):
        bring.put(db, f["id"], f"image:{code}", {"terms": "cc_by"})
    assert bring.waiting(db, FILM) and db.val("select count(*) from asset") == n and not db.one("select 1 from attempt")
    out = bring.put(db, f["id"], f"image:{code}", {"terms": "cc_by", "credit": "Somebody, example.org"})
    assert common.about_file(db, out["asset"])["licence"]["id"] == "cc_by" and bring.waiting(db, FILM) == []
    f2 = staged(db, tone(tmp_path / "hum.wav", 3))
    with pytest.raises(RuleRefused, match="where this file comes from"):
        bring.put(db, f2["id"], "cue:CUE-HUM", {})
    out = bring.put(db, f2["id"], "cue:CUE-HUM", {"terms": "own", "title": "the hum of the real fridge"})
    assert db.val("select terms_id from asset_licence where asset_id=?", out["asset"]) == "own" and out["report"]["outcome"] in ("pass", "warn")
    c = next(c for c in voice.cues_state(db, FILM) if c["id"] == "CUE-HUM")
    assert c["attempts"][0]["outcome"] == "pending" and c["status"] != "approved effect"
    common.judge(db, out["attempt"], "accepted")
    assert edit.heard(db, FILM, {"cue": "CUE-HUM"}, False)[:2] == (out["asset"], "approved effect")
    f3 = staged(db, tone(tmp_path / "rain.wav", 2, 500))
    out = bring.put(db, f3["id"], "library", {"terms": "own", "title": "Rain on tin", "kind": "ambience"})
    assert not db.one("select 1 from library_item where asset_id=?", out["asset"])
    common.verify_terms(db, "own")
    common.admit(db, out["asset"], "studio", None, "ambience", "Rain on tin", ["rain"])
    voice.use_library(db, FILM, "CUE-ROOM", out["asset"])
    assert edit.heard(db, FILM, {"cue": "CUE-ROOM"}, False)[0] == out["asset"]
    with pytest.raises(RuleRefused, match="already"):
        staged(db, tmp_path / "rain.wav")
    with pytest.raises(RuleRefused, match="no longer waiting"):
        bring.put(db, f3["id"], "library", {"terms": "own"})


def test_music_terms_that_exclude_film_are_refused(db, tmp_path):
    cue = voice.add_cue(db, FILM, db.val("select id from scene where film_id=?", FILM), "music", "a low drone under the stairs")
    with pytest.raises(RuleRefused, match="do not allow"):
        voice.import_cue(db, FILM, cue, tone(tmp_path / "m.wav"), "elevenlabs_music")
    t = common.add_terms(db, "Band of a friend", "https://example.org/terms", {k: "yes" for k in ("store", "edit", "sync", "commercial", "youtube", "reuse_films")}, True)
    with pytest.raises(RuleRefused, match="credit"):
        voice.import_cue(db, FILM, cue, tmp_path / "m.wav", t)
    r = voice.import_cue(db, FILM, cue, tmp_path / "m.wav", t, credit="Music: Someone")
    assert common.about_file(db, r["asset"])["licence"]["attribution_text"] == "Music: Someone"
    with pytest.raises(RuleRefused):
        common.add_terms(db, "x", "", {}, False)


def test_another_version_replaces_the_first_everywhere(db, tmp_path):
    cue = "CUE-HUM"
    a = voice.import_cue(db, FILM, cue, tone(tmp_path / "a.wav", 2, 100), "own")
    common.judge(db, a["attempt"], "accepted")
    with pytest.raises(RuleRefused, match="another version"):
        voice.import_cue(db, FILM, cue, tone(tmp_path / "b.wav", 2, 130), "own")
    tid = db.val("select id from generation_target where ref_id=?", cue)
    flow.reopen(db, tid, "deeper please")
    assert edit.heard(db, FILM, {"cue": cue}, False)[0] == a["asset"]            # the first stays until the second is accepted
    b = voice.import_cue(db, FILM, cue, tmp_path / "b.wav", "own")
    common.judge(db, b["attempt"], "accepted")
    assert edit.heard(db, FILM, {"cue": cue}, False)[0] == b["asset"]
    v = db.val("select v.id from slot_version v where v.asset_id=?", a["asset"])
    flow.restore_version(db, v, "the first was better")
    assert edit.heard(db, FILM, {"cue": cue}, False)[0] == a["asset"]


def test_a_clip_without_a_package_is_marked_and_counts(db, tmp_path, blocking):
    shot_id = motion.other_shots(db, FILM)[0]["id"]
    c = clip_from(lambda x, y: np.full((1080, 1920, 3), 90, np.uint8), tmp_path / "own.mp4", 60)
    with pytest.raises(RuleRefused, match="how this clip was made"):
        motion.import_clip(db, FILM, shot_id, c)
    r = motion.import_clip(db, FILM, shot_id, c, reason="filmed with my own camera at night")
    assert r["report"]["outside_package"]
    common.judge(db, r["attempt"], "accepted")
    assert edit.picture(db, FILM, {"shot": shot_id}, 24, False)[1] == "generated clip"
    img = images.add_need(db, FILM, "MY-STILL", "frame", shot=shot_id, role="still")
    with pytest.raises(RuleRefused):
        images.add_need(db, FILM, img, "frame", shot=shot_id, role="still")
    with pytest.raises(RuleRefused):
        images.add_need(db, FILM, "X1", "reference", entity="nobody")


def test_wide_films_get_frames_the_app_can_make(db, tmp_path, blocking):
    """The app makes 16:9 or 9:16. A 21:9 film asks for 16:9 and crops in the edit instead of refusing or squeezing."""
    db.run("update film set width=1920,height=816 where id=?", FILM)
    make_images(db, tmp_path)
    pr = work.make_pair(db, "T1-07")
    pk = work.approve_pair(db, pr["id"], True)
    d = packages.doc_of(db, "video", pk)
    assert {(a["width"], a["height"]) for a in d["attachments"]} == {(1920, 1080)}
    assert not packages.blocking(packages.lint_video(d)), packages.lint_video(d)


def test_next_step_names_one_thing(db):
    n = gates.next_step(db, FILM)
    assert n["title"] and n["why"] and n["how"] and n["page"]
    for x in gates.needs_you(db, FILM):
        assert x["how"], x


# ------------------------------------------------------------ in the browser
def test_browser_bring_place_judge_and_cut(page, site, tmp_path):          # noqa: F811
    a, _ = site
    db = a.state.db
    nav(page, "Bring in files")
    inp = page.locator("main input[type=file]")
    (tmp_path / "letter.pdf").write_bytes(b"%PDF")
    inp.set_input_files([str(tone(tmp_path / "CUE-ROOM quiet room.wav", 3, 90)), str(tmp_path / "letter.pdf")])
    toast(page, "not supported")
    card = page.locator("section.card", has_text="CUE-ROOM quiet room.wav")
    card.wait_for(timeout=20000)
    assert card.locator("select[name=to]").input_value() == "cue:CUE-ROOM" and "file name fits" in card.inner_text()
    shot(page, "bring-waiting")
    card.get_by_role("button", name="Bring it in").click()
    toast(page, "where this file comes from")
    card.locator("select[name=terms]").select_option("own")
    card.get_by_role("button", name="Bring it in").click()
    toast(page, "waits for your judgement")
    page.get_by_text("No file is waiting").wait_for()
    nav(page, "Home")
    page.locator(".need", has_text="Purpose of CUE-ROOM").get_by_role("link", name="Open").click()
    row = page.locator("[id='CUE-ROOM']")
    row.wait_for()
    assert "aim" in row.get_attribute("class")
    row.get_by_role("button", name="Accept").click()
    toast(page, "Accepted")
    page.locator("[id='CUE-ROOM'] .tag", has_text="approved effect").wait_for()
    row.locator("summary", has_text="About this file").click()
    row.get_by_text("In use as").wait_for()
    assert "originals" in row.inner_text() and "approved effect" in row.inner_text()
    shot(page, "sound-filled")
    # the library, then into the edit by hand
    nav(page, "Library")
    page.locator("main").get_by_label("What it is").fill("Distant train")
    page.locator("main select[name=kind]").first.select_option("ambience")
    page.locator("main select[name=terms]").first.select_option("own")
    page.locator("main input[type=file]").set_input_files(str(tone(tmp_path / "train.wav", 4, 60)))
    page.get_by_text("Distant train").first.wait_for(timeout=20000)
    page.locator("tr", has_text="own recordings").get_by_role("button", name="I have read and confirm").click()
    page.locator("tr", has_text="own recordings").get_by_text("you confirmed").wait_for()
    page.locator("tr", has_text="Distant train").get_by_role("button", name="Admit").click()
    page.locator("main").get_by_text("Distant train").first.wait_for()
    nav(page, "Cut")
    page.locator("summary", has_text="Add a sound or a picture").click()
    v = page.locator("select[name=what] option", has_text="Distant train").get_attribute("value")
    page.locator("select[name=what]").select_option(v)
    page.locator("input[name=why]").first.fill("the world outside")
    page.get_by_role("button", name="Add the sound").click()
    page.locator(".lane button", has_text="Distant-train").wait_for(timeout=15000)
    tl, st, _ = edit.resolve(db, FILM, db.val("select scope from cut_rev order by rowid desc limit 1"))
    assert any(x["id"] == "Distant-train" and x["bus"] == "ambience" for x in tl["audio"])
    room = [x for x in tl["audio"] if x.get("cue") == "CUE-ROOM"]
    assert all(x["asset"] == db.val("select asset_id from slot_current c join slot s on s.id=c.slot_id where s.ref_id='CUE-ROOM'") for x in room)
    assert page.errors == []


def test_browser_gemini_clip_by_hand(page, site, tmp_path):          # noqa: F811
    a, _ = site
    db = a.state.db
    fig = make_images(db, tmp_path)
    nav(page, "Gemini video")
    card = page.locator("section[id='T1-07']")
    card.get_by_role("button", name="Check the pair").click()
    card.get_by_role("button", name="These are the frames").click()
    if page.locator("dialog[open]").count():
        page.locator("dialog[open] button.primary").click()
    page.wait_for_function("() => document.querySelector(\"section[id='T1-07']\").innerText.includes('Package')", timeout=15000)
    pk = db.val("select id from video_package order by rowid desc limit 1")
    for role in ("cinematographer", "continuity-inspector"):
        review(db, "video", pk, role)
    nav(page, "Home")
    nav(page, "Gemini video")
    card.get_by_role("button", name="Approve this package").click()
    card.locator("input[type=file]").wait_for(state="attached", timeout=15000)
    (tmp_path / "broken.mp4").write_bytes(b"nothing" * 99)
    card.locator("input[type=file]").set_input_files(str(tmp_path / "broken.mp4"))
    toast(page, "cannot be read")
    card.get_by_text("you can try again").wait_for()
    card.locator("input[type=file]").set_input_files(str(tone(tmp_path / "sound.wav")))
    toast(page, "not a video file")
    c = clip_from(fig, tmp_path / "gemini.mp4")
    before = c.read_bytes()
    card.get_by_label("Account label").fill("account A")
    card.locator("input[type=file]").set_input_files(str(c))
    card.get_by_role("link", name="1 clip: watch and judge").wait_for(timeout=90000)
    aid = db.val("select result_asset_id from attempt where result_asset_id is not null and target_id in(select id from generation_target where kind='video')")
    assert assets.path_of(db, aid).read_bytes() == before and not os.access(assets.path_of(db, aid), os.W_OK)
    nav(page, "Home")
    page.locator(".need", has_text="Gemini clip for").get_by_role("link", name="Open").click()
    one = page.locator(f"section[id='{aid}']")
    one.get_by_role("button", name="Good").click()
    one.get_by_text("your blind verdict").wait_for()
    one.get_by_role("button", name="Accept").click()
    toast(page, "Accepted")
    assert edit.picture(db, FILM, {"shot": "T1-07"}, 24, False)[:1][0]["asset"] == aid
    nav(page, "Gemini video")
    card.get_by_role("button", name="Ask for another version").wait_for()
    assert page.errors == []


def test_a_file_dropped_anywhere_is_not_opened_by_the_browser(page, site):          # noqa: F811
    nav(page, "Home")
    page.evaluate("""() => { const d = new DataTransfer(); d.items.add(new File([new Uint8Array(8)], 'x.pdf')); const e = new DragEvent('drop', { dataTransfer: d, bubbles: true, cancelable: true }); document.body.dispatchEvent(e); window.__stopped = e.defaultPrevented; }""")
    toast(page, "not supported")
    assert page.evaluate("window.__stopped") and page.locator("main h1").count() == 1


# ------------------------------------------------------------ the rest of the interface
def test_reveal_only_shows_files_of_the_studio(db, tmp_path, monkeypatch):
    seen = []
    aid, _ = assets.add(db, tone(tmp_path / "t.wav"), origin="recorded")
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: seen.append(cmd))
    (tmp_path / "outside.txt").write_text("x")
    with pytest.raises(assets.Missing):
        common.reveal(tmp_path / "outside.txt")
    common.reveal(assets.path_of(db, aid))
    assert len(seen) == 1 and "explorer" in seen[0]


# ------------------------------------------------------------ starting and stopping
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(env):
    return subprocess.Popen([sys.executable, "-m", "studio.daemon", "--no-browser"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")


def link(p, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        l = p.stdout.readline()
        if l.startswith("http://"):
            return l.strip()
        if not l and p.poll() is not None:
            raise AssertionError("ended early")
    raise AssertionError("no link")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def cookie(url):
    try:
        urllib.request.build_opener(NoRedirect).open(url)
    except urllib.error.HTTPError as e:
        assert e.code == 303, e.read()
        return e.headers["set-cookie"].split(";")[0]


def call(port, path, ck="", body=None):
    h = {"Cookie": ck, "Origin": f"http://127.0.0.1:{port}", "x-studio": "1", "Content-Type": "application/json"}
    with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(), headers=h), timeout=20) as r:
        return json.loads(r.read())


def test_start_again_opens_the_running_studio_and_stop_is_orderly(tmp_path):
    port = free_port()
    env = {**os.environ, "STUDIO_DATA": str(tmp_path / "d"), "STUDIO_PORT": str(port), "STUDIO_PROJECTS": str(tmp_path / "none"), "PYTHONUNBUFFERED": "1"}
    (tmp_path / "d").mkdir()
    p = start(env)
    try:
        first = cookie(link(p))
        assert call(port, "/api/films", first)["films"] == []                     # a fresh copy has no film and still works
        q = start(env)
        again = cookie(link(q))
        out, _ = q.communicate(timeout=20)
        assert q.returncode == 0 and "already running" in out
        assert call(port, "/api/films", again)["films"] == [] and call(port, "/api/films", first)["films"] == []
        with pytest.raises(urllib.error.HTTPError) as e:                          # nobody else gets in
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/reopen", data=b"guess", headers={"Origin": f"http://127.0.0.1:{port}", "x-studio": "1"}))
        assert e.value.code == 403
        assert call(port, "/api/stop", first, {})["stopping"]
        assert p.wait(20) == 0 and not (tmp_path / "d" / "run.json").exists()
    finally:
        p.kill()
    p = start(env)                                                                  # killed, as by a power cut, then started again
    try:
        link(p)
        p.kill()
        p.wait()
        assert (tmp_path / "d" / "run.json").exists()
        p = start(env)
        ck = cookie(link(p))
        assert call(port, "/api/films", ck)["films"] == []
    finally:
        p.kill()


def test_a_foreign_program_on_the_port_is_named(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        env = {**os.environ, "STUDIO_DATA": str(tmp_path), "STUDIO_PORT": str(s.getsockname()[1]), "PYTHONUNBUFFERED": "1"}
        p = start(env)
        out, _ = p.communicate(timeout=60)
        assert p.returncode == 1 and "Something else is using port" in out
