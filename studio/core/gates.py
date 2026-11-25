"""Where a film stands, what the user is needed for, and the approvals that only the user can give."""
from __future__ import annotations

import json

import psutil

from . import backup, cut, docs, edit, films, flow, images, packages, script
from .db import DB, RuleRefused, data_dir, now

GATES = [("G0_script", "Script fixed"), ("G1_plan", "Production plan"), ("G2_canon", "Canon: people, places, things"), ("G3_shots", "Shot design per scene"), ("G3_images", "Image plan"), ("G4_frame_pair", "Frames of a generated shot"), ("G5_package", "Gemini package"),
         ("G7_clip", "Imported clip"), ("G9_voice_package", "Voice package"), ("G9_sfx_package", "Sound package"), ("G8_scene", "Scene edit"), ("G10_film", "Film edit"), ("G11_release", "Release")]


# ------------------------------------------------------------ approvals of edits and clips
def approve_cut(db: DB, cid, reason=""):
    c = db.one("select * from cut_rev where id=?", cid)
    if not c:
        raise RuleRefused("there is no such edit")
    last = edit.latest(db, c["film_id"], c["scope"])
    if last["id"] != cid:
        raise RuleRefused(f"revision {c['rev']} is not the newest; look at revision {last['rev']}")
    if not db.one("select 1 from render r join timeline_rev t on t.id=r.timeline_rev_id where t.cut_rev_id=? and r.kind in('review','master')", cid):
        raise RuleRefused("render this revision and watch it first")
    _, status, prob = edit.resolve(db, c["film_id"], c["scope"], last["doc"])
    serious = [p for p in prob if p["severity"] in ("critical", "major")]
    neg = [r for r in cut_reviews(db, cid) if r["verdict"] in ("REJECTED", "NEEDS_CORRECTION")]
    if (serious or neg) and len(reason.strip()) < 10:
        raise RuleRefused("there are open points: " + "; ".join([f"{p['where']}: {p['text']}" for p in serious[:3]] + [f"{r['role']} says {r['verdict'].replace('_', ' ').lower()}" for r in neg]) + ". Correct them, or approve with a reason.")
    gate = "G10_film" if c["scope"] == "film" else "G8_scene"
    with db.tx():
        db.run("update cut_rev set status='superseded' where film_id=? and scope=? and status='approved'", c["film_id"], c["scope"])
        db.run("update cut_rev set status='approved',approved_by='user' where id=?", cid)
        a = flow.approve(db, gate, "cut_rev", cid, [("cut", cid)], "user", {"problems": len(prob), "stand_ins": sum(v not in FINISHED for v in status.values())}, *(("waived", reason.strip()) if (serious or neg) else ("approved", None)))
    flow.changed(db, c["film_id"], "cut.approved")
    return a


FINISHED = ("generated clip", "generated still", "approved take", "approved effect", "library sound", "studio sound", "chosen file", "black", "title card")


def cut_reviews(db: DB, cid):
    return [{**r, "body": json.loads(r.pop("body_json"))} for r in db.q("select r.id,r.verdict,r.body_json,t.role from report r join task t on t.id=r.task_id where t.target_type='cut_rev' and t.target_id=? order by r.rowid", cid)]


def approve_clip(db: DB, aid, reason=""):
    """The clip's defects are known and you still want it: that is the gate, not the word 'good'."""
    at = db.one("select a.*,t.film_id from attempt a join generation_target t on t.id=a.target_id where a.result_asset_id=? and t.kind='video'", aid)
    if not at:
        raise RuleRefused("this is not a clip of the film")
    if at["outcome"] != "accepted":
        raise RuleRefused("accept the clip first")
    rv = db.q("select r.id,r.verdict,t.role from report r join task t on t.id=r.task_id where t.target_type='asset' and t.target_id=? and t.gate='G7_clip' order by r.rowid", aid)
    bad = [r for r in rv if r["verdict"] in ("REJECTED", "NEEDS_CORRECTION", "UNCERTAIN", "NEEDS_INSPECTION")]
    if (not rv or bad) and len(reason.strip()) < 10:
        raise RuleRefused("no inspector has looked at it yet; write why you approve it anyway" if not rv else f"{bad[-1]['role']} says {bad[-1]['verdict'].replace('_', ' ').lower()}; write why you approve it anyway")
    flow.set_hash(db, "asset", aid, db.val("select sha256 from asset where id=?", aid))
    return flow.approve(db, "G7_clip", "asset", aid, [("asset", aid)], "user", {"reports": [r["id"] for r in rv]}, *(("waived", reason.strip()) if (not rv or bad) else ("approved", None)))


