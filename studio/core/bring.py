"""Bringing files in. Any file may be dropped in one place: the studio looks at it, says what it is and where it probably belongs, and the user decides.
Nothing is stored in the film before that. The same checks run as when the file is dropped on its own page."""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from . import assets, common, flow, images, motion, voice
from .db import DB, RuleRefused, data_dir, new_id, now

KEEP_DAYS = 14


def folder() -> Path:
    d = data_dir() / "inbox" / "waiting"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------ where a file could go
def destinations(db: DB, film, kind) -> list[dict]:
    """Every place in this film that can take a file of this kind now. 'stem' is the file name the studio asked for."""
    out = []
    if kind == "image":
        for s in images.specs(db, film):
            t = db.one("select state from generation_target where film_id=? and kind='image' and ref_id=?", film, s["code"])
            out.append({"id": f"image:{s['code']}", "page": "images", "title": f"Image {s['code']}", "stem": s["code"], "open": not t or t["state"] == "open", "reason": _again(db, film, "image", s["code"])})
    elif kind == "video":
        for s in motion.video_state(db, film):
            if s["approved"]:
                out.append({"id": f"clip:{s['package']}", "page": "video", "title": f"Gemini clip for {s['code']}", "stem": s["code"], "open": not s["target"] or s["target"]["state"] == "open", "reason": _again(db, film, "video", s["shot"]), "shot": s["shot"]})
        for s in db.q("select sh.id,sh.code from shot sh join scene c on c.id=sh.scene_id where c.film_id=? and sh.state<>'dropped' order by c.number,sh.ordinal", film):
            t = db.one("select state from generation_target where film_id=? and kind='video' and ref_id=?", film, s["id"])
            out.append({"id": f"shotclip:{s['id']}", "page": "video", "title": f"Clip for {s['code']}, made without a studio package", "stem": s["code"], "open": not t or t["state"] == "open", "reason": True, "shot": s["id"], "outside": True})
    elif kind == "audio":
        for p in voice.voice_state(db, film):
            if not p["approved"]:
                continue
            for l in p["lines"]:
                out.append({"id": f"line:{p['id']}/{l['line_id']}", "page": "voice", "title": f"{l['speaker']}: {l['text'][:70]}", "stem": re.sub(r"\{.*?\}", "", (l.get("file_name") or l["line_id"])).rsplit(".", 1)[0], "words": l["text"], "open": not l["target"] or l["target"]["state"] == "open", "reason": _again(db, film, "voice", l["line_id"]), "casting": p["casting"]})
        for p in voice.sfx_state(db, film):
            if p["approved"]:
                out.append({"id": f"sfx:{p['id']}", "page": "sound", "title": f"Sound effect: {p['doc']['sound']}", "stem": re.sub(r"\{.*?\}", "", p["doc"]["file_name"]).rsplit(".", 1)[0], "open": not p["target"] or p["target"]["state"] == "open", "reason": _again(db, film, "sfx", p["cue"])})
        for c in voice.cues_state(db, film):
            if not c["package"] and c["kind"] != "designed_silence":
                out.append({"id": f"cue:{c['id']}", "page": "sound", "title": f"{c['kind'].replace('_', ' ').capitalize()}: {c['purpose'][:70]}", "stem": c["id"].split(":")[-1], "open": not c["target"] or c["target"]["state"] == "open", "reason": _again(db, film, "sfx", c["id"]), "licence": True})
        out.append({"id": "library", "page": "library", "title": "The library, for use in any film", "stem": "", "open": True, "reason": False, "licence": True})
    return out


def _again(db, film, kind, ref):
    t = db.val("select id from generation_target where film_id=? and kind=? and ref_id=?", film, kind, ref)
    return bool(t and flow.counted(db, t))


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def suggest(db: DB, film, path: Path, name: str, kind: str) -> list[dict]:
    """Destinations with a score and the reason for it. File name first, then content: spoken words, or the first frame."""
    dest, stem = destinations(db, film, kind), norm(Path(name).stem)
    heard = None
    if kind == "audio":
        from ..inspect import audio as ia
        try:
            secs = float(assets.probe(path)["format"]["duration"])
            heard = ia.transcribe(path) if secs < 90 and any(d.get("words") for d in dest) else None
        except Exception:
            heard = None
    looks = {}
    if kind == "video":
        looks = _first_frames(db, film, path)
    for d in dest:
        score, why = 0.0, ""
        s = norm(d["stem"])
        if s and (s in stem or stem in s and len(stem) > 5):
            score, why = .9, "the file name fits"
        if d.get("words") and heard and heard["text"]:
            from ..inspect import audio as ia
            a, b = ia.words(d["words"]), ia.words(heard["text"])
            same = len(set(a) & set(b)) / max(1, len(set(a) | set(b)))
            if same > .5 and same > score:
                score, why = same, f"these words are spoken: “{heard['text'][:60]}”"
        if d.get("shot") in looks and looks[d["shot"]] > .6 and looks[d["shot"]] > score:
            score, why = looks[d["shot"]], "its first frame looks like the approved first frame"
        if d.get("outside"):
            score *= .6
        if not d["open"]:
            score *= .4
        d["score"], d["why"] = round(score, 2), why
    return sorted(dest, key=lambda d: -d["score"])


