"""The edit: decisions with reasons about shots, lines and sounds. It names what should be seen and heard, not files; the best material that exists
fills each place, and what is still a stand-in is said plainly.

Agents and the user change an edit only through commands. A command is applied completely or refused with clip, frame and rule; none is ignored.
"""
from __future__ import annotations

import copy
import json
import math
import threading
from pathlib import Path

import jsonschema
import soundfile as sf

from ..engine import render
from . import assets, films, flow, images, script
from .db import DB, RuleRefused, data_dir, dumps, new_id, now, sha

_lock = threading.RLock()
_T = render.SCHEMA["properties"]
_CLIP = _T["video"]["items"]["properties"]["clips"]["items"]["properties"]
_AUD = _T["audio"]["items"]["properties"]
_REF = {"anyOf": [{"type": "string"}, {"type": "object", "additionalProperties": False, "properties": {"depth_of": {"type": "string"}, "image": {"type": "string"}, "asset": {"type": "string"}, "matte_of": {"type": "string"}}, "minProperties": 1, "maxProperties": 1}]}
_EFFECT = {**_CLIP["effects"]["items"], "properties": {**_CLIP["effects"]["items"]["properties"], "mask": _REF}}
_AT = {"type": "object", "additionalProperties": False, "properties": {"clip": {"type": "string"}, "after": {"type": "string"}, "silence": {"type": "string"}, "part": {"type": "string"}, "start": {"const": True}, "end": {"const": True}, "frame": {"type": "integer"}}}
_REASON = {"type": "string", "minLength": 3, "maxLength": 300}
CLIP = {"type": "object", "additionalProperties": False, "required": ["id", "source", "dur", "reason"], "properties": {
    "id": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,40}$"}, "track": {"type": "integer", "minimum": 0, "maximum": 9}, "at": _AT, "dur": {"type": "integer", "minimum": 1, "maximum": 20000},
    "source": {"type": "object", "additionalProperties": False, "minProperties": 1, "properties": {"shot": {"type": "string"}, "part": {"type": "string"}, "image": {"type": "string"}, "asset": {"type": "string"}, "solid": {"enum": ["black"]}, "card": {"type": "string", "maxLength": 200}}},
    "src_in": {"type": "integer", "minimum": 0}, "speed": _CLIP["speed"], "hold_last": {"type": "boolean"}, "reason": _REASON, "effects": {"type": "array", "items": _EFFECT}, "transform": _CLIP["transform"], "blend": {"enum": sorted(render.BLEND)}, "matte": _REF,
    "transition_in": {"type": "object", "additionalProperties": False, "required": ["type", "dur"], "properties": {"type": {"enum": sorted(render.TRANSITIONS)}, "dur": {"type": "integer", "minimum": 0, "maximum": 480}, "reason": {"type": "string"}}},
    "until": {"type": "object", "additionalProperties": False, "required": ["silence"], "properties": {"silence": {"type": "string"}, "plus": {"type": "integer", "minimum": 0}}}, "finish": {"type": "boolean"}}}
SOUND = {"type": "object", "additionalProperties": False, "required": ["id", "source", "bus", "at", "reason"], "properties": {
    "id": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,40}$"}, "source": {"type": "object", "additionalProperties": False, "minProperties": 1, "maxProperties": 1, "properties": {"line": {"type": "string"}, "cue": {"type": "string"}, "sound": {"type": "string"}, "asset": {"type": "string"}}},
    "bus": _AUD["bus"], "at": _AT, "to": _AT, "src_in": _AUD["src_in"], "frames": {"type": "integer", "minimum": 1}, "gain_db": _AUD["gain_db"], "pan": _AUD["pan"], "distance": _AUD["distance"], "occlusion": _AUD["occlusion"],
    "fade_in": _AUD["fade_in"], "fade_out": _AUD["fade_out"], "effects": _AUD["effects"], "reason": _REASON}}
SILENCE = {"type": "object", "additionalProperties": False, "required": ["id", "at", "frames", "max_dbfs", "reason"], "properties": {"id": {"type": "string"}, "at": _AT, "not_before": _AT, "frames": {"type": "integer", "minimum": 2}, "max_dbfs": {"type": "number", "maximum": -30}, "reason": _REASON}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["schema_version", "scope", "clips", "audio"], "properties": {
    "schema_version": {"const": "1"}, "scope": {"type": "string"}, "finish": {"type": "array", "items": _EFFECT}, "clips": {"type": "array", "items": CLIP}, "audio": {"type": "array", "items": SOUND},
    "silences": {"type": "array", "items": SILENCE}, "rooms": {"type": "object", "additionalProperties": {"type": "object", "additionalProperties": False, "properties": {"seconds": {"type": "number", "minimum": .02, "maximum": 8}, "damp": {"type": "number", "minimum": 200, "maximum": 16000}, "seed": {"type": "integer"}, "pre": {"type": "number", "minimum": 0, "maximum": .2}}}},
    "ducking": _T["ducking"], "loudness": {"type": "number", "minimum": -24, "maximum": -12}, "markers": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["at", "label"], "properties": {"at": _AT, "label": {"type": "string"}}}},
    "parts": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["scene"], "properties": {"scene": {"type": "string"}, "transition_in": CLIP["properties"]["transition_in"], "reason": {"type": "string"}}}},
    "subtitle": {"type": "boolean"}}}
FINISH = [{"op": "film_curve", "version": "1", "params": {"shoulder": .8}}, {"op": "vignette", "version": "1", "params": {"amount": .35}}, {"op": "grain", "version": "1", "params": {"amount": .022, "size": 1.5}}]


class EditRefused(RuleRefused):
    """Where: clip or sound, frame, rule."""