# ------------------------------------------------------------ lists for pages
def scopes(db: DB, film):
    out = [{"scope": f"scene:{s['id']}", "title": f"Scene {s['number']}", "slug": s["slug"], "rev": (edit.latest(db, film, f"scene:{s['id']}") or {}).get("rev"), "shots": db.val("select count(*) from shot where scene_id=? and state<>'dropped'", s["id"])} for s in db.q("select id,number,slug from scene where film_id=? order by number", film)]
    out.append({"scope": "film", "title": "Whole film", "slug": "", "rev": (edit.latest(db, film, "film") or {}).get("rev"), "shots": None})
    return out


def shots(db: DB, film):
    st, out = cut.last_status(db, film), []
    for s in db.q("select sh.id,sh.code,sh.tier,sh.state,sh.scene_id,s.number,s.slug,ss.spec_json,ss.technique,ss.status,ss.rev from shot sh join scene s on s.id=sh.scene_id join shot_spec ss on ss.shot_id=sh.id where s.film_id=? and sh.state<>'dropped' and ss.status<>'superseded' order by s.number,sh.ordinal,sh.code", film):
        j = json.loads(s.pop("spec_json"))
        d = films.blocking_dir(db, film)
        parts = [p.name for p in sorted(d.iterdir()) if p.is_dir() and (p.name in (s["code"], s["id"].replace(":", "_")) or p.name.startswith(s["code"]))] if d.exists() else []
        pic = None
        for role in ("still", "first", "plate"):
            code = db.val("select code from image_spec where film_id=? and shot_id=? and role=? and retired=0", film, s["id"], role)
            pic = pic or (images.approved(db, film, code) if code else None)
        out.append({**s, "spec": j, "now": st.get(s["code"]) or next((v for k, v in st.items() if k.startswith(s["code"]) and not k.startswith(("voice", "sound"))), None), "previs": parts, "picture": pic, "can_previs": bool(j.get("previs")),
                    "images": db.q("select code,role,kind from image_spec where film_id=? and shot_id=? and retired=0 order by ordinal", film, s["id"]), "motion": bool(db.one("select 1 from motion_plan where shot_id=?", s["id"]))})
    return {"shots": out}


def makers(db: DB, film):
    """What could be asked of an agent now, and what is missing for the rest."""
    from ..agents import service
    out, scenes = [], db.q("select id,number,slug from scene where film_id=? order by number", film)

    def item(action, scope, title):
        ok, why = service.prerequisites(db, film, action, scope)
        role, kind, *_ = service.MAKE[action]
        t = db.one("select id,state from task where action=? and target_id=? and state in('ready','leased') order by rowid desc limit 1", action, f"{film}|{scope}")
        d = db.one("select id,rev,status from work_doc where film_id=? and kind=? and scope=? order by rev desc limit 1", film, kind, scope) if kind != "cut" else None
        out.append({"action": action, "scope": scope, "title": title, "role": role, "ready": ok, "why": why, "task": t, "doc": d})
    item("make_plan", "film", "Production plan")
    item("make_canon", "film", "Canon")
    item("make_images", "film", "Identity pictures and image plan")
    for s in scenes:
        n = f"Scene {s['number']}"
        item("make_shots", f"scene:{s['id']}", f"{n}: shots")
        item("make_images", f"scene:{s['id']}", f"{n}: frames")
        item("make_sound", f"scene:{s['id']}", f"{n}: voices and sounds")
        item("make_cut", f"scene:{s['id']}", f"{n}: edit")
        item("make_mix", f"scene:{s['id']}", f"{n}: sound of the edit")
        item("make_post", f"scene:{s['id']}", f"{n}: finishing")
    for r in db.q("select sh.id,sh.code from shot sh join scene s on s.id=sh.scene_id where s.film_id=? and sh.state<>'dropped' and exists(select 1 from image_spec i where i.shot_id=sh.id and i.role='first') and exists(select 1 from image_spec i where i.shot_id=sh.id and i.role='last') order by s.number,sh.ordinal", film):
        item("make_motion", f"shot:{r['id']}", f"{r['code']}: motion")
    item("make_release", "film", "Release text")
    return out


