"""Voices and sound effects: by hand first, by API when the user chose it. Same package, same checks, same attempts either way."""
from __future__ import annotations

import json
from pathlib import Path

from ..connectors.broker import Broker, Refused
from ..inspect import audio as ia
from . import assets, flow, packages
from .common import _close_task, _open_or_start, checks, licence, save_check, take_in, usable_terms
from .db import DB, RuleRefused, new_id, now


CONTINUOUS = ("ambience", "room_tone", "music", "motif")


def voice_state(db: DB, film):
    out = []
    for p in db.q("select v.* from voice_package v where film_id=? and rev=(select max(rev) from voice_package x where x.film_id=v.film_id and x.series=v.series) order by series", film):
        doc = json.loads(p["doc_json"])
        lines = []
        for l in doc["lines"]:
            tg = db.one("select * from generation_target where film_id=? and kind='voice' and ref_id=?", film, l["line_id"])
            takes = db.q("""select t.*,a.relpath,at.outcome,at.id attempt_id,at.attempt_no from audio_take t join asset a on a.id=t.asset_id left join attempt at on at.result_asset_id=t.asset_id where t.line_id=? order by take_no""", l["line_id"])
            for t in takes:
                t["checks"] = checks(db, t["asset_id"])
            lines.append({**l, "target": tg, "limit": flow.limit(db, tg["id"]) if tg else 3, "takes": takes, "api_attempts": _api_history(db, tg)})
        prob = packages.lint_voice(doc, {r["id"]: r["text"] for r in db.q("select id,text from script_line where film_id=?", film)})
        out.append({"id": p["id"], "rev": p["rev"], "doc": doc, "lines": lines, "problems": prob, "reviews": _rv(db, "voice", p["id"]), "approved": flow.live(db, "G9_voice_package", p["id"]), "casting": packages.is_casting(doc), "cast": packages.cast_voices(db, p["id"]),
                    "task": db.one("select * from manual_task where kind='voice' and package_id=?", p["id"])})
    return sorted(out, key=lambda x: not x["casting"])


def _rv(db, kind, pid):
    return [{**r, "body": json.loads(r.pop("body_json"))} for r in packages.reviews(db, kind, pid)]


def _api_history(db, tg):
    return db.q("select a.attempt_no,a.outcome,a.reason,r.state,r.est_amount,r.actual_amount,r.created_at from attempt a left join api_request r on r.attempt_id=a.id where a.target_id=? order by a.attempt_no", tg["id"]) if tg else []


def import_voice(db: DB, pid, line_id, path: Path, credits=None, note="", reason="", settings=None, voice="", voice_kind=""):
    """A take made by hand. Waiting has no deadline; a second take needs a reason, because the first one must have been judged."""
    film = db.val("select film_id from voice_package where id=?", pid)
    if not flow.live(db, "G9_voice_package", pid):
        raise RuleRefused("approve this package first")
    doc = packages.doc_of(db, "voice", pid)
    line = next(l for l in doc["lines"] if l["line_id"] == line_id)
    if packages.is_casting(doc) and (not voice.strip() or voice_kind not in ("provider_permanent", "library")):
        raise RuleRefused("write the exact name of the voice you used, and whether it has the infinity sign")
    tid = flow.target(db, film, "voice", line_id, f"{line['speaker']}: {line['text'][:60]}")
    aid, at = take_in(db, tid, path, "audio", {"provider": "elevenlabs", "model_id": doc["model"], "settings": settings or doc["settings"], "text": line["spoken_text"], "note": note}, origin="manual_audio", reason=reason,
                      provider="elevenlabs", model=doc["model"], provenance={"package": pid, "line": line_id, "settings": settings or doc["settings"], "note": note})
    assets.set_licence(db, aid, "elevenlabs_speech", "starter")
    pr = json.loads(db.val("select probe_json from asset where id=?", aid))
    rep = ia.inspect_voice(assets.path_of(db, aid), pr, line["text"])
    bid = None
    if packages.is_casting(doc):
        own = packages.voice_owner(db, db.val("select speaker_id from script_line where id=?", line_id))
        bid = db.insert("voice_binding", id=new_id("vb"), revision_id=own + "_r1", provider="elevenlabs", provider_voice_id=voice.strip()[:120], category=voice_kind, model_id=doc["model"],
                        settings_json={**doc["settings"], "name": voice.strip()[:120]}, status="candidate")
    n = 1 + (db.val("select max(take_no) from audio_take where line_id=?", line_id) or 0)
    db.insert("audio_take", id=new_id("tk"), line_id=line_id, asset_id=aid, take_no=n, binding_id=bid, settings_json=settings or doc["settings"], credits_confirmed=credits, word_timing_json=rep.get("transcript", {}).get("words"),
              package_id=pid, source="manual", confirmed_by="user", format_json=rep["facts"])
    save_check(db, aid, "voice", rep)
    _close_task(db, "voice", pid, all(db.one("select 1 from audio_take where line_id=?", l["line_id"]) for l in doc["lines"]))
    return {"asset": aid, "attempt": at, "report": rep}


