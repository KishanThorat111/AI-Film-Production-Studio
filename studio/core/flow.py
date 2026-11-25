"""Attempts, approvals, slots, pauses. Thin: the database triggers decide, this module phrases and records."""
from __future__ import annotations

import json

from .db import DB, RuleRefused, new_id, now, sha


LISTENERS: list = []          # called when something happened that may make new work possible


def changed(db: DB, film, what):
    """A listener that fails must never undo what the user did; the failure is written down and repaired at the next change or start."""
    for f in LISTENERS:
        try:
            f(db, film, what)
        except Exception:
            import traceback
            from .jobs import log_error
            log_error(f"after {what}", traceback.format_exc())


# ------------------------------------------------------------ dependency hashes and approvals
def set_hash(db: DB, dep_type, dep_id, value, field="*"):
    db.run("insert into current_hash(dep_type,dep_id,field,hash) values(?,?,?,?) on conflict(dep_type,dep_id,field) do update set hash=excluded.hash",
           dep_type, dep_id, field, value)


def approve(db: DB, gate, ttype, tid, deps: list[tuple[str, str]], actor, evidence=None, outcome="approved", reason=None):
    aid = new_id("ap")
    with db.tx():
        db.insert("approval", id=aid, gate=gate, target_type=ttype, target_id=tid, outcome=outcome, actor=actor, evidence_json=evidence or {}, reason=reason, created_at=now())
        for t, i in deps:
            h = db.val("select hash from current_hash where dep_type=? and dep_id=? and field='*'", t, i)
            if h is None:
                raise RuleRefused(f"nothing recorded for {t} {i}")
            db.insert("approval_dep", approval_id=aid, dep_type=t, dep_id=i, hash=h)
        db.event(actor, f"gate.{outcome}", ttype, tid, {"gate": gate})
    return aid


def live(db: DB, gate, tid) -> bool:
    return bool(db.one("select 1 from approval_live where gate=? and target_id=?", gate, tid))


# ------------------------------------------------------------ targets and attempts
def target(db: DB, film, kind, ref, title, max_attempts=3) -> str:
    r = db.one("select id from generation_target where film_id=? and kind=? and ref_id=?", film, kind, ref)
    if r:
        return r["id"]
    return db.insert("generation_target", id=new_id("tg"), film_id=film, kind=kind, ref_id=ref, title=title, max_attempts=max_attempts)


def attempts(db: DB, tid) -> list[dict]:
    return db.q("select * from attempt where target_id=? order by attempt_no", tid)


def counted(db: DB, tid) -> list[dict]:
    """Attempts that really happened. A withdrawn one produced nothing and cost nothing."""
    return db.q("select * from attempt_counted where target_id=? order by attempt_no", tid)


def limit(db: DB, tid) -> int:
    return db.val("select max_attempts+(select coalesce(sum(extra),0) from attempt_extension where target_id=t.id) from generation_target t where id=?", tid)


def start_attempt(db: DB, tid, origin, spec: dict, reason=None, findings=None) -> str:
    prev, real = attempts(db, tid), counted(db, tid)
    if prev and prev[-1]["outcome"] == "pending" and not prev[-1]["result_asset_id"]:
        try:                                  # the last one never left this computer: take it back instead of blocking the asset
            withdraw(db, prev[-1]["id"], "replaced by a changed request before anything was sent", "system")
            real = counted(db, tid)
        except RuleRefused:
            pass
    aid = new_id("at")
    db.insert("attempt", id=aid, target_id=tid, attempt_no=len(prev) + 1, origin=origin, spec_json=spec, spec_hash=sha(spec), reason=reason,
              prior_attempt_id=real[-1]["id"] if real else None, findings_json=findings or [], created_at=now())
    return aid


def may_start(db: DB, tid, reason="") -> str:
    """Would the next attempt be accepted? Says in plain words why not. Returns the reason to record."""
    t, real, reason = db.one("select state from generation_target where id=?", tid), [a for a in counted(db, tid) if a["outcome"] != "pending" or a["result_asset_id"]], (reason or "").strip()      # one that was begun and never sent is taken back when the next one starts
    if t["state"] == "approved":
        raise RuleRefused("this is approved; choose 'Ask for another version' first")
    if t["state"] == "stopped" or len(real) >= limit(db, tid):
        raise RuleRefused("all attempts are used; nothing more is taken in unless you allow another attempt")
    if real and real[-1]["outcome"] == "pending":
        raise RuleRefused("judge the previous one first")
    if real and not reason and real[-1]["outcome"] == "accepted":
        reason = "another version: " + (db.val("select reason from target_reopen where target_id=? order by at desc limit 1", tid) or "")
    if real and len(reason) < 10:
        raise RuleRefused("write what was wrong with the previous one and what changed (at least ten characters)")
    return reason