def check_doc(doc):
    e = sorted(jsonschema.Draft202012Validator(SCHEMA).iter_errors(doc), key=lambda x: len(x.path))
    if e:
        raise EditRefused("; ".join(f"{'/'.join(map(str, x.path)) or 'edit'}: {x.message[:160]}" for x in e[:4]))
    ids = [c["id"] for c in doc["clips"]] + [a["id"] for a in doc["audio"]] + [s["id"] for s in doc.get("silences", [])]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise EditRefused(f"used twice: {sorted(dup)}")
    for c in doc["clips"]:
        if c.get("track", 0) and "at" not in c:
            raise EditRefused(f"{c['id']}: a clip on an upper track needs 'at'")
        if not c.get("track", 0) and "at" in c:
            raise EditRefused(f"{c['id']}: clips on track 0 follow each other; move it instead of giving it a time")
        for e in c.get("effects", []):
            check_effect(c["id"], e)
    for e in doc.get("finish", []):
        check_effect("finish", e)
    for a in doc["audio"]:
        for e in a.get("effects", []):
            if e.get("op") not in render.SOUND_OPS:
                raise EditRefused(f"{a['id']}: sound effect '{e.get('op')}' is not built; there are {sorted(render.SOUND_OPS)}")
            if e["op"] == "room" and e.get("name") != "room" and e.get("name") not in doc.get("rooms", {}):
                raise EditRefused(f"{a['id']}: there is no room called {e.get('name')!r}")


def check_effect(where, e):
    if e["op"] in render.TEXT_OPS:
        return
    if e["op"] not in render.OPS or e["op"] in ("place", "over", "matte"):
        raise EditRefused(f"{where}: effect '{e['op']}' is not built in this engine; there are {sorted(set(render.OPS) - {'place', 'over', 'matte'})}")
    bad = set(e["params"]) - {n for n, _ in render.OPS[e["op"]][0]} - {"aux"}
    if bad:
        raise EditRefused(f"{where}: {e['op']} has no parameters {sorted(bad)}; it has {[n for n, _ in render.OPS[e['op']][0]]}")


# ------------------------------------------------------------ revisions
def latest(db: DB, film, scope):
    r = db.one("select * from cut_rev where film_id=? and scope=? order by rev desc limit 1", film, scope)
    return {**r, "doc": json.loads(r["doc_json"])} if r else None


def best(db: DB, film, scope):
    """The approved revision, if it is not older than a newer draft the user asked for; otherwise the newest."""
    r = db.one("select * from cut_rev where film_id=? and scope=? and status='approved' order by rev desc limit 1", film, scope)
    return {**r, "doc": json.loads(r["doc_json"])} if r else latest(db, film, scope)


def _store(db, film, scope, doc, author, base=None, commands=(), agent_hash=None, task=None, note=None):
    check_doc(doc)
    n = 1 + (db.val("select max(rev) from cut_rev where film_id=? and scope=?", film, scope) or 0)
    cid = db.insert("cut_rev", id=new_id("ct"), film_id=film, scope=scope, rev=n, base_rev=base, doc_json=doc, content_hash=sha(doc), commands_json=list(commands), author=author, agent_hash=agent_hash, task_id=task, note=note, created_at=now())
    flow.set_hash(db, "cut", cid, sha(doc))
    db.event(author, "cut.revised", "cut_rev", cid, {"scope": scope, "rev": n, "commands": len(commands)})
    flow.changed(db, film, "cut.revised")
    return cid


def import_cut(db: DB, film, scope, doc, author):
    """From a project file. A file never overwrites an edit that somebody has worked on since."""
    doc = {**doc, "scope": scope}
    r = latest(db, film, scope)
    if r and (r["content_hash"] == sha(doc) or r["author"] != author):
        return r["id"]
    return _store(db, film, scope, doc, author, note="from the project file")


def first_assembly(db: DB, film, scene, author="assembly") -> str:
    """Every designed shot of a scene in script order at its designed length, with the scene's lines under the shots that cover them. A starting point, not an edit."""
    scope = f"scene:{scene}"
    if latest(db, film, scope):
        raise RuleRefused("this scene already has an edit")
    f = films.fps(db, film)
    clips, audio, where = [], [], {}
    for s in db.q("select sh.id,sh.code,ss.spec_json from shot sh join shot_spec ss on ss.shot_id=sh.id where sh.scene_id=? and ss.status<>'superseded' and ss.rev=(select max(rev) from shot_spec x where x.shot_id=sh.id) order by sh.ordinal,sh.code", scene):
        j = json.loads(s["spec_json"])
        clips.append({"id": s["code"], "source": {"shot": s["id"]}, "dur": int(j.get("frames") or f * 4), "reason": (j.get("purpose") or "designed shot")[:300], "effects": []})
        for r in db.q("select span_id from shot_span where shot_id=?", s["id"]):
            where.setdefault(r["span_id"], s["code"])
    if not clips:
        raise RuleRefused("no shots have been designed for this scene")
    last = {}
    for l in lines_of(db, film, scene):
        clip = where.get(l["span_id"], clips[0]["id"])
        at = {"after": last[clip], "frame": f // 2} if clip in last else {"clip": clip, "frame": f // 2}
        audio.append({"id": short(l["id"]), "source": {"line": l["id"]}, "bus": "narration" if l["narration"] else "dialogue", "at": at, "reason": "script order"})
        last[clip] = short(l["id"])
    return _store(db, film, scope, {"schema_version": "1", "scope": scope, "finish": FINISH, "clips": clips, "audio": audio, "silences": [], "rooms": {}}, author)


def short(i):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in i.split(":")[-1])[:40]


def lines_of(db: DB, film, scene):
    r = db.one("select span_from,span_to from scene where id=?", scene)
    return db.q("""select l.id,l.text,l.span_id,l.speaker_id,s.ordinal,s.kind='narration' or c.kind='voice' and s.kind<>'dialogue' narration from script_line l join source_span s on s.id=l.span_id left join canon_entity c on c.id=l.speaker_id
        where l.film_id=? and s.ordinal>=? and s.ordinal<? and l.id not like '%CAST-%' order by s.ordinal,l.rowid""", film, r["span_from"] if r["span_from"] is not None else 0, r["span_to"] if r["span_to"] is not None else 10 ** 9)


