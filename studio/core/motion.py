"""From two approved frames to a Gemini package for the user's hands, and back from the imported clip to evidence."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import cv2

from ..connectors.broker import Broker, Refused
from ..inspect import image as ii
from ..inspect import video as iv
from . import assets, films, flow, images, packages
from .common import _close_task, _open_or_start, checks, reviews_of, save_check, take_in, views_dir
from .db import DB, RuleRefused, data_dir, new_id, now, sha


def film_of_shot(db: DB, shot):
    return db.val("select s.film_id from shot sh join scene s on s.id=sh.scene_id where sh.id=?", shot)


def current_spec(db: DB, shot) -> dict:
    r = db.one("select * from shot_spec where shot_id=? and status<>'superseded' order by rev desc limit 1", shot)
    if not r:
        raise RuleRefused("this shot has no design yet")
    return r


def plan_of(db: DB, shot) -> dict:
    r = db.one("select * from motion_plan where shot_id=?", shot)
    if not r:
        raise RuleRefused("no motion has been planned for this shot")
    return {**r, "draft": json.loads(r["draft_json"]), "zones": json.loads(r["zones_json"])}


def shot_of_pair(db: DB, fid):
    return db.val("select ss.shot_id from frame_pair f join shot_spec ss on ss.id=f.shot_spec_id where f.id=?", fid)


def video_state(db: DB, film):
    out = []
    for m in db.q("select m.*,sh.code from motion_plan m join shot sh on sh.id=m.shot_id join scene s on s.id=sh.scene_id where s.film_id=? order by s.number,sh.ordinal,sh.code", film):
        shot, fr, d = m["shot_id"], [m["first_image"], m["last_image"]], json.loads(m["draft_json"])
        a, b = (images.approved(db, film, x) for x in fr)
        pair = db.one("select f.* from frame_pair f join shot_spec ss on ss.id=f.shot_spec_id where ss.shot_id=? order by f.rowid desc limit 1", shot)
        pk = db.one("select * from video_package where frame_pair_id=? order by rev desc limit 1", pair["id"]) if pair else None
        doc = packages.doc_of(db, "video", pk["id"]) if pk else None
        tg = db.one("select * from generation_target where film_id=? and kind='video' and ref_id=?", film, shot)
        atts = db.q("select a.*,(select 1 from attempt_withdrawal w where w.attempt_id=a.id) withdrawn from attempt a where target_id=? order by attempt_no", tg["id"]) if tg else []
        for x in atts:
            x["checks"] = checks(db, x["result_asset_id"]) if x["result_asset_id"] else []
            x["reviews"] = reviews_of(db, x["result_asset_id"]) if x["result_asset_id"] else []
        out.append({"shot": shot, "code": m["code"], "frames_needed": fr, "first": a, "last": b, "pair": {**pair, "checks": json.loads(pair["checks_json"])} if pair else None, "pair_approved": bool(pair and flow.live(db, "G4_frame_pair", pair["id"])),
                    "package": pk["id"] if pk else None, "doc": doc, "problems": packages.lint_video(doc) if doc else [], "reviews": [{**r, "body": json.loads(r.pop("body_json"))} for r in packages.reviews(db, "video", pk["id"])] if pk else [], "approved": bool(pk and flow.live(db, "G5_package", pk["id"])),
                    "target": tg, "limit": flow.limit(db, tg["id"]) if tg else 3, "used": len(flow.counted(db, tg["id"])) if tg else 0, "attempts": atts, "ledger": db.q("select g.*,l.label from gen_attempt g left join account_label l on l.id=g.account_label_id where package_id=? order by attempt_no", pk["id"]) if pk else [],
                    "task": db.one("select * from manual_task where kind='video' and package_id=?", pk["id"]) if pk else None, "draft": d})
    return out


def frame_slots(db: DB, shot):
    m, film = plan_of(db, shot), film_of_shot(db, shot)
    return [("slot", db.val("select id from slot where film_id=? and kind='image' and ref_id=?", film, x)) for x in (m["first_image"], m["last_image"])]


def make_pair(db: DB, shot):
    m, film, spec = plan_of(db, shot), film_of_shot(db, shot), current_spec(db, shot)
    a, b = (images.approved(db, film, x) for x in (m["first_image"], m["last_image"]))
    if not a or not b:
        raise RuleRefused("approve the first and the last frame on the Images page first")
    out, name = [], safe(shot)
    w, h = gemini_size(db, film)
    for src, tag in ((a, "1_FIRST"), (b, "2_LAST")):
        img, info = ii.conform(ii.read(assets.path_of(db, src)), w, h)
        tmp = data_dir() / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        p = tmp / f"{name}_{tag}.png"
        cv2.imwrite(str(p), img)
        aid, _ = assets.add(db, p, origin="derived", storage="derived", parents=[(src, "conform", info)], provenance={"frame": tag, "shot": shot})
        out.append(aid)
    d = m["draft"]["duration_s"]
    rep = ii.pair(assets.path_of(db, out[0]), assets.path_of(db, out[1]), m["camera"], m["zones"], sum(d.values()) / 2)
    n = 1 + (db.val("select max(rev) from frame_pair where shot_spec_id=?", spec["id"]) or 0)
    fid = new_id("fp")
    with db.tx():
        db.insert("frame_pair", id=fid, shot_spec_id=spec["id"], rev=n, first_asset_id=out[0], last_asset_id=out[1], checks_json=rep)
        flow.set_hash(db, "frame_pair", fid, sha(out))
        for x in out:
            flow.set_hash(db, "asset", x, db.val("select sha256 from asset where id=?", x))
        db.event("system", "pair.checked", "frame_pair", fid, {"outcome": rep["outcome"]})
    return {"id": fid, "report": rep}


def safe(shot):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in shot)


def approve_pair(db: DB, fid, accept_warnings=False):
    f = db.one("select * from frame_pair where id=?", fid)
    rep = json.loads(f["checks_json"])
    if rep["outcome"] == "fail":
        raise RuleRefused("the pair failed its checks; correct a frame instead of spending a generation")
    if rep["outcome"] == "warn" and not accept_warnings:
        raise RuleRefused("the pair has warnings; look at them and confirm")
    shot = shot_of_pair(db, fid)
    m, film = plan_of(db, shot), film_of_shot(db, shot)
    deps = [("frame_pair", fid), ("asset", f["first_asset_id"]), ("asset", f["last_asset_id"])] + frame_slots(db, shot)
    flow.approve(db, "G4_frame_pair", "frame_pair", fid, deps, "user", {"checks": rep["outcome"]})
    d, name, (gw, gh) = dict(m["draft"]), safe(shot), gemini_size(db, film)
    d["attachments"] = [{"order": i + 1, "file": f"{name}_{i + 1}_{t}.png", "role": r, "sha256": db.val("select sha256 from asset where id=?", a), "width": gw, "height": gh}
                        for i, (t, r, a) in enumerate((("FIRST", "first_frame", f["first_asset_id"]), ("LAST", "last_frame", f["last_asset_id"])))]
    pid = packages.register(db, "video", film, d, manual_profile="gemini_app", frame_pair=fid, maker="motion-engineer")
    folder = package_folder(shot)
    folder.mkdir(parents=True, exist_ok=True)
    for x, a in zip(d["attachments"], (f["first_asset_id"], f["last_asset_id"])):
        shutil.copyfile(assets.path_of(db, a), folder / x["file"])
    (folder / "PROMPT.txt").write_text(d["prompt"], encoding="utf-8")
    flow.changed(db, film, "video_package.made")
    return pid


def package_folder(shot) -> Path:
    return data_dir() / "packages" / safe(shot)


def log_generation(db: DB, pid, state, label="", model="", mode="", usage=""):
    n = 1 + (db.val("select max(attempt_no) from gen_attempt where package_id=?", pid) or 0)
    lid = None
    if label.strip():
        r = db.one("select id from account_label where label=?", label.strip())
        lid = r["id"] if r else db.insert("account_label", id=new_id("ac"), label=label.strip()[:40])
    return db.insert("gen_attempt", id=new_id("ga"), package_id=pid, attempt_no=n, account_label_id=lid, app_model=model, app_mode=mode, usage_note=usage, state=state, confirmed_by="user", performed_on=now()[:10], created_at=now())


def import_video(db: DB, pid, path: Path, label="", model="", usage="", reason=""):
    if not flow.live(db, "G5_package", pid):
        raise RuleRefused("this package is not approved, or something it depends on has changed")
    pr = db.one("select f.*,v.prompt from video_package v join frame_pair f on f.id=v.frame_pair_id where v.id=?", pid)
    shot = shot_of_pair(db, pr["id"])
    film, doc = film_of_shot(db, shot), packages.doc_of(db, "video", pid)
    tid = flow.target(db, film, "video", shot, f"Gemini clip for {shot}")
    aid, at = take_in(db, tid, path, "video", {"provider": "gemini app", "model": model, "prompt": doc["prompt"], "package": pid}, origin="manual_video", reason=reason, provider="gemini app", model=model, provenance={"package": pid, "label": label, "film": film, "shot": shot})
    ga = log_generation(db, pid, "generated", label, model, "", usage)
    db.run("update gen_attempt set result_asset_id=? where id=?", aid, ga)
    assets.set_licence(db, aid, "gemini_app")
    rep = inspect_video(db, aid, shot, pr["first_asset_id"], pr["last_asset_id"], doc)
    _close_task(db, "video", pid, True)
    others = match_other_shots(db, aid, shot)
    flow.changed(db, film, "clip.arrived")
    return {"asset": aid, "attempt": at, "report": rep, "better_match": others}


def import_clip(db: DB, film, shot, path: Path, label="", model="", reason=""):
    """A clip the user made without a studio package. It is the user's right; it is recorded as such, inspected, and follows the same attempts and judging."""
    if film_of_shot(db, shot) != film:
        raise RuleRefused("the film has no such shot")
    if len((reason or "").strip()) < 10:
        raise RuleRefused("write how this clip was made and why it did not come from a package (at least ten characters)")
    tid = flow.target(db, film, "video", shot, f"Clip for {shot}")
    aid, at = take_in(db, tid, path, "video", {"provider": "gemini app", "model": model, "outside_package": reason.strip()}, origin="manual_video", reason=reason, provider="gemini app", model=model, provenance={"outside_package": reason.strip(), "label": label, "film": film, "shot": shot})
    assets.set_licence(db, aid, "gemini_app")
    m = db.one("select first_image,last_image from motion_plan where shot_id=?", shot)
    first, last = (images.approved(db, film, m[k]) for k in ("first_image", "last_image")) if m else (None, None)
    rep = inspect_video(db, aid, shot, first, last)
    flow.changed(db, film, "clip.arrived")
    return {"asset": aid, "attempt": at, "report": rep, "better_match": match_other_shots(db, aid, shot)}