def withdraw(db: DB, attempt_id, reason, by="user"):
    """An attempt that produced nothing and cost nothing does not count. The database checks both."""
    with db.tx():
        db.insert("attempt_withdrawal", attempt_id=attempt_id, reason=reason, withdrawn_by=by, at=now())
        db.event(by, "attempt.withdrawn", "attempt", attempt_id, {"reason": reason})


def reopen(db: DB, tid, reason, extra=1):
    """The user wants something better than the approved version. The approved one stays in place until a new one is approved."""
    last = counted(db, tid)
    if not last or last[-1]["outcome"] != "accepted":
        raise RuleRefused("only an accepted item can be reopened")
    if len((reason or "").strip()) < 5:
        raise RuleRefused("write why you want another version")
    with db.tx():
        db.insert("target_reopen", id=new_id("ro"), target_id=tid, after_attempt_id=last[-1]["id"], reason=reason.strip(), reopened_by="user", at=now())
        room = limit(db, tid) - len(last)
        if room < extra:
            db.insert("attempt_extension", id=new_id("ax"), target_id=tid, extra=min(3, extra - max(0, room)), granted_by="user", reason=reason.strip(), created_at=now())
        db.run("update generation_target set state='open' where id=?", tid)
        db.event("user", "target.reopened", "generation_target", tid, {"reason": reason})


def judge_attempt(db: DB, attempt_id, outcome, actor):
    a = db.one("select * from attempt where id=?", attempt_id)
    with db.tx():
        db.run("update attempt set outcome=? where id=?", outcome, attempt_id)
        if outcome == "accepted":
            db.run("update generation_target set state='approved' where id=?", a["target_id"])
        elif outcome in ("needs_correction", "rejected", "refused") and len(counted(db, a["target_id"])) >= limit(db, a["target_id"]):
            db.run("update generation_target set state='stopped' where id=?", a["target_id"])
            db.event("system", "attempts.exhausted", "generation_target", a["target_id"], {"attempts": a["attempt_no"]})
        db.event(actor, f"attempt.{outcome}", "attempt", attempt_id)


def extend(db: DB, tid, extra, reason):
    with db.tx():
        db.insert("attempt_extension", id=new_id("ax"), target_id=tid, extra=extra, granted_by="user", reason=reason, created_at=now())
        db.run("update generation_target set state='open' where id=? and state='stopped'", tid)
        db.event("user", "attempts.extended", "generation_target", tid, {"extra": extra, "reason": reason})


# ------------------------------------------------------------ versions
def slot(db: DB, film, kind, ref, title) -> str:
    r = db.one("select id from slot where film_id=? and kind=? and ref_id=?", film, kind, ref)
    return r["id"] if r else db.insert("slot", id=new_id("sl"), film_id=film, kind=kind, ref_id=ref, title=title)


def add_version(db: DB, slot_id, asset_id, by, reason, evidence=None) -> str:
    n = 1 + (db.val("select max(version_no) from slot_version where slot_id=?", slot_id) or 0)
    has = db.one("select 1 from slot_current where slot_id=?", slot_id)
    vid = new_id("sv")
    db.insert("slot_version", id=vid, slot_id=slot_id, version_no=n, asset_id=asset_id, status="proposed_replacement" if has else "candidate", proposed_by=by,
              reason=reason, evidence_json=evidence or {}, created_at=now())
    return vid


def decide_version(db: DB, version_id, ok: bool):
    db.run("update slot_version set status=?,approved_by=?,decided_at=? where id=?", "approved" if ok else "declined", "user" if ok else None, now(), version_id)
    if ok:
        v = db.one("select slot_id,asset_id from slot_version where id=?", version_id)
        set_hash(db, "slot", v["slot_id"], v["asset_id"])          # whatever was built on the old version is stale from here
    db.event("user", "version.approved" if ok else "version.declined", "slot_version", version_id)