# ------------------------------------------------------------ sound effects
def sfx_state(db: DB, film):
    out = []
    for p in db.q("select x.*,c.purpose,c.sourcing,c.search_json from sfx_package x join sound_cue c on c.id=x.cue_id where x.film_id=? and x.rev=(select max(rev) from sfx_package y where y.cue_id=x.cue_id) order by x.cue_id", film):
        doc = json.loads(p["doc_json"])
        tg = db.one("select * from generation_target where film_id=? and kind='sfx' and ref_id=?", film, p["cue_id"])
        atts = db.q("select a.*,r.state rstate,r.est_amount,r.actual_amount from attempt a left join api_request r on r.attempt_id=a.id and r.state<>'failed' where a.target_id=? order by attempt_no", tg["id"]) if tg else []
        for a in atts:
            a["checks"] = checks(db, a["result_asset_id"]) if a["result_asset_id"] else []
        out.append({"id": p["id"], "cue": p["cue_id"], "rev": p["rev"], "doc": doc, "problems": packages.lint_sfx(doc), "reviews": _rv(db, "sfx", p["id"]), "approved": flow.live(db, "G9_sfx_package", p["id"]),
                    "target": tg, "limit": flow.limit(db, tg["id"]) if tg else 3, "attempts": atts, "task": db.one("select * from manual_task where kind='sfx' and package_id=?", p["id"])})
    return out


def cues_state(db: DB, film):
    """Every sound the film needs, whatever fills it: a generated effect, a recording, the library, or nothing yet."""
    from . import edit
    out = []
    for c in db.q("select c.*,s.number from sound_cue c join scene s on s.id=c.scene_id where s.film_id=? order by s.number,c.rowid", film):
        tg = db.one("select * from generation_target where film_id=? and kind='sfx' and ref_id=?", film, c["id"])
        pk = db.one("select id from sfx_package where cue_id=? order by rev desc limit 1", c["id"])
        a, st, _ = edit.heard(db, film, {"cue": c["id"]}, False)
        atts = db.q("select a.*,x.provenance_json from attempt_counted a left join asset x on x.id=a.result_asset_id where a.target_id=? order by a.attempt_no", tg["id"]) if tg else []
        for x in atts:
            x["checks"] = checks(db, x["result_asset_id"]) if x["result_asset_id"] else []
            x["licence"] = db.val("select terms_id from asset_licence where asset_id=?", x["result_asset_id"]) if x["result_asset_id"] else None
        out.append({"id": c["id"], "scene": c["number"], "kind": c["kind"], "purpose": c["purpose"], "sourcing": c["sourcing"], "spec": json.loads(c["spec_json"]), "package": pk["id"] if pk else None, "target": tg, "limit": flow.limit(db, tg["id"]) if tg else 3, "used": len(atts), "attempts": atts, "heard": a, "status": st})
    return out


def add_cue(db: DB, film, scene, kind, purpose):
    """A sound the user wants: ambience, music, anything. It has no file until one is brought in."""
    from .db import new_id as nid
    if not db.one("select 1 from scene where id=? and film_id=?", scene, film):
        raise RuleRefused("choose a scene")
    if kind not in ("foley", "hard_fx", "ambience", "room_tone", "music", "motif"):
        raise RuleRefused("choose what kind of sound it is")
    if len(purpose.strip()) < 5:
        raise RuleRefused("write what the sound is for")
    n = 1 + db.val("select count(*) from sound_cue c join scene s on s.id=c.scene_id where s.film_id=?", film)
    cid = f"{film}:{kind.upper().replace('_', '-')}-{n:02d}"
    while db.one("select 1 from sound_cue where id=?", cid):
        n += 1
        cid = f"{film}:{kind.upper().replace('_', '-')}-{n:02d}"
    db.insert("sound_cue", id=cid, scene_id=scene, kind=kind, purpose=purpose.strip()[:300], spec_json={"by": "user"}, sourcing="record")
    db.event("user", "cue.added", "sound_cue", cid, {"kind": kind})
    return cid


