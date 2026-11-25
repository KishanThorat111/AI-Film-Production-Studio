"""Loads a film that is described by files (a benchmark, or a project exported earlier). The files are data: this module knows their shape, not their story."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import films, flow, packages, script
from .db import DB, ROOT, new_id, now, sha


def found() -> list[Path]:
    """Project folders: public ones in benchmark/, the user's own in private/benchmark/ (never published). STUDIO_PROJECTS names other places."""
    places = [Path(x) for x in os.environ["STUDIO_PROJECTS"].split(os.pathsep)] if os.environ.get("STUDIO_PROJECTS") else [ROOT / "benchmark", ROOT / "private" / "benchmark"]
    return sorted(p.parent for d in places for p in d.glob("*/project.json"))


def load(db: DB, folder: Path, script_file: Path | None = None) -> str | None:
    """Creates the film once; afterwards new or corrected files become new revisions. Returns the film, or None if its script is not here."""
    pj = json.loads((folder / "project.json").read_text(encoding="utf-8"))
    f = pj["film"]
    film = f["id"]
    plan = json.loads((folder / pj["plan"]).read_text(encoding="utf-8"))
    if not db.one("select 1 from film where id=?", film):
        src = script_file or next((p for base in (folder, ROOT, ROOT / "private") for p in sorted(base.glob(pj["script"]))), None)
        if not src:
            return None
        films.create(db, f["title"], universe=f["universe"]["id"], universe_title=f["universe"]["title"], fps=f["fps"], size=tuple(f["size"]), film_id=film, benchmark=bool(f.get("is_benchmark")), status=f.get("status", "development"), profile=f.get("profile", {}), by="system")
    if not script.current(db, film):
        src = script_file or next((p for base in (folder, ROOT, ROOT / "private") for p in sorted(base.glob(pj["script"]))), None)
        if not src:
            return None
        script.ingest(db, film, src, layout=pj.get("layout"), by="user")
    for a in pj.get("allowances", []):
        if not db.one("select 1 from spend_envelope where id=?", a["id"]):
            db.insert("spend_envelope", id=a["id"], film_id=film, purpose=a["purpose"], limit_amount=a["limit"], max_per_request=a["max"], opened_by="user", unit="musd")
    for d in pj.get("decisions", []):
        if not db.one("select 1 from decision where id=?", d["id"]):
            db.insert("decision", id=d["id"], film_id=film, question=d["question"], options_json=[], chosen=d["chosen"], rationale=d["rationale"], decided_by="user", created_at=now())
    _scenes(db, film, pj)
    _story(db, film, pj, plan)
    _images(db, film, plan)
    _packages(db, film, folder / pj["packages"], pj)
    if pj.get("cut") and (folder / pj["cut"]).exists():
        from . import edit
        c = json.loads((folder / pj["cut"]).read_text(encoding="utf-8"))
        sc = db.val("select id from scene where film_id=? and number=?", film, int(c["scope"].split(":")[1]))
        edit.import_cut(db, film, f"scene:{sc}", c, author="project file")
    if pj.get("sounds"):
        if films.setting(db, film, "stand_in_sounds") != pj["sounds"]:
            films.set_setting(db, film, "stand_in_sounds", pj["sounds"], by="system")
    return film


def _scenes(db, film, pj):
    sp = script.spans(db, film)
    doc = script.current(db, film)
    ranges = {s["number"]: s for s in script.scenes([dict(x) for x in sp], doc["format"] or "labelled")}
    for s in pj["scenes"]:
        r = ranges[s["number"]]
        if db.one("select 1 from scene where id=?", s["id"]):
            db.run("update scene set span_from=?,span_to=? where id=? and (span_from is not ? or span_to is not ?)", r["from"], r["to"], s["id"], r["from"], r["to"])
        else:
            db.insert("scene", id=s["id"], film_id=film, number=s["number"], slug=s["slug"], story_time=s.get("story_time"), span_from=r["from"], span_to=r["to"])


def _span(db, film, quote, scene=None):
    hits = [s["id"] for s in script.spans(db, film, scene) if quote in s["text"]]
    if not hits:
        raise ValueError(f"the script does not contain: {quote!r}")
    if scene and len(hits) != 1:
        raise ValueError(f"quote must match exactly one place in the scene: {quote!r} matched {len(hits)}")
    return hits[0]


