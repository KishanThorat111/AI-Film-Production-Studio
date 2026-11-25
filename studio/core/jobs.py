"""Background work. Jobs are rows, so a crash leaves a trace and a restart can continue. Two lanes: heavy (one at a time) and light."""
from __future__ import annotations

import json
import shutil
import threading
import time
import traceback

import psutil

from .db import DB, data_dir, dumps, new_id, now, sha

HEAVY, LIGHT = ("gpu", "cpu"), ("io", "net")
FLOOR_GB = 40
RESERVE_MB = 300          # stays free for Windows; a 1080p render was measured to run with 1.1 GB available
STALE_S = 900


class Jobs:
    def __init__(self, db: DB):
        self.db, self.handlers, self.valid = db, {}, {}
        self._stop, self._wake = threading.Event(), {"heavy": threading.Event(), "light": threading.Event()}
        for j in db.q("select id from media_job where state='running'"):
            db.run("update media_job set state='interrupted',error='the application stopped; it can continue from the last finished part',ended_at=? where id=?", now(), j["id"])
        self.threads = [threading.Thread(target=self._run, args=("heavy", HEAVY), daemon=True, name="jobs-heavy"), threading.Thread(target=self._run, args=("light", LIGHT), daemon=True, name="jobs-light")]

    def start(self):
        for t in self.threads:
            t.start()

    def add(self, kind, args, key=None, ram=400, disk=200, resource="gpu", film=None):
        k = key or sha([kind, args])
        r = self.db.one("select * from media_job where idem_key=?", k)
        if r:
            again = r["state"] in ("failed", "interrupted", "cancelled")
            if r["state"] == "done":                       # done means the result still exists
                again = kind in self.valid and not self.valid[kind](json.loads(r["args_json"]), json.loads(r["result_json"] or "null"))
            if again:
                self.db.run("update media_job set state='queued',attempt=attempt+1,error=null,ended_at=null where id=?", r["id"])
            jid = r["id"]
        else:
            jid = self.db.insert("media_job", id=new_id("jb"), kind=kind, args_json=args, idem_key=k, resource=resource, est_ram_mb=ram, est_disk_mb=disk, film_id=film, created_at=now())
        for w in self._wake.values():
            w.set()
        return jid

    def resume(self, jid):
        self.db.run("update media_job set state='queued',attempt=attempt+1,error=null,ended_at=null where id=? and state in('interrupted','failed','cancelled')", jid)
        for w in self._wake.values():
            w.set()

    def cancel(self, jid):
        self.db.run("update media_job set state='cancelled',ended_at=? where id=? and state in('queued','running')", now(), jid)

    def stopping(self, jid):
        return self._stop.is_set() or self.db.val("select state from media_job where id=?", jid) == "cancelled"

    def progress(self, jid, done, total, text=""):
        self.db.run("update media_job set progress_json=?,heartbeat=? where id=?", dumps({"done": done, "total": total, "text": text}), now(), jid)

    def result(self, jid, value):
        self.db.run("update media_job set result_json=? where id=?", dumps(value), jid)

    def stalled(self):
        return self.db.q("select id,kind,heartbeat from media_job where state='running' and heartbeat<datetime('now',?)", f"-{STALE_S} seconds")

    def _next(self, lane):
        """The oldest job that fits into memory now. A big one that waits never blocks a small one."""
        free, floor = psutil.virtual_memory().available / 2 ** 20, shutil.disk_usage(data_dir()).free
        for j in self.db.q(f"select * from media_job where state='queued' and resource in({','.join('?' * len(lane))}) order by rowid", *lane):
            if floor < FLOOR_GB * 2 ** 30 + j["est_disk_mb"] * 2 ** 20:
                self.db.run("update media_job set state='failed',error=?,ended_at=? where id=?", f"not enough disk space: {floor // 2 ** 30} GB free, {FLOOR_GB} GB must stay free", now(), j["id"])
                continue
            if free < j["est_ram_mb"] + RESERVE_MB:
                big = sorted(psutil.process_iter(["name", "memory_info"]), key=lambda p: -(p.info["memory_info"].rss if p.info["memory_info"] else 0))[:1]
                name = f" (largest now: {big[0].info['name']})" if big else ""
                self.progress(j["id"], 0, 1, f"waiting for memory: {free / 1024:.1f} GB free, {(j['est_ram_mb'] + RESERVE_MB) / 1024:.1f} GB needed{name}")
                continue
            return j
        return None

    def _run(self, name, lane):
        db = self.db
        while not self._stop.is_set():
            try:
                j = self._next(lane)
            except Exception:
                j = None
            if not j:
                self._wake[name].wait(2)
                self._wake[name].clear()
                continue
            if not db.run("update media_job set state='running',heartbeat=?,started_at=?,error=null where id=? and state='queued'", now(), now(), j["id"]).rowcount:
                continue
            t0 = time.time()
            try:
                out = self.handlers[j["kind"]](j["id"], json.loads(j["args_json"]))
                if out is not None:
                    self.result(j["id"], out)
                db.run("update media_job set state='done',progress_json=?,ended_at=? where id=? and state='running'", dumps({"done": 1, "total": 1}), now(), j["id"])
            except KeyboardInterrupt:
                db.run("update media_job set state='interrupted',error='stopped; it continues from the last finished part',ended_at=? where id=? and state='running'", now(), j["id"])
            except Exception as e:
                ref = log_error(f"job {j['kind']} {j['id']}", traceback.format_exc())
                db.run("update media_job set state='failed',error=?,ended_at=? where id=?", f"{type(e).__name__}: {e} [{ref}]"[:500], now(), j["id"])
            act = {"render": "render", "preview": "render", "backup": "backup", "drill": "backup", "previs": "render", "inspect": "inspection"}.get(j["kind"])
            if act:
                db.insert("time_log", id=new_id("tm"), film_id=j["film_id"], activity=act, basis_json={"kind": j["kind"]}, seconds=round(time.time() - t0, 2), ref=j["id"], at=now())

    def stop(self):
        self._stop.set()
        for w in self._wake.values():
            w.set()
        for t in self.threads:
            if t.is_alive():
                t.join(10)


def log_error(where, text) -> str:
    """Writes to data/errors.log without keys and returns the number the user can quote. The file is kept below 5 MB."""
    from ..connectors.http import scrub
    ref = new_id("E")[-6:].upper()
    p = data_dir() / "errors.log"
    if p.exists() and p.stat().st_size > 5 * 2 ** 20:
        p.replace(p.with_suffix(".old.log"))
    with p.open("a", encoding="utf-8") as f:
        f.write(f"{now()} {ref} {where}\n{scrub(text)}\n")
    return ref
