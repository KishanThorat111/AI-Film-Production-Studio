"""Numbered, checksummed schema steps. An existing database is copied before every change; a step either lands completely or not at all.

studio/schema/0001_baseline.sql is the P0 schema. Every later change is a new file NNNN_name.sql; files that have been applied are never edited.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

FOLDER = Path(__file__).resolve().parents[1] / "schema"


class SchemaProblem(RuntimeError):
    """The message is meant for the user."""


class Damaged(SchemaProblem):
    pass


def steps(folder: Path = FOLDER) -> list[tuple[int, str, bytes]]:
    out = []
    for p in sorted(folder.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        out.append((int(p.name[:4]), p.stem[5:], p.read_bytes()))
    if [n for n, _, _ in out] != list(range(1, len(out) + 1)):
        raise SchemaProblem("the schema steps are not numbered 1, 2, 3, ...")
    return out


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def statements(sql: str):
    """Split a script into statements; triggers contain semicolons, so SQLite itself decides where one ends."""
    buf = ""
    for line in sql.splitlines(keepends=True):
        if not buf.strip() and re.match(r"\s*(--.*)?$", line):
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            yield from _split(buf)
            buf = ""
    if buf.strip() and not all(re.match(r"\s*(--.*)?$", x) for x in buf.splitlines()):
        raise SchemaProblem("a schema step ends in the middle of a statement: " + buf.strip()[:80])


def _split(buf: str):
    """Several complete statements may share a line."""
    start, i = 0, 0
    while True:
        i = buf.find(";", i)
        if i < 0:
            break
        i += 1
        part = buf[start:i]
        if sqlite3.complete_statement(part):
            if part.strip(" \n\r\t;"):
                yield part
            start = i


def version(c: sqlite3.Connection) -> int:
    names = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    if "meta" not in names:
        if names:
            raise Damaged("this file is not a studio database, or it lost its description")
        return 0
    if "schema_step" in names:
        return c.execute("select coalesce(max(n),0) from schema_step").fetchone()[0]
    row = c.execute("select value from meta where key='schema_sha'").fetchone()
    if row and row[0] == _sha(steps()[0][2]):
        return 1
    raise SchemaProblem("this database was made by an unknown version of the studio; restore a matching copy")


def check(c: sqlite3.Connection):
    try:
        bad = [r[0] for r in c.execute("pragma integrity_check(5)")]
    except sqlite3.DatabaseError as e:
        raise Damaged(f"the database file is damaged ({e})") from None
    if bad != ["ok"]:
        raise Damaged("the database file is damaged: " + "; ".join(bad)[:300])
    fk = c.execute("pragma foreign_key_check").fetchall()
    if fk:
        raise Damaged(f"{len(fk)} rows point at rows that no longer exist (first: {tuple(fk[0])[:3]})")


def copy_before(path: Path, frm: int, to: int) -> Path:
    import time
    d = path.parent / "before-migration"
    d.mkdir(exist_ok=True)
    dst = d / f"studio-v{frm}-to-v{to}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    a, b = sqlite3.connect(path), sqlite3.connect(dst)
    try:
        a.backup(b)
        b.execute("pragma journal_mode=delete")
        check(b)
    finally:
        a.close()
        b.close()
    return dst


def _apply(c: sqlite3.Connection, todo, now: str):
    """Everything in one transaction. Foreign keys are checked at the end, because tables may be rebuilt."""
    c.execute("begin exclusive")
    try:
        have = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        if "schema_step" not in have:
            c.execute("create table if not exists meta(key text primary key, value text not null)")
            c.execute("create table schema_step(n integer primary key, name text not null, sha256 text not null, applied_at text not null)")
            if "film" in have:                        # a P0 database: step 1 is already there
                n, name, raw = steps()[0]
                c.execute("insert into schema_step values(?,?,?,?)", (n, name, _sha(raw), now))
        done = c.execute("select coalesce(max(n),0) from schema_step").fetchone()[0]
        for n, name, raw in todo:
            if n <= done:
                continue
            for s in statements(raw.decode("utf-8")):
                if not re.match(r"\s*pragma", re.sub(r"--[^\n]*\n", "", s), re.I):
                    c.execute(s)
            c.execute("insert into schema_step values(?,?,?,?)", (n, name, _sha(raw), now))
        fk = c.execute("pragma foreign_key_check").fetchall()
        if fk:
            raise SchemaProblem(f"the change would break {len(fk)} links between records; nothing was changed")
        c.execute("insert into meta values('schema_version',?) on conflict(key) do update set value=excluded.value", (str(max(n for n, _, _ in steps())),))
        c.execute("commit")
    except BaseException:
        if c.in_transaction:
            c.execute("rollback")
        raise


def migrate(path: Path, now: str, folder: Path = FOLDER) -> dict:
    """Bring the file to the current version. Returns what happened. Creates a new database if the file does not exist."""
    all_steps = steps(folder)
    fresh = not path.exists()
    if not fresh and path.stat().st_size == 0:
        raise Damaged("the database file is empty; it existed before, so it was damaged")
    c = sqlite3.connect(path, isolation_level=None, timeout=20)
    try:
        try:
            c.execute("pragma journal_mode=wal")
            v = version(c) if not fresh else 0
        except sqlite3.DatabaseError as e:
            if "locked" in str(e) or "busy" in str(e):
                raise SchemaProblem("another program is holding the database; close other studio windows and try again") from None
            raise Damaged(f"the database file cannot be read ({e})") from None
        if v > len(all_steps):
            raise SchemaProblem(f"this database is from a newer studio (version {v}); this program knows {len(all_steps)}. Use the newer program.")
        if v:
            names = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
            if "schema_step" in names:
                for n, name, h in c.execute("select n,name,sha256 from schema_step").fetchall():
                    if _sha(all_steps[n - 1][2]) != h:
                        raise SchemaProblem(f"schema step {n} ({name}) was edited after it had been applied; steps are never edited")
        todo = [s for s in all_steps if s[0] > v]
        if not todo and not fresh:
            return {"from": v, "to": v, "copy": None}
        kept = None
        if not fresh and v:
            check(c)
            kept = copy_before(path, v, len(all_steps))
        c.execute("pragma foreign_keys=off")
        _apply(c, all_steps if not v else [s for s in all_steps if s[0] > v], now)
        return {"from": v, "to": len(all_steps), "copy": str(kept) if kept else None}
    finally:
        c.close()