def _story(db, film, pj, plan):
    uni = films.get(db, film)["universe_id"]
    scene = pj["scenes"][0]["id"]
    with db.tx():
        for e in plan["entities"]:
            if not db.one("select 1 from canon_entity where id=?", e["id"]):
                db.insert("canon_entity", id=e["id"], universe_id=uni, kind=e["kind"], name=e["name"])
                db.insert("canon_revision", id=e["id"] + "_r1", entity_id=e["id"], rev=1, spec_json=e["spec"], content_hash=sha(e["spec"]), status="proposed")
            db.run("insert or ignore into film_canon(film_id,entity_id) values(?,?)", film, e["id"])
        for n, s in enumerate(plan["shots"]):
            sid, sc = s.get("id", s["code"]), s.get("scene", scene)
            cur = db.one("select * from shot_spec where shot_id=? order by rev desc limit 1", sid)
            if not cur:
                db.insert("shot", id=sid, scene_id=sc, code=s["code"], tier=s["tier"], ordinal=n)
                for q in s["quotes"]:
                    db.run("insert or ignore into shot_span(shot_id,span_id) values(?,?)", sid, _span(db, film, q, sc))
                db.insert("shot_spec", id=sid + "_r1", shot_id=sid, rev=1, purpose=s["purpose"], spec_json=s, technique=s["technique"], motion_requirement=s.get("motion_requirement"),
                          technique_reason=s.get("technique_reason"), fallback_json=s.get("fallback"), content_hash=sha(s), status="draft")
            else:
                db.run("update shot set ordinal=? where id=? and ordinal<>?", n, sid, n)
                if cur["content_hash"] != sha(s) and cur["status"] == "draft":      # a corrected design; a locked one is left alone
                    db.run("update shot_spec set spec_json=?,purpose=?,motion_requirement=?,content_hash=? where id=?", json.dumps(s, ensure_ascii=False), s["purpose"], s.get("motion_requirement"), sha(s), cur["id"])
        for l in plan["lines"]:
            if not db.one("select 1 from script_line where id=?", l["id"]):
                casting = bool(l.get("casting") or l["id"].startswith("CAST-"))
                db.insert("script_line", id=l["id"], film_id=film, span_id=_span(db, film, l["quote"], None if casting else l.get("scene", scene)), speaker_id=l["speaker_id"], text=l["text"], direction_json=l.get("direction", {}))
            elif l["id"].startswith("CAST-") and not db.one("select 1 from audio_take where line_id=?", l["id"]):      # audition texts may change until one is recorded
                db.run("update script_line set text=?,span_id=? where id=? and text<>?", l["text"], _span(db, film, l["quote"]), l["id"], l["text"])
        for c in plan["cues"]:
            if not db.one("select 1 from sound_cue where id=?", c["id"]):
                db.insert("sound_cue", id=c["id"], scene_id=c.get("scene", scene), kind=c["kind"], purpose=c["purpose"], spec_json=c["spec"], sourcing=c.get("sourcing"), search_json=c.get("search"))
            else:                                                                    # purpose and search record may be corrected; the cue stays the same
                db.run("update sound_cue set purpose=?,search_json=? where id=?", c["purpose"], json.dumps(c.get("search")) if c.get("search") else None, c["id"])


KIND = {"anchor": ("anchor", None), "identity": ("reference", None), "reference": ("reference", None), "keyframe": ("frame", "still"), "first_frame": ("frame", "first"), "last_frame": ("frame", "last"), "plate": ("frame", "plate")}


def _images(db, film, plan):
    specs, ip = {i["id"]: i for i in plan.get("images", [])}, plan.get("image_plan", {})
    order = list(ip.get("order", specs))
    for c in ip.get("comparison", []):
        order.insert(order.index(c) + 1, c + "~2")
    shots = {r["code"]: r["id"] for r in db.q("select sh.id,sh.code from shot sh join scene s on s.id=sh.scene_id where s.film_id=?", film)}
    for n, code in enumerate(order):
        s = specs[code.removesuffix("~2")]
        kind, role = KIND[s["kind"]]
        row = {"ordinal": n, "kind": "comparison" if code.endswith("~2") else kind, "entity_id": s["for"] if s["for"] not in shots else None, "shot_id": shots.get(s["for"]), "role": role, "aspect": s["aspect"],
               "refs_json": s["refs"], "prompt": s["prompt"], "base_code": code.removesuffix("~2") if code.endswith("~2") else None, "shows_json": s.get("shows", [])}
        put_image(db, film, code, row)


def put_image(db, film, code, row, doc_id=None):
    h = sha({k: row[k] for k in sorted(row) if k != "ordinal"})
    old = db.one("select id,content_hash,ordinal from image_spec where film_id=? and code=?", film, code)
    vals = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()}
    if not old:
        return db.insert("image_spec", id=new_id("is"), film_id=film, code=code, content_hash=h, doc_id=doc_id, **vals)
    if old["content_hash"] != h or old["ordinal"] != row["ordinal"]:
        db.run(f"update image_spec set {','.join(k + '=?' for k in vals)},content_hash=?,doc_id=coalesce(?,doc_id),retired=0 where id=?", *vals.values(), h, doc_id, old["id"])
    return old["id"]


def put_motion(db, shot, first, last, camera, zones, draft, doc_id=None):
    h = sha([first, last, camera, zones, draft])
    if not db.one("select 1 from motion_plan where shot_id=?", shot):
        db.insert("motion_plan", shot_id=shot, first_image=first, last_image=last, camera=camera, zones_json=zones, draft_json=draft, content_hash=h, doc_id=doc_id)
    else:
        db.run("update motion_plan set first_image=?,last_image=?,camera=?,zones_json=?,draft_json=?,content_hash=?,doc_id=coalesce(?,doc_id) where shot_id=? and content_hash<>?", first, last, camera, json.dumps(zones), json.dumps(draft, ensure_ascii=False), h, doc_id, shot, h)


def _packages(db, film, folder: Path, pj):
    rd = lambda f: json.loads(f.read_text(encoding="utf-8"))
    for f in sorted(folder.glob("voice_*.json")):
        d = rd(f)
        packages.register(db, "voice", film, d, manual_profile="voice_manual", api_profile="elevenlabs_api", maker="sound-designer", changed=d.get("changed_vs_prior"))
    for f in sorted(folder.glob("sfx_*.json")):
        d = rd(f)
        packages.register(db, "sfx", film, d, manual_profile="sfx_manual", api_profile="elevenlabs_api", cue=d["cue"], maker="sound-designer", changed=d.get("changed_vs_prior"))
    shots = {r["code"]: r["id"] for r in db.q("select sh.id,sh.code from shot sh join scene s on s.id=sh.scene_id where s.film_id=?", film)}
    for f in sorted(folder.glob("video_*.json")):
        d = rd(f)
        m = pj.get("motion", {}).get(d["shot"], {"camera": "locked", "zones": []})
        put_motion(db, shots[d["shot"]], *d["_frames"], m["camera"], m["zones"], {k: v for k, v in d.items() if k != "_frames"})
