"""Operations: is the studio healthy, what do agents cost and contribute, how long do things really take."""
from __future__ import annotations

import json
import shutil
import statistics

import psutil

from . import backup, flow
from .db import DB, data_dir
from .jobs import FLOOR_GB


def health(db: DB, jobs, app_state) -> dict:
    items = []

    def add(name, ok, text, action=None):
        items.append({"name": name, "state": ok, "text": text, "action": action})
    m = psutil.virtual_memory()
    free = m.available / 2 ** 30
    add("Memory", "good" if free > 1.8 else "warn", f"{free:.1f} of {m.total / 2 ** 30:.0f} GB free", None if free > 1.8 else "Close Chrome or other large programs before rendering")
    d = shutil.disk_usage(data_dir()).free / 2 ** 30
    add("Disk", "good" if d > FLOOR_GB * 1.5 else "warn" if d > FLOOR_GB else "bad", f"{d:.0f} GB free; {FLOOR_GB} GB stay reserved", None if d > FLOOR_GB * 1.5 else "Free some space")
    b = backup.state(db)
    h = b["newest_hours"]
    add("Copies", b["level"], "never copied" if h is None else f"newest verified copy {h:.0f} h ago, on {b['separate_drives']} other drive(s); {b['changed_since']} changes since", "Open the Backup page" if b["level"] != "good" else None)
    dr = [t["drill"] for t in b["targets"] if t["drill"]]
    add("Restore test", "good" if dr and dr[-1]["outcome"] == "equal" else "warn", f"last {dr[-1]['at']}: {dr[-1]['outcome']}" if dr else "a copy that was never restored is only a hope", None if dr else "Run a restore test on the Backup page")
    bad = db.verify_events()
    add("Log", "good" if not bad else "bad", "every entry fits the one before" if not bad else f"{len(bad)} entries do not fit their hash, first number {bad[0]}", None if not bad else "Compare with the newest copy")
    add("Schema", "good", f"version {db.val('select max(n) from schema_step')}" + (f"; brought forward from {app_state.migrated['from']} at this start" if app_state.migrated.get("copy") else ""))
    st = jobs.stalled()
    add("Jobs", "warn" if st else "good", f"{len(st)} without a sign of life" if st else f"{db.val('select count(*) from media_job where state in(?,?)', 'queued', 'running')} waiting or running", "Cancel and start again" if st else None)
    ff = shutil.which("ffmpeg")
    add("ffmpeg", "good" if ff else "bad", ff or "not found", None if ff else "Install ffmpeg with NVENC")
    exp = db.q("select surface,expires_on from provider_profile where expires_on<date('now','+14 days')")
    add("Provider facts", "warn" if exp else "good", f"{len(exp)} need checking again soon: {', '.join(x['surface'] for x in exp[:3])}" if exp else "checked recently")
    miss = missing_files(db)
    add("Files", "bad" if miss else "good", f"{len(miss)} original files are missing" if miss else "every original is in place", "Restore them from a copy" if miss else None)
    order = {"bad": 0, "warn": 1, "good": 2}
    return {"items": items, "level": min((i["state"] for i in items), key=order.get)}


def missing_files(db: DB):
    root = data_dir() / "media"
    return [r["id"] for r in db.q("select id,relpath from asset where storage_class='original'") if not (root / r["relpath"]).exists()]


# ------------------------------------------------------------ agents
def about(db: DB, t) -> str:
    """What a task is about, in words a person knows."""
    k, i = t["target_type"], t["target_id"]
    q = {"voice_package": "select series||', revision '||rev from voice_package where id=?", "sfx_package": "select cue_id||', revision '||rev from sfx_package where id=?", "work_doc": "select replace(kind,'_',' ')||' '||case scope when 'film' then '' else scope end||', revision '||rev from work_doc where id=?",
         "cut_rev": "select scope||', revision '||rev from cut_rev where id=?", "asset": "select g.title||', attempt '||a.attempt_no from attempt a join generation_target g on g.id=a.target_id where a.result_asset_id=?",
         "video_package": "select ss.shot_id from video_package v join frame_pair f on f.id=v.frame_pair_id join shot_spec ss on ss.id=f.shot_spec_id where v.id=?"}.get(k)
    v = db.val(q, i) if q else (i or "").split("|")[-1]
    if v and v.startswith("scene:"):
        n = db.one("select number,slug from scene where id=?", v.split(":", 1)[1].split(",")[0])
        v = f"scene {n['number']}: {n['slug'][:40]}" + (", " + v.split(", ", 1)[1] if ", " in v else "") if n else v
    return v or i


