"""Generation packages and their linters. A package is the same whether the user runs it by hand or the API does."""
from __future__ import annotations

import json
import re

import jsonschema

from ..inspect.audio import words
from . import flow
from .db import DB, ROOT, RuleRefused, dumps, new_id, now, sha

SPEC = ROOT / "studio/schema"
SCHEMAS = {k: json.loads((SPEC / f"{k}_package.schema.json").read_text(encoding="utf-8")) for k in ("voice", "video")}

# What each model accepts. Read from ElevenLabs' own guides on 25 Nov 2026; unknown models are refused rather than guessed.
MODELS = {
    "eleven_v4": {"title": "Eleven v4", "tags": True, "breaks": False, "ipa_slashes": True, "max": 10_000, "dialogue": True, "stitch": True},
    "eleven_v4_turbo": {"title": "Eleven v4 Turbo", "tags": True, "breaks": False, "ipa_slashes": False, "max": 10_000, "dialogue": False, "stitch": True},
    "eleven_v3": {"title": "Eleven v3", "tags": True, "breaks": False, "ipa_slashes": False, "max": 5_000, "dialogue": True, "stitch": False},
    "eleven_multilingual_v2": {"title": "Multilingual v2", "tags": False, "breaks": True, "ipa_slashes": False, "max": 10_000, "dialogue": False, "stitch": True},
}
FORMATS = {"mp3_44100_128": 1, "opus_48000_128": 1, "wav_24000": 2, "pcm_24000": 2, "opus_48000_192": 2}


def P(sev, where, text):
    return {"severity": sev, "where": where, "text": text}


def _schema(kind, doc):
    return [P("critical", "/".join(map(str, e.path)) or "package", e.message[:200]) for e in jsonschema.Draft202012Validator(SCHEMAS[kind]).iter_errors(doc)]


def spoken_to_words(spoken: str, pron: list[dict]) -> list[str]:
    t = re.sub(r"<[^>]+>", " ", spoken)
    t = re.sub(r"\[[^\]]*\]", " ", t)
    for p in pron or []:
        t = re.sub(re.escape(p["say_as"]), p["term"], t, flags=re.I)
    return words(t)


