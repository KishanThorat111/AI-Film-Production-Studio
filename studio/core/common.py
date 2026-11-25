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


def used_by(db: DB, aid):
    r = db.one("select t.title,a.attempt_no from attempt a join generation_target t on t.id=a.target_id where a.result_asset_id=?", aid)
    return f"{r['title']}, attempt {r['attempt_no']}" if r else db.val("select 'library: '||title from library_item where asset_id=?", aid)


def take_in(db: DB, tid, path: Path, kind, spec: dict, *, origin, reason="", provider=None, model=None, provenance=None):
    """A file made by hand becomes the next attempt. Everything that can refuse it is asked before the file is stored."""
    if assets.KIND_BY_EXT.get(path.suffix.lower()) != kind:
        raise assets.ImportProblem({"image": "not an image", "video": "not a video file", "audio": f"{path.suffix or 'this file'} is not an audio file"}[kind])
    has = {s["codec_type"] for s in assets.probe(path)["streams"]}
    if {"image": "video"}.get(kind, kind) not in has:
        raise assets.ImportProblem(f"the file contains no {'sound' if kind == 'audio' else 'picture'}")
    reason = flow.may_start(db, tid, reason)
    aid, new = assets.add(db, path, origin=origin, provider=provider, model=model, provenance=provenance, actor="user")
    if not new and (u := used_by(db, aid)):
        raise RuleRefused(f"this exact file has already been imported ({u})")
    at = flow.start_attempt(db, tid, "manual", {**spec, "file": db.val("select sha256 from asset where id=?", aid)}, reason or None, [{"note": reason}] if flow.counted(db, tid) else [])
    db.run("update attempt set result_asset_id=? where id=?", aid, at)
    return aid, at


def usable_terms(db: DB, terms, credit=""):
    """Asked before a file is stored, so that a refusal leaves nothing behind."""
    t = db.one("select * from licence_terms where id=?", terms)
    if not t:
        raise RuleRefused("choose where this file comes from")
    if t["r_sync"] == "no":
        raise RuleRefused(f"{t['source']}: these terms do not allow use in a film")
    if t["attribution_required"] and len(credit.strip()) < 3:
        raise RuleRefused(f"{t['source']}: write the credit that has to be shown (name, title, link)")
    return t


def licence(db: DB, aid, terms, credit=""):
    """Where a file comes from, said by the user. Once said it stays, because an approval may rest on it."""
    usable_terms(db, terms, credit)
    old = db.one("select terms_id from asset_licence where asset_id=?", aid)
    if old and old["terms_id"] != terms:
        raise RuleRefused("the source of this file is already recorded")
    assets.set_licence(db, aid, terms, None, credit.strip()[:400] or None)
    db.event("user", "asset.licence", "asset", aid, {"terms": terms})


def add_terms(db: DB, source, url, rights: dict, attribution: bool, note=""):
    """A source the studio does not know yet, as the user read it. It still has to be confirmed before it counts."""
    if len(source.strip()) < 3 or len(url.strip()) < 8:
        raise RuleRefused("write the name of the source and the address of its terms")
    keys = ("store", "edit", "sync", "commercial", "youtube", "reuse_films")
    if any(rights.get(k) not in ("yes", "no", "unclear") for k in keys):
        raise RuleRefused("answer yes, no or unclear for every right")
    base = re.sub(r"[^a-z0-9]+", "_", source.lower()).strip("_")[:40] or "source"
    tid, n = base, 1
    while db.one("select 1 from licence_terms where id=?", tid):
        n += 1
        tid = f"{base}_{n}"
    db.insert("licence_terms", id=tid, source=source.strip()[:120], version="read " + now()[:10], url=url.strip()[:300], evidence_relpath="docs/LICENCES.md", checked_on=now()[:10], **{f"r_{k}": rights[k] for k in keys}, subscription_bound=0, attribution_required=int(bool(attribution)), content_id_risk="unknown", ambiguity=note.strip()[:300] or None)
    db.event("user", "terms.added", "licence_terms", tid)
    return tid


def about_file(db: DB, aid) -> dict:
    """What the studio knows about one file: where it lies, where it came from, what may be done with it."""
    a = db.one("select * from asset where id=?", aid)
    if not a:
        raise assets.Missing("there is no such file")
    pr, p = json.loads(a["probe_json"] or "{}"), assets.path_of(db, aid)
    v = next((s for s in pr.get("streams", []) if s.get("codec_type") == "video"), {})
    l = db.one("select t.id,t.source,t.verified_by,t.r_sync,t.r_youtube,t.r_reuse_films,x.attribution_text from asset_licence x join licence_terms t on t.id=x.terms_id where x.asset_id=?", aid)
    return {"id": aid, "kind": a["kind"], "origin": a["origin"], "kept": a["storage_class"], "path": str(p), "exists": p.exists(), "bytes": a["bytes"], "sha": a["sha256"], "created": a["created_at"], "provider": a["provider"], "model": a["provider_model"], "provenance": json.loads(a["provenance_json"] or "{}"),
            "seconds": float(pr.get("format", {}).get("duration") or 0), "size": f"{v['width']}x{v['height']}" if v.get("width") else None, "licence": l, "used": used_by(db, aid),
            "current_in": [f"{r['title']} (version {r['n']})" for r in db.q("select s.title,max(v.version_no) n from slot_current c join slot_version v on v.slot_id=c.slot_id and v.asset_id=c.asset_id join slot s on s.id=c.slot_id where c.asset_id=? group by s.id", aid)]}


def reveal(p: Path):
    """Show a file in the Windows Explorer. Only files of the studio."""
    import subprocess
    p = p.resolve()
    if not p.is_relative_to(data_dir().resolve()) or not p.exists():
        raise assets.Missing("this file is not there")
    subprocess.Popen(f'explorer /select,"{p}"' if p.is_file() else f'explorer "{p}"')


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
