"""Verified copies of everything that cannot be made again, and the way back.

A copy is a *set*: a checked database, a manifest, the code, and references to content-named objects that are shared between sets.
Nothing here ever writes over an existing set, an existing object or an existing data folder. Keys are not in the database, so they are not in copies.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

from . import migrate
from .db import DB, ROOT, data_dir, new_id, now

EXPENSIVE = ("blocking", "agent-runs", "library", "sources")     # folders under data/ that cost hours or money to make again
KEEP = {"daily": 7, "weekly": 4, "monthly": 6}
DEFAULT = "E:/AIFilmStudio-Backup"


class BackupProblem(Exception):
    pass


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def available(path: Path) -> bool:
    """The drive is there. A missing drive is a waiting state, not a fault."""
    try:
        return Path(path.anchor).exists() if path.drive else path.parent.exists()
    except OSError:
        return False


def same_disk(path) -> bool:
    return Path(path).drive.upper() == data_dir().resolve().drive.upper()


def wanted(db: DB) -> list[tuple[str, Path]]:
    """(name inside the data folder, file). Originals, rendered media, sources, and the expensive folders."""
    out = {}
    for a in db.q("select relpath from asset where storage_class in('original','derived')"):
        out[f"media/{a['relpath']}"] = data_dir() / "media" / a["relpath"]
    for n in EXPENSIVE:
        d = data_dir() / n
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file():
                    out[f.relative_to(data_dir()).as_posix()] = f
    for f in data_dir().glob("review_*.jsonl"):
        out[f.name] = f
    if data_dir().resolve() == (ROOT / "data").resolve():        # what is never published must still be copied: private notes, scripts, their history
        for f in list((ROOT / "private").rglob("*")) + [x for e in ("*.docx", "*.pdf", "*.md") for x in ROOT.glob(e)]:
            if f.is_file() and "__pycache__" not in f.parts:
                out["project/" + f.relative_to(ROOT).as_posix()] = f
    return sorted(out.items())


def _object(target: Path, h: str, suffix: str) -> Path:
    return target / "objects" / h[:2] / h[2:4] / (h + suffix)


def _bundle(dst: Path) -> bool:
    """The code and its history. A missing git is reported, not fatal."""
    if not (ROOT / ".git").exists() or not shutil.which("git"):
        return False
    return subprocess.run(["git", "-C", str(ROOT), "bundle", "create", str(dst), "--all"], capture_output=True, text=True).returncode == 0


def _nothing(done, total, text=""):
    pass


def run(db: DB, target_id: str, progress=_nothing, with_code=True) -> dict:
    t = db.one("select * from backup_target where id=?", target_id)
    if not t:
        raise BackupProblem("unknown backup place")
    root, t0 = Path(t["path"]), time.time()

    def record(state, **kw):
        db.insert("backup_run", id=new_id("bk"), target_id=target_id, state=state, seconds=round(time.time() - t0, 1), made_at=now(), **kw)
        db.event("system", f"backup.{state}", "backup_target", target_id, kw)
        return {"state": state, "target": t["label"], **kw}

    if db.val("select value from meta where key='restored'"):
        raise BackupProblem("this studio runs on a restored copy; say on the Backup page that it is your studio before new copies are made")
    if not available(root):
        return record("unavailable", error="the drive is not connected")
    seq = db.val("select coalesce(max(seq),0) from event")
    part = None
    try:
        (root / "sets").mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S")
        while (root / "sets" / name).exists():
            time.sleep(1)
            name = time.strftime("%Y%m%d-%H%M%S")
        part = root / "sets" / (name + ".part")
        part.mkdir()
        src, dst = sqlite3.connect(db.path), sqlite3.connect(part / "studio.db")
        try:
            src.backup(dst)
            dst.execute("pragma journal_mode=delete")
            migrate.check(dst)
        finally:
            src.close()
            dst.close()
        files, missing, total, want = {}, [], 0, wanted(db)
        known, memo = {}, root / "known.json"                    # size and time -> hash, so unchanged files are not read again every day
        if memo.exists():
            try:
                known = json.loads(memo.read_text(encoding="utf-8"))
            except ValueError:
                known = {}
        for i, (rel, p) in enumerate(want):
            if not p.exists():
                missing.append(rel)
                continue
            st = p.stat()
            k = f"{rel}|{st.st_size}|{st.st_mtime_ns}"
            h = known.get(k) or sha(p)
            if rel.startswith("media/") and p.stem != h:
                raise BackupProblem(f"{rel} no longer matches its own name; it was changed or damaged")
            known[k] = h
            o = _object(root, h, p.suffix)
            if not (o.exists() and o.stat().st_size == st.st_size):
                o.parent.mkdir(parents=True, exist_ok=True)
                tmp = o.with_suffix(o.suffix + ".part")
                shutil.copyfile(p, tmp)
                if sha(tmp) != h:                                 # read back from the other drive
                    tmp.unlink()
                    raise BackupProblem(f"{rel} did not copy correctly")
                os.replace(tmp, o)
            files[rel] = {"sha256": h, "bytes": st.st_size, "ext": p.suffix}
            total += st.st_size
            if i % 25 == 0:
                progress(i, len(want), "copying")
        code = _bundle(part / "code.bundle") if with_code else False
        man = {"made": now(), "event_seq": seq, "database_sha256": sha(part / "studio.db"), "code": code, "code_sha256": sha(part / "code.bundle") if code else None,
               "missing": missing, "files": files, "counts": counts(part / "studio.db")}
        (part / "MANIFEST.json").write_text(json.dumps(man, indent=0), encoding="utf-8")
        memo.write_text(json.dumps({k: v for k, v in known.items() if k.split("|")[0] in files}), encoding="utf-8")
        part.rename(root / "sets" / name)
        part = None
        prune(root)
        return record("verified", set_name=name, files=len(files) + 1, bytes=total, event_seq=seq, error=f"{len(missing)} listed files are missing here: {missing[:3]}" if missing else None)
    except (OSError, sqlite3.DatabaseError, BackupProblem, migrate.SchemaProblem) as e:
        return record("failed", error=f"{type(e).__name__}: {e}"[:400])
    finally:
        if part is not None and part.exists():
            shutil.rmtree(part, ignore_errors=True)               # only our own unfinished folder


def counts(dbfile: Path) -> dict:
    c = sqlite3.connect(f"file:{dbfile.as_posix()}?mode=ro", uri=True)
    try:
        names = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' and name not like 'lesson_fts%' order by name")]
        return {n: c.execute(f'select count(*) from "{n}"').fetchone()[0] for n in names}
    finally:
        c.close()


def sets(root: Path) -> list[str]:
    d = root / "sets"
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.endswith(".part") and (p / "MANIFEST.json").exists()) if d.exists() else []


def prune(root: Path):
    """Old sets are thinned; objects and the newest sets are never removed."""
    names = sets(root)
    keep, seen = set(names[-3:]), {"daily": [], "weekly": [], "monthly": []}
    for n in reversed(names):
        t = time.strptime(n, "%Y%m%d-%H%M%S")
        for kind, key in (("daily", n[:8]), ("weekly", time.strftime("%Y-%W", t)), ("monthly", n[:6])):
            if key not in seen[kind] and len(seen[kind]) < KEEP[kind]:
                seen[kind].append(key)
                keep.add(n)
    for n in names:
        if n not in keep:
            shutil.rmtree(root / "sets" / n, ignore_errors=True)


def verify_set(root: Path, name: str, deep=True) -> list[str]:
    """What is wrong with this set on that drive. An empty list means complete."""
    d, out = root / "sets" / name, []
    man = json.loads((d / "MANIFEST.json").read_text(encoding="utf-8"))
    if sha(d / "studio.db") != man["database_sha256"]:
        out.append("the database copy changed")
    if man["code"] and sha(d / "code.bundle") != man["code_sha256"]:
        out.append("the code copy changed")
    for rel, f in man["files"].items():
        o = _object(root, f["sha256"], f["ext"])
        if not o.exists() or o.stat().st_size != f["bytes"] or (deep and sha(o) != f["sha256"]):
            out.append(rel)
    return out


def restore(root: Path, name: str, dest: Path, progress=_nothing) -> dict:
    """Into a folder that does not exist yet. Never over anything."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise BackupProblem("the folder for the restored studio must be new or empty")
    d = root / "sets" / name
    if not (d / "MANIFEST.json").exists():
        raise BackupProblem("no complete copy with that name exists there")
    man = json.loads((d / "MANIFEST.json").read_text(encoding="utf-8"))
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(d / "studio.db", dest / "studio.db")
    if sha(dest / "studio.db") != man["database_sha256"]:
        raise BackupProblem("the database copy does not match its record")
    bad = []
    for i, (rel, f) in enumerate(man["files"].items()):
        o, p = _object(root, f["sha256"], f["ext"]), dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(o, p)
            if sha(p) != f["sha256"]:
                bad.append(rel)
        except OSError:
            bad.append(rel)
        if i % 25 == 0:
            progress(i, len(man["files"]), "restoring")
    c = sqlite3.connect(dest / "studio.db")
    try:
        migrate.check(c)
    finally:
        c.close()
    same = counts(dest / "studio.db") == man["counts"]
    c = sqlite3.connect(dest / "studio.db")                        # a restored studio does not write into the old copies until the user says it is the one that counts
    try:
        c.execute("insert into meta(key,value) values('restored',?) on conflict(key) do update set value=excluded.value", (f"{name}, {man['made']}",))
        c.commit()
    finally:
        c.close()
    return {"files": len(man["files"]), "damaged": bad, "counts_equal": same, "missing_when_made": man["missing"], "outcome": "equal" if same and not bad else "different", "made": man["made"]}