def lint_voice(doc: dict, script: dict[str, str]) -> list[dict]:
    """script: line id -> exact approved text."""
    out = _schema("voice", doc)
    if out:
        return out
    m = MODELS.get(doc["model"])
    if not m:
        return out + [P("critical", "model", f"{doc['model']} is not a model this studio has verified rules for")]
    if doc["method"] == "dialogue_scene" and not m["dialogue"]:
        out.append(P("critical", "method", f"{m['title']} does not support dialogue scenes"))
    total = 0
    bound = {b["speaker"] for b in doc["voice_bindings"]}
    for l in doc["lines"]:
        w = l["line_id"]
        total += len(l["spoken_text"])
        if w not in script:
            out.append(P("critical", w, "this line is not in the approved script"))
        elif l["text"] != script[w]:
            out.append(P("critical", w, "the text differs from the approved script"))
        if spoken_to_words(l["spoken_text"], l.get("pronunciation")) != words(l["text"]):
            out.append(P("critical", w, "the spoken text changes the words of the script"))
        if l["speaker"] not in bound:
            out.append(P("critical", w, f"no voice is assigned to {l['speaker']}"))
        sp = l["spoken_text"]
        if re.search(r"<\s*break", sp):
            n = [float(x) for x in re.findall(r'time="([\d.]+)s"', sp)]
            if not m["breaks"]:
                out.append(P("critical", w, f"{m['title']} ignores or reads out break tags; use punctuation or a pause tag"))
            elif any(x > 3 for x in n) or len(n) > 3:
                out.append(P("major", w, "more than three breaks, or one longer than three seconds, makes speech unstable"))
        if re.search(r"\[[^\]]+\]", sp):
            if not m["tags"]:
                out.append(P("critical", w, f"{m['title']} would read delivery tags aloud"))
            if len(re.findall(r"\[[^\]]+\]", sp)) > 3:
                out.append(P("moderate", w, "more than three delivery tags in one line compete with each other"))
        if re.search(r"/[^/\s]{2,}/", sp) and not m["ipa_slashes"]:
            out.append(P("critical", w, f"{m['title']} does not read /IPA/ spelling"))
        if re.search(r"\d", re.sub(r"<[^>]+>", "", sp)):
            out.append(P("major", w, "write numbers as words so the reading is not left to chance"))
        if len(re.findall(r"\b[A-Z]{3,}\b", re.sub(r"\[[^\]]*\]", "", sp))) > 1:
            out.append(P("moderate", w, "more than one capitalised word over-stresses the line"))
        if len(re.findall(r"!", sp)) > 1:
            out.append(P("moderate", w, "exclamation marks push towards shouting"))
        if l.get("takes_planned", 1) > 3:
            out.append(P("critical", w, "at most three attempts per line"))
        d = l["delivery"]
        if re.fullmatch(r"(?i)\s*(scared|sad|angry|emotional|normal|neutral|dramatic)\.?\s*", d["intent"]):
            out.append(P("major", w, "the intent is a label, not a playable direction"))
        if len(sp) > m["max"]:
            out.append(P("critical", w, f"longer than {m['max']} characters"))
    if doc["method"] == "dialogue_scene" and total > 2000:
        out.append(P("critical", "lines", f"{total} characters; a dialogue request should stay at or below 2,000"))
    if total != doc["characters"]:
        out.append(P("major", "characters", f"says {doc['characters']}, counted {total}"))
    s = doc["settings"]
    if not .7 <= float(s.get("speed", 1)) <= 1.2:
        out.append(P("critical", "settings", "speed must be between 0.7 and 1.2"))
    for k in ("stability", "similarity_boost", "style"):
        if k in s and not 0 <= float(s[k]) <= 1:
            out.append(P("critical", "settings", f"{k} must be between 0 and 1"))
    if doc["output_format"]["requested"] not in FORMATS:
        out.append(P("critical", "format", f"{doc['output_format']['requested']} is not verified for Starter"))
    return out


SFX_SCHEMA = {"type": "object", "additionalProperties": False,
              "required": ["schema_version", "package", "cue", "purpose", "sound", "prompt", "duration_seconds", "prompt_influence", "loop", "model_id", "output_format", "perspective", "acceptance", "library_search", "manual_steps", "file_name"],
              "properties": {"schema_version": {"const": "1"}, "package": {"type": "string"}, "cue": {"type": "string"}, "purpose": {"type": "string", "minLength": 15}, "sound": {"type": "string"},
                             "prompt": {"type": "string", "minLength": 10, "maxLength": 450}, "duration_seconds": {"type": "number", "minimum": .5, "maximum": 30}, "prompt_influence": {"type": "number", "minimum": 0, "maximum": 1},
                             "loop": {"type": "boolean"}, "model_id": {"enum": ["eleven_text_to_sound_v3"]}, "output_format": {"enum": ["mp3_44100_128", "wav_48000"]}, "perspective": {"type": "string"},
                             "acceptance": {"type": "array", "minItems": 1, "items": {"type": "string"}}, "avoid": {"type": "string"},
                             "library_search": {"type": "object", "required": ["terms", "hits", "why_not"], "properties": {"terms": {"type": "array", "minItems": 1}, "hits": {"type": "integer"}, "why_not": {"type": "string", "minLength": 8}}},
                             "manual_steps": {"type": "array", "minItems": 3}, "file_name": {"type": "string"}, "placement": {"type": "string"}, "how_takes_count": {"type": "string"}, "changed_vs_prior": {"type": ["string", "null"]}}}
MUSICAL = r"\b(music|melody|song|chord|orchestra\w*|violin|piano|soundtrack|score|beat|bpm|tune)\b"


