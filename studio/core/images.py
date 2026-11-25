"""Still images: what the film needs, references, generation through the broker, import by hand, checks."""
from __future__ import annotations

import json
from pathlib import Path

from ..connectors.broker import Broker, Refused
from ..inspect import image as ii
from . import assets, films, flow
from .common import _open_or_start, checks, licence, save_check, take_in, usable_terms
from .db import DB, RuleRefused, data_dir, sha

ASPECT = {"16:9": 16 / 9, "9:16": 9 / 16, "3:4": 3 / 4, "4:3": 4 / 3, "1:1": 1.0, "2:3": 2 / 3, "3:2": 3 / 2, "21:9": 21 / 9}
PIXELS = {"16:9": "1536x1024", "3:2": "1536x1024", "4:3": "1536x1024", "21:9": "1536x1024", "1:1": "1024x1024"}
PROVIDERS = {"google_images": ("google_images_api", {"size": "2K"}, "google_images"), "openai_images": ("openai_images_api", {"quality": "medium"}, "openai_images"), "bfl_images": ("bfl_images_api", {"size": "2k"}, "bfl_images")}


def spec(db: DB, film, code) -> dict:
    r = db.one("select * from image_spec where film_id=? and code=? and retired=0", film, code)
    if not r:
        raise RuleRefused(f"the film has no image called {code}")
    return {**r, "refs": json.loads(r["refs_json"]), "shows": json.loads(r["shows_json"])}


def specs(db: DB, film) -> list[dict]:
    return [{**r, "refs": json.loads(r["refs_json"]), "shows": json.loads(r["shows_json"])} for r in db.q("select * from image_spec where film_id=? and retired=0 order by ordinal,code", film)]


def approved(db: DB, film, code):
    r = db.one("select v.asset_id from slot s join slot_current v on v.slot_id=s.id where s.film_id=? and s.kind='image' and s.ref_id=?", film, code)
    return r["asset_id"] if r else None


# ------------------------------------------------------------ who is in the picture
def characters_shown(db: DB, film, code, _seen=None) -> set[str]:
    """Characters visible in an image: stated, or inherited from the identity pictures it is built from."""
    seen = _seen if _seen is not None else set()
    if code in seen:
        return set()
    seen.add(code)
    r = db.one("select entity_id,refs_json,shows_json from image_spec where film_id=? and code=?", film, code)
    if not r:
        return set()
    out = set(json.loads(r["shows_json"]))
    if r["entity_id"] and db.val("select kind from canon_entity where id=?", r["entity_id"]) == "character":
        out.add(r["entity_id"])
    for x in json.loads(r["refs_json"]):
        if not x.startswith("BLOCK"):
            out |= characters_shown(db, film, x, seen)
    return out


def identity_refs(db: DB, film, entity, without=None) -> list:
    """Approved identity pictures of one character."""
    out = []
    for r in db.q("select code from image_spec where film_id=? and entity_id=? and kind in('anchor','reference') and retired=0 order by ordinal", film, entity):
        a = approved(db, film, r["code"]) if r["code"] != without else None
        if a:
            out.append(ii.read(assets.path_of(db, a)))
    return out


def refs_for_shot(db: DB, film, shot) -> dict[str, list]:
    codes = [r["code"] for r in db.q("select code from image_spec where film_id=? and shot_id=? and retired=0", film, shot)]
    who = set().union(*[characters_shown(db, film, c) for c in codes]) if codes else set()
    return {e: r for e in sorted(who) if (r := identity_refs(db, film, e))}


def check(db: DB, film, code, aid):
    from ..inspect import face
    s = spec(db, film, code)
    rep = ii.inspect(assets.path_of(db, aid), {"aspect": ASPECT[s["aspect"]]})
    if s["kind"] != "anchor":
        for e in sorted(characters_shown(db, film, code)):
            refs = identity_refs(db, film, e, without=code)
            if not refs:
                continue
            f = face.compare(ii.read(assets.path_of(db, aid)), refs)
            name = db.val("select name from canon_entity where id=?", e)
            rep["items"].append((f"same face as {name} (measurement only)", f["outcome"], f["text"]))
            rep["facts"].setdefault("face_similarity", {})[e] = f.get("similarity")
            rep["examined"].append(f"face similarity to the approved pictures of {name}")
            rep["not_examined"] = [x for x in rep["not_examined"] if "identity" not in x] + ["whether it is the same person (your eye decides)"]
            if f["outcome"] == "warn" and rep["outcome"] == "pass":
                rep["outcome"] = "warn"
    save_check(db, aid, "image", rep)
    return rep