# ------------------------------------------------------------ commands
def _find(doc, cid, kind="clips"):
    for c in doc[kind]:
        if c["id"] == cid:
            return c
    raise EditRefused(f"there is no {'clip' if kind == 'clips' else 'sound' if kind == 'audio' else 'silence'} called {cid!r}")


def _index(doc, before=None, after=None):
    """Position in the list. Order matters only on track 0."""
    ids = [c["id"] for c in doc["clips"]]
    for ref, add in ((before, 0), (after, 1)):
        if ref is not None:
            if ref not in ids:
                raise EditRefused(f"there is no clip called {ref!r}")
            return ids.index(ref) + add
    return len(ids)


def _put(c, key, v):
    if v is None:
        c.pop(key, None)
    else:
        c[key] = v


def c_place(d, *, clip, before=None, after=None, **_):
    d["clips"].insert(_index(d, before, after), clip)


def c_trim(d, *, id, dur=None, src_in=None, **_):
    c = _find(d, id)
    if dur is not None:
        c["dur"] = dur
    if src_in is not None:
        c["src_in"] = src_in


def c_slip(d, *, id, frames, **_):
    c = _find(d, id)
    if c.get("src_in", 0) + frames < 0:
        raise EditRefused(f"{id}: cannot slip before the first frame of its source")
    c["src_in"] = c.get("src_in", 0) + frames


def c_move(d, *, id, before=None, after=None, at=None, track=None, **_):
    c = _find(d, id)
    d["clips"].remove(c)
    if track is not None:
        c["track"] = track
    if at is not None:
        c["at"] = at
    if not c.get("track"):
        c.pop("at", None)
    d["clips"].insert(_index(d, before, after), c)


def c_remove(d, *, id, **_):
    for k in ("clips", "audio", "silences"):
        for c in d.get(k, []):
            if c["id"] == id:
                d[k].remove(c)
                return
    raise EditRefused(f"there is nothing called {id!r}")


def c_source(d, *, id, source, **_):
    kind = "clips" if any(c["id"] == id for c in d["clips"]) else "audio"
    _find(d, id, kind)["source"] = source


def _setter(key, kind="clips"):
    def f(d, *, id, value, **_):
        _put(_find(d, id, kind), key, value)
    return f


def c_add_effect(d, *, id, effect, index=None, **_):
    e = _find(d, id).setdefault("effects", [])
    e.insert(len(e) if index is None else index, effect)


def c_set_effect(d, *, id, index, params=None, enabled=None, mask=None, **_):
    e = _find(d, id).get("effects", [])
    if not 0 <= index < len(e):
        raise EditRefused(f"{id}: it has {len(e)} effects, there is no number {index}")
    if params is not None:
        e[index]["params"] = {**e[index]["params"], **params}
    if enabled is not None:
        e[index]["enabled"] = enabled
    if mask is not None:
        e[index]["mask"] = mask


def c_remove_effect(d, *, id, index, **_):
    e = _find(d, id).get("effects", [])
    if not 0 <= index < len(e):
        raise EditRefused(f"{id}: it has {len(e)} effects, there is no number {index}")
    e.pop(index)


def c_place_sound(d, *, sound, **_):
    d["audio"].append(sound)


def c_set_sound(d, *, id, fields, **_):
    s = _find(d, id, "audio")
    bad = set(fields) - set(SOUND["properties"]) | (set(fields) & {"id"})
    if bad:
        raise EditRefused(f"{id}: a sound has no {sorted(bad)}")
    for k, v in fields.items():
        _put(s, k, v)


def c_silence(d, *, silence, **_):
    d.setdefault("silences", []).append(silence)


def c_marker(d, *, marker, **_):
    d.setdefault("markers", []).append(marker)


def _whole(key):
    def f(d, *, value, **_):
        _put(d, key, value)
    return f


COMMANDS = {"place": (c_place, "put a new clip before or after another"), "trim": (c_trim, "change length or start in the source"), "slip": (c_slip, "show another part of the source, same length"), "move": (c_move, "change the order or the track"),
            "remove": (c_remove, "take out a clip, a sound or a silence"), "set_source": (c_source, "use another shot, part, image, line, cue or sound"), "set_speed": (_setter("speed"), "retime: a number, or keyframes"), "set_transform": (_setter("transform"), "position, scale, rotation, opacity; keyframes allowed"),
            "set_transition": (_setter("transition_in"), "how it begins: " + ", ".join(sorted(render.TRANSITIONS))), "set_blend": (_setter("blend"), ", ".join(sorted(render.BLEND))), "set_matte": (_setter("matte"), "a black and white picture that shows or hides"), "set_hold": (_setter("hold_last"), "keep the last frame when the source ends"), "set_until": (_setter("until"), "let the picture stay until a silence is complete"),
            "add_effect": (c_add_effect, "append an effect"), "set_effect": (c_set_effect, "change parameters, switch, or limit with a mask"), "remove_effect": (c_remove_effect, "take out an effect"),
            "place_sound": (c_place_sound, "add a line, cue, library sound or asset"), "set_sound": (c_set_sound, "change time, level, place, fades or effects of a sound"), "plan_silence": (c_silence, "a scripted silence, enforced in the mix"),
            "marker": (c_marker, "name a moment"), "set_finish": (_whole("finish"), "effects on every picture clip"), "set_rooms": (_whole("rooms"), "acoustic spaces"), "set_ducking": (_whole("ducking"), "what gives way to what, by how much"), "set_loudness": (_whole("loudness"), "target level"), "set_parts": (_whole("parts"), "scenes of the film in order")}