def other_shots(db: DB, film):
    """Shots without a planned generation. A clip made another way can still be brought in for them."""
    out = []
    for r in db.q("select sh.id,sh.code from shot sh join scene s on s.id=sh.scene_id where s.film_id=? and sh.state<>'dropped' and not exists(select 1 from motion_plan m where m.shot_id=sh.id) order by s.number,sh.ordinal,sh.code", film):
        tg = db.one("select * from generation_target where film_id=? and kind='video' and ref_id=?", film, r["id"])
        out.append({**r, "target": tg, "limit": flow.limit(db, tg["id"]) if tg else 3, "used": len(flow.counted(db, tg["id"])) if tg else 0})
    return out


def gemini_size(db: DB, film):
    """The app makes 16:9 or 9:16. A wider film is cropped in the edit, not in the generator."""
    f = films.get(db, film)
    return (1920, 1080) if f["width"] >= f["height"] else (1080, 1920)


def inspect_video(db: DB, aid, shot=None, first=None, last=None, doc=None):
    p = assets.path_of(db, aid)
    f, l = (ii.read(assets.path_of(db, x)) if x else None for x in (first, last))
    d = doc["duration_s"] if doc else {"min": 3, "max": 10}
    film = film_of_shot(db, shot) if shot else None
    m = db.one("select camera from motion_plan where shot_id=?", shot) if shot else None
    want = {"aspect": 16 / 9, "duration": (d["min"], d["max"]), "camera": (m or {}).get("camera", "locked")}
    if film:
        fl = films.get(db, film)
        gw, gh = gemini_size(db, film)
        want.update(aspect=gw / gh, fps=fl["fps_num"])
    rep = iv.inspect(p, json.loads(db.val("select probe_json from asset where id=?", aid)), want, views_dir(aid), f, l)
    if film:
        from ..inspect import face
        rep["outside_package"] = json.loads(db.val("select provenance_json from asset where id=?", aid)).get("outside_package")
        for e, refs in images.refs_for_shot(db, film, shot).items():
            name, t = db.val("select name from canon_entity where id=?", e), face.track(p, refs)
            rep.setdefault("faces", {})[e] = t
            rep["technical"]["items"].append((f"same face as {name} through the clip (measurement only)", t["outcome"], t["text"]))
    save_check(db, aid, "video", rep)
    return rep


