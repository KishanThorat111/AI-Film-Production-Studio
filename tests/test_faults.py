"""Things that go wrong outside the program: full disk, locked files, lost graphics card, killed process, two windows at once."""
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from studio.core import assets, cut, edit, flow, packages, work
from studio.core.db import DB, RuleRefused
from studio.engine import render
from tests.conftest import ROOT, review
from tests.test_workflow import cast, tone

F, SC = "TESTFILM", "scene:TF-SC"


class J:
    def __init__(self):
        self.said = []

    def progress(self, *a):
        self.said.append(a[-1])

    def stopping(self, *a):
        return False


@pytest.mark.parametrize("err,word", [(OSError(28, "No space left on device"), "disk is full"), (PermissionError(13, "Permission denied"), "Windows refused")])
def test_an_import_that_cannot_be_written_leaves_nothing(db, data, tmp_path, monkeypatch, err, word):
    p = tone(tmp_path / "a.wav")

    def no(*a, **k):
        open(a[1], "wb").write(b"half")
        raise err
    monkeypatch.setattr(shutil, "copyfile", no)
    n = db.val("select count(*) from asset")
    with pytest.raises(assets.ImportProblem, match=word):
        assets.add(db, p, origin="manual_audio")
    assert db.val("select count(*) from asset") == n and not list((data / "media").rglob("*.part")) and p.exists()


def test_a_lost_graphics_device_is_opened_again_once(db, blocking, monkeypatch):
    cut.ensure_sounds(db)
    tl, _, _ = edit.resolve(db, F, SC, make=True)
    tl["size"], tl["frames"] = [320, 180], 200
    for t in tl["video"]:
        t["clips"] = [c for c in t["clips"] if c["at"] < 200]
        t["clips"][-1]["dur"] = 200 - t["clips"][-1]["at"]
    calls, real = [], render.Renderer.frame

    def frame(self, n):
        calls.append(n)
        if len(calls) == 130:
            raise RuntimeError("Device lost")
        return real(self, n)
    monkeypatch.setattr(render.Renderer, "frame", frame)
    monkeypatch.setattr(cut.time, "sleep", lambda s: None)
    j = J()
    r, segs = cut.picture(tl, lambda a: edit._path(db, a), j, "j")
    assert sum(render.count_frames(s) for s in segs) == 200 and calls.count(0) == 1        # the finished segment was kept
    assert any("lost" in str(x) for x in j.said)


def test_the_process_is_killed_while_it_renders(tmp_path):
    """Hard kill; the next start finds a whole database, an interrupted job, and the finished segments."""
    data = tmp_path / "d"
    code = ("import sys,threading,time\nsys.path.insert(0,'.')\nfrom studio.core.db import DB\nfrom studio.core import base,films,edit,cut\nfrom studio.core.jobs import Jobs\n"
            "db=DB();base.base(db);f=films.create(db,'Kill me',film_id='K')\ndb.insert('scene',id='K1',film_id=f,number=1,slug='s')\n"
            "edit.apply(db,f,'scene:K1',0,[{'op':'place','clip':{'id':f'c{i}','source':{'card':f'Card {i}'},'dur':200,'reason':'card'},'reason':'card'} for i in range(12)],'user')\n"
            "j=Jobs(db);j.handlers['render']=lambda jid,a:cut.render_job(db,j,jid,a);j.start();j.add('render',{'film':f,'scope':'scene:K1','kind':'review'},ram=0,disk=0)\ntime.sleep(300)\n")
    env = {**os.environ, "STUDIO_DATA": str(data)}
    p = subprocess.Popen([sys.executable, "-c", code], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    seg, t0 = data / "cache" / "segments", time.time()
    while time.time() - t0 < 120 and len([x for x in seg.glob("*.mkv") if ".part" not in x.name] if seg.exists() else []) < 2:
        assert p.poll() is None, p.stderr.read()[-800:]
        time.sleep(.2)
    p.kill()
    p.wait()
    done = {x.name for x in seg.glob("*.mkv") if ".part" not in x.name}
    assert len(done) >= 2
    os.environ["STUDIO_DATA"] = str(data)
    try:
        db = DB(check=True)
        jobs = work.Jobs(db)
        row = db.one("select id,state from media_job")
        assert row["state"] == "interrupted"
        tl, _, _ = edit.resolve(db, "K", "scene:K1", make=True)
        r = render.Renderer(tl, lambda a: edit._path(db, a))
        r.render(seg)
        assert r.rendered <= tl["frames"] - 96 * 2 and done <= {x.name for x in seg.glob("*.mkv")}
        jobs.resume(row["id"])
        assert db.val("select state from media_job") == "queued"
        db.close()
    finally:
        os.environ.pop("STUDIO_DATA")


def test_two_windows_decide_at_the_same_moment(db):
    p = db.val("select id from sfx_package where cue_id='CUE-CLOTH'")
    review(db, "sfx", p, "story-editor")
    out = []

    def go():
        x = DB(db.path)
        try:
            packages.approve(x, "sfx", p)
            out.append("ok")
        except (RuleRefused, sqlite3.OperationalError) as e:
            out.append(str(e))
    T = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in T]
    [t.join() for t in T]
    assert "ok" in out and flow.live(db, "G9_sfx_package", p) and db.val("select count(*) from manual_task where package_id=?", p) == 1


def test_the_same_take_dropped_twice_at_once_counts_once(db, tmp_path):
    cast(db)
    p = db.val("select id from voice_package where series='TF-VP-NARR'")
    review(db, "voice", p, "story-editor")
    packages.approve(db, "voice", p)
    f, out = tone(tmp_path / "a.wav"), []

    def go():
        try:
            work.import_voice(DB(db.path), p, "T1-N1", f)
            out.append("ok")
        except Exception as e:
            out.append(type(e).__name__)
    T = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in T]
    [t.join() for t in T]
    assert out.count("ok") == 1 and len(flow.counted(db, db.val("select id from generation_target where ref_id='T1-N1'"))) == 1 and not set(out) - {"ok", "RuleRefused"}, out


def test_a_held_database_is_explained(data):
    DB().close()
    c = sqlite3.connect(data / "studio.db", isolation_level=None)
    c.execute("pragma journal_mode=delete")
    c.execute("begin exclusive")
    from studio.core import migrate
    real = sqlite3.connect
    migrate.sqlite3.connect = lambda *a, **k: real(*a, **{**k, "timeout": .2})
    try:
        with pytest.raises(migrate.SchemaProblem, match="holding the database"):
            DB()
    finally:
        migrate.sqlite3.connect = real
        c.execute("rollback")