def apply(db: DB, film, scope, base_rev: int, commands: list[dict], author, *, agent_hash=None, task=None, note=None, dry=False) -> dict:
    """All or nothing. Every command needs a reason."""
    cur = latest(db, film, scope)
    if not cur and base_rev:
        raise EditRefused("there is no such edit")
    if cur and cur["rev"] != base_rev:
        raise EditRefused(f"the edit has changed: the newest revision is {cur['rev']}, these commands were written for {base_rev}. Look at what changed and send them again.")
    if not commands:
        raise EditRefused("no commands")
    if len(commands) > 400:
        raise EditRefused("at most 400 commands at once")
    doc = copy.deepcopy(cur["doc"]) if cur else {"schema_version": "1", "scope": scope, "finish": FINISH, "clips": [], "audio": [], "silences": [], "rooms": {}}
    for n, c in enumerate(commands, 1):
        op = c.get("op")
        if op not in COMMANDS:
            raise EditRefused(f"command {n}: there is no command {op!r}; there are {sorted(COMMANDS)}")
        if len(str(c.get("reason", "")).strip()) < 3:
            raise EditRefused(f"command {n} ({op}): every change needs a reason")
        try:
            COMMANDS[op][0](doc, **{k: v for k, v in c.items() if k not in ("op",)})
        except EditRefused as e:
            raise EditRefused(f"command {n} ({op}): {e}") from None
        except TypeError as e:
            raise EditRefused(f"command {n} ({op}): {str(e).replace('c_', '').split('()')[-1].strip()}") from None
    check_doc(doc)
    tl, status, prob = resolve(db, film, scope, doc)
    crit = [p for p in prob if p["severity"] == "critical"]
    if crit:
        raise EditRefused("; ".join(f"{p['where']}: {p['text']}" for p in crit[:6]))
    if dry:
        return {"doc": doc, "problems": prob, "frames": tl["frames"], "changes": changes(cur["doc"] if cur else None, doc)}
    cid = _store(db, film, scope, doc, author, cur["rev"] if cur else None, commands, agent_hash, task, note)
    return {"id": cid, "rev": db.val("select rev from cut_rev where id=?", cid), "problems": prob, "frames": tl["frames"], "changes": changes(cur["doc"] if cur else None, doc)}


def changes(a, b) -> list[str]:
    """What a person would want to know about the difference."""
    if not a:
        return ["new edit"]
    out = []
    for kind, word in (("clips", "clip"), ("audio", "sound"), ("silences", "silence")):
        x, y = {c["id"]: c for c in a.get(kind, [])}, {c["id"]: c for c in b.get(kind, [])}
        out += [f"{word} {i} removed" for i in x if i not in y] + [f"{word} {i} added" for i in y if i not in x]
        for i in x:
            if i in y and x[i] != y[i]:
                out.append(f"{word} {i}: " + ", ".join(k if k not in ("dur",) else f"length {x[i].get(k)} to {y[i].get(k)}" for k in sorted(set(x[i]) | set(y[i])) if x[i].get(k) != y[i].get(k)))
    if [c["id"] for c in a["clips"] if not c.get("track")] != [c["id"] for c in b["clips"] if not c.get("track")] and {c["id"] for c in a["clips"]} == {c["id"] for c in b["clips"]}:
        out.append("order of clips changed")
    out += [f"{k} changed" for k in ("finish", "rooms", "ducking", "loudness", "parts", "markers") if a.get(k) != b.get(k)]
    return out


# ------------------------------------------------------------ material
def slot_asset(db: DB, film, kind, ref):
    r = db.one("select v.asset_id from slot s join slot_current v on v.slot_id=s.id where s.film_id=? and s.kind=? and s.ref_id=?", film, kind, ref)
    return r["asset_id"] if r else None


def shot_id(db: DB, film, ref):
    r = db.one("select sh.id,sh.scene_id from shot sh join scene s on s.id=sh.scene_id where s.film_id=? and (sh.id=? or sh.code=?)", film, ref, ref)
    return r["id"] if r else None


def sequence_len(d: Path) -> int:
    """Frames of a previs sequence counted from 0 without a gap."""
    n = 0
    if d.exists():
        names = {p.name for p in d.iterdir()}
        while f"{n:04d}.png" in names:
            n += 1
    return n


def previs_clip(db: DB, film, part, make=True):
    """The previs pictures of a shot as a clip. Made once; a changed sequence is a new clip."""
    d = films.blocking_dir(db, film) / part
    n = sequence_len(d)
    if n < 2:
        return None, n
    key = f"{film}/{part}/{n}/{int(max((d / f'{i:04d}.png').stat().st_mtime for i in (0, n - 1)))}"
    old = db.val("select id from asset where provenance_json like ?", f'%"previs_key":"{key}"%')
    if not old:                                          # made before the key existed
        old = db.val("select id from asset where provenance_json=?", dumps({"blocking_clip": part, "frames": n}))
    if old or not make:
        return old, n
    import subprocess
    out = data_dir() / "tmp" / f"previs_{new_id('p')}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    f = films.get(db, film)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(f["fps_num"]), "-i", str(d / "%04d.png"), "-vf", f"scale={f['width']}:{f['height']}:flags=lanczos", "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", str(out)], check=True, timeout=600)
    aid, _ = assets.add(db, out, origin="rendered", storage="derived", provenance={"previs_key": key}, actor="blender", move=True)
    assets.set_licence(db, aid, "own")
    return aid, n