# ------------------------------------------------------------ previs pictures as references
def blocking_asset(db: DB, film, ref: str, make=True):
    """BLOCK:<shot or part>[:<frame>] -> an asset made by the previs worker. make=False: None if it is not registered yet."""
    _, part, *f = ref.split(":")
    p = films.blocking_dir(db, film) / part / f"{f[0] if f else '0000'}.png"
    if not p.exists():
        raise RuleRefused(f"the previs picture for {part} has not been rendered yet")
    h = assets.file_sha(p)
    r = db.one("select id from asset where sha256=? and origin='rendered'", h)
    if r:
        return r["id"]
    if not make:
        return None
    aid, _ = assets.add(db, p, origin="rendered", storage="derived", provenance={"blocking": ref, "film": film}, actor="blender")
    assets.set_licence(db, aid, "own")
    return aid


# ------------------------------------------------------------ page state
def image_state(db: DB, film):
    out, all_ = [], specs(db, film)
    for s in all_:
        tg = db.one("select * from generation_target where film_id=? and kind='image' and ref_id=?", film, s["code"])
        at = db.q("select a.*,r.state rstate,r.est_amount,r.actual_amount,p.surface,(select 1 from attempt_withdrawal w where w.attempt_id=a.id) withdrawn from attempt a left join api_request r on r.attempt_id=a.id and r.state<>'failed' left join provider_profile p on p.id=r.profile_id where a.target_id=? order by attempt_no", tg["id"]) if tg else []
        for a in at:
            a["checks"] = checks(db, a["result_asset_id"]) if a["result_asset_id"] else []
            a["spec"] = json.loads(a.pop("spec_json"))
        need = [r for r in s["refs"] if not r.startswith("BLOCK") and not approved(db, film, r)]
        out.append({"id": s["code"], "kind": s["kind"], "role": s["role"], "aspect": s["aspect"], "refs": s["refs"], "prompt": s["prompt"], "base": s["base_code"], "shot": s["shot_id"], "for": s["entity_id"] or s["shot_id"], "target": tg, "limit": flow.limit(db, tg["id"]) if tg else 3,
                    "used": len(flow.counted(db, tg["id"])) if tg else 0, "attempts": at, "approved": approved(db, film, s["code"]), "waiting_for": need, "reviews": _reviews(db, at)})
    return {"images": out, "comparison": [s["base_code"] for s in all_ if s["base_code"]], "providers": sorted(PROVIDERS)}


def _reviews(db, attempts):
    ids = [a["result_asset_id"] for a in attempts if a["result_asset_id"]]
    if not ids:
        return []
    return [{**r, "body": json.loads(r.pop("body_json"))} for r in db.q(f"select r.id,r.verdict,r.body_json,t.role,t.target_id from report r join task t on t.id=r.task_id where t.target_type='asset' and t.target_id in({','.join('?' * len(ids))}) order by r.rowid", *ids)]