def restore_version(db: DB, version_id, reason):
    v = db.one("select * from slot_version where id=?", version_id)
    n = 1 + db.val("select max(version_no) from slot_version where slot_id=?", v["slot_id"])
    vid = new_id("sv")
    db.insert("slot_version", id=vid, slot_id=v["slot_id"], version_no=n, asset_id=v["asset_id"], status="approved", proposed_by="user", approved_by="user",
              reason=f"restored version {v['version_no']}: {reason}", created_at=now(), decided_at=now())
    set_hash(db, "slot", v["slot_id"], v["asset_id"])
    db.event("user", "version.restored", "slot_version", vid, {"from": version_id})
    return vid


# ------------------------------------------------------------ pauses
def pause(db: DB, film, service, reason, error, options=("retry", "manual", "wait", "cancel")) -> str:
    r = db.one("select id from stage_pause where film_id=? and service=? and resolution is null", film, service)
    if r:
        return r["id"]
    pid = new_id("pz")
    with db.tx():
        db.insert("stage_pause", id=pid, film_id=film, service=service, reason=reason, error_json=error, options_json=list(options), opened_at=now())
        db.event("system", "stage.paused", "stage_pause", pid, {"service": service, "reason": reason})
    return pid


def resolve_pause(db: DB, pid, resolution):
    with db.tx():
        p = db.one("select * from stage_pause where id=?", pid)
        db.run("update stage_pause set resolution=?,resolved_by='user',resolved_at=? where id=? and resolution is null", resolution, now(), pid)
        if resolution == "manual" and p["service"] != "stills":
            set_mode(db, p["film_id"], p["service"], "manual")
        db.event("user", "stage.resolved", "stage_pause", pid, {"resolution": resolution})


def set_mode(db: DB, film, service, mode):
    db.insert("production_mode", film_id=film, service=service, mode=mode, set_by="user", set_at=now())
    db.event("user", "mode.set", "film", film, {"service": service, "mode": mode})


def mode(db: DB, film, service) -> str:
    return db.val("select mode from mode_current where film_id=? and service=?", film, service) or "manual"


# ------------------------------------------------------------ learning
def propose_lesson(db: DB, scope: dict, statement: str, support: list, contradict=None):
    lid = new_id("ls")
    with db.tx():
        db.insert("lesson", id=lid, scope_json=scope, statement=statement, support_json=support, contradict_json=contradict or [], status="candidate")
        db.run("insert into lesson_fts(statement,lesson_id) values(?,?)", statement, lid)
    return lid


def learn_from_target(db: DB, tid):
    """After a target is accepted on a later attempt: record what changed between the failure and the success as a candidate lesson."""
    t = db.one("select * from generation_target where id=?", tid)
    a = attempts(db, tid)
    if not a or a[-1]["outcome"] != "accepted" or len(a) < 2:
        return None
    before, after = json.loads(a[-2]["spec_json"]), json.loads(a[-1]["spec_json"])
    changed = {k: {"from": before.get(k), "to": after.get(k)} for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)}
    scope = {"kind": t["kind"], "provider": after.get("provider"), "model": after.get("model_id") or after.get("model"), "changed": sorted(changed)}
    txt = f"{t['kind']}: after '{(a[-1]['reason'] or '')[:140]}', changing {', '.join(sorted(changed)) or 'nothing recorded'} led to acceptance (attempt {a[-1]['attempt_no']})."
    return propose_lesson(db, scope, txt, [{"target": tid, "from": a[-2]["id"], "to": a[-1]["id"], "changed": changed}])


def lessons_for(db: DB, kind, limit_n=5):
    return [x for x in db.q("select * from lesson where status in('trial','adopted') order by id desc") if json.loads(x["scope_json"]).get("kind") == kind][:limit_n]


def agent_record(db: DB) -> list[dict]:
    """What the executive sees about each role: reports, verdicts, findings you marked wrong, retries caused."""
    return db.q("""select t.role, count(distinct r.id) reports, sum(r.verdict='APPROVED') approved, sum(r.verdict in('REJECTED','NEEDS_CORRECTION')) negative,
        (select count(*) from finding f join report r2 on r2.id=f.report_id join task t2 on t2.id=r2.task_id where t2.role=t.role and f.human_verdict='false') wrong_findings,
        (select count(*) from finding f join report r2 on r2.id=f.report_id join task t2 on t2.id=r2.task_id where t2.role=t.role and f.human_verdict='true') confirmed_findings
        from task t join report r on r.task_id=t.id group by t.role""")
