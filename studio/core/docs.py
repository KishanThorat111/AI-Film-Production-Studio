"""Creative documents: proposed by an agent (or a file), checked by rules, reviewed by somebody else, approved by the user, and only then put into effect.

production_plan  what the script says, beat by beat, every claim with its source
canon            people, places, things, looks and voices as measurable facts
shot_list        one scene as designed shots
image_plan       the stills a film needs, and what each is built from
motion_plan      how one shot moves between two frames
sound_plan       cues, voice packages and effect packages of one scene
release          titles, description, chapters, credits for YouTube
"""
from __future__ import annotations

import json
import re

import jsonschema

from . import films, flow, packages, project, script
from .db import DB, ROOT, RuleRefused, dumps, new_id, now, sha

PLAN_SCHEMA = json.loads((ROOT / "studio/schema/production_plan.schema.json").read_text(encoding="utf-8"))
S, I, N, B = {"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "boolean"}


def txt(mx, mn=1):
    return {"type": "string", "minLength": mn, "maxLength": mx}


def obj(req, **props):
    return {"type": "object", "additionalProperties": False, "required": req, "properties": props}


def arr(items, mn=0, mx=400):
    return {"type": "array", "items": items, "minItems": mn, "maxItems": mx}


ENTITY_KINDS = ["character", "wardrobe", "environment", "prop", "camera", "look", "voice", "sound", "rule"]
CODE = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$"}
CANON = obj(["schema_version", "entities"], schema_version={"const": "1"}, note=txt(600, 0), entities=arr(obj(
    ["id", "kind", "name", "spec", "must_hold"], id=CODE, kind={"enum": ENTITY_KINDS}, name=txt(80), spec={"type": "object", "minProperties": 1},
    must_hold=arr(txt(200), 1, 40), may_vary=arr(txt(200), 0, 40), same_person_as=CODE, valid_from=S, valid_to=S,
    facts=arr(obj(["field", "basis"], field=txt(60), basis={"enum": ["stated", "implied", "invented"]}, cite=arr(S)))), 1))
PREVIS = obj(["camera"], set=S, rooms=arr({"type": "object"}), figures=arr({"type": "object"}), props=arr({"type": "object"}), lights=arr({"type": "object"}), camera={"type": "object"}, frames=I)
SHOT = obj(["code", "tier", "frames", "spans", "purpose", "camera", "light", "technique"], code=CODE, tier={"enum": ["A", "B", "C"]}, frames={"type": "integer", "minimum": 8, "maximum": 2400}, spans=arr(S, 1), purpose=txt(300, 10),
           camera=obj(["mode", "size", "move"], mode={"enum": ["cinematic", "diegetic"]}, size=txt(60), lens_mm=N, height_m=N, angle=S, move=txt(200), screen_direction=S, focus=S, device=S), light=txt(400), withheld=S, sound_plan=S, continuity=arr(txt(200)),
           technique={"enum": ["reuse", "graphics", "still", "still_move", "layered_2p5d", "render_3d", "plate_comp", "generated", "hybrid"]}, motion_requirement=S, technique_reason=S, why_not_richer=S, fallback={"type": "object"}, canon=arr(CODE), previs=PREVIS, notes=S)
SHOTS = obj(["schema_version", "scene", "intention", "shots"], schema_version={"const": "1"}, scene=S, intention=txt(600, 10), shots=arr(SHOT, 1, 60), left_out=arr(obj(["span", "reason"], span=S, reason=txt(200, 5))), geography=txt(800, 0))
IMAGES = obj(["schema_version", "images"], schema_version={"const": "1"}, note=txt(600, 0), images=arr(obj(
    ["code", "kind", "aspect", "refs", "prompt"], code=CODE, kind={"enum": ["anchor", "reference", "frame"]}, entity=CODE, shot=S, role={"enum": ["still", "first", "last", "plate", "layer"]},
    aspect={"enum": ["16:9", "9:16", "3:4", "4:3", "1:1", "2:3", "3:2", "21:9"]}, refs=arr(S, 0, 6), prompt=txt(4000, 60), shows=arr(CODE), acceptance=arr(txt(200)), why=txt(300, 0)), 1))
MOTION = obj(["schema_version", "shot", "first_image", "last_image", "camera", "zones", "draft"], schema_version={"const": "1"}, shot=S, first_image=S, last_image=S, camera={"enum": ["locked", "moving"]},
             zones=arr({"type": "array", "items": N, "minItems": 4, "maxItems": 4}), draft={"type": "object"}, likely_failure=txt(400, 0))
SOUNDP = obj(["schema_version", "scene"], schema_version={"const": "1"}, scene=S, intention=txt(600, 0), cues=arr(obj(["id", "kind", "purpose", "spec"], id=CODE, kind={"enum": ["foley", "hard_fx", "ambience", "room_tone", "music", "motif", "designed_silence"]}, purpose=txt(300, 5), spec={"type": "object"},
              sourcing={"enum": ["reuse", "reuse_edited", "variant", "generate", "record", "none"]}, search=obj(["terms", "hits"], terms=arr(S, 1), hits=I, why_not=S))), voice_packages=arr({"type": "object"}), sfx_packages=arr({"type": "object"}), casting_lines=arr(obj(["id", "speaker", "text", "quote"], id=CODE, speaker=S, text=txt(400), quote=txt(400))))
RELEASE = obj(["schema_version", "title_options", "description", "tags", "thumbnail"], schema_version={"const": "1"}, title_options=arr(txt(100, 5), 1, 10), chosen_title=txt(100, 0), description=txt(4800, 20), tags=arr(txt(60), 0, 40), chapters=arr(obj(["frame", "title"], frame=I, title=txt(80))),
              thumbnail=obj(["concept"], concept=txt(400), text=txt(40, 0), image=S, frame=I), credits=arr(txt(200)), disclosure=obj(["altered_or_synthetic", "note"], altered_or_synthetic=B, note=txt(400)), made_for_kids={"const": False}, content_notes=arr(txt(120)), shorts=arr(obj(["title", "from_frame", "to_frame", "hook"], title=txt(100), from_frame=I, to_frame=I, hook=txt(200))))
KINDS = {
    "production_plan": {"schema": PLAN_SCHEMA, "gate": "G1_plan", "maker": "story-editor", "reviewers": ["continuity-inspector"], "needs": ["script"]},
    "canon": {"schema": CANON, "gate": "G2_canon", "maker": "production-designer", "reviewers": ["continuity-inspector"], "needs": ["plan"]},
    "shot_list": {"schema": SHOTS, "gate": "G3_shots", "maker": "cinematographer", "reviewers": ["film-editor"], "needs": ["plan", "canon"]},
    "image_plan": {"schema": IMAGES, "gate": "G3_images", "maker": "image-maker", "reviewers": ["cinematographer"], "needs": ["canon", "shots"]},
    "motion_plan": {"schema": MOTION, "gate": None, "maker": "motion-engineer", "reviewers": [], "needs": ["shots"]},         # its package is reviewed and approved (G5)
    "sound_plan": {"schema": SOUNDP, "gate": None, "maker": "sound-designer", "reviewers": [], "needs": ["shots"]},           # its packages are reviewed and approved (G9)
    "release": {"schema": RELEASE, "gate": "G11_text", "maker": "release-auditor", "reviewers": [], "needs": []},
}
VAGUE = re.compile(r"\b(cinematic masterpiece|award[- ]winning|best quality|8k|4k|ultra[- ]?realistic|hyper[- ]?realistic|trending on|masterpiece|octane|unreal engine|in the style of)\b", re.I)
REAL = re.compile(r"\b(looks? like|resembl\w+|similar to|like the (?:actor|actress|singer))\s+[A-Z][a-z]+ [A-Z][a-z]+")


def P(sev, where, text):
    return {"severity": sev, "where": where, "text": text}


def entity_id(db: DB, film, x):
    """The same short name may be used in another world. Inside this one it always means this world's entry."""
    uni = films.get(db, film)["universe_id"]
    u = db.val("select universe_id from canon_entity where id=?", x)
    return x if u in (None, uni) else f"{uni}-{x}"


# ------------------------------------------------------------ checks by rules
def lint(db: DB, film, kind, scope, doc) -> list[dict]:
    out = [P("critical", "/".join(map(str, e.path)) or "document", e.message[:200]) for e in list(jsonschema.Draft202012Validator(KINDS[kind]["schema"]).iter_errors(doc))[:20]]
    if not out:
        out = {"production_plan": _lint_plan, "canon": _lint_canon, "shot_list": _lint_shots, "image_plan": _lint_images, "motion_plan": _lint_motion, "sound_plan": _lint_sound, "release": _lint_release}[kind](db, film, scope, doc)
    return out


def _lint_plan(db, film, scope, d):
    out, sp = [], script.spans(db, film)
    ids, kind, text = {s["id"] for s in sp}, {s["id"]: s["kind"] for s in sp}, {s["id"]: s["text"] for s in sp}
    if d["film_id"] != film:
        out.append(P("critical", "film_id", f"this film is {film}"))
    if d["script_sha256"] != (script.current(db, film) or {}).get("sha256"):
        out.append(P("critical", "script_sha256", "written for another revision of the script"))
    used = set()

    def cites(where, c):
        for x in c:
            if x not in ids:
                out.append(P("critical", where, f"cites {x}, which is not in the script"))
        used.update(c)
    for e in d["entities"]:
        for f in e["facts"]:
            cites(f"entity {e['id']}", f["cite"])
    for sc in d["scenes"]:
        for b in sc["beats"]:
            cites(f"scene {sc['number']} beat {b['id']}", b["cite"])
    for k in d["knowledge"]:
        cites("knowledge", k["cite"])
    for i in d["invariants"]:
        cites(f"rule {i['id']}", i["cite"])
    if d.get("tone"):
        cites("tone", d["tone"]["cite"])
    numbers = {r["number"] for r in db.q("select number from scene where film_id=?", film)}
    miss = numbers - {s["number"] for s in d["scenes"]}
    if miss:
        out.append(P("critical", "scenes", f"scenes {sorted(miss)} are not in the plan"))
    in_scene = set()
    for r in db.q("select span_from,span_to from scene where film_id=? and span_from is not null", film):
        in_scene |= {s["id"] for s in sp[r["span_from"]:r["span_to"]]}
    beats = {x for sc in d["scenes"] for b in sc["beats"] for x in b["cite"]}
    story = [s["id"] for s in sp if s["kind"] in ("action", "dialogue", "narration", "sound") and s["id"] in in_scene]
    gone = [x for x in story if x not in beats]
    if gone:
        out.append(P("critical", "coverage", f"{len(gone)} pieces of the script belong to no beat, first: {gone[0]} ({text[gone[0]][:50]!r})"))
    known = [b["id"] for sc in d["scenes"] for b in sc["beats"]]
    if len(set(known)) != len(known):
        out.append(P("critical", "beats", "beat ids repeat"))
    for k in d["knowledge"]:
        if k["when_beat"] not in known:
            out.append(P("major", "knowledge", f"refers to beat {k['when_beat']!r}, which does not exist"))
    for q in d["open_issues"]:
        for x in q["quotes"]:
            if not any(x.strip().strip('"') in t for t in text.values()):
                out.append(P("critical", "open_issues", f"this quote is not in the script: {x[:60]!r}"))
    names = {e["name"].upper() for e in d["entities"] if e["kind"] == "character"}
    for who in {script.speaker(s) for s in sp if script.speaker(s)}:
        if not any(who in n or n in who for n in names) and not who.startswith("NARRAT"):
            out.append(P("major", "entities", f"{who} speaks but is not among the characters"))
    return out


def _lint_canon(db, film, scope, d):
    out, seen = [], set()
    for e in d["entities"]:
        if e["id"] in seen:
            out.append(P("critical", e["id"], "listed twice"))
        seen.add(e["id"])
        old = db.one("select universe_id,kind from canon_entity where id=?", entity_id(db, film, e["id"]))
        if old and old["kind"] != e["kind"]:
            out.append(P("critical", e["id"], f"it is a {old['kind']}, not a {e['kind']}"))
        s = json.dumps(e["spec"])
        if e["kind"] == "character" and not re.search(r"\d", s):
            out.append(P("major", e["id"], "nothing in this person is measurable (height, age, hair length); two pictures cannot be compared with it"))
        if e["kind"] == "environment" and not re.search(r"\d", s):
            out.append(P("major", e["id"], "a place without a single dimension cannot be built twice the same way"))
        for f in e.get("facts", []):
            if f["basis"] != "invented" and not f.get("cite"):
                out.append(P("critical", e["id"], f"'{f['field']}' is called {f['basis']} but cites nothing"))
            for c in f.get("cite", []):
                if not db.one("select 1 from source_span s join source_doc d on d.id=s.doc_id where s.id=? and d.film_id=?", c, film):
                    out.append(P("critical", e["id"], f"cites {c}, which is not in the script"))
        vague = [m for m in e["must_hold"] if len(m.split()) < 2]
        if vague:
            out.append(P("moderate", e["id"], f"'{vague[0]}' says too little to be checked"))
    return out


def _lint_shots(db, film, scope, d):
    out, sc = [], scope.split(":", 1)[1]
    if d["scene"] != sc:
        out.append(P("critical", "scene", f"this list is for {sc}"))
    sp = script.spans(db, film, sc)
    ids = {s["id"]: s for s in sp}
    codes = [s["code"] for s in d["shots"]]
    if len(set(codes)) != len(codes):
        out.append(P("critical", "shots", "shot codes repeat"))
    other = {r["code"] for r in db.q("select sh.code from shot sh join scene s on s.id=sh.scene_id where s.film_id=? and s.id<>?", film, sc)}
    for c in set(codes) & other:
        out.append(P("critical", c, "this code is used in another scene"))
    cov = {}
    for s in d["shots"]:
        for x in s["spans"]:
            if x not in ids:
                out.append(P("critical", s["code"], f"cites {x}, which is not in this scene"))
            cov.setdefault(x, []).append(s)
        if s["technique"] in ("generated", "hybrid") and not (s.get("motion_requirement") and s.get("technique_reason") and s.get("fallback")):
            out.append(P("critical", s["code"], "a generated shot states the motion it needs, why cheaper ways fail, and a fallback"))
        if s["camera"]["mode"] == "diegetic" and not s["camera"].get("device"):
            out.append(P("critical", s["code"], "a camera that exists in the story names its device"))
        if re.search(r"dynamic|more interesting|for variety", (s.get("technique_reason") or "") + s["purpose"], re.I):
            out.append(P("major", s["code"], "movement for its own sake is not a purpose"))
        for e in s.get("canon", []):
            if not db.one("select 1 from film_canon where film_id=? and entity_id=?", film, entity_id(db, film, e)):
                out.append(P("critical", s["code"], f"there is no canon entry {e}"))
    left = {x["span"] for x in d.get("left_out", [])}
    for s in sp:
        if s["kind"] in ("action", "dialogue", "narration") and s["id"] not in cov and s["id"] not in left:
            out.append(P("critical", s["id"], f"no shot covers: {s['text'][:60]}"))
    fps = films.fps(db, film)
    for x, shots in cov.items():
        if x in ids and ids[x]["kind"] in ("dialogue", "narration"):
            need = len(script.words(ids[x]).split()) / 2.7 * fps
            have = sum(s["frames"] for s in shots)
            if have < need * .8:
                out.append(P("major", shots[0]["code"], f"{have / fps:.1f} s cover a line that takes about {need / fps:.1f} s; let it continue over the next shot or lengthen"))
    sizes = [s["camera"]["size"].lower() for s in d["shots"]]
    for i in range(len(sizes) - 3):
        if len(set(sizes[i:i + 4])) == 1:
            out.append(P("moderate", codes[i + 3], "four shots in a row have the same size"))
            break
    gen = sum(s["technique"] in ("generated", "hybrid") for s in d["shots"])
    if gen > max(3, len(codes) * .6):
        out.append(P("moderate", "shots", f"{gen} of {len(codes)} shots need a Gemini generation; every one costs the user's hands"))
    return out


def _lint_images(db, film, scope, d):
    out, have = [], {r["code"]: r for r in db.q("select * from image_spec where film_id=?", film)}
    codes = {i["code"] for i in d["images"]}
    if len(codes) != len(d["images"]):
        out.append(P("critical", "images", "codes repeat"))
    fl = films.get(db, film)
    wide = "16:9" if fl["width"] > fl["height"] else "9:16" if fl["width"] < fl["height"] else "1:1"
    depth = {}

    def deep(c, seen=()):
        if c in seen:
            out.append(P("critical", c, "is built from itself"))
            return 0
        i = next((x for x in d["images"] if x["code"] == c), None)
        refs = i["refs"] if i else json.loads(have[c]["refs_json"]) if c in have else []
        return 1 + max([deep(r, seen + (c,)) for r in refs if not r.startswith("BLOCK")], default=-1)
    for i in d["images"]:
        for r in i["refs"]:
            if not r.startswith("BLOCK:") and r not in codes and r not in have:
                out.append(P("critical", i["code"], f"is built from {r}, which does not exist"))
        depth[i["code"]] = deep(i["code"])
        if depth[i["code"]] > 3:
            out.append(P("major", i["code"], "is a copy of a copy of a copy; identity drifts. Build it from the anchor"))
        if i["kind"] in ("anchor", "reference") and not (i.get("entity") and db.one("select 1 from film_canon where film_id=? and entity_id=?", film, entity_id(db, film, i["entity"]))):
            out.append(P("critical", i["code"], "an identity picture belongs to a canon entry"))
        if i["kind"] == "anchor" and i["refs"]:
            out.append(P("critical", i["code"], "an anchor is not built from other pictures"))
        if i["kind"] == "frame":
            sh = i.get("shot")
            if not sh or not db.one("select 1 from shot sh join scene s on s.id=sh.scene_id where s.film_id=? and (sh.id=? or sh.code=?)", film, sh, sh):
                out.append(P("critical", i["code"], "a frame belongs to a shot of this film"))
            if not i.get("role"):
                out.append(P("critical", i["code"], "say whether it is the still, the first or the last frame"))
            if wide and i["aspect"] != wide:
                out.append(P("critical", i["code"], f"frames of this film are {wide}"))
            if i.get("shows") and not any(have.get(r, {}).get("kind") in ("anchor", "reference") or any(x["code"] == r and x["kind"] in ("anchor", "reference") for x in d["images"]) or r in codes for r in i["refs"]):
                out.append(P("major", i["code"], "shows a person but is built from no identity picture"))
        if VAGUE.search(i["prompt"]):
            out.append(P("major", i["code"], f"'{VAGUE.search(i['prompt']).group(0)}' tells the model nothing about this picture"))
        if REAL.search(i["prompt"]):
            out.append(P("critical", i["code"], "compares an invented person with a real one"))
        if i.get("shows") and i["kind"] == "anchor" and "real person" not in i["prompt"].lower() and "invented" not in i["prompt"].lower():
            out.append(P("moderate", i["code"], "say that the person is invented"))
    for k, v in have.items():
        if k in codes and v["kind"] == "anchor" and db.one("select 1 from slot s join slot_current c on c.slot_id=s.id where s.film_id=? and s.kind='image' and s.ref_id=?", film, k):
            i = next(x for x in d["images"] if x["code"] == k)
            if i["prompt"] != v["prompt"]:
                out.append(P("critical", k, "this anchor is approved; changing it is a canon decision of the user"))
    return out


def _ratio(w, h):
    from math import gcd
    g = gcd(w, h)
    return (w // g, h // g)


def _lint_motion(db, film, scope, d):
    out = []
    for c, role in ((d["first_image"], "first"), (d["last_image"], "last")):
        if not db.one("select 1 from image_spec where film_id=? and code=?", film, c):
            out.append(P("critical", role, f"there is no image {c}"))
    from .motion import gemini_size
    gw, gh = gemini_size(db, film)
    fake = {**d["draft"], "attachments": [{"order": i + 1, "file": f"{n}.png", "role": r, "sha256": "0" * 64, "width": gw, "height": gh} for i, (n, r) in enumerate((("a", "first_frame"), ("b", "last_frame")))]}
    return out + [P(p["severity"], f"draft {p['where']}", p["text"]) for p in packages.lint_video(fake)]


def _lint_sound(db, film, scope, d):
    out, sc = [], scope.split(":", 1)[1]
    lines = {r["id"]: r["text"] for r in db.q("select id,text from script_line where film_id=?", film)}
    for c in d.get("casting_lines", []):
        lines[f"{film}:CAST-{c['id']}"] = c["text"]
        if not any(c["quote"] in s["text"] for s in script.spans(db, film)):
            out.append(P("critical", c["id"], "an audition text is taken from the script"))
    for v in d.get("voice_packages", []):
        out += [P(p["severity"], f"{v.get('package', 'voice')} {p['where']}", p["text"]) for p in packages.lint_voice(v, lines)]
    cues = {c["id"] for c in d.get("cues", [])} | {r["id"] for r in db.q("select c.id from sound_cue c join scene s on s.id=c.scene_id where s.film_id=?", film)}
    for x in d.get("sfx_packages", []):
        out += [P(p["severity"], f"{x.get('cue', 'sound')} {p['where']}", p["text"]) for p in packages.lint_sfx(x)]
        if x.get("cue") not in cues and f"{film}:{x.get('cue')}" not in cues:
            out.append(P("critical", str(x.get("cue")), "belongs to no cue"))
    for c in d.get("cues", []):
        if c.get("sourcing") in ("generate", "variant") and not c.get("search"):
            out.append(P("critical", c["id"], "search the library first and record what was found"))
        if c["kind"] == "music" and c.get("sourcing") == "generate":
            out.append(P("critical", c["id"], "music is not generated: the licence excludes film"))
    return out


def _lint_release(db, film, scope, d):
    out = []
    long = [t for t in d["title_options"] if len(t) > 70]
    if long:
        out.append(P("moderate", "titles", f"longer than 70 characters is cut off on phones: {long[0][:40]}..."))
    shout = [t for t in d["title_options"] if sum(w.isupper() and len(w) > 3 for w in t.split()) > 2 or "!!" in t]
    if shout:
        out.append(P("major", "titles", f"reads as bait: {shout[0]}"))
    if sum(len(t) + 1 for t in d["tags"]) > 480:
        out.append(P("critical", "tags", "more than 500 characters together"))
    ch = d.get("chapters", [])
    if ch:
        if ch[0]["frame"] != 0 or len(ch) < 3:
            out.append(P("critical", "chapters", "YouTube needs at least three, the first at the beginning"))
        fps = films.fps(db, film)
        if any(b["frame"] - a["frame"] < 10 * fps for a, b in zip(ch, ch[1:])):
            out.append(P("critical", "chapters", "each at least 10 seconds"))
    if "disclosure" not in d:
        out.append(P("critical", "disclosure", "state whether realistic pictures or voices were made with AI"))
    for s in d.get("shorts", []):
        n = (s["to_frame"] - s["from_frame"]) / films.fps(db, film)
        if not 5 <= n <= 180:
            out.append(P("critical", s["title"], f"{n:.0f} s: a Short has at most three minutes"))
    return out


# ------------------------------------------------------------ proposing
def based_on(db: DB, film, kind, scope):
    """What this kind of document is built on, with the hash it had."""
    keys = {"script": ("script", film), "plan": ("plan", film), "canon": ("canon", film), "shots": ("shots", scope.split(":", 1)[-1])}
    out = []
    for n in KINDS[kind]["needs"]:
        t, i = keys[n]
        h = db.val("select hash from current_hash where dep_type=? and dep_id=? and field='*'", t, i)
        if h:
            out.append([t, i, h])
    return out


def propose(db: DB, film, kind, scope, doc, made_by, *, agent_hash=None, task=None, changed=None) -> dict:
    if kind not in KINDS:
        raise RuleRefused(f"unknown kind of document; there are {sorted(KINDS)}")
    prob = lint(db, film, kind, scope, doc)
    crit = [p for p in prob if p["severity"] == "critical"]
    if crit:
        raise RuleRefused("not accepted: " + "; ".join(f"{p['where']}: {p['text']}" for p in crit[:10]) + (f"; and {len(crit) - 10} more" if len(crit) > 10 else ""))
    last = db.one("select * from work_doc where film_id=? and kind=? and scope=? order by rev desc limit 1", film, kind, scope)
    if last and last["content_hash"] == sha(doc):
        return {"id": last["id"], "rev": last["rev"], "problems": prob, "unchanged": True}
    if last and not (changed or "").strip():
        raise RuleRefused("say what changed against the previous revision, and which finding it answers")
    did = new_id("wd")
    with db.tx():
        if last and last["status"] == "proposed":
            db.run("update work_doc set status='superseded' where id=?", last["id"])
        db.insert("work_doc", id=did, film_id=film, kind=kind, scope=scope, rev=(last["rev"] + 1 if last else 1), doc_json=doc, content_hash=sha(doc), based_on_json=based_on(db, film, kind, scope), made_by=made_by, agent_hash=agent_hash, task_id=task,
                  changed_vs_prior=changed if last else None, problems_json=prob, created_at=now())
        flow.set_hash(db, "work_doc", did, sha(doc))
        db.event(made_by, "doc.proposed", "work_doc", did, {"kind": kind, "scope": scope})
        if KINDS[kind]["gate"] is None:            # what it leads to is reviewed and approved piece by piece
            EFFECT[kind](db, film, scope, doc, did)
            db.run("update work_doc set status='superseded',decision_note='its packages are reviewed one by one' where id=?", did)
    flow.changed(db, film, "doc.proposed")
    return {"id": did, "rev": db.val("select rev from work_doc where id=?", did), "problems": prob}


def get(db: DB, did) -> dict:
    r = db.one("select * from work_doc where id=?", did)
    if not r:
        raise RuleRefused("there is no such document")
    return {**r, "doc": json.loads(r["doc_json"]), "problems": json.loads(r["problems_json"]), "based_on": json.loads(r["based_on_json"])}


def reviews(db: DB, did):
    return [{**r, "body": json.loads(r.pop("body_json"))} for r in db.q("""select r.id,r.verdict,r.body_json,r.created_at,t.role,t.action from task t join report r on r.task_id=t.id
        where t.kind='review' and t.target_type='work_doc' and t.target_id=? and t.superseded_by is null order by r.rowid""", did)]


def stale(db: DB, d) -> list[str]:
    return [t for t, i, h in (d["based_on"] if "based_on" in d else json.loads(d["based_on_json"])) if db.val("select hash from current_hash where dep_type=? and dep_id=? and field='*'", t, i) != h]


def current(db: DB, film, kind, scope="film"):
    r = db.one("select id from work_doc where film_id=? and kind=? and scope=? and status='approved' order by rev desc limit 1", film, kind, scope)
    return get(db, r["id"]) if r else None


def listing(db: DB, film):
    out = []
    for r in db.q("select * from work_doc w where film_id=? and rev=(select max(rev) from work_doc x where x.film_id=w.film_id and x.kind=w.kind and x.scope=w.scope) order by rowid desc", film):
        d = get(db, r["id"])
        out.append({k: d[k] for k in ("id", "kind", "scope", "rev", "status", "made_by", "changed_vs_prior", "decision_note", "created_at", "problems")} | {"reviews": reviews(db, d["id"]), "stale": stale(db, d), "gate": KINDS.get(d["kind"], {}).get("gate"), "doc": d["doc"]})
    return out


def approve(db: DB, did, note="", waive_reason=""):
    """The user's approval. A rule's objection cannot be overruled; a reviewer's can, with a reason."""
    d = get(db, did)
    k = KINDS[d["kind"]]
    if d["status"] != "proposed":
        raise RuleRefused(f"this revision is {d['status']}")
    if not k["gate"]:
        raise RuleRefused("this document is approved piece by piece")
    old = stale(db, d)
    if old:
        raise RuleRefused(f"what it is based on has changed since: {', '.join(old)}. Ask for a new revision.")
    prob = lint(db, d["film_id"], d["kind"], d["scope"], d["doc"])
    crit = [p for p in prob if p["severity"] == "critical"]
    if crit:
        raise RuleRefused("; ".join(f"{p['where']}: {p['text']}" for p in crit[:3]))
    rv = reviews(db, did)
    waive = len(waive_reason.strip()) >= 10
    missing = [r for r in k["reviewers"] if r not in {x["role"] for x in rv}]
    bad = [x for x in rv if x["verdict"] != "APPROVED" and x["id"] == max((y["id"], y["created_at"]) for y in rv if y["role"] == x["role"])[0]]
    if (missing or bad) and not waive:
        raise RuleRefused(f"not reviewed yet by {', '.join(missing)}" if missing else f"{bad[0]['role']} says {bad[0]['verdict'].replace('_', ' ').lower()}")
    with db.tx():
        EFFECT[d["kind"]](db, d["film_id"], d["scope"], d["doc"], did)
        db.run("update work_doc set status='superseded' where film_id=? and kind=? and scope=? and status='approved'", d["film_id"], d["kind"], d["scope"])
        db.run("update work_doc set status='approved',decided_by='user',decided_at=?,decision_note=? where id=?", now(), (waive_reason or note or "").strip() or None, did)
        flow.approve(db, k["gate"], "work_doc", did, [("work_doc", did)] + [(t, i) for t, i, _ in d["based_on"]], "user", {"reviews": [r["id"] for r in rv]}, *(("waived", waive_reason.strip()) if (missing or bad) else ("approved", None)))
    flow.changed(db, d["film_id"], "doc.approved")
    return did


def send_back(db: DB, did, note):
    """The user wants something else. The maker gets the note with the next task."""
    if len((note or "").strip()) < 5:
        raise RuleRefused("write what should be different")
    d = get(db, did)
    db.run("update work_doc set status='returned',decided_by='user',decided_at=?,decision_note=? where id=? and status in('proposed','approved')", now(), note.strip(), did)
    db.event("user", "doc.returned", "work_doc", did, {"note": note})
    flow.changed(db, d["film_id"], "doc.returned")


# ------------------------------------------------------------ putting an approved document into effect
def key(film, code):
    """Codes are unique inside a film; rows are unique in the studio."""
    return code if code.startswith(film + ":") else f"{film}:{code}"


def _do_plan(db, film, scope, d, did):
    uni, sp = films.get(db, film)["universe_id"], script.spans(db, film)
    for e in d["entities"]:
        _entity(db, film, uni, e["id"], e["kind"], e["name"], {f["field"]: f["value"] for f in e["facts"]} or {"named": True})
    for sc in d["scenes"]:
        db.run("update scene set story_time=coalesce(?,story_time) where film_id=? and number=?", sc.get("story_time"), film, sc["number"])
    db.run("update story_node set status='retired' where film_id=? and status='accepted'", film)

    def node(kind, text, cite, when=None):
        nid = db.insert("story_node", id=new_id("sn"), film_id=film, kind=kind, statement=text[:600], story_time=when, status="accepted")
        for c in dict.fromkeys(cite):
            db.run("insert or ignore into story_cite(node_id,span_id) values(?,?)", nid, c)
    for sc in d["scenes"]:
        for b in sc["beats"]:
            node("event", b["what"], b["cite"], sc.get("story_time"))
            if b.get("question_opened"):
                node("question", b["question_opened"], b["cite"])
    for k in d["knowledge"]:
        node({"planted": "clue", "revealed": "reveal", "withheld": "belief", "red_herring": "belief"}[k["fairness"]], k["fact"], k["cite"])
    db.run("update invariant set status='retired' where film_id=? and status='active'", film)
    for i in d["invariants"]:
        iid = key(film, i["id"]) + f"_{did[-4:]}"
        db.insert("invariant", id=iid, film_id=film, severity=i["severity"], method=i["method"], checker=i["checker"], params_json={**i["params"], "rule": i["rule"]}, span_id=i["cite"][0], status="active", activated_by="user")
    for q in d["open_issues"]:
        node("open_issue", q["question"], [s["id"] for s in sp if any(x.strip().strip('"') in s["text"] for x in q["quotes"])][:4])
        if not db.one("select 1 from decision where film_id=? and question=?", film, q["question"]):
            db.insert("decision", id=new_id("dc"), film_id=film, question=q["question"], options_json=q["options"], evidence_json={"quotes": q["quotes"], "blocks": q.get("blocks", [])}, created_at=now())
    _lines(db, film, uni, d, sp)
    flow.set_hash(db, "plan", film, sha(d))


def _entity(db, film, uni, eid, kind, name, spec):
    eid = entity_id(db, film, eid)
    if not db.one("select 1 from canon_entity where id=?", eid):
        db.insert("canon_entity", id=eid, universe_id=uni, kind=kind, name=name)
        db.insert("canon_revision", id=f"{eid}_r1", entity_id=eid, rev=1, spec_json=spec, content_hash=sha(spec), status="proposed")
    db.run("insert or ignore into film_canon(film_id,entity_id) values(?,?)", film, eid)


def _lines(db, film, uni, d, sp):
    """Every spoken piece becomes a line with its speaker. The words are the script's, unchanged."""
    people = [e for e in d["entities"] if e["kind"] == "character"]
    doc = script.current(db, film)["id"]
    for s in sp:
        if s["kind"] not in ("dialogue", "narration"):
            continue
        who = script.speaker(s) or ("NARRATOR" if s["kind"] == "narration" else None)
        if not who:
            continue
        p = next((e for e in people if who in e["name"].upper() or e["name"].upper() in who), None)
        vid = f"{p['id']}-VOICE" if p else key(film, "V-" + re.sub(r"[^A-Z0-9]+", "-", who).strip("-"))
        vid = entity_id(db, film, vid)
        _entity(db, film, uni, vid, "voice", f"{p['name'] if p else who.title()} (voice)", {"speaks_for": p["id"]} if p else {"stated": who})
        lid = f"{film}:L{s['ordinal']:04d}" if int(db.val("select revision from source_doc where id=?", doc)) == 1 else f"{film}:L{s['ordinal']:04d}r{db.val('select revision from source_doc where id=?', doc)}"
        if not db.one("select 1 from script_line where id=?", lid):
            db.insert("script_line", id=lid, film_id=film, span_id=s["id"], speaker_id=vid, text=script.words(s), direction_json={})


def _do_canon(db, film, scope, d, did):
    uni = films.get(db, film)["universe_id"]
    for e in d["entities"]:
        e = {**e, "id": entity_id(db, film, e["id"])}
        spec = {**e["spec"], "must_hold": e["must_hold"], "may_vary": e.get("may_vary", []), **({"same_person_as": entity_id(db, film, e["same_person_as"])} if e.get("same_person_as") else {})}
        if not db.one("select 1 from canon_entity where id=?", e["id"]):
            db.insert("canon_entity", id=e["id"], universe_id=uni, kind=e["kind"], name=e["name"])
        db.run("insert or ignore into film_canon(film_id,entity_id) values(?,?)", film, e["id"])
        last = db.one("select * from canon_revision where entity_id=? order by rev desc limit 1", e["id"])
        if last and last["status"] == "locked" and last["content_hash"] == sha(spec):
            continue
        db.run("update canon_revision set status='retired' where entity_id=? and status in('locked','proposed')", e["id"])
        n = (last["rev"] if last else 0) + 1
        db.insert("canon_revision", id=f"{e['id']}_r{n}", entity_id=e["id"], rev=n, spec_json=spec, content_hash=sha(spec), valid_from=e.get("valid_from"), valid_to=e.get("valid_to"), status="locked", locked_by="user")
        flow.set_hash(db, "canon_entity", e["id"], sha(spec))
    flow.set_hash(db, "canon", film, sha([r["content_hash"] for r in db.q("select r.content_hash from canon_revision r join film_canon f on f.entity_id=r.entity_id where f.film_id=? and r.status='locked' order by r.entity_id", film)]))


def _do_shots(db, film, scope, d, did):
    sc = scope.split(":", 1)[1]
    keep = set()
    for n, s in enumerate(d["shots"]):
        old = db.one("select id from shot where scene_id=? and code=?", sc, s["code"])
        sid = old["id"] if old else key(film, s["code"])
        keep.add(sid)
        if not old:
            db.insert("shot", id=sid, scene_id=sc, code=s["code"], tier=s["tier"], ordinal=n, state="designed")
        else:
            db.run("update shot set tier=?,ordinal=?,state='designed' where id=?", s["tier"], n, sid)
            db.run("delete from shot_span where shot_id=?", sid)
        for x in dict.fromkeys(s["spans"]):
            db.insert("shot_span", shot_id=sid, span_id=x)
        last = db.one("select * from shot_spec where shot_id=? order by rev desc limit 1", sid)
        if last and last["content_hash"] == sha(s):
            continue
        db.run("update shot_spec set status='superseded' where shot_id=? and status<>'superseded'", sid)
        rev = (last["rev"] if last else 0) + 1
        ss = db.insert("shot_spec", id=f"{sid}_r{rev}", shot_id=sid, rev=rev, purpose=s["purpose"], spec_json=s, technique=s["technique"], motion_requirement=s.get("motion_requirement"), technique_reason=s.get("technique_reason"), fallback_json=s.get("fallback"), content_hash=sha(s), status="locked")
        for e in dict.fromkeys(entity_id(db, film, x) for x in s.get("canon", [])):
            r = db.one("select id,entity_id from canon_revision where entity_id=? order by rev desc limit 1", e)
            if r:
                db.run("insert or ignore into shot_canon(shot_spec_id,canon_revision_id,role,must_hold) values(?,?,?,1)", ss, r["id"], db.val("select kind from canon_entity where id=?", e))
        flow.set_hash(db, "shot", sid, sha(s))
    for r in db.q("select id from shot where scene_id=?", sc):
        if r["id"] not in keep:
            db.run("update shot set state='dropped' where id=?", r["id"])
            db.run("update shot_spec set status='superseded' where shot_id=?", r["id"])
    flow.set_hash(db, "shots", sc, sha(d))


def _do_images(db, film, scope, d, did):
    base = db.val("select coalesce(max(ordinal),-1)+1 from image_spec where film_id=?", film)
    for n, i in enumerate(d["images"]):
        sid = None
        if i.get("shot"):
            sid = db.val("select sh.id from shot sh join scene s on s.id=sh.scene_id where s.film_id=? and (sh.id=? or sh.code=?)", film, i["shot"], i["shot"])
        old = db.val("select ordinal from image_spec where film_id=? and code=?", film, i["code"])
        project.put_image(db, film, i["code"], {"ordinal": old if old is not None else base + n, "kind": i["kind"], "entity_id": entity_id(db, film, i["entity"]) if i.get("entity") else None, "shot_id": sid, "role": i.get("role"), "aspect": i["aspect"], "refs_json": i["refs"], "prompt": i["prompt"], "base_code": None, "shows_json": [entity_id(db, film, x) for x in i.get("shows", [])]}, did)


def _do_motion(db, film, scope, d, did):
    sid = db.val("select sh.id from shot sh join scene s on s.id=sh.scene_id where s.film_id=? and (sh.id=? or sh.code=?)", film, d["shot"], d["shot"])
    if not sid:
        raise RuleRefused("there is no such shot")
    project.put_motion(db, sid, d["first_image"], d["last_image"], d["camera"], d["zones"], d["draft"], did)


def _do_sound(db, film, scope, d, did):
    sc = scope.split(":", 1)[1]
    for c in d.get("casting_lines", []):
        lid = f"{film}:CAST-{c['id']}"
        span = next(s["id"] for s in script.spans(db, film) if c["quote"] in s["text"])
        sp = db.val("select id from canon_entity where id=? or name=?", c["speaker"], c["speaker"])
        if not sp:
            raise RuleRefused(f"{c['id']}: no voice called {c['speaker']}")
        if not db.one("select 1 from script_line where id=?", lid):
            db.insert("script_line", id=lid, film_id=film, span_id=span, speaker_id=sp, text=c["text"], direction_json={})
    for c in d.get("cues", []):
        cid = c["id"] if db.one("select 1 from sound_cue c join scene s on s.id=c.scene_id where c.id=? and s.film_id=?", c["id"], film) else key(film, c["id"])
        if not db.one("select 1 from sound_cue where id=?", cid):
            db.insert("sound_cue", id=cid, scene_id=sc, kind=c["kind"], purpose=c["purpose"], spec_json=c["spec"], sourcing=c.get("sourcing"), search_json=c.get("search"))
        else:
            db.run("update sound_cue set purpose=?,spec_json=?,sourcing=?,search_json=? where id=?", c["purpose"], dumps(c["spec"]), c.get("sourcing"), dumps(c["search"]) if c.get("search") else None, cid)
    for v in d.get("voice_packages", []):
        packages.register(db, "voice", film, v, manual_profile="voice_manual", api_profile="elevenlabs_api", maker="sound-designer", changed=v.get("changed_vs_prior"))
    for x in d.get("sfx_packages", []):
        cue = x["cue"] if db.one("select 1 from sound_cue where id=?", x["cue"]) else key(film, x["cue"])
        packages.register(db, "sfx", film, x, manual_profile="sfx_manual", api_profile="elevenlabs_api", cue=cue, maker="sound-designer", changed=x.get("changed_vs_prior"))


def _do_release(db, film, scope, d, did):
    films.set_setting(db, film, "release_text", d, by="user")


EFFECT = {"production_plan": _do_plan, "canon": _do_canon, "shot_list": _do_shots, "image_plan": _do_images, "motion_plan": _do_motion, "sound_plan": _do_sound, "release": _do_release}
