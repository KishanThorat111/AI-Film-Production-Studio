"""Nothing may be lost: migrations, damage, copies, the way back."""
import json
import os
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest

from studio.core import assets, backup, migrate
from studio.core.db import DB, now
from tests.conftest import ROOT
from tests.test_workflow import tone


def p0(path):
    """A database exactly as P0 made it."""
    _, _, raw = migrate.steps()[0]
    c = sqlite3.connect(path)
    c.executescript(raw.decode("utf-8"))
    c.execute("create table meta(key text primary key, value text not null)")
    c.execute("insert into meta values('schema_sha',?)", (migrate._sha(raw),))
    c.execute("insert into universe values('u','U')")
    c.commit()
    c.close()


def test_a_p0_database_is_brought_forward_and_a_copy_is_kept(data):
    p0(data / "studio.db")
    d = DB()
    assert d.migration["from"] == 1 and d.migration["to"] == len(migrate.steps())
    assert d.val("select count(*) from universe") == 1 and d.val("select count(*) from backup_target") == 0
    assert Path(d.migration["copy"]).exists()
    d.close()
    assert DB().migration["copy"] is None                   # the second start changes nothing


def test_old_and_new_databases_have_the_same_shape(data, tmp_path):
    p0(data / "studio.db")

    def shape(d):
        return sorted((r["type"], r["name"], " ".join(r["sql"].split())) for r in d.q("select type,name,sql from sqlite_master where sql is not null and name not like 'sqlite_%' and name not like 'lesson_fts_%'"))
    assert shape(DB()) == shape(DB(tmp_path / "other.db"))


def extended(tmp_path, sql):
    f = tmp_path / "steps"
    shutil.copytree(migrate.FOLDER, f)
    (f / f"{len(migrate.steps()) + 1:04d}_next.sql").write_text(sql)
    return f


def test_a_failing_step_changes_nothing(data, tmp_path):
    p0(data / "studio.db")
    f = extended(tmp_path, "CREATE TABLE fine(a);\nINSERT INTO nowhere VALUES(1);\n")
    n = "select count(*) from sqlite_master"
    before = sqlite3.connect(data / "studio.db").execute(n).fetchone()
    with pytest.raises(sqlite3.OperationalError):
        migrate.migrate(data / "studio.db", now(), f)
    assert sqlite3.connect(data / "studio.db").execute(n).fetchone() == before


def test_a_step_that_breaks_links_is_refused(data, tmp_path):
    p0(data / "studio.db")
    f = extended(tmp_path, "INSERT INTO film(id,universe_id,title,fps_num,width,height,status) VALUES('f','nobody','t',24,1,1,'development');\n")
    with pytest.raises(migrate.SchemaProblem):
        migrate.migrate(data / "studio.db", now(), f)


def test_edited_steps_and_newer_databases_are_refused(data, tmp_path):
    DB().close()
    f = tmp_path / "steps"
    shutil.copytree(migrate.FOLDER, f)
    (f / "0002_backup.sql").write_text("-- changed\n")
    with pytest.raises(migrate.SchemaProblem, match="edited"):
        migrate.migrate(data / "studio.db", now(), f)
    g = tmp_path / "few"
    g.mkdir()
    shutil.copy(migrate.FOLDER / "0001_baseline.sql", g)
    with pytest.raises(migrate.SchemaProblem, match="newer"):
        migrate.migrate(data / "studio.db", now(), g)