def _first_frames(db, film, path):
    import cv2
    from ..inspect import image as ii
    from ..inspect import video as iv
    out = {}
    try:
        fr = cv2.resize(iv.frame_at(path, 0), (480, 270))
    except Exception:
        return out
    for r in db.q("select m.shot_id,m.first_image from motion_plan m join shot sh on sh.id=m.shot_id join scene s on s.id=sh.scene_id where s.film_id=?", film):
        a = images.approved(db, film, r["first_image"])
        if a and assets.path_of(db, a).exists():
            out[r["shot_id"]] = iv.similarity(cv2.resize(ii.read(assets.path_of(db, a)), (480, 270)), fr)["score"]
    return out


# ------------------------------------------------------------ the waiting room
def stage(db: DB, film, src: Path, name: str) -> dict:
    """Keeps a dropped file outside the film until the user says where it belongs."""
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name)[-80:]
    kind = assets.KIND_BY_EXT.get(Path(name).suffix.lower())
    if not kind:
        raise assets.ImportProblem(f"{Path(name).suffix or 'this kind of file'} is not supported. Pictures: png, jpg, webp. Clips: mp4, mov, webm, mkv. Sound: wav, mp3, flac, ogg, opus, m4a.")
    probe = assets.probe(src)
    old = db.one("select id from asset where sha256=? and storage_class='original'", assets.file_sha(src))
    if old and (u := common.used_by(db, old["id"])):
        raise RuleRefused(f"this exact file is already in the studio ({u})")
    fid = new_id("in")
    d = folder() / fid
    d.mkdir()
    p = d / name
    shutil.move(str(src), p)
    v = next((s for s in probe["streams"] if s["codec_type"] == "video"), {})
    meta = {"id": fid, "film": film, "name": name, "kind": kind, "bytes": p.stat().st_size, "seconds": round(float(probe["format"].get("duration") or 0), 2), "size": f"{v['width']}x{v['height']}" if v.get("width") else None, "at": now(), "to": suggest(db, film, p, name, kind)}
    (d / "about.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def waiting(db: DB, film) -> list[dict]:
    out = []
    for d in sorted(folder().iterdir()):
        f = d / "about.json"
        if not f.exists() or f.stat().st_mtime < time.time() - KEEP_DAYS * 86400:
            if d.stat().st_mtime < time.time() - 3600:                # not one that is arriving now
                shutil.rmtree(d, ignore_errors=True)
            continue
        m = json.loads(f.read_text(encoding="utf-8"))
        if m["film"] == film:
            can, seen = {x["id"]: x for x in destinations(db, film, m["kind"])}, {y["id"]: y for y in m["to"]}       # what can be filled may have changed since
            m["to"] = sorted(({**x, "score": seen.get(k, {}).get("score", 0), "why": seen.get(k, {}).get("why", "")} for k, x in can.items()), key=lambda x: -x["score"])
            out.append(m)
    return out


def count(film) -> int:
    n = 0
    for f in folder().glob("*/about.json"):
        n += json.loads(f.read_text(encoding="utf-8"))["film"] == film
    return n


def _get(fid):
    d = folder() / re.sub(r"[^A-Za-z0-9_-]", "", fid)
    if not (d / "about.json").exists():
        raise RuleRefused("this file is no longer waiting")
    m = json.loads((d / "about.json").read_text(encoding="utf-8"))
    return m, d / m["name"]


def path_of(fid) -> Path:
    return _get(fid)[1]


def discard(db: DB, fid):
    m, p = _get(fid)
    shutil.rmtree(p.parent, ignore_errors=True)
    db.event("user", "inbox.discarded", "file", fid, {"name": m["name"]})


def put(db: DB, fid, dest: str, fields: dict) -> dict:
    """The user's decision. Runs exactly the import that the destination's own page runs; a refusal leaves the file waiting."""
    m, p = _get(fid)
    film = m["film"]
    g = lambda k: str(fields.get(k) or "").strip()
    cr = int(g("credits")) if g("credits").isdigit() else None
    what, _, ref = dest.partition(":")
    if what == "image":
        r = images.import_image(db, film, ref, p, g("note"), g("reason"), g("terms"), g("credit"))
    elif what == "clip":
        r = motion.import_video(db, ref, p, g("label"), g("model"), g("usage"), g("reason"))
    elif what == "shotclip":
        r = motion.import_clip(db, film, ref, p, g("label"), g("model"), g("reason"))
    elif what == "line":
        pid, _, line = ref.partition("/")
        r = voice.import_voice(db, pid, line, p, cr, g("note"), g("reason"), None, g("voice"), g("voice_kind"))
    elif what == "sfx":
        r = voice.import_sfx(db, ref, p, cr, g("reason"))
    elif what == "cue":
        r = voice.import_cue(db, film, ref, p, g("terms"), g("title") or m["name"], g("reason"), g("credit"))
    elif what == "library":
        r = voice.import_library(db, p, g("terms"), g("title") or m["name"], g("kind") or "foley", g("credit"))
    else:
        raise RuleRefused("choose where it belongs")
    shutil.rmtree(p.parent, ignore_errors=True)
    db.event("user", "inbox.placed", "file", fid, {"name": m["name"], "to": dest})
    return r