def needs_you(db: DB, film) -> list[dict]:
    """Everything that waits for the user, most unblocking first. Waiting is normal; only the first group is trouble."""
    out = []

    def add(p, kind, title, why, page, ref=None, trouble=False):
        out.append({"priority": p, "kind": kind, "title": title, "why": why, "page": page, "ref": ref, "trouble": trouble})
    for r in db.q("select r.id,r.capability,r.est_amount,e.unit from api_request r join spend_envelope e on e.id=r.envelope_id where r.state='unknown'"):
        add(1, "unknown", "A paid request has no answer", "It may have been charged. Check your account history, then say what you found.", "home", r["id"], True)
    for p in db.q("select * from stage_pause where film_id=? and resolution is null", film):
        add(2, "pause", f"{p['service'].capitalize()} is paused", p["reason"], "home", p["id"], True)
    for t in db.q("select * from generation_target where film_id=? and state='stopped'", film):
        add(3, "stopped", t["title"], "All attempts are used. Look at why, then allow more or change the plan.", {"voice": "voice", "sfx": "sound", "image": "images", "video": "video"}.get(t["kind"], "review"), t["id"], True)
    if not script.current(db, film):
        add(5, "script", "Import the script", "Everything starts with it.", "script")
    for d in db.q("select id,question from decision where film_id=? and chosen is null", film):
        add(10, "decision", d["question"][:120], "The script leaves this open; only you decide.", "script", d["id"])
    for d in docs.listing(db, film):
        if d["status"] == "proposed" and d["gate"]:
            need = [r for r in docs.KINDS[d["kind"]]["reviewers"] if r not in {x["role"] for x in d["reviews"]}]
            if not need:
                add(12, "doc", f"{d['kind'].replace('_', ' ').capitalize()} {'' if d['scope'] == 'film' else d['scope']}", "Reviewed and waiting for your decision.", "plan", d["id"])
    for kind, table, q in (("voice", "voice_package", "select id,series name from voice_package v where film_id=? and rev=(select max(rev) from voice_package x where x.film_id=v.film_id and x.series=v.series)"), ("sfx", "sfx_package", "select id,cue_id name from sfx_package v where film_id=? and rev=(select max(rev) from sfx_package x where x.cue_id=v.cue_id)")):
        for p in db.q(q, film):
            rv = packages.reviews(db, kind, p["id"])
            if not flow.live(db, packages.GATE[kind][0], p["id"]) and rv:
                add(14, "package", p["name"], f"Reviewed ({rv[-1]['verdict'].replace('_', ' ').lower()}) and waiting for your approval.", "voice" if kind == "voice" else "sound", p["id"])
    for p in db.q("select v.id,ss.shot_id from video_package v join frame_pair f on f.id=v.frame_pair_id join shot_spec ss on ss.id=f.shot_spec_id join shot sh on sh.id=ss.shot_id join scene s on s.id=sh.scene_id where s.film_id=? and v.rowid=(select max(x.rowid) from video_package x join frame_pair g on g.id=x.frame_pair_id join shot_spec s2 on s2.id=g.shot_spec_id where s2.shot_id=ss.shot_id)", film):
        if not flow.live(db, "G5_package", p["id"]) and packages.reviews(db, "video", p["id"]):
            add(14, "package", f"Gemini package for {p['shot_id']}", "Reviewed and waiting for your approval.", "video", p["id"])
    for a in db.q("select a.id,t.title,t.kind,a.created_at from attempt a join generation_target t on t.id=a.target_id where t.film_id=? and a.outcome='pending' and a.result_asset_id is not null and t.kind<>'inspection'", film):
        add(16, "judge", a["title"], "A result is here. Judge it.", {"image": "images", "video": "review", "voice": "voice", "sfx": "sound"}[a["kind"]], a["id"])
    for t in db.q("select * from manual_task where film_id=? and state='waiting'", film):
        add(20, "manual", {"voice": "Record or generate a voice package by hand", "sfx": "Make a sound by hand", "video": "Generate a clip in the Gemini app"}[t["kind"]], "Waiting has no deadline.", {"voice": "voice", "sfx": "sound", "video": "video"}[t["kind"]], t["package_id"])
    for s in scopes(db, film):
        cur = edit.latest(db, film, s["scope"])
        if cur and cur["status"] == "draft" and db.one("select 1 from render r join timeline_rev t on t.id=r.timeline_rev_id where t.cut_rev_id=? and r.kind='review'", cur["id"]):
            add(22, "cut", f"{s['title']}: revision {cur['rev']}", "Rendered and waiting for your decision.", "cut", cur["id"])
    for j in db.q("select id,kind,error from media_job where state='interrupted' and (film_id=? or film_id is null)", film):
        add(8, "job", f"A {j['kind']} was interrupted", "It can continue from the last finished part.", "home", j["id"])
    b = backup.state(db)
    if b["level"] != "good":
        add(4 if b["level"] == "bad" else 30, "backup", "No recent copy of your work" if b["level"] == "bad" else "Only one other drive holds a copy", "Make a copy on the Backup page." if b["level"] == "bad" else "Add a second place, for example another external drive.", "backup", None, b["level"] == "bad")
    return sorted(out, key=lambda x: x["priority"])