@pytest.mark.parametrize("damage", ["empty", "cut", "page", "other"])
def test_damage_is_noticed_and_explained(data, damage):
    d = DB()
    for i in range(400):
        d.event("system", "x", "y", str(i), {"pad": "z" * 200})
    d.close()
    c = sqlite3.connect(data / "studio.db")
    c.execute("pragma wal_checkpoint(truncate)")
    c.execute("pragma journal_mode=delete")
    c.close()
    b = (data / "studio.db").read_bytes()
    new = {"empty": b"", "cut": b[: len(b) // 3], "page": b[:-8192] + os.urandom(8192), "other": b"hello" * 5000}[damage]
    (data / "studio.db").write_bytes(new)
    with pytest.raises(migrate.Damaged):
        DB(check=True)


def put(db, data, tmp_path):
    aid, _ = assets.add(db, tone(tmp_path / "a.wav"), origin="manual_audio")
    (data / "blocking" / "S1").mkdir(parents=True)
    (data / "blocking" / "S1" / "0000.png").write_bytes(b"picture")
    (data / "cache").mkdir(exist_ok=True)
    (data / "cache" / "x").write_bytes(b"rebuildable")
    return aid


def test_copy_restore_compare(db, data, tmp_path):
    aid = put(db, data, tmp_path)
    root = tmp_path / "other-drive"
    t = backup.add_target(db, str(root), "test drive")
    r = backup.run(db, t)
    assert r["state"] == "verified", r
    man = json.loads((root / "sets" / r["set_name"] / "MANIFEST.json").read_text())
    assert "blocking/S1/0000.png" in man["files"] and not any(f.startswith("cache") for f in man["files"]) and man["code"]
    assert backup.verify_set(root, r["set_name"]) == []
    assert backup.restore(root, r["set_name"], tmp_path / "back")["outcome"] == "equal"
    d = DB(tmp_path / "back" / "studio.db", check=True)
    assert (tmp_path / "back" / "media" / d.val("select relpath from asset where id=?", aid)).read_bytes() == assets.path_of(db, aid).read_bytes()
    with pytest.raises(backup.BackupProblem, match="restored copy"):
        backup.run(d, t)                                              # a restored studio leaves the old copies alone
    assert backup.due(d) == [] and backup.state(d)["restored"]
    backup.adopt(d)
    assert backup.run(d, t)["state"] == "verified"
    with pytest.raises(backup.BackupProblem):
        backup.restore(root, r["set_name"], tmp_path / "back")       # never over an existing studio
    dr = backup.drill(db, t, tmp_path)
    assert dr["outcome"] == "equal" and dr["code"] and not list(tmp_path.glob("restore-drill*"))
    assert backup.state(db)["targets"][0]["drill"]["outcome"] == "equal"


def test_a_damaged_copy_is_noticed(db, data, tmp_path):
    put(db, data, tmp_path)
    t = backup.add_target(db, str(tmp_path / "o"), "o")
    r = backup.run(db, t)
    obj = next((tmp_path / "o" / "objects").rglob("*.png"))
    obj.write_bytes(b"picturE")
    assert backup.verify_set(tmp_path / "o", r["set_name"]) == ["blocking/S1/0000.png"]
    assert backup.drill(db, t, tmp_path)["outcome"] == "different"


def test_a_missing_drive_waits(db):
    t = backup.add_target(db, "Q:/nowhere/studio", "unplugged")
    assert backup.run(db, t)["state"] == "unavailable" and backup.due(db) == []


def test_a_full_drive_leaves_no_half_set(db, data, tmp_path, monkeypatch):
    put(db, data, tmp_path)
    t = backup.add_target(db, str(tmp_path / "full"), "full")
    real = shutil.copyfile

    def full(a, b, *k, **kw):
        if "objects" in str(b):
            raise OSError(28, "No space left on device")
        return real(a, b, *k, **kw)
    monkeypatch.setattr(shutil, "copyfile", full)
    r = backup.run(db, t)
    assert r["state"] == "failed" and "space" in r["error"] and not list((tmp_path / "full" / "sets").iterdir())


def test_a_damaged_original_is_not_recorded_as_protected(db, data, tmp_path):
    p = assets.path_of(db, put(db, data, tmp_path))
    t = backup.add_target(db, str(tmp_path / "third"), "third")
    os.chmod(p, stat.S_IWRITE)
    p.write_bytes(p.read_bytes()[:-1] + b"x")
    r = backup.run(db, t)
    assert r["state"] == "failed" and "matches" in r["error"]


def test_due_and_thinning(db, data, tmp_path):
    t = backup.add_target(db, str(tmp_path / "o"), "o")
    assert backup.due(db) == [t]
    backup.run(db, t, with_code=False)
    assert backup.due(db) == []
    db.run("insert into event(at,actor,verb,target_type,target_id,payload_json) values(datetime('now','-2 hours'),'user','gate.approved','x','y','{}')")
    assert backup.due(db) == [t]
    for d in range(60):
        n = f"2026{1 + d // 28:02d}{1 + d % 28:02d}-120000"
        (tmp_path / "o" / "sets" / n).mkdir()
        (tmp_path / "o" / "sets" / n / "MANIFEST.json").write_text("{}")
    backup.prune(tmp_path / "o")
    assert 8 <= len(backup.sets(tmp_path / "o")) <= 18


def test_copies_inside_the_project_are_refused(db):
    with pytest.raises(backup.BackupProblem):
        backup.add_target(db, str(ROOT / "data" / "copy"), "x")