def match_other_shots(db: DB, aid, shot):
    """Is this perhaps the clip for a different shot? Compares the first frame with every prepared first frame of the film."""
    fr, film, sc = iv.frame_at(assets.path_of(db, aid), 0), film_of_shot(db, shot), {}
    for r in db.q("""select ss.shot_id,f.first_asset_id from frame_pair f join shot_spec ss on ss.id=f.shot_spec_id join shot sh on sh.id=ss.shot_id join scene s on s.id=sh.scene_id
            where s.film_id=? and f.rowid=(select max(x.rowid) from frame_pair x join shot_spec y on y.id=x.shot_spec_id where y.shot_id=ss.shot_id)""", film):
        ref = ii.read(assets.path_of(db, r["first_asset_id"]))
        sc[r["shot_id"]] = iv.similarity(cv2.resize(ref, (480, 270)), cv2.resize(fr, (480, 270)))["score"]
    best = max(sc, key=sc.get) if sc else None
    return best if best and best != shot and sc[best] > sc.get(shot, 0) + .1 else None


GEMINI_PROMPT = """You are an independent inspector of one short film clip made by a generative model. You have not seen the prompt. Report only what you can see or hear.
Answer with JSON only: {"shots": int, "what_happens": str, "camera": str, "people": [{"description": str, "identity_stable": bool, "problems": [str]}],
"defects": [{"time": "MM:SS.s", "kind": "anatomy|morph|vanish|appear|flicker|physics|camera|cut|texture|text|other", "severity": "critical|major|minor", "description": str}],
"stillness_at_end_seconds": number, "unexpected_sound": str, "cannot_tell": [str]}. An empty defects list is a strong claim; make it only if you checked the whole clip."""