def picture(db: DB, film, src: dict, want: int, make: bool):
    """Best picture for a place. Returns engine clip fields and a status word; None asset means: not made yet."""
    if "asset" in src:
        k = db.val("select kind from asset where id=?", src["asset"])
        if not k:
            raise EditRefused(f"asset {src['asset']} does not exist")
        return {"kind": k, "asset": src["asset"]}, "chosen file"
    if "solid" in src:
        return {"kind": "solid"}, "black"
    if "card" in src and "shot" not in src:
        return {"kind": "solid", "card": src["card"]}, "title card"
    if "image" in src:
        a = images.approved(db, film, src["image"])
        return ({"kind": "image", "asset": a}, "generated still") if a else ({"kind": "solid", "card": f"{src['image']}\nimage not made yet"}, "missing")
    sid = shot_id(db, film, src["shot"])
    if not sid:
        raise EditRefused(f"the film has no shot {src['shot']!r}")
    part = src.get("part") or sid
    v = slot_asset(db, film, "video", part if src.get("part") else sid)
    if v:
        rep = db.one("select value_json from check_result where asset_id=? and check_name='video' order by rowid desc limit 1", v)
        rng = json.loads(rep["value_json"]).get("usable") if rep else None
        a, b = max(rng, key=lambda r: r[1] - r[0]) if rng else (0, render.count_frames(assets.path_of(db, v)) - 1)
        return {"kind": "video", "asset": v, "src_in": a, "_avail": b - a + 1}, "generated clip"
    for r in db.q("select code from image_spec where film_id=? and shot_id=? and role='still' and retired=0 order by ordinal", film, sid):
        a = images.approved(db, film, r["code"])
        if a and not src.get("part"):
            return {"kind": "image", "asset": a}, "generated still"
    code = db.val("select code from shot where id=?", sid)
    for name in ([src["part"]] if src.get("part") else [sid, code]):
        aid, n = previs_clip(db, film, name.replace(":", "_"), make)
        if n >= 2:
            return ({"kind": "video", "asset": aid, "src_in": 0, "_avail": n}, "3D blocking") if aid else ({"kind": "solid", "card": f"{code}\nprevis clip not prepared yet"}, "not prepared yet")
        if n == 1:
            a = images.blocking_asset(db, film, f"BLOCK:{name.replace(':', '_')}", make)
            return ({"kind": "image", "asset": a}, "3D blocking") if a else ({"kind": "solid", "card": f"{code}: previs picture not prepared yet"}, "not prepared yet")
    for role in ("first", "last", "plate"):
        for r in db.q("select code from image_spec where film_id=? and shot_id=? and role=? and retired=0 order by ordinal", film, sid, role):
            a = images.approved(db, film, r["code"])
            if a:
                return {"kind": "image", "asset": a}, "a single frame, no motion yet"
    purpose = db.val("select purpose from shot_spec where shot_id=? order by rev desc limit 1", sid) or ""
    return {"kind": "solid", "card": f"{code}\n{purpose}"}, "storyboard card"


def depth_asset(db: DB, film, name, make):
    p = films.blocking_dir(db, film) / name.replace(":", "_") / "depth.png"
    if not p.exists():
        return None
    r = db.one("select id from asset where sha256=?", assets.file_sha(p))
    if r:
        return r["id"]
    if not make:
        return None
    aid, _ = assets.add(db, p, origin="rendered", storage="derived", provenance={"depth": name, "film": film}, actor="blender")
    assets.set_licence(db, aid, "own")
    return aid


def _file(db, film, ref, make):
    if isinstance(ref, str):
        return ref
    if "asset" in ref:
        return ref["asset"]
    if "image" in ref:
        return images.approved(db, film, ref["image"])
    if "depth_of" in ref:
        return depth_asset(db, film, ref["depth_of"], make)
    return db.val("select id from asset where provenance_json like ? order by rowid desc limit 1", f'%"matte_of":"{ref["matte_of"]}"%')


def speech_seconds(text):
    return max(.6, len(text.split()) / 2.5)


def temp_voice(db: DB, line_id, text):
    """Windows' built-in voice, offline and free. Temp use only; the database refuses it in a final mix. None if this computer has none."""
    import os
    import subprocess
    old = db.val("select asset_id from audio_take where line_id=? and source='temp'", line_id)
    if old:
        return old
    p = data_dir() / "tmp" / f"temp_{short(line_id)}_{new_id('t')}.wav"
    p.parent.mkdir(parents=True, exist_ok=True)
    ps = "Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;$s.Rate=-1;$s.SetOutputToWaveFile($env:ST_OUT);$s.Speak($env:ST_TXT);$s.Dispose()"
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], env={**os.environ, "ST_OUT": str(p), "ST_TXT": text}, check=True, timeout=60, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return None
    aid, _ = assets.add(db, p, origin="authored", provenance={"temp_voice": line_id}, actor="engine")
    p.unlink(missing_ok=True)
    assets.set_licence(db, aid, "windows_tts")
    n = 1 + (db.val("select max(take_no) from audio_take where line_id=?", line_id) or 0)
    db.insert("audio_take", id=new_id("tk"), line_id=line_id, asset_id=aid, take_no=n, settings_json={}, source="temp", format_json={})
    return aid


def library_sound(db: DB, title):
    return db.val("select l.asset_id from library_item l where l.title=? and l.retired_at is null", title) or db.val("select id from asset where provenance_json like ?", f'%"synth":"{title}"%')


def heard(db: DB, film, src: dict, make: bool):
    """(asset or None, status, text of the line or None)"""
    if "asset" in src:
        if not db.one("select 1 from asset where id=?", src["asset"]):
            raise EditRefused(f"asset {src['asset']} does not exist")
        return src["asset"], "chosen file", None
    if "line" in src:
        l = db.one("select text from script_line where id=? and film_id=?", src["line"], film)
        if not l:
            raise EditRefused(f"the film has no line {src['line']!r}")
        a = slot_asset(db, film, "voice", src["line"])
        if a:
            return a, "approved take", l["text"]
        t = db.val("select asset_id from audio_take where line_id=? and source='temp'", src["line"]) or (temp_voice(db, src["line"], l["text"]) if make else None)
        return t, "temporary computer voice" if t else "not made yet", l["text"]
    if "cue" in src:
        c = db.one("select c.id,c.asset_id from sound_cue c join scene s on s.id=c.scene_id where c.id=? and s.film_id=?", src["cue"], film)
        if not c:
            raise EditRefused(f"the film has no sound cue {src['cue']!r}")
        a = slot_asset(db, film, "sfx", src["cue"])
        if a:
            return a, "approved effect", None
        if c["asset_id"]:
            return c["asset_id"], "library sound", None
        s = films.setting(db, film, "stand_in_sounds", {}).get(src["cue"])
        a = library_sound(db, s) if s else None
        return a, "procedural placeholder" if a else "missing", None
    a = library_sound(db, src["sound"])
    return a, "studio sound" if a else "missing", None


def seconds_of(db: DB, aid) -> float:
    p = assets.path_of(db, aid)
    if p.suffix.lower() == ".wav" and p.exists():
        return sf.info(str(p)).duration
    return float(json.loads(db.val("select probe_json from asset where id=?", aid))["format"]["duration"])


