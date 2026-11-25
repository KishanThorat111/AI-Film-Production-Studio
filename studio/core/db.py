"""Database access. One SQLite file, WAL, short transactions. The schema enforces the rules; this module only opens doors to it."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import migrate

ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return Path(os.environ.get("STUDIO_DATA", ROOT / "data"))


def now() -> str:
    """UTC, in the same shape SQLite's datetime('now') uses, so the two can be compared."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(o) -> str:
    b = o if isinstance(o, bytes) else (o if isinstance(o, str) else dumps(o)).encode("utf-8")
    return hashlib.sha256(b).hexdigest()


class RuleRefused(Exception):
    """A rule in the database refused the change. The message is safe to show to the user."""


class DB:
    def __init__(self, path: Path | None = None, check: bool = False):
        self.path = Path(path) if path else data_dir() / "studio.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.migration = migrate.migrate(self.path, now())
        if check:
            migrate.check(self.conn())

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, isolation_level=None, timeout=20)
            c.row_factory = sqlite3.Row
            c.execute("pragma foreign_keys=on")
            c.execute("pragma journal_mode=wal")
            c.execute("pragma synchronous=full")
            self._local.c = c
        return c

    def close(self):
        c = getattr(self._local, "c", None)
        if c is not None:
            c.close()
            self._local.c = None

    @contextmanager
    def tx(self):
        c = self.conn()
        if c.in_transaction:
            yield c
            return
        c.execute("begin immediate")
        try:
            yield c
        except BaseException:
            c.execute("rollback")
            raise
        else:
            c.execute("commit")

    def q(self, sql: str, *a) -> list[dict]:
        return [dict(r) for r in self.conn().execute(sql, a).fetchall()]

    def one(self, sql: str, *a) -> dict | None:
        r = self.conn().execute(sql, a).fetchone()
        return dict(r) if r else None

    def val(self, sql: str, *a):
        r = self.conn().execute(sql, a).fetchone()
        return r[0] if r else None

    def run(self, sql: str, *a):
        try:
            with self.tx() as c:
                return c.execute(sql, a)
        except sqlite3.IntegrityError as e:
            raise RuleRefused(str(e)) from e

    def insert(self, table: str, **kw):
        cols = ",".join(kw)
        vals = [dumps(v) if isinstance(v, (dict, list)) else v for v in kw.values()]
        self.run(f"insert into {table}({cols}) values({','.join('?' * len(vals))})", *vals)
        return kw.get("id")

    def event(self, actor: str, verb: str, ttype: str, tid: str, payload=None):
        """Each entry carries a hash of itself and the entry before it, so a later change is noticed."""
        with self.tx() as c:
            prev = c.execute("select chain from event order by seq desc limit 1").fetchone()
            at, body = now(), dumps(payload or {})
            c.execute("insert into event(at,actor,verb,target_type,target_id,payload_json,chain) values(?,?,?,?,?,?,?)",
                      (at, actor, verb, ttype, tid, body, sha([prev[0] if prev else None, at, actor, verb, ttype, tid, body])))

    def verify_events(self) -> list[int]:
        """Numbers of entries whose hash does not fit. Entries from before the chain existed are skipped."""
        bad, prev, started = [], None, False
        for r in self.conn().execute("select seq,at,actor,verb,target_type,target_id,payload_json,chain from event order by seq"):
            if r["chain"] is None:
                if started:
                    bad.append(r["seq"])
                prev = None
                continue
            if sha([prev, r["at"], r["actor"], r["verb"], r["target_type"], r["target_id"], r["payload_json"]]) != r["chain"]:
                bad.append(r["seq"])
            prev, started = r["chain"], True
        return bad