def lint_sfx(doc: dict) -> list[dict]:
    out = [P("critical", "/".join(map(str, e.path)) or "package", e.message[:200]) for e in jsonschema.Draft202012Validator(SFX_SCHEMA).iter_errors(doc)]
    if out:
        return out
    if re.search(MUSICAL, re.sub(r"\b(no|without)\b[^,.;:]*", " ", doc["prompt"], flags=re.I), re.I):
        out.append(P("critical", "prompt", "asks for music; ElevenLabs music may not be used in a film"))
    if len(re.findall(r"\b(and then|then|followed by|before|after)\b", doc["prompt"], re.I)) > 1:
        out.append(P("moderate", "prompt", "several events in one sound are hard to time; split them"))
    if re.search(r"\b(scary|creepy|horror|eerie|spooky)\b", doc["prompt"], re.I) and not re.search(r"\b(metal|wood|fabric|air|glass|concrete|plaster|wire|coil|hinge|footstep|breath|insulation|cardboard|drywall)\w*\b", doc["prompt"], re.I):
        out.append(P("major", "prompt", "names a mood but no physical source"))
    if not re.search(r"\b(no |without )", doc["prompt"], re.I):
        out.append(P("minor", "prompt", "does not say what must be absent (music, voices, reverb)"))
    return out


def lint_video(doc: dict) -> list[dict]:
    out = _schema("video", doc)
    if out:
        return out
    b, pr = doc["blocks"], doc["prompt"]
    low = pr.lower()
    at = {a["role"]: a for a in doc["attachments"]}
    if "first_frame" not in at:
        out.append(P("critical", "attachments", "no first frame"))
    if "last_frame" not in at:
        out.append(P("major", "attachments", "no last frame (only you can waive it)"))
    want = (1920, 1080) if doc["aspect_ratio"] == "16:9" else (1080, 1920)
    for a in doc["attachments"]:
        if a["role"].endswith("frame") and (a["width"], a["height"]) != want:
            out.append(P("critical", a["file"], f"must be exactly {want[0]}x{want[1]}"))
    if not re.search(r"first (attached )?image.*first frame", low) or ("last_frame" in at and not re.search(r"(second|last) (attached )?image.*last frame", low)):
        out.append(P("critical", "prompt", "does not say plainly which image is the first and which the last frame"))
    if re.search(r"<(first|last)_frame>|<image_ref", low):
        out.append(P("critical", "prompt", "role tags are deprecated; use plain sentences"))
    locked = "locked" in b["camera"].lower() or "does not move" in b["camera"].lower()
    moves = re.findall(r"\b(pan|tilt|zoom|dolly|push in|push-in|track(?:s|ing)?|orbit|crane|handheld)\b", re.sub(r"\b(no|not|never|without)\b[^.;]*", "", pr, flags=re.I), re.I)
    if locked and moves:
        out.append(P("critical", "camera", f"says locked, but the prompt also asks for: {', '.join(sorted({m.lower() for m in moves}))}"))
    if re.search(r"\b(cut to|then we see|next shot|montage)\b", low):
        out.append(P("critical", "prompt", "asks for more than one shot"))
    if re.search(r"\b(text|title|caption|subtitle|sign) (that )?(says|reads|appears)\b", low):
        out.append(P("critical", "prompt", "asks for readable text; text is added by the engine"))
    if "no " not in low:
        out.append(P("major", "prompt", "does not say what must not happen"))
    acts = [s for s in re.split(r"[;.]| and then | then ", b["subject_motion"]) if s.strip()]
    if len(acts) > 2:
        out.append(P("major", "motion", "more than two actions in one clip"))
    if re.search(r"(?<!no )(?<!without )\b(dialogue|says|speaks|talking)\b", low) and "no dialogue" not in low and "no speech" not in low:
        out.append(P("major", "audio", "speech is not excluded"))
    f = doc.get("feasibility")
    if f:
        if f["moving_entities"] > 2:
            out.append(P("major", "feasibility", "more than two moving things"))
        if f["max_travel_frac_per_s"] > .25:
            out.append(P("critical", "feasibility", "something must cross a quarter of the frame every second"))
        if f["new_content_frac"] > .45:
            out.append(P("critical", "feasibility", "nearly half of the picture must be invented"))
    if not any(a["mandatory"] for a in doc["acceptance"]):
        out.append(P("major", "acceptance", "nothing is mandatory"))
    return out


