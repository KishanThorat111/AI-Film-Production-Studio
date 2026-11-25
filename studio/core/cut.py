"""Renders an edit: preview, review cut, master. Says plainly what is still a stand-in and whether rights would allow a finished film."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf

from ..engine import render, sound, synth
from . import assets, edit
from .db import DB, data_dir, new_id, now, sha


# ------------------------------------------------------------ studio-made sounds (shared by every film)
def ensure_sounds(db: DB):
    for title, (fn, kind, tags) in synth.SOUNDS.items():
        if db.one("select 1 from library_item where title=?", title) or db.one("select 1 from asset where provenance_json like ?", f'%"synth":"{title}"%'):
            continue
        tmp = data_dir() / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        p = tmp / f"syn_{new_id('s')}.wav"
        sf.write(str(p), fn().astype(np.float32), 48000, subtype="PCM_24")
        aid, _ = assets.add(db, p, origin="authored", provenance={"synth": title}, actor="engine")
        p.unlink(missing_ok=True)
        assets.set_licence(db, aid, "own")


def library_ready(db: DB):
    """Sounds that could be in the library: made by the studio, or brought in for it."""
    out = []
    for r in db.q("select a.id,a.origin,a.provenance_json,(select 1 from library_item l where l.asset_id=a.id) admitted,t.source,t.verified_by from asset a left join asset_licence x on x.asset_id=a.id left join licence_terms t on t.id=x.terms_id "
                  "where a.kind='audio' and (provenance_json like '%\"synth\"%' or (a.origin in('licensed','recorded') and not exists(select 1 from attempt b where b.result_asset_id=a.id)))"):
        p = json.loads(r.pop("provenance_json") or "{}")
        out.append({**r, "title": p.get("synth") or p.get("title") or "untitled", "kind": p.get("library_kind"), "made": "synth" in p})
    return out


# ------------------------------------------------------------ state for pages: reads only
BUS = {"foley": "foley", "hard_fx": "hard_fx", "ambience": "ambience", "room_tone": "ambience", "music": "music", "motif": "music"}


def placeable(db: DB, film, scope):
    """What can be put into this edit by name: lines and sounds of the scene, library sounds, shots, approved images."""
    sc = db.val("select id from scene where id=?", scope[6:]) if scope.startswith("scene:") else None
    lines = edit.lines_of(db, film, sc) if sc else []
    cues = db.q("select id,kind,purpose from sound_cue where scene_id=? and kind<>'designed_silence'", sc) if sc else []
    return {"lines": [{"id": l["id"], "text": l["text"][:70], "bus": "narration" if l["narration"] else "dialogue", "ready": bool(edit.slot_asset(db, film, "voice", l["id"]))} for l in lines],
            "cues": [{"id": c["id"], "purpose": c["purpose"][:70], "bus": BUS[c["kind"]], "status": edit.heard(db, film, {"cue": c["id"]}, False)[1]} for c in cues],
            "library": [{"id": r["asset_id"], "title": r["title"], "kind": r["kind"], "bus": BUS.get(r["kind"], "foley")} for r in db.q("select asset_id,title,kind from library_item where retired_at is null and kind<>'impulse_response' order by kind,title")],
            "shots": db.q("select id,code from shot where scene_id=? and state<>'dropped' order by ordinal,code", sc) if sc else [],
            "images": [r["code"] for r in db.q("select i.code from image_spec i join slot s on s.film_id=i.film_id and s.kind='image' and s.ref_id=i.code join slot_current c on c.slot_id=s.id where i.film_id=? and i.retired=0 and i.kind='frame' order by i.ordinal", film)]}


def state(db: DB, film, scope):
    cur = edit.latest(db, film, scope)
    if not cur:
        return {"missing": "There is no edit for this yet."}
    try:
        tl, status, prob = edit.resolve(db, film, scope, cur["doc"])
    except edit.EditRefused as e:
        return {"missing": f"The edit cannot be shown: {e}", "revisions": revisions(db, film, scope)}
    return {"rev": cur["rev"], "status_of_rev": cur["status"], "author": cur["author"], "doc": cur["doc"], "timeline": tl, "status": status, "problems": prob, "planned": [p for p in edit.planned(prob) if p["text"]], "renders": renders(db, film, scope), "revisions": revisions(db, film, scope), "final_blockers": final_blockers(db, film, tl),
            "notes": db.q("select * from cut_note where film_id=? and scope=? order by rowid desc", film, scope)}


def revisions(db: DB, film, scope):
    return db.q("select id,rev,base_rev,author,status,note,created_at,json_array_length(commands_json) commands from cut_rev where film_id=? and scope=? order by rev desc", film, scope)


def renders(db: DB, film, scope):
    rows = db.q("""select r.id,r.kind,r.asset_id,r.qa_json,r.created_at,r.seconds,t.rev,t.id timeline_rev,c.rev cut_rev from render r join timeline_rev t on t.id=r.timeline_rev_id join timeline x on x.id=t.timeline_id
        left join cut_rev c on c.id=t.cut_rev_id where x.film_id=? and x.name in(?,?) order by r.rowid desc limit 30""", film, scope, "benchmark" if scope.startswith("scene:") else scope)
    for x in rows:
        x["qa"] = json.loads(x.pop("qa_json") or "{}")
    return rows


def last_status(db: DB, film):
    """What the newest stored timelines of this film were made from, without making anything."""
    out = {}
    for r in db.q("select t.status_json from timeline_rev t join timeline x on x.id=t.timeline_id where x.film_id=? and t.status_json is not null order by t.rowid", film):
        out.update(json.loads(r["status_json"]))
    return out


# ------------------------------------------------------------ rights
def final_blockers(db: DB, film, tl):
    """Would the rights rules allow this as a finished film? Asks the database inside a transaction that is rolled back."""
    out = []
    for a in used_assets(tl):
        c = db.conn()
        c.execute("begin")
        try:
            c.execute("insert into asset_use(id,film_id,asset_id,purpose,used_as) values(?,?,?,'final','check')", (new_id("u"), film, a))
        except Exception as e:
            p = db.one("select provenance_json,origin from asset where id=?", a)
            out.append({"asset": a, "why": str(e), "what": (p or {}).get("provenance_json", "")[:120]})
        finally:
            c.execute("rollback")
    return out


def used_assets(tl) -> set[str]:
    out = {x["asset"] for x in tl["audio"] if x.get("asset")}
    for t in tl["video"]:
        for c in t["clips"]:
            out |= {x for x in [c.get("asset"), c.get("matte")] + [e.get("mask") for e in c.get("effects", [])] + [e["params"].get("aux") for e in c.get("effects", [])] if isinstance(x, str)}
    return out


# ------------------------------------------------------------ rendering
def save_timeline(db: DB, film, scope, tl, status, cut_rev=None, author="film-editor"):
    tid = db.val("select id from timeline where film_id=? and name=?", film, scope) or db.insert("timeline", id=new_id("tl"), film_id=film, name=scope)
    h = sha(tl)
    r = db.one("select id from timeline_rev where timeline_id=? and content_hash=?", tid, h)
    if r:
        return r["id"]
    n = 1 + (db.val("select max(rev) from timeline_rev where timeline_id=?", tid) or 0)
    return db.insert("timeline_rev", id=new_id("tr"), timeline_id=tid, rev=n, doc_json=tl, content_hash=h, author=author, status="draft", cut_rev_id=cut_rev, status_json=status, created_at=now())


SCALE = {"preview": .5, "review": 1.0, "master": 1.0}


def picture(tl, res, jobs, jid, **k):
    """The picture, segment by segment. A lost graphics device is opened again once; finished segments are kept."""
    for attempt in (1, 2):
        r = render.Renderer(tl, res, **k)
        try:
            return r, r.render(data_dir() / "cache" / "segments", lambda d, n: jobs.progress(jid, d, n, "rendering picture"), lambda: jobs.stopping(jid))
        except KeyboardInterrupt:
            raise
        except Exception as e:
            lost = any(w in f"{type(e).__name__} {e}".lower() for w in ("device", "lost", "gpu", "out of memory", "outofmemory", "encoder failed"))
            if attempt == 2 or not lost:
                raise
            jobs.progress(jid, 0, tl["frames"], "the graphics device was lost; opening it again")
            del r
            time.sleep(2)


def render_job(db: DB, jobs, jid, args):
    film, scope, kind = args["film"], args["scope"], args.get("kind", "review")
    cur = edit.latest(db, film, scope) if not args.get("cut_rev") else db.one("select * from cut_rev where id=?", args["cut_rev"])
    if not cur:
        raise RuntimeError("there is no edit to render")
    doc = cur.get("doc") or json.loads(cur["doc_json"])
    jobs.progress(jid, 0, 1, "preparing stand-ins")
    tl, status, prob = edit.resolve(db, film, scope, doc, make=True)
    crit = [x for x in prob if x["severity"] == "critical"]
    if crit:
        raise RuntimeError("the edit has problems: " + "; ".join(f"{x['where']}: {x['text']}" for x in crit)[:300])
    if kind == "master":
        bad = {k: v for k, v in status.items() if v not in ("generated clip", "generated still", "approved take", "approved effect", "library sound", "studio sound", "chosen file", "black", "title card")}
        rights = final_blockers(db, film, tl)
        if bad or rights:
            raise RuntimeError(f"a master needs finished material: {len(bad)} stand-ins ({', '.join(list(bad)[:4])}), {len(rights)} rights problems")
    rid = save_timeline(db, film, scope, tl, status, cur["id"])
    work = data_dir() / "renders" / rid / kind
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    res = lambda a: edit._path(db, a)
    r, segs = picture(tl, res, jobs, jid, scale=SCALE[kind], burn=kind == "preview")
    t1 = time.time()
    m = render.join(segs, work / "master.mkv")
    jobs.progress(jid, tl["frames"], tl["frames"], "mixing sound")
    mx = sound.mix(tl, res)
    y, info = sound.master(mx, tl.get("loudness", -16))
    wav = sound.write(y, work / "mix.wav")
    if kind != "preview":
        sound.stems(mx, work / "stems")
    del y, mx
    t2 = time.time()
    jobs.progress(jid, tl["frames"], tl["frames"], "making the delivery file")
    d = render.deliver(m, wav, work / "cut.mp4", tl["fps"], kbps=4000 if kind == "preview" else 16000)
    t3 = time.time()
    jobs.progress(jid, tl["frames"], tl["frames"], "checking the file")
    q = render.qa(work / "cut.mp4", tl, wav)
    aid, _ = assets.add(db, work / "cut.mp4", origin="rendered", storage="derived" if kind != "preview" else "proxy", provenance={"timeline_rev": rid, "kind": kind}, actor="engine")
    rep = {"version": "e0.2", "outcome": q["outcome"], "items": q["items"], "facts": {"frames": tl["frames"], "seconds": round(tl["frames"] * tl["fps"][1] / tl["fps"][0], 2), "render_fps": round(r.rendered / r.render_s, 1) if r.render_s else None, "frames_rendered": r.rendered, "frames_reused": tl["frames"] - r.rendered,
           "picture_s": round(t1 - t0, 1), "sound_s": round(t2 - t1, 1), "deliver_s": round(t3 - t2, 1), "check_s": round(time.time() - t3, 1), "sync": d, "loudness": info, "gpu": r.g.info.get("device"), "engine_rss_mb": psutil.Process().memory_info().rss // 2 ** 20, "problems": prob}, "status": status}
    db.insert("check_result", id=new_id("cr"), asset_id=aid, check_name="render", check_version=rep["version"], outcome=rep["outcome"], value_json=rep, basis_hash=sha([rep["items"], now(), jid]), created_at=now())
    rn = db.insert("render", id=new_id("rn"), timeline_rev_id=rid, kind=kind, engine_version=render.ENGINE_VERSION, gpu_key=r.g.key, settings_json={"size": [r.w, r.h]}, asset_id=aid, qa_json=rep, created_at=now(), seconds=round(time.time() - t0, 1))
    db.insert("time_log", id=new_id("tm"), film_id=film, activity="render", basis_json={"kind": kind, "size": [r.w, r.h], "gpu": r.g.info.get("device"), "engine": render.ENGINE_VERSION, "reused": tl["frames"] - r.rendered}, seconds=round(time.time() - t0, 2), units=tl["frames"], unit="frame", ref=rn, at=now())
    for a in used_assets(tl):
        if not db.one("select 1 from asset_use where film_id=? and asset_id=? and timeline_rev_id=?", film, a, rid):
            db.insert("asset_use", id=new_id("us"), film_id=film, asset_id=a, timeline_rev_id=rid, purpose="temp", used_as="review cut")
    for p in (work / "master.mkv", work / "cut.mp4"):        # the stored asset is the copy that counts
        if kind != "master":
            p.unlink(missing_ok=True)
    from . import flow
    flow.changed(db, film, "rendered")
    return {"render": rn, "asset": aid, "outcome": rep["outcome"]}


def trim_cache(max_gb=20):
    """Rendered segments can always be made again; the oldest go first."""
    d = data_dir() / "cache" / "segments"
    files = sorted((p for p in d.rglob("*.mkv")), key=lambda p: p.stat().st_mtime) if d.exists() else []
    total = sum(p.stat().st_size for p in files)
    for p in files:
        if total <= max_gb * 2 ** 30:
            break
        total -= p.stat().st_size
        p.unlink(missing_ok=True)