# ------------------------------------------------------------ resolving an edit to an engine timeline
def resolve(db: DB, film, scope, doc=None, make=False):
    """(timeline, status, problems). make=False never writes anything: what does not exist yet becomes a named gap."""
    with _lock:
        if doc is None:
            cur = latest(db, film, scope)
            if not cur:
                raise RuleRefused("there is no edit yet")
            doc = cur["doc"]
        if doc.get("parts"):
            tl, st, pr = _film(db, film, doc, make)
        else:
            tl, st, pr = _scene(db, film, doc, make)
        render.validate(tl)
        pr = pr + render.lint(tl, lambda a: _path(db, a)) + lint(db, film, scope, doc, tl, st)
        return tl, st, pr


def planned(prob) -> list[dict]:
    return [p["planned"] for p in prob if "planned" in p]


def _path(db, a):
    try:
        return assets.path_of(db, a) if a else None
    except KeyError:
        return None


def _scene(db: DB, film, doc, make):
    fl, prob, status, at, ends, sil = films.get(db, film), [], {}, {}, {}, {}
    fps, sr = fl["fps_num"] / fl["fps_den"], 48000
    S = lambda frame: int(round(frame / fps * sr))
    finish, clips, f, base = doc.get("finish", []), [], 0, []
    for c in doc["clips"]:                                     # track 0: one after the other
        if c.get("track", 0):
            continue
        fields, st = picture(db, film, c["source"], c["dur"], make)
        status[c["id"]] = st
        ti = c.get("transition_in")
        if ti and ti["type"] in render.OVERLAP and base:
            f -= min(ti["dur"], base[-1]["dur"] - 1)
        e = _clip(db, film, c, fields, finish, f, make, prob)
        at[c["id"]], ends[c["id"]] = f, f + e["dur"]
        base.append(e)
        f += e["dur"]
    total = f

    def when(a, what):
        n = a.get("frame", 0)
        if "clip" in a:
            if a["clip"] not in at:
                raise EditRefused(f"{what}: there is no clip called {a['clip']!r}")
            return at[a["clip"]] + n
        if "after" in a:
            if a["after"] not in ends:
                raise EditRefused(f"{what}: {a['after']!r} must come before it in the list")
            return math.ceil(ends[a["after"]]) + n
        if "silence" in a:
            if a["silence"] not in sil:
                raise EditRefused(f"{what}: there is no silence called {a['silence']!r}")
            return sil[a["silence"]][0] + n
        return (total if a.get("end") else 0) + n

    audio, late, spoken = [], [], []

    def place(a):
        aid, st, text = heard(db, film, a["source"], make)
        kind = "voice" if "line" in a["source"] else "sound"
        status[f"{kind} {a['id']}"] = st
        p = when(a["at"], a["id"])
        q = when(a["to"], a["id"]) if "to" in a else p + a["frames"] if "frames" in a else None
        dur = seconds_of(db, aid) * fps if aid and _path(db, aid) and _path(db, aid).exists() else speech_seconds(text) * fps if text else 0
        real = min(dur, q - p) if q is not None and dur else dur
        ends[a["id"]] = p + real
        if kind == "voice":
            spoken.append((p, a["source"]["line"], p + real, a["id"]))
        if q is not None and q <= p:
            raise EditRefused(f"{a['id']}: it would end (frame {q}) before it starts (frame {p})")
        if aid is None:
            prob.append({"severity": "moderate" if st == "not made yet" else "major", "where": a["id"], "text": f"its sound is {st}", "planned": {"id": a["id"], "a": int(p), "b": int(math.ceil(p + real)), "text": text or a["reason"], "status": st}})
            return
        x = {"id": a["id"], "asset": aid, "bus": a["bus"], "at_sample": S(p), "reason": a["reason"]}
        if kind == "voice":
            x["line"] = a["source"]["line"]
        if "cue" in a["source"]:
            x["cue"] = a["source"]["cue"]
        if q is not None:
            x["dur_samples"] = S(q - p)
        for k in ("src_in", "gain_db", "pan", "distance", "occlusion", "fade_in", "fade_out", "effects"):
            if k in a:
                x[k] = a[k]
        audio.append(x)

    for a in doc["audio"]:
        if any(z.get("silence") for z in (a["at"], a.get("to", {}))):      # placed once the silences are known
            late.append(a)
        else:
            place(a)
    for s in doc.get("silences", []):
        p = max([when(s["at"], s["id"])] + ([when(s["not_before"], s["id"])] if "not_before" in s else []))
        sil[s["id"]] = (p, s["frames"], s)
        ends[s["id"]] = p + s["frames"]
    for c, e in zip([c for c in doc["clips"] if not c.get("track", 0)], base):    # a picture that waits for a silence to finish
        if "until" in c:
            if c["until"]["silence"] not in sil:
                raise EditRefused(f"{c['id']}: there is no silence called {c['until']['silence']!r}")
            p, n, _ = sil[c["until"]["silence"]]
            need = p + n + c["until"].get("plus", 0) - (e["at"] + e["dur"])
            if need > 0:
                if c is not [x for x in doc["clips"] if not x.get("track", 0)][-1]:
                    raise EditRefused(f"{c['id']}: only the last clip can wait for a silence")
                e["dur"] += need
                if e["kind"] == "video":
                    e["hold_last"] = True
                total = e["at"] + e["dur"]
    for a in late:
        place(a)
    tracks = {0: base}
    for c in doc["clips"]:
        if c.get("track", 0):
            fields, st = picture(db, film, c["source"], c["dur"], make)
            status[c["id"]] = st
            p = when(c["at"], c["id"])
            if p < 0 or p >= total:
                raise EditRefused(f"{c['id']}: it would start at frame {p}; the edit has {total}")
            e = _clip(db, film, c, fields, [] if c.get("finish") is not True else finish, p, make, prob)
            e["dur"] = min(e["dur"], total - p)
            at[c["id"]], ends[c["id"]] = p, p + e["dur"]
            tracks.setdefault(c["track"], []).append(e)
    for p, n, s in sil.values():
        for x in spoken:
            if x[0] < p + n and x[2] > p + .5:
                prob.append({"severity": "critical" if x[0] >= p else "major", "where": x[3], "text": f"speech runs into the scripted silence {s['id']} and would be cut off"})
    if str(doc.get("scope", "")).startswith("scene:"):
        order = [l["id"] for l in lines_of(db, film, doc["scope"].split(":", 1)[1])]
        seq = [x[1] for x in sorted(spoken) if x[1] in order]
        if seq != sorted(seq, key=order.index):
            prob.append({"severity": "moderate", "where": "lines", "text": "lines are not in script order"})
    tl = {"schema_version": "1", "film_id": film, "fps": [fl["fps_num"], fl["fps_den"]], "size": [fl["width"], fl["height"]], "sample_rate": sr, "colour": "linear-bt709", "frames": max(1, total),
          "video": [{"track": t, "clips": sorted(v, key=lambda c: c["at"])} for t, v in sorted(tracks.items())], "audio": audio,
          "silences": [{"from_frame": p, "frames": n, "max_dbfs": s["max_dbfs"], "reason": s["reason"]} for p, n, s in sil.values()],
          "markers": [{"f": v, "label": k} for k, v in at.items() if any(c["id"] == k for c in base)] + [{"f": when(m["at"], "marker"), "label": m["label"]} for m in doc.get("markers", [])]}
    if doc.get("ducking") is not None:
        tl["ducking"] = doc["ducking"]
    tl["rooms"] = doc.get("rooms", {})
    tl["loudness"] = doc.get("loudness", -16)
    return tl, status, prob