def drill(db: DB, target_id: str, folder: Path | None = None, progress=_nothing) -> dict:
    """Restore the newest set into a throw-away folder, open it as a studio, compare, remove the folder."""
    t = db.one("select * from backup_target where id=?", target_id)
    root = Path(t["path"])
    if not available(root) or not sets(root):
        raise BackupProblem("there is no complete copy on that drive")
    name, dest = sets(root)[-1], (folder or data_dir().parent) / f"restore-drill-{time.strftime('%Y%m%d-%H%M%S')}"
    t0 = time.time()
    try:
        r = restore(root, name, dest, progress)
        code = verify_code(root / "sets" / name / "code.bundle")
        d = DB(dest / "studio.db", check=True)                    # opens and migrates exactly as a start would
        r["opens"] = d.val("select count(*) from film") is not None
        d.close()
        r.update(code=code, seconds=round(time.time() - t0, 1))
    except Exception as e:
        r = {"outcome": "failed", "error": f"{type(e).__name__}: {e}"[:300]}
    finally:
        shutil.rmtree(dest, ignore_errors=True)
    db.insert("restore_drill", id=new_id("rd"), target_id=target_id, set_name=name, outcome=r["outcome"], detail_json=r, at=now())
    db.event("system", "backup.drill", "backup_target", target_id, {"outcome": r["outcome"], "set": name})
    return {**r, "set": name}