def import_cue(db: DB, film, cue, path: Path, terms, title="", reason="", credit=""):
    """A recording, ambience or music made or found elsewhere. The licence is recorded with the file; judging it is the same as for every other sound."""
    c = db.one("select c.* from sound_cue c join scene s on s.id=c.scene_id where c.id=? and s.film_id=?", cue, film)
    if not c:
        raise RuleRefused("the film has no such sound")
    t = usable_terms(db, terms, credit)
    tid = flow.target(db, film, "sfx", cue, c["purpose"][:80])
    aid, at = take_in(db, tid, path, "audio", {"provider": t["source"], "title": title}, origin="recorded" if terms == "own" else "licensed", reason=reason, provenance={"title": title or path.stem, "cue": cue, "film": film, "source": t["source"], "file": path.name})
    licence(db, aid, terms, credit)
    rep = ia.measure(assets.path_of(db, aid), json.loads(db.val("select probe_json from asset where id=?", aid)), {"continuous": c["kind"] in CONTINUOUS})
    save_check(db, aid, "sound", rep)
    flow.changed(db, film, "sound.arrived")
    return {"asset": aid, "attempt": at, "report": rep}


def use_library(db: DB, film, cue, asset):
    """Fill a cue with a library sound the user has heard."""
    if not db.one("select 1 from sound_cue c join scene s on s.id=c.scene_id where c.id=? and s.film_id=?", cue, film):
        raise RuleRefused("the film has no such sound")
    l = db.one("select title from library_item where asset_id=? and retired_at is null", asset)
    if not l:
        raise RuleRefused("this sound is not in the library")
    s = flow.slot(db, film, "sfx", cue, cue)
    v = flow.add_version(db, s, asset, "user", f"library: {l['title']}")
    flow.decide_version(db, v, True)
    tid = flow.target(db, film, "sfx", cue, cue)
    db.run("update sound_cue set sourcing='reuse' where id=?", cue)
    db.event("user", "cue.library", "sound_cue", cue, {"asset": asset, "target": tid})
    flow.changed(db, film, "sound.arrived")


def import_library(db: DB, path: Path, terms, title, kind, credit=""):
    """A sound for every film. It is stored with its licence; admitting it needs confirmed terms."""
    usable_terms(db, terms, credit)
    if assets.KIND_BY_EXT.get(path.suffix.lower()) != "audio":
        raise assets.ImportProblem("not an audio file")
    aid, new = assets.add(db, path, origin="recorded" if terms == "own" else "licensed", provenance={"title": title.strip()[:120] or path.stem, "library_kind": kind, "file": path.name}, actor="user")
    if not new:
        raise RuleRefused("this exact file has already been imported")
    licence(db, aid, terms, credit)
    save_check(db, aid, "sound", ia.measure(assets.path_of(db, aid), json.loads(db.val("select probe_json from asset where id=?", aid)), {"continuous": kind in CONTINUOUS}))
    return {"asset": aid, "new": new}


def library_picks(db: DB):
    return db.q("select asset_id id,title,kind from library_item where retired_at is null order by kind,title")


def _sfx_spec(doc):
    return {"provider": "elevenlabs", "text": doc["prompt"], "duration_seconds": doc["duration_seconds"], "prompt_influence": doc["prompt_influence"], "loop": doc["loop"], "model_id": doc["model_id"], "output_format": doc["output_format"]}


def _sfx_import(db, aid, doc, pid):
    assets.set_licence(db, aid, "elevenlabs_sfx", "starter")        # film only: the database keeps it out of the shared library
    rep = ia.measure(assets.path_of(db, aid), json.loads(db.val("select probe_json from asset where id=?", aid)), {"duration": (doc["duration_seconds"] * .6, doc["duration_seconds"] * 1.4), "continuous": bool(doc["loop"])})
    save_check(db, aid, "sound", rep)
    _close_task(db, "sfx", pid, True)
    return rep