def agents(db: DB, film) -> dict:
    """Per role and version: what it was given, what it cost, what it found, and whether the user agreed."""
    from ..agents import dispatch, service
    rows = []
    for role in service.ROLES:
        u = db.one("select count(*) runs,sum(output_tokens) out_,sum(input_tokens+coalesce(cache_write_tokens,0)) in_,avg(seconds) secs,sum(outcome='handed_in') ok,sum(outcome<>'handed_in') failed from usage_log where role=?", role)
        r = db.one("select count(*) reports,sum(r.verdict='APPROVED') approved from report r join task t on t.id=r.task_id where t.role=?", role)
        f = db.one("""select count(*) findings,sum(f.severity in('critical','major')) serious,sum(f.human_verdict='true') confirmed,sum(f.human_verdict='false') wrong from finding f join report r on r.id=f.report_id join task t on t.id=r.task_id where t.role=?""", role)
        refused = db.val("select count(*) from event where actor=? and verb in('report.refused','work.refused')", role)
        v = db.one("select hash,model,effort,seen_at from agent_version where role=? order by rowid desc limit 1", role)
        rows.append({"role": role, "file": service.agent_file(role).exists(), "kind": [k for k, s in (("maker", service.MAKERS), ("checker", service.CHECKERS)) if role in s], "version": v["hash"][:8] if v else None, "model": v["model"] if v else None, "effort": v["effort"] if v else None, "runs": u["runs"], "output_tokens": u["out_"] or 0, "input_tokens": u["in_"] or 0, "seconds": round(u["secs"]) if u["secs"] else None,
                     "handed_in": u["ok"] or 0, "ended_without": u["failed"] or 0, "reports": r["reports"], "approved": r["approved"] or 0, "findings": f["findings"], "serious": f["serious"] or 0, "confirmed": f["confirmed"] or 0, "wrong": f["wrong"] or 0, "refused": refused, "tokens_per_result": round((u["out_"] or 0) / u["ok"]) if u["ok"] else None})
    acc = db.val("select count(*) from generation_target where film_id=? and state='approved'", film) or 0
    tok = db.val("select sum(output_tokens) from usage_log where film_id=?", film) or 0
    return {"roles": rows, "misses": misses(db, film), "tasks": [{**t, "about": about(db, t)} for t in db.q("select t.id,t.kind,t.action,t.role,t.state,t.target_type,t.target_id,t.created_at,t.closed_at,t.attempt,t.tier from task t where t.film_id=? and t.state in('ready','leased','blocked') order by t.priority,t.rowid", film)], "recent": db.q("select id,kind,action,role,state,target_type,target_id,closed_at from task where film_id=? and closed_at is not null order by closed_at desc limit 25", film),
            "tokens_per_accepted_asset": round(tok / acc) if acc and tok else None, "film_tokens": tok, "usage": dispatch.usage(db), "bench": db.q("select id,kind,subject,result_json,created_at from bench_run order by rowid desc limit 20")}


def misses(db: DB, film) -> dict:
    """Clips where the user's blind verdict and the inspection disagree. The only honest measure of a checker."""
    out = {"judged": 0, "user_bad_inspection_clean": [], "user_good_inspection_bad": [], "agree": 0}
    for a in db.q("select a.id from asset a join attempt at on at.result_asset_id=a.id join generation_target t on t.id=at.target_id where t.film_id=? and t.kind='video'", film):
        h = db.one("select verdict from human_label where asset_id=? and blind=1 order by rowid desc limit 1", a["id"])
        ck = db.one("select value_json from check_result where asset_id=? and check_name='video' order by rowid desc limit 1", a["id"])
        if not h or not ck:
            continue
        v = json.loads(ck["value_json"])
        flagged = any(f["severity"] in ("critical", "major") for f in v.get("flags", [])) or bool(db.one("select 1 from report r join task t on t.id=r.task_id where t.target_id=? and r.verdict in('REJECTED','NEEDS_CORRECTION')", a["id"]))
        out["judged"] += 1
        if h["verdict"] == "bad" and not flagged:
            out["user_bad_inspection_clean"].append(a["id"])
        elif h["verdict"] == "good" and flagged:
            out["user_good_inspection_bad"].append(a["id"])
        else:
            out["agree"] += 1
    return out


# ------------------------------------------------------------ time
def estimates(db: DB) -> list[dict]:
    """Measured history only. Fewer than five samples: no estimate."""
    groups = {}
    for r in db.q("select activity,basis_json,seconds,units,unit from time_log order by rowid"):
        b = json.loads(r["basis_json"])
        key = (r["activity"], r["unit"] or "", json.dumps({k: b[k] for k in ("kind", "size", "gpu", "profile", "capability", "role", "action", "model") if k in b}, sort_keys=True))
        groups.setdefault(key, []).append(r["seconds"] / r["units"] if r["units"] else r["seconds"])
    out = []
    for (act, unit, basis), v in sorted(groups.items()):
        x = {"activity": act, "per": unit or "run", "basis": json.loads(basis), "samples": len(v)}
        if len(v) >= 5:
            q = statistics.quantiles(v, n=10)
            x.update(median_s=round(statistics.median(v), 3), low_s=round(q[1], 3), high_s=round(q[7], 3))
        out.append(x)
    return out