def verify_code(bundle: Path):
    if not bundle.exists():
        return None
    return subprocess.run(["git", "bundle", "verify", str(bundle)], capture_output=True, text=True, cwd=str(ROOT)).returncode == 0


def add_target(db: DB, path: str, label: str, by="user") -> str:
    p = Path(path)
    if not p.is_absolute():
        raise BackupProblem("write the whole path, for example F:/StudioBackup")
    try:
        inside = p.resolve().is_relative_to(ROOT.resolve())
    except OSError:
        inside = False
    if inside:
        raise BackupProblem("a copy inside the project folder protects nothing")
    tid = new_id("bt")
    db.insert("backup_target", id=tid, path=p.as_posix(), label=label.strip()[:60] or p.as_posix(), added_by=by, added_at=now())
    db.event(by, "backup.target_added", "backup_target", tid, {"path": p.as_posix()})
    return tid


def ensure_default(db: DB):
    """The drive agreed in P0. STUDIO_BACKUP replaces it, for example in tests."""
    if not db.one("select 1 from backup_target"):
        p = os.environ.get("STUDIO_BACKUP", DEFAULT)
        add_target(db, p, "WD drive" if p == DEFAULT else "Backup", by="system")


def adopt(db: DB):
    """The user says: this restored folder is my studio now."""
    db.run("delete from meta where key='restored'")
    db.event("user", "backup.adopted", "studio", "studio")


def state(db: DB) -> dict:
    out = []
    for t in db.q("select * from backup_target where enabled=1 order by added_at"):
        good = db.one("select *,(julianday('now')-julianday(made_at))*24 hours from backup_run where target_id=? and state='verified' order by rowid desc limit 1", t["id"])
        last = db.one("select * from backup_run where target_id=? order by rowid desc limit 1", t["id"])
        out.append({**t, "available": available(Path(t["path"])), "same_disk": same_disk(t["path"]), "good": good, "last": last,
                    "drill": db.one("select * from restore_drill where target_id=? order by rowid desc limit 1", t["id"])})
    good = [x["good"] for x in out if x["good"]]
    changed = db.val("select count(*) from event where seq>? and actor<>'system'", max([g["event_seq"] or 0 for g in good] or [0]))
    places = len({Path(x["path"]).drive.upper() for x in out if x["good"] and not x["same_disk"]})
    hours = min([g["hours"] for g in good], default=None)
    level = "bad" if hours is None or (hours > 48 and changed) else "warn" if places < 2 or (hours > 24 and changed) else "good"
    return {"restored": db.val("select value from meta where key='restored'"), "targets": out, "changed_since": changed, "separate_drives": places, "newest_hours": hours, "level": level}


def due(db: DB) -> list[str]:
    """Targets that want a copy now: a day has passed with changes, or an approval is an hour old and not yet copied."""
    out = []
    if db.val("select value from meta where key='restored'"):
        return out
    for t in db.q("select * from backup_target where enabled=1"):
        if not available(Path(t["path"])):
            continue
        g = db.one("select event_seq,(julianday('now')-julianday(made_at))*24 h from backup_run where target_id=? and state='verified' order by rowid desc limit 1", t["id"])
        if not g:
            out.append(t["id"])
            continue
        new = db.q("select verb,(julianday('now')-julianday(at))*24 h from event where seq>? and verb not like 'backup.%' order by seq", g["event_seq"] or 0)
        if (new and g["h"] >= 24) or any(("approved" in e["verb"] or "accepted" in e["verb"]) and e["h"] >= 1 for e in new):
            out.append(t["id"])
    return out