def progress(db: DB, film):
    """The chain from script to release, each link with a count."""
    n = lambda q, *a: db.val(q, *a) or 0
    sh = n("select count(*) from shot sh join scene s on s.id=sh.scene_id where s.film_id=? and sh.state<>'dropped'", film)
    img = n("select count(*) from image_spec where film_id=? and retired=0 and kind<>'comparison'", film)
    img_ok = n("select count(*) from image_spec i join slot s on s.film_id=i.film_id and s.kind='image' and s.ref_id=i.code join slot_current c on c.slot_id=s.id where i.film_id=? and i.retired=0 and i.kind<>'comparison'", film)
    mo = n("select count(*) from motion_plan m join shot sh on sh.id=m.shot_id join scene s on s.id=sh.scene_id where s.film_id=?", film)
    mo_ok = n("select count(*) from slot s join slot_current c on c.slot_id=s.id where s.film_id=? and s.kind='video'", film)
    li = n("select count(*) from script_line where film_id=? and id not like '%CAST-%'", film)
    li_ok = n("select count(*) from slot s join slot_current c on c.slot_id=s.id where s.film_id=? and s.kind='voice' and s.ref_id not like '%CAST-%'", film)
    cu = n("select count(*) from sound_cue c join scene s on s.id=c.scene_id where s.film_id=? and c.kind<>'designed_silence'", film)
    cu_ok = n("select count(*) from slot s join slot_current c on c.slot_id=s.id where s.film_id=? and s.kind='sfx'", film)
    sc = n("select count(*) from scene where film_id=?", film)
    ed = len({r["scope"] for r in db.q("select distinct scope from cut_rev where film_id=? and scope like 'scene:%'", film)})
    ed_ok = len({r["scope"] for r in db.q("select distinct scope from cut_rev where film_id=? and scope like 'scene:%' and status='approved'", film)})
    has = lambda k: bool(n("select 1 from current_hash where dep_type=? and dep_id=?", k, film))
    return [{"key": "script", "title": "Script", "done": int(bool(script.current(db, film))), "of": 1, "page": "script"}, {"key": "plan", "title": "Plan", "done": int(has("plan")), "of": 1, "page": "plan"}, {"key": "canon", "title": "Canon", "done": n("select count(*) from canon_revision r join film_canon f on f.entity_id=r.entity_id where f.film_id=? and r.status='locked'", film), "of": n("select count(*) from film_canon where film_id=?", film), "page": "plan"},
            {"key": "shots", "title": "Scenes", "done": n("select count(distinct s.id) from scene s join shot sh on sh.scene_id=s.id where s.film_id=? and sh.state<>'dropped'", film), "of": sc, "page": "shots", "note": f"{sh} shots"}, {"key": "images", "title": "Images", "done": img_ok, "of": img, "page": "images"}, {"key": "clips", "title": "Gemini clips", "done": mo_ok, "of": mo, "page": "video"},
            {"key": "voices", "title": "Voice lines", "done": li_ok, "of": li, "page": "voice"}, {"key": "sounds", "title": "Sounds", "done": cu_ok, "of": cu, "page": "sound"}, {"key": "edits", "title": "Scene edits", "done": ed_ok, "of": sc, "page": "cut", "note": f"{ed} started"}, {"key": "film", "title": "Film edit", "done": n("select count(*) from cut_rev where film_id=? and scope='film' and status='approved'", film) and 1, "of": 1, "page": "cut"},
            {"key": "release", "title": "Release", "done": n("select count(*) from release_package where film_id=? and status='approved' and kind='long'", film) and 1, "of": 1, "page": "release"}]