def _clip(db, film, c, fields, finish, at, make, prob):
    e = {"id": c["id"], "at": at, "dur": c["dur"], "reason": c["reason"], **{k: v for k, v in fields.items() if not k.startswith("_")}}
    if "shot" in c["source"]:
        e["shot"] = c["source"].get("part") or c["source"]["shot"]
    eff = []
    for x in c.get("effects", []) + (finish if c.get("finish", True) else []):
        x = copy.deepcopy(x)
        miss = False
        if isinstance(x["params"].get("aux"), dict):
            a = _file(db, film, x["params"]["aux"], make)
            miss = a is None
            x["params"]["aux"] = a
        if isinstance(x.get("mask"), dict):
            a = _file(db, film, x["mask"], make)
            miss = miss or a is None
            x["mask"] = a
        if miss:
            prob.append({"severity": "major", "where": c["id"], "text": f"{x['op']}: the picture it needs does not exist yet, so this effect is left out of this version"})
            continue
        eff.append(x)
    e["effects"] = eff
    if c.get("matte") is not None:
        a = _file(db, film, c["matte"], make)
        if a:
            e["matte"] = a
        else:
            prob.append({"severity": "major", "where": c["id"], "text": "its matte does not exist yet"})
    for k in ("transform", "blend", "transition_in", "speed", "hold_last"):
        if k in c:
            e[k] = c[k]
    if e["kind"] == "video":
        a0 = fields.get("src_in", 0)
        e["src_in"] = a0 + c.get("src_in", 0)
        avail = fields.get("_avail")
        if avail is not None and not c.get("hold_last") and c.get("speed") is None:
            path = _path(db, e["asset"])
            step = render.source_fps(path) / (films.fps(db, film)) if path and path.exists() else 1
            have = int((avail - c.get("src_in", 0)) / step + 1e-6)
            if have < 1:
                raise EditRefused(f"{c['id']}: it starts after the last usable frame of its source")
            if have < e["dur"]:
                prob.append({"severity": "info", "where": c["id"], "text": f"shortened to {have} frames: its source has no more usable frames"})
                e["dur"] = have
    return e