def blocking(problems):
    return [p for p in problems if p["severity"] in ("critical", "major")]


# ---------------------------------------------------------------- registration
GATE = {"voice": ("G9_voice_package", "voice_package"), "sfx": ("G9_sfx_package", "sfx_package"), "video": ("G5_package", "video_package")}


def register(db: DB, kind, film, doc, *, manual_profile, api_profile=None, frame_pair=None, cue=None, maker="", changed=None) -> str:
    """Stores a package revision. The text is fixed from here on."""
    h = sha(doc)
    if kind == "voice":
        prev = db.one("select * from voice_package where film_id=? and series=? order by rev desc limit 1", film, doc["package"])
        table, extra = "voice_package", {"film_id": film, "series": doc["package"], "manual_profile_id": manual_profile, "api_profile_id": api_profile}
    elif kind == "sfx":
        prev = db.one("select * from sfx_package where cue_id=? order by rev desc limit 1", cue)
        table, extra = "sfx_package", {"film_id": film, "cue_id": cue, "manual_profile_id": manual_profile, "api_profile_id": api_profile}
    else:
        prev = db.one("select * from video_package where frame_pair_id=? order by rev desc limit 1", frame_pair)
        table, extra = "video_package", None
    if prev and prev["content_hash"] == h:
        return prev["id"]
    pid, rev = new_id({"voice": "vp", "sfx": "xp", "video": "gp"}[kind]), (prev["rev"] + 1 if prev else 1)
    ch = changed or doc.get("changed_vs_prior")
    with db.tx():
        if kind == "video":
            db.insert("video_package", id=pid, frame_pair_id=frame_pair, rev=rev, profile_id=manual_profile, prompt=doc["prompt"], settings_json={"aspect": doc["aspect_ratio"], "duration": doc["duration_s"]},
                      attachments_json=doc["attachments"], acceptance_json=doc["acceptance"], content_hash=h, changed_vs_prior_json=dumps({"change": ch}) if rev > 1 and ch else None)
            db.run("insert into meta(key,value) values(?,?) on conflict(key) do update set value=excluded.value", f"video_doc:{pid}", dumps(doc))
        else:
            db.insert(table, id=pid, rev=rev, doc_json=doc, content_hash=h, changed_vs_prior_json=dumps({"change": ch}) if rev > 1 and ch else None, **extra)
        if kind == "voice":
            for i, l in enumerate(doc["lines"]):
                db.insert("voice_package_line", package_id=pid, line_id=l["line_id"], ordinal=i)
        flow.set_hash(db, f"{kind}_package", pid, h)
        db.insert("asset_binding", asset_id=_marker(db), target_type=f"{kind}_package", target_id=pid, role="candidate", bound_by=maker or "unknown")
        db.event(maker or "system", "package.registered", f"{kind}_package", pid, {"rev": rev})
    flow.changed(db, film, "package.registered")
    return pid


def _marker(db: DB) -> str:
    """asset_binding needs an asset; packages are documents, so they share one marker asset that carries no media."""
    r = db.one("select id from asset where relpath='marker'")
    return r["id"] if r else db.insert("asset", id=new_id("as"), sha256=sha("marker"), kind="document", origin="authored", storage_class="cache", relpath="marker", bytes=0, created_at=now())


def doc_of(db: DB, kind, pid) -> dict:
    if kind == "video":
        return json.loads(db.val("select value from meta where key=?", f"video_doc:{pid}"))
    return json.loads(db.val(f"select doc_json from {kind}_package where id=?", pid))


def voice_owner(db: DB, speaker_entity):
    """The canon voice a speaker uses: narration may be the same person as a character."""
    s = db.val("select spec_json from canon_revision where entity_id=? order by rev desc limit 1", speaker_entity)
    return json.loads(s or "{}").get("same_person_as", speaker_entity)


