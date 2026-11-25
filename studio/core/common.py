"""What images, voices, sounds and clips share: checks, judging, uploads, rights, the library."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import assets, flow
from .db import DB, RuleRefused, data_dir, new_id, now, sha


def views_dir(asset_id) -> Path:
    return data_dir() / "views" / asset_id


def save_check(db: DB, asset_id, name, rep):
    basis = sha([rep.get("version"), rep.get("items"), rep.get("flags"), rep.get("facts")])
    if not db.one("select 1 from check_result where asset_id=? and check_name=? and check_version=? and basis_hash=?", asset_id, name, rep.get("version", "1"), basis):
        db.insert("check_result", id=new_id("cr"), asset_id=asset_id, check_name=name, check_version=rep.get("version", "1"), outcome=rep["outcome"], value_json=rep, basis_hash=basis, created_at=now())


def checks(db: DB, asset_id):
    return [{**r, "value": json.loads(r.pop("value_json"))} for r in db.q("select id,check_name,outcome,created_at,value_json from check_result where asset_id=? order by rowid desc", asset_id)]


def inbox(name: str) -> Path:
    d = data_dir() / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{new_id('up')}_{re.sub(r'[^A-Za-z0-9._-]', '_', Path(name or 'file').name)[-80:]}"


def spool(upload: bytes, name: str) -> Path:
    p = inbox(name)
    p.write_bytes(upload)
    return p


def clear_inbox(p: Path, keep_days=7):
    """An upload is either stored by now or was refused. Nothing stays behind, except files somebody else put there recently."""
    import time
    p.unlink(missing_ok=True)
    for x in p.parent.glob("up_*"):
        if x.stat().st_mtime < time.time() - keep_days * 86400:
            x.unlink(missing_ok=True)


def _close_task(db, kind, pid, done):
    if done:
        db.run("update manual_task set state='imported',closed_at=? where kind=? and package_id=? and state='waiting'", now(), kind, pid)


def judge(db: DB, attempt_id, verdict, note=""):
    """The user's ears and eyes decide. verdict: accepted | needs_correction | rejected"""
    a = db.one("select a.*,t.kind,t.ref_id,t.film_id,t.title from attempt a join generation_target t on t.id=a.target_id where a.id=?", attempt_id)
    if not a["result_asset_id"]:
        raise RuleRefused("there is no result to judge")
    with db.tx():
        db.insert("human_label", id=new_id("hl"), asset_id=a["result_asset_id"], blind=0, kind={"voice": "listen", "sfx": "listen", "video": "watch"}.get(a["kind"], "look"), verdict={"accepted": "good", "needs_correction": "fixable", "rejected": "bad"}[verdict],
                  tags_json={"note": note}, created_at=now())
        flow.judge_attempt(db, attempt_id, verdict, "user")
        if verdict == "accepted":
            s = flow.slot(db, a["film_id"], a["kind"], a["ref_id"], a["title"])
            v = flow.add_version(db, s, a["result_asset_id"], "user", note or "accepted", {"attempt": attempt_id})
            flow.decide_version(db, v, True)
            b = db.val("select binding_id from audio_take where asset_id=?", a["result_asset_id"])
            if b:                                    # the casting choice is the user's acceptance; a later version can replace it
                db.run("update voice_binding set status='retired' where status='approved' and revision_id=(select revision_id from voice_binding where id=?)", b)
                db.run("update voice_binding set status='approved',approved_by='user',approved_sample_asset_id=? where id=?", a["result_asset_id"], b)
            flow.learn_from_target(db, a["target_id"])
    flow.changed(db, a["film_id"], "judged")


def _open_or_start(db, tid, spec, reason):
    """Continue an attempt whose request never got an answer; otherwise begin the next one."""
    prev = flow.attempts(db, tid)
    if prev and prev[-1]["outcome"] == "pending" and not prev[-1]["result_asset_id"] and prev[-1]["spec_hash"] == sha(spec):
        if db.val("select count(*) from api_request where attempt_id=? and state='failed'", prev[-1]["id"]) < 3:
            return prev[-1]["id"]
    return flow.start_attempt(db, tid, "api", spec, reason or None, [{"note": reason}] if flow.counted(db, tid) else [])


def reviews_of(db: DB, asset):
    return [{**r, "body": json.loads(r.pop("body_json"))} for r in db.q("select r.id,r.verdict,r.body_json,r.created_at,t.role from report r join task t on t.id=r.task_id where t.target_type='asset' and t.target_id=? order by r.rowid", asset)]


# ------------------------------------------------------------ rights
def verify_terms(db: DB, tid):
    db.run("update licence_terms set verified_by='user' where id=? and verified_by is null", tid)
    db.event("user", "terms.verified", "licence_terms", tid)


def admit(db: DB, aid, scope, scope_ref, kind, title, tags):
    db.insert("library_item", asset_id=aid, scope=scope, scope_ref=scope_ref, kind=kind, title=title, tags_json=tags, admitted_by="user", admitted_on=now())
    db.event("user", "library.admit", "asset", aid, {"scope": scope})


def search_library(db: DB, text):
    q = [w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2]
    out = []
    for r in db.q("select l.*,a.relpath,a.probe_json from library_item l join asset a on a.id=l.asset_id where retired_at is null"):
        hay = (r["title"] + " " + r["tags_json"]).lower()
        n = sum(w in hay for w in q)
        if n or not q:
            r["score"] = n
            out.append(r)
    return sorted(out, key=lambda r: -r["score"])