def gemini_spec(db: DB, aid, fps=12):
    a = db.one("select * from asset where id=?", aid)
    secs = float(json.loads(a["probe_json"])["format"]["duration"])
    if secs > 12:
        raise Refused("only clips up to 12 seconds are sent")
    if assets.path_of(db, aid).stat().st_size > 14_000_000:
        raise Refused("the file is too large to send inline; a smaller proxy would be needed")
    kind = "video" if a["kind"] == "video" else "audio"
    return kind, {"provider": "gemini", "kind": kind, "seconds": round(secs, 2), "fps": fps, "prompt": GEMINI_PROMPT, "sha": a["sha256"]}


def gemini_inspect(db: DB, br: Broker, film, aid, fps=12, expect=None):
    """A second opinion from a model that takes video. Evidence, never a verdict."""
    kind, spec = gemini_spec(db, aid, fps)
    env = films.envelope(db, film, "inspection")
    if not env:
        raise Refused("this film has no inspection allowance yet; open one on the Budget page")
    price = br.estimate("gemini_inspect", f"inspect_{kind}", spec)
    if expect is not None and price > expect:
        raise Refused(f"the price is now at most ${price / 1e6:.3f}, more than you agreed to; nothing was sent")
    tid = flow.target(db, film, "inspection", aid, f"Gemini inspection of {aid}", 2)
    prev = flow.counted(db, tid)
    if prev and prev[-1]["outcome"] == "accepted":
        raise Refused("this clip has already been inspected; the stored answer is shown")
    at = _open_or_start(db, tid, spec, "")
    r = br.run(film=film, profile="gemini_inspect_api", capability=f"inspect_{kind}", envelope=env, attempt=at, spec=spec, refs=[aid], requested_by="user", cost_class="verification", store=False)
    if r["state"] == "succeeded":
        from ..connectors.adapters import parse_json
        j = parse_json(r["text"] or "")
        cut_off = str(r["meta"].get("finish") or "").upper() in ("MAX_TOKENS", "LENGTH")
        flow.judge_attempt(db, at, "accepted" if j else "rejected", "system")
        rep = {"version": "g0.2", "outcome": "warn" if not j or cut_off or j.get("defects") else "pass", "items": [("answer complete", "warn", "the answer was cut off")] if cut_off else [], "facts": {"model": r["meta"].get("model"), "fps": fps, "usage": r["meta"].get("usage"), "cut_off": cut_off}, "answer": j or {"unreadable": (r["text"] or "")[:1500]},
               "examined": [f"the clip itself at {fps} frames per second, with sound, by Gemini"], "not_examined": ["it is one model's opinion and cannot approve anything"]}
        save_check(db, aid, "gemini", rep)
        r["report"] = rep
    return r