def status(db: DB, film, app_state):
    m = psutil.virtual_memory()
    st = cut.last_status(db, film)
    return {"film": db.one("select id,title,status,logline,is_benchmark,fps_num,width,height from film where id=?", film), "modes": {s: flow.mode(db, film, s) for s in ("voice", "sfx", "stills", "inspection", "video")}, "needs_you": needs_you(db, film), "progress": progress(db, film),
            "stand_ins": sorted(k for k, v in st.items() if v not in FINISHED), "shots": [{"code": s["code"], "tier": s["tier"], "purpose": s["spec"]["purpose"], "technique": s["technique"], "now": s["now"]} for s in shots(db, film)["shots"]], "parts": st,
            "pauses": [{**p, "error": json.loads(p["error_json"]), "options": json.loads(p["options_json"])} for p in db.q("select * from stage_pause where film_id=? and resolution is null", film)],
            "waiting": db.q("select * from manual_task where film_id=? and state='waiting'", film), "unknown": db.q("select r.id,r.capability,r.est_amount,r.created_at,r.provider_job_id,e.unit,p.surface from api_request r join spend_envelope e on e.id=r.envelope_id join provider_profile p on p.id=r.profile_id where r.state='unknown'"),
            "jobs": [{**j, "progress": json.loads(j["progress_json"] or "{}")} for j in db.q("select id,kind,state,progress_json,error from media_job order by rowid desc limit 6")],
            "machine": {"ram_free_gb": round(m.available / 2 ** 30, 1), "ram_total_gb": round(m.total / 2 ** 30, 1), "disk_free_gb": round(__import__("shutil").disk_usage(data_dir()).free / 2 ** 30), "studio_mb": psutil.Process().memory_info().rss // 2 ** 20},
            "recovered": app_state.recovered, "migrated": app_state.migrated, "backup": {k: v for k, v in backup.state(db).items() if k != "targets"}, "stopped": db.q("select * from generation_target where film_id=? and state='stopped'", film),
            "agents": db.q("select role,count(*) n from task where film_id=? and state='ready' group by role", film)}