def cast_voices(db: DB, pid):
    out = {}
    for r in db.q("select distinct l.speaker_id from voice_package_line v join script_line l on l.id=v.line_id where v.package_id=?", pid):
        own = voice_owner(db, r["speaker_id"])
        b = db.one("select b.* from voice_binding b join canon_revision c on c.id=b.revision_id where c.entity_id=? and b.status='approved' order by b.rowid desc limit 1", own)
        out[own] = {"name": json.loads(b["settings_json"]).get("name"), "category": b["category"], "sample": b["approved_sample_asset_id"], "provider_voice_id": b["provider_voice_id"]} if b else None
    return out


def is_casting(doc):
    return bool(doc.get("casting")) or all("CAST-" in l["line_id"] for l in doc["lines"])


def reviews(db: DB, kind, pid) -> list[dict]:
    return db.q("""select r.id,r.verdict,r.body_json,r.created_at,t.role,t.state from task t join report r on r.task_id=t.id
        where t.kind='review' and t.target_type=? and t.target_id=? and t.superseded_by is null order by r.created_at""", f"{kind}_package", pid)


def approve(db: DB, kind, pid, waive_reason=""):
    """The user's approval. Refused while the linter or an independent review blocks it; a review (never the linter) may be waived with a reason."""
    doc = doc_of(db, kind, pid)
    gate, ttype = GATE[kind]
    if kind == "voice":
        film = db.val("select film_id from voice_package where id=?", pid)
        prob = lint_voice(doc, {r["id"]: r["text"] for r in db.q("select id,text from script_line where film_id=?", film)})
    else:
        prob = {"sfx": lint_sfx, "video": lint_video}[kind](doc)
    if blocking(prob):
        raise RuleRefused("the package has problems: " + "; ".join(p["text"] for p in blocking(prob)[:3]))
    if kind == "voice" and not is_casting(doc) and not all(cast_voices(db, pid).values()):
        raise RuleRefused("choose the voice first: accept a take in the casting package")
    rv = reviews(db, kind, pid)
    waive = len(waive_reason.strip()) >= 10
    if waive and (not rv or rv[-1]["verdict"] != "APPROVED"):
        from ..agents import service
        if not service.past_reports(db, kind, pid):
            raise RuleRefused("no specialist has looked at any revision yet; let one review it before you decide against it")
    elif not rv:
        raise RuleRefused("an independent specialist has not reviewed this package yet")
    elif rv[-1]["verdict"] != "APPROVED":
        raise RuleRefused(f"the independent review says {rv[-1]['verdict'].replace('_', ' ').lower()}")
    deps = [(f"{kind}_package", pid)]
    if kind == "video":                    # a package is only as current as its frames
        from .motion import frame_slots
        fp = db.one("select f.id,ss.shot_id from video_package v join frame_pair f on f.id=v.frame_pair_id join shot_spec ss on ss.id=f.shot_spec_id where v.id=?", pid)
        if not flow.live(db, "G4_frame_pair", fp["id"]):
            raise RuleRefused("the frames have changed since this package was made")
        deps += [("frame_pair", fp["id"])] + frame_slots(db, fp["shot_id"])
    aid = flow.approve(db, gate, ttype, pid, deps, "user", {"reviews": [r["id"] for r in rv]}, *(("waived", waive_reason.strip()) if waive and (not rv or rv[-1]["verdict"] != "APPROVED") else ("approved", None)))
    film = db.val(f"select film_id from {kind}_package where id=?", pid) if kind != "video" else db.val(
        "select s.film_id from video_package v join frame_pair f on f.id=v.frame_pair_id join shot_spec ss on ss.id=f.shot_spec_id join shot sh on sh.id=ss.shot_id join scene s on s.id=sh.scene_id where v.id=?", pid)
    if not db.one("select 1 from manual_task where kind=? and package_id=?", kind, pid):
        db.insert("manual_task", id=new_id("mt"), film_id=film, kind=kind, package_id=pid, opened_at=now())
    flow.changed(db, film, "package.approved")
    return aid