def add_need(db: DB, film, code, kind, *, entity=None, shot=None, role=None, aspect="16:9", prompt=""):
    """The user names a picture the film needs, so that it can be imported or generated without asking an agent first."""
    import re
    from .project import put_image
    code = code.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,39}", code):
        raise RuleRefused("a short name of letters, digits, dots and dashes")
    if db.one("select 1 from image_spec where film_id=? and code=?", film, code):
        raise RuleRefused("there is already an image with this name")
    if kind not in ("anchor", "reference", "frame") or aspect not in ASPECT:
        raise RuleRefused("unknown kind or format")
    if kind == "frame":
        if role not in ("still", "first", "last", "plate", "layer") or not db.one("select 1 from shot sh join scene s on s.id=sh.scene_id where sh.id=? and s.film_id=?", shot, film):
            raise RuleRefused("a frame belongs to a shot: choose the shot and whether it is the still, the first or the last frame")
    elif not db.one("select 1 from film_canon where film_id=? and entity_id=?", film, entity):
        raise RuleRefused("an identity picture belongs to a person, place or thing of the canon")
    n = db.val("select coalesce(max(ordinal),-1)+1 from image_spec where film_id=?", film)
    put_image(db, film, code, {"ordinal": n, "kind": kind, "entity_id": entity if kind != "frame" else None, "shot_id": shot if kind == "frame" else None, "role": role if kind == "frame" else None, "aspect": aspect, "refs_json": [], "prompt": prompt.strip() or "brought in by hand", "base_code": None, "shows_json": [entity] if entity and kind == "frame" else []})
    db.event("user", "image.needed", "image_spec", code, {"kind": kind})
    return code


# ------------------------------------------------------------ making
def spec_for(db: DB, film, code, provider, prompt=None):
    if provider not in PROVIDERS:
        raise RuleRefused("unknown image provider")
    s, refs = spec(db, film, code), []
    for r in s["refs"]:
        if r.startswith("BLOCK"):
            refs.append(blocking_asset(db, film, r))
        else:
            a = approved(db, film, r)
            if not a:
                raise RuleRefused(f"approve {r} first; this image is built from it")
            refs.append(a)
    sp = {"provider": provider, "prompt": prompt or s["prompt"], "aspect": s["aspect"], "reference_ids": refs, **PROVIDERS[provider][1]}
    if provider == "openai_images":
        sp["pixels"] = PIXELS.get(s["aspect"], "1024x1536")
    return sp, refs


def generate_image(db: DB, br: Broker, film, code, provider, prompt=None, reason="", expect=None):
    """expect: the price the user agreed to. A higher one is refused before anything is sent."""
    s = spec(db, film, code)
    if s["base_code"]:
        used = db.val("""select p.connector_id from attempt a join generation_target t on t.id=a.target_id join api_request r on r.attempt_id=a.id join provider_profile p on p.id=r.profile_id
            where t.film_id=? and t.kind='image' and t.ref_id=? and r.state='succeeded' order by a.attempt_no desc limit 1""", film, s["base_code"])
        if not used:
            raise RuleRefused(f"make {s['base_code']} first; this is its comparison on another provider")
        if used == provider:
            raise RuleRefused("choose a different provider than the first one; otherwise nothing is compared")
    env = films.envelope(db, film, "image")
    if not env:
        raise Refused("this film has no picture allowance yet; open one on the Budget page")
    sp, refs = spec_for(db, film, code, provider, prompt)
    cap = "image_edit" if refs else "image_generate"
    price = br.estimate(provider, cap, sp)
    if expect is not None and price > expect:
        raise Refused(f"the price is now at most ${price / 1e6:.3f}, more than you agreed to; nothing was sent")
    tid = flow.target(db, film, "image", code, code)
    prev = flow.counted(db, tid)
    at = _open_or_start(db, tid, sp, reason)
    r = br.run(film=film, profile=PROVIDERS[provider][0], capability=cap, envelope=env, attempt=at, spec=sp, refs=refs, requested_by="user", cost_class="first" if not prev else "retry")
    if r["state"] == "succeeded":
        assets.set_licence(db, r["asset"], PROVIDERS[provider][2], "paid api")
        r["report"] = check(db, film, code, r["asset"])
        flow.changed(db, film, "image.arrived")
    return r


def import_image(db: DB, film, code, path: Path, note="", reason="", terms="", credit=""):
    """An image made elsewhere may stand in for an API image; it follows the same attempts and checks."""
    spec(db, film, code)
    if terms:
        usable_terms(db, terms, credit)
    tid = flow.target(db, film, "image", code, code)
    aid, at = take_in(db, tid, path, "image", {"provider": "by hand", "note": note}, origin="authored", reason=reason, provenance={"imported_for": code, "film": film, "note": note})
    if terms:
        licence(db, aid, terms, credit)
    rep = check(db, film, code, aid)
    flow.changed(db, film, "image.arrived")
    return {"asset": aid, "attempt": at, "report": rep}