def import_sfx(db: DB, pid, path: Path, credits=None, reason=""):
    p = db.one("select * from sfx_package where id=?", pid)
    doc = json.loads(p["doc_json"])
    if not flow.live(db, "G9_sfx_package", pid):
        raise RuleRefused("approve this sound first")
    tid = flow.target(db, p["film_id"], "sfx", p["cue_id"], doc["sound"])
    aid, at = take_in(db, tid, path, "audio", {**_sfx_spec(doc), "credits": credits}, origin="manual_audio", reason=reason, provider="elevenlabs", model=doc["model_id"], provenance={"package": pid})
    return {"asset": aid, "attempt": at, "report": _sfx_import(db, aid, doc, pid)}


def _price(now_credits, agreed):
    if agreed is not None and now_credits > agreed:
        raise Refused(f"the price is now up to {now_credits} credits, more than the {agreed} you agreed to; nothing was sent")


def generate_sfx(db: DB, br: Broker, pid, reason="", expect=None):
    p = db.one("select * from sfx_package where id=?", pid)
    doc = json.loads(p["doc_json"])
    if flow.mode(db, p["film_id"], "sfx") != "api":
        raise Refused("sound effects are in manual mode for this film")
    env = db.val("select id from spend_envelope where film_id=? and unit='credits' and purpose like '%sound%' and closed_at is null", p["film_id"])
    if not env:
        raise Refused("no included credits have been released for sound effects; see Budget")
    _price(br.estimate("elevenlabs", "sfx", _sfx_spec(doc)), expect)
    tid = flow.target(db, p["film_id"], "sfx", p["cue_id"], doc["sound"])
    prev = flow.counted(db, tid)
    at = _open_or_start(db, tid, _sfx_spec(doc), reason)
    r = br.run(film=p["film_id"], profile=p["api_profile_id"], capability="sfx", envelope=env, attempt=at, spec=_sfx_spec(doc), requested_by="user", cost_class="first" if not prev else "retry", sfx_package=pid)
    if r["state"] == "succeeded":
        r["report"] = _sfx_import(db, r["asset"], doc, pid)
    return r


def tts_spec(db: DB, pid, line_id, voice_id):
    doc = packages.doc_of(db, "voice", pid)
    line = next((l for l in doc["lines"] if l["line_id"] == line_id), None)
    if not line:
        raise RuleRefused("this line is not part of the package")
    return {"provider": "elevenlabs", "text": line["spoken_text"], "model_id": doc["model"], "voice_id": voice_id, "voice_settings": doc["settings"], "output_format": doc["output_format"]["requested"], "seed": doc.get("seed")}


def generate_voice(db: DB, br: Broker, pid, line_id, voice_id, reason="", expect=None):
    p = db.one("select * from voice_package where id=?", pid)
    doc = json.loads(p["doc_json"])
    line = next(l for l in doc["lines"] if l["line_id"] == line_id)
    if flow.mode(db, p["film_id"], "voice") != "api":
        raise Refused("voices are in manual mode for this film")
    env = db.val("select id from spend_envelope where film_id=? and unit='credits' and purpose like '%voice%' and closed_at is null", p["film_id"])
    if not env:
        raise Refused("no included credits have been released for voices; see Budget")
    spec = tts_spec(db, pid, line_id, voice_id)
    _price(br.estimate("elevenlabs", "tts", spec), expect)
    tid = flow.target(db, p["film_id"], "voice", line_id, f"{line['speaker']}: {line['text'][:60]}")
    prev = flow.counted(db, tid)
    at = _open_or_start(db, tid, spec, reason)
    r = br.run(film=p["film_id"], profile=p["api_profile_id"], capability="tts", envelope=env, attempt=at, spec=spec, requested_by="user", cost_class="first" if not prev else "retry", voice_package=pid)
    if r["state"] == "succeeded":
        aid = r["asset"]
        assets.set_licence(db, aid, "elevenlabs_speech", "starter")
        rep = ia.inspect_voice(assets.path_of(db, aid), json.loads(db.val("select probe_json from asset where id=?", aid)), line["text"])
        n = 1 + (db.val("select max(take_no) from audio_take where line_id=?", line_id) or 0)
        db.insert("audio_take", id=new_id("tk"), line_id=line_id, asset_id=aid, take_no=n, settings_json=spec, package_id=pid, source="api", request_id=r["request"], format_json=rep["facts"])
        save_check(db, aid, "voice", rep)
        r["report"] = rep
    return r
