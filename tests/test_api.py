"""The local server: plain refusals, no dead ends, no silent size limits, several films."""
import os
import stat

import pytest

from tests.conftest import ROOT, review
from tests.test_workflow import cast, tone

H = {"origin": "http://127.0.0.1:8765", "x-studio": "1"}


@pytest.fixture
def site(data, fake):
    from fastapi.testclient import TestClient
    from studio.connectors.secrets import MemoryVault
    from studio.daemon.app import create
    a = create(MemoryVault(), start_jobs=False)
    c = TestClient(a, base_url="http://127.0.0.1:8765", follow_redirects=False, raise_server_exceptions=False)
    c.get("/launch?code=" + a.state.launch)
    return a, c


def test_every_page_answers_for_every_film(site):
    a, c = site
    f = c.post("/api/films", json={"title": "Empty film"}, headers=H).json()["id"]
    for film in (f, "TESTFILM"):
        for u in ("status", "script", "docs", "shots", "cut", "images", "voice", "sfx", "video", "review", "learning", "agents", "money", "release", "preflight"):
            r = c.get(f"/api/{u}?film={film}")
            assert r.status_code == 200, (film, u, r.text[:300])
    for u in ("films", "connectors", "library", "log", "jobs", "backup", "health", "machine"):
        assert c.get(f"/api/{u}").status_code == 200, u


@pytest.mark.parametrize("url,body,code", [
    ("/api/mode", {}, 400), ("/api/money/credits", {"service": "video", "amount": 5}, 409), ("/api/money/credits", {"service": "sfx", "amount": "abc"}, 400), ("/api/connectors/elevenlabs/key", {}, 400),
    ("/api/connectors/elevenlabs/key", {"key": "short"}, 409), ("/api/make", {"action": "make_everything"}, 409), ("/api/cut/commands", {"scope": "x"}, 400), ("/api/docs/nothing/approve", {}, 409),
    ("/api/cut/approve", {"id": "nothing"}, 409), ("/api/package/film/x/approve", {}, 400), ("/api/films", {"title": ""}, 400), ("/api/money/allowance", {"kind": "image", "usd": "lots"}, 400), ("/api/money/allowance", {"kind": "image", "usd": 11}, 409)])
def test_wrong_input_is_explained(site, url, body, code):
    r = site[1].post(url, json=body, headers=H)
    assert r.status_code == code and len(r.json()["error"]) > 5 and "not found" != r.json()["error"], r.text


def test_internal_errors_say_so_and_carry_a_number(site, monkeypatch, data):
    a, c = site
    from studio.core import gates

    def boom(*x):
        raise KeyError("secret internals")
    monkeypatch.setattr(gates, "status", boom)
    r = c.get("/api/status")
    assert r.status_code == 500 and r.json()["ref"] in (data / "errors.log").read_text() and "internals" not in r.text


def test_unreadable_and_oversized_bodies(site):
    c = site[1]
    assert c.post("/api/mode", content=b"not json", headers={**H, "content-type": "application/json"}).status_code == 400

    def big():
        for _ in range(12):
            yield b" " * 2 ** 20
    assert c.post("/api/mode", content=big(), headers={**H, "content-type": "application/json"}).status_code == 413


def test_uploads_leave_nothing_behind(site, data, tmp_path):
    a, c = site
    db = a.state.db
    p = db.val("select id from voice_package where series='TF-VP-NARR'")
    cast(db)
    review(db, "voice", p, "story-editor")
    assert c.post(f"/api/package/voice/{p}/approve", json={}, headers=H).status_code == 200
    r = c.post(f"/api/voice/{p}/T1-N1/import", files={"file": ("take.wav", tone(tmp_path / "t.wav").read_bytes())}, headers=H)
    assert r.status_code == 200, r.text
    r = c.post(f"/api/voice/{p}/T1-N2/import", files={"file": ("bad.wav", b"not sound" * 50)}, headers=H)
    assert r.status_code == 409 and not list((data / "inbox").glob("*"))


def test_nothing_is_generated_without_an_agreed_price(site, fake):
    a, c = site
    one = a.state.db.val
    for u, b in (("/api/images/IMG-LEAD-ANCHOR/generate", {"provider": "google_images"}), (f"/api/sfx/{one('select id from sfx_package limit 1')}/generate", {}), (f"/api/voice/{one('select id from voice_package limit 1')}/T1-N1/generate", {"voice_id": "x"})):
        r = c.post(u, json=b, headers=H)
        assert r.status_code == 400 and "price" in r.json()["error"], (u, r.text)
    assert fake.gen == 0


def test_a_lost_file_is_named_and_pages_still_open(site, data):
    a, c = site
    for f in (data / "media" / "originals").rglob("*.wav"):
        os.chmod(f, stat.S_IWRITE)
        f.unlink()
    assert c.get("/api/status").status_code == 200 and c.get("/api/cut").status_code == 200
    aid = a.state.db.val("select id from asset where kind='audio' limit 1")
    r = c.get(f"/media/{aid}")
    assert r.status_code == 404 and "restore" in r.json()["error"]
    assert any(i["name"] == "Files" and i["state"] == "bad" for i in c.get("/api/health").json()["items"])


def test_script_to_first_assembly_through_the_server(site):
    a, c = site
    c.post("/api/films", json={"title": "Low Water"}, headers=H)
    src = (ROOT / "benchmark/lowwater/script.fountain").read_bytes()
    pv = c.post("/api/script/preview", files={"file": ("script.fountain", src)}, headers=H).json()
    assert pv["layout"] == "screenplay" and len(pv["scenes"]) == 2 and set(pv["speakers"]) == {"NORA", "TOMAS", "NARRATOR"}
    assert c.post("/api/script/import", files={"file": ("script.fountain", src)}, headers=H).status_code == 200
    s = c.get("/api/script").json()
    assert [x["number"] for x in s["scenes"]] == [1, 2] and s["doc"]["original_name"] == "script.fountain"
    m = {x["action"] + x["scope"]: x for x in c.get("/api/docs").json()["next"]}
    assert m["make_planfilm"]["ready"] and not m["make_canonfilm"]["ready"] and "plan" in m["make_canonfilm"]["why"]
    assert c.post("/api/make", json={"action": "make_canon"}, headers=H).status_code == 409
    assert c.post("/api/make", json={"action": "make_plan", "note": "keep the mother calm"}, headers=H).status_code == 200
    assert c.post("/api/make", json={"action": "make_plan"}, headers=H).status_code == 409          # already waiting
    assert any(n["kind"] == "backup" for n in c.get("/api/status").json()["needs_you"])


def test_static_files_stay_inside_the_web_folder(site):
    c = site[1]
    for p in ("/web/../studio/core/db.py", "/web/..%2fstudio%2fcore%2fdb.py", "/web/%2e%2e/requirements.lock.txt"):
        assert c.get(p).status_code in (404, 400), p


def test_previs_pictures_stay_inside_their_folder(site):
    c = site[1]
    for p in ("/previs/TESTFILM/..%2f..%2f/studio.db", "/previs/TESTFILM/x/..%2f..%2fstudio.db"):
        assert c.get(p).status_code in (404, 400), p