def _film(db: DB, film, doc, make):
    """Scenes one after the other, then anything the film-level edit adds on top: titles, music, transitions between scenes."""
    tl, status, prob, off, starts = None, {}, [], 0, {}
    for p in doc["parts"]:
        cur = best(db, film, f"scene:{p['scene']}")
        name = db.one("select number,slug from scene where id=? and film_id=?", p["scene"], film)
        if not name:
            raise EditRefused(f"the film has no scene {p['scene']!r}")
        if not cur:
            prob.append({"severity": "major", "where": f"scene {name['number']}", "text": "has no edit yet"})
            continue
        t, s, pr = _scene(db, film, cur["doc"], make)
        tag = f"s{name['number']}"
        prob += [{**x, "where": f"{tag}/{x['where']}"} for x in pr]
        status.update({f"{tag}/{k}": v for k, v in s.items()})
        ti = p.get("transition_in")
        if ti and ti["type"] in render.OVERLAP and tl:
            off -= ti["dur"]
        starts[p["scene"]] = off
        for tr in t["video"]:
            for c in tr["clips"]:
                c["id"], c["at"] = f"{tag}/{c['id']}", c["at"] + off
        if ti:
            t["video"][0]["clips"][0]["transition_in"] = ti
        so = int(round(off / (t["fps"][0] / t["fps"][1]) * 48000))
        for a in t["audio"]:
            a["id"], a["at_sample"] = f"{tag}/{a['id']}", a["at_sample"] + so
            a["effects"] = [{**e, "name": f"{tag}/{e['name']}"} if e.get("op") == "room" and e["name"] in t["rooms"] else e for e in a.get("effects", [])]
        t["rooms"] = {f"{tag}/{k}" if k != "room" else f"{tag}/room": v for k, v in t["rooms"].items()}
        for x in t["silences"]:
            x["from_frame"] += off
        for m in t["markers"]:
            m["f"] += off
            m["label"] = f"{tag}/{m['label']}"
        if tl is None:
            tl = t
        else:
            by = {x["track"]: x for x in tl["video"]}
            for tr in t["video"]:
                if tr["track"] in by:
                    by[tr["track"]]["clips"] += tr["clips"]
                else:
                    tl["video"].append(tr)
            tl["audio"] += t["audio"]
            tl["silences"] += t["silences"]
            tl["markers"] += t["markers"]
            tl["rooms"].update(t["rooms"])
        off += t["frames"]
        tl["frames"] = off
    if tl is None:
        raise RuleRefused("no scene of this film has an edit yet")
    tl["loudness"] = doc.get("loudness", -16)
    if doc.get("ducking") is not None:
        tl["ducking"] = doc["ducking"]
    tl["rooms"].update(doc.get("rooms", {}))
    ends = {}

    def when(a, what):
        n = a.get("frame", 0)
        if "part" in a:
            if a["part"] not in starts:
                raise EditRefused(f"{what}: scene {a['part']!r} is not in the film edit")
            return starts[a["part"]] + n
        if "after" in a and a["after"] in ends:
            return math.ceil(ends[a["after"]]) + n
        if "clip" in a or "silence" in a:
            raise EditRefused(f"{what}: in the film edit, times refer to a scene, the start or the end")
        return (tl["frames"] if a.get("end") else 0) + n
    fps = tl["fps"][0] / tl["fps"][1]
    for c in doc["clips"]:
        if not c.get("track"):
            raise EditRefused(f"{c['id']}: in the film edit, clips lie on upper tracks above the scenes")
        fields, st = picture(db, film, c["source"], c["dur"], make)
        status[c["id"]] = st
        e = _clip(db, film, c, fields, [], when(c["at"], c["id"]), make, prob)
        e["dur"] = min(e["dur"], tl["frames"] - e["at"])
        next((x for x in tl["video"] if x["track"] == c["track"]), None) or tl["video"].append({"track": c["track"], "clips": []})
        next(x for x in tl["video"] if x["track"] == c["track"])["clips"].append(e)
    for a in doc["audio"]:
        aid, st, text = heard(db, film, a["source"], make)
        status[f"sound {a['id']}"] = st
        if aid is None:
            prob.append({"severity": "major", "where": a["id"], "text": f"its sound is {st}"})
            continue
        p = when(a["at"], a["id"])
        q = when(a["to"], a["id"]) if "to" in a else p + a["frames"] if "frames" in a else None
        ends[a["id"]] = q if q is not None else p + seconds_of(db, aid) * fps
        x = {"id": a["id"], "asset": aid, "bus": a["bus"], "at_sample": int(round(p / fps * 48000)), "reason": a["reason"], **{k: a[k] for k in ("src_in", "gain_db", "pan", "distance", "occlusion", "fade_in", "fade_out", "effects") if k in a}}
        if q is not None:
            x["dur_samples"] = int(round((q - p) / fps * 48000))
        tl["audio"].append(x)
    tl["video"].sort(key=lambda t: t["track"])
    for t in tl["video"]:
        t["clips"].sort(key=lambda c: c["at"])
    return tl, status, prob


# ------------------------------------------------------------ judging an edit by rules
def lint(db: DB, film, scope, doc, tl, status) -> list[dict]:
    out, fps = [], tl["fps"][0] / tl["fps"][1]
    add = lambda sev, where, text: out.append({"severity": sev, "where": where, "text": text})
    base = [c for t in tl["video"] if t["track"] == 0 for c in t["clips"]]
    run, since, told = 0, None, set()
    for c in base:
        s = c.get("transform", {})
        still = c["kind"] in ("image", "solid") and not any(isinstance(s.get(k), list) and s.get(k) and isinstance(s[k][0], dict) for k in ("pos", "scale", "rot")) and not any(e["op"] in ("depth_parallax", "beam", "shimmer", "shaft", "dust", "glow_pulse", "flicker") for e in c.get("effects", []))
        if c["dur"] < fps / 4:
            add("moderate", c["id"], f"only {c['dur']} frames: shorter than a quarter second")
        if c["kind"] == "image" and c["dur"] > 10 * fps:
            add("moderate", c["id"], f"a single still for {c['dur'] / fps:.1f} s")
        planned = status.get(c["id"]) in ("storyboard card", "3D blocking", "not prepared yet", "missing")
        if still and not planned:                      # a stand-in says nothing about the movement of the real shot
            since = since or c["id"]
            run += c["dur"]
            if run > 12 * fps and since not in told:
                told.add(since)
                add("moderate", c["id"], f"nothing has moved for {run / fps:.0f} s (since {since})")
        else:
            run, since = 0, None
    voice = sorted([x for x in tl["audio"] if x["bus"] in ("dialogue", "narration")], key=lambda x: x["at_sample"])
    for a, b in zip(voice, voice[1:]):
        if a["at_sample"] + seconds_of(db, a["asset"]) * 48000 > b["at_sample"] + 4800:
            add("moderate", b["id"], f"starts while {a['id']} is still speaking")
    if scope.startswith("scene:"):
        sc = scope.split(":", 1)[1]
        used = {a["source"]["line"] for a in doc["audio"] if "line" in a["source"]}
        want = lines_of(db, film, sc)
        sh = {r["span_id"] for r in db.q("select span_id from shot_span x join shot sh on sh.id=x.shot_id where sh.scene_id=?", sc)}
        for l in want:
            if l["id"] not in used and (not sh or l["span_id"] in sh):
                add("major", l["id"], f"line is not in the edit: {l['text'][:60]}")
        cuts = {c["at"] for c in base[1:]}
        for x in voice:
            r = db.one("select word_timing_json from audio_take where asset_id=?", x["asset"])
            for w in json.loads((r or {}).get("word_timing_json") or "[]") or []:
                a = x["at_sample"] / 48000 * fps + w["a"] * fps
                b = x["at_sample"] / 48000 * fps + w["b"] * fps
                hit = [k for k in cuts if a + 1.5 < k < b - 1.5]
                if hit:
                    add("info", x["id"], f"a cut at frame {hit[0]} falls inside the word '{w['w']}'")
        placed = {shot_id(db, film, c["source"]["shot"]) for c in doc["clips"] if "shot" in c["source"]}
        for r in db.q("select id,code from shot where scene_id=? and state<>'dropped'", sc):
            if r["id"] not in placed:
                add("moderate", r["code"], "designed shot is not used")
    return out
