"""Any script becomes citable pieces. Rules only: nothing here decides meaning, and nothing here knows a story.

Three layouts are recognised: screenplay (Fountain, Final Draft, or the same typed in Word or plain text), labelled
audio-visual script ("Scene 4", "Visual:", "Narration:", "NAME: words"), and prose. The user sees the result before it is fixed.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .db import DB, RuleRefused, data_dir, new_id, now, sha

FORMATS = {".docx", ".txt", ".md", ".fountain", ".fdx", ".pdf"}
HEAD = re.compile(r"^\s*(?:(\d+)[A-Z]?[.)]?\s+)?((?:INT|EXT|EST|I/E|INT\.?/EXT|EXT\.?/INT)[.\s].+?)(?:\s+(\d+)[A-Z]?\.?)?\s*$", re.I)
AV_HEAD = re.compile(r"(SCENE|Scene) (\d+)\b")
CUE = re.compile(r"^[A-Z][A-Z0-9 .'\-]{0,38}(?:\s*\([^)]{1,30}\))*\s*\^?$")
TRANS = re.compile(r"^(FADE (IN|OUT|TO)|CUT TO|SMASH CUT|DISSOLVE|MATCH CUT|.+ TO:)[ .:A-Z]*$")
LABEL = re.compile(r"[A-Z][A-Z. ]+(\([^)]*\))?: ")


class ScriptProblem(Exception):
    pass


def read(path: Path) -> list[tuple[str, str]]:
    """(text, layout hint) per paragraph. The hint comes from Word styles or Final Draft types, if there are any."""
    ext = path.suffix.lower()
    if ext not in FORMATS:
        raise ScriptProblem(f"{ext or 'this file type'} is not a script format the studio reads ({', '.join(sorted(FORMATS))})")
    if ext == ".docx":
        import docx
        try:
            return [(p.text, p.style.name if p.style is not None else "") for p in docx.Document(str(path)).paragraphs]
        except Exception as e:
            raise ScriptProblem(f"the Word file cannot be read ({type(e).__name__})") from None
    if ext == ".fdx":
        import xml.etree.ElementTree as ET
        raw = path.read_bytes()
        if b"<!ENTITY" in raw:
            raise ScriptProblem("the file contains definitions the studio does not accept")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            raise ScriptProblem(f"the Final Draft file cannot be read ({e})") from None
        return [("".join(t.text or "" for t in p.iter("Text")), p.get("Type", "")) for p in root.iter("Paragraph")]
    if ext == ".pdf":
        raise ScriptProblem("save the PDF as Word or text first: a PDF has lost the structure of a script")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return [(x, "") for x in path.read_text(encoding=enc).splitlines()]
        except UnicodeDecodeError:
            continue
    raise ScriptProblem("the text encoding is not readable")


def detect(paras) -> str:
    t = [x.strip() for x, _ in paras if x.strip()]
    if any(h in ("Scene Heading", "Character", "Dialogue") for _, h in paras):
        return "screenplay"
    heads, av = sum(bool(HEAD.match(x)) for x in t), sum(bool(AV_HEAD.match(x)) for x in t)
    labels = sum(bool(re.match(r"(Visual|Sound|Narration)\b", x)) for x in t)
    if av and labels >= av:
        return "labelled"
    if heads:
        return "screenplay"
    return "labelled" if av and labels else "prose"


def labelled(paras):
    mode = None
    for raw, _ in paras:
        s = raw.strip()
        if not s:
            continue
        if AV_HEAD.match(s):
            kind, mode = "heading", None
        elif re.match(r"Visual\b", s):
            kind, mode = "action", None
        elif re.match(r"Sound\b", s):
            kind, mode = "sound", None
        elif re.match(r"Narration\b", s):
            kind, mode = "narration", "narration"
        elif LABEL.match(s):
            kind, mode = "dialogue", None
        elif re.match(r"(Clue|Note|Act|Text)\b", s):
            kind, mode = "note", None
        else:
            kind = mode or "other"
        yield kind, s


def screenplay(paras):
    """Character cue, parenthetical and speech become one piece, so that a line always carries its speaker."""
    body, i, styled = [(x.rstrip(), h) for x, h in paras], 0, any(h in ("Character", "Dialogue") for _, h in paras)
    while i < len(body) and re.match(r"^(Title|Credit|Author|Source|Draft date|Contact|Copyright)\s*:", body[i][0], re.I):
        yield "note", body[i][0].strip()
        i += 1
    while i < len(body):
        s, h = body[i][0].strip(), body[i][1]
        i += 1
        if not s:
            continue
        if s.startswith(("/*", "[[")) or re.match(r"^[=#]", s):
            yield "note", s
        elif h == "Scene Heading" or HEAD.match(s) or (s.startswith(".") and not s.startswith("..")):
            yield "heading", s.lstrip(".")
        elif h == "Transition" or TRANS.match(s) or (s.startswith(">") and not s.endswith("<")):
            yield "transition", s.lstrip("> ")
        elif h == "Character" or (not styled and (s.startswith("@") or CUE.match(s)) and _speech_follows(body, i)):
            name, words = s.lstrip("@").rstrip("^ ").strip(), []
            paren = []
            while i < len(body):
                x, hx = body[i][0].strip(), body[i][1]
                if not x or (hx and hx not in ("Dialogue", "Parenthetical") and styled) or HEAD.match(x):
                    break
                if not styled and CUE.match(x) and words and _speech_follows(body, i + 1):
                    break
                (paren if x.startswith("(") and x.endswith(")") and not words else words).append(x)
                i += 1
            kind = "narration" if re.match(r"NARRAT", name) else "dialogue"
            yield kind, f"{name}{' ' + ' '.join(paren) if paren else ''}: {' '.join(words)}"
        elif re.match(r"^(SOUND|SFX|SOUND EFFECT)S?\s*[:\-]", s):
            yield "sound", s
        else:
            yield "action", s.lstrip("!")


def _speech_follows(body, i):
    """A cue is followed directly, or after no more than its own paragraph gap, by speech that is not another capital line."""
    if i >= len(body):
        return False
    x = body[i][0].strip()
    return bool(x) and not HEAD.match(x) and not (x.isupper() and len(x) > 3 and not x.startswith("("))


def prose(paras):
    for raw, h in paras:
        s = raw.strip()
        if s:
            yield ("heading" if re.match(r"^(#+ |chapter \w+|part \w+)", s, re.I) or h.startswith("Heading") else "other"), s


LAYOUTS = {"labelled": labelled, "screenplay": screenplay, "prose": prose}


def parse(path: Path, layout: str | None = None) -> dict:
    """What the file contains, before anything is stored."""
    paras = read(path)
    if sum(len(x) for x, _ in paras) < 20:
        raise ScriptProblem("the file contains almost no text")
    lay = layout or detect(paras)
    if lay not in LAYOUTS:
        raise ScriptProblem("unknown layout")
    sp = [{"kind": k, "text": s} for k, s in LAYOUTS[lay](paras)]
    sc = scenes(sp, lay)
    unsure = []
    if not sc:
        unsure.append("no scene headings were found; the whole text becomes one scene")
    n_other = sum(s["kind"] == "other" for s in sp)
    if lay != "prose" and n_other > len(sp) * .2:
        unsure.append(f"{n_other} of {len(sp)} pieces could not be classified")
    if lay == "prose":
        unsure.append("this reads as prose, not as a script: dialogue and narration are not separated")
    odd = [s["text"][:60] for s in sp if s["kind"] == "dialogue" and len(s["text"].split(":", 1)[0]) > 40]
    if odd:
        unsure.append(f"{len(odd)} speaker names look unusual, for example: {odd[0]}")
    return {"layout": lay, "spans": sp, "scenes": sc, "speakers": sorted({speaker(s) for s in sp if speaker(s)}), "unsure": unsure,
            "words": sum(len(s["text"].split()) for s in sp)}


def scenes(sp, layout) -> list[dict]:
    out = []
    for i, s in enumerate(sp):
        if s["kind"] != "heading":
            continue
        if layout == "labelled":
            m = AV_HEAD.match(s["text"])
            num = int(m.group(2)) if m else None
        else:
            m = HEAD.match(s["text"])
            num = int(m.group(1) or m.group(3)) if m and (m.group(1) or m.group(3)) else None
        if out:
            out[-1]["to"] = i
        out.append({"number": num, "heading": s["text"], "from": i, "to": len(sp)})
    used, nxt = {s["number"] for s in out if s["number"]}, 1
    seen = set()
    for s in out:                                   # no number in the script, or the same number twice: the next free one
        if not s["number"] or s["number"] in seen:
            while nxt in used or nxt in seen:
                nxt += 1
            s["number"] = nxt
        seen.add(s["number"])
    return out or ([{"number": 1, "heading": "Whole text", "from": 0, "to": len(sp)}] if sp else [])


def speaker(span) -> str | None:
    if span["kind"] not in ("dialogue", "narration") or ":" not in span["text"]:
        return None
    name = re.sub(r"\s*\(.*?\)", "", span["text"].split(":", 1)[0]).strip()
    return name if name and name.isupper() and len(name) <= 40 else None


def words(span) -> str:
    """What is said, without the speaker."""
    if span["kind"] in ("dialogue", "narration") and ":" in span["text"]:
        return span["text"].split(":", 1)[1].strip()
    return span["text"].strip()


def keep_file(path: Path, film) -> str:
    """The script itself is copied into the studio, so it is backed up with everything else."""
    h = sha(path.read_bytes())
    rel = f"sources/{re.sub(r'[^A-Za-z0-9_-]', '_', film)}/{h[:16]}{path.suffix.lower()}"
    dst = data_dir() / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copyfile(path, dst)
    return rel


def ingest(db: DB, film, path: Path, *, layout=None, by="user", name=None) -> str:
    """Fixes a script revision. A changed script is a new revision; the earlier one stays readable."""
    h = sha(path.read_bytes())
    old = db.one("select id from source_doc where film_id=? and sha256=?", film, h)
    if old:
        return old["id"]
    p = parse(path, layout)
    if not p["spans"]:
        raise ScriptProblem("nothing was found in the file")
    rev = 1 + (db.val("select max(revision) from source_doc where film_id=? and kind='script'", film) or 0)
    did, rel = new_id("sd"), keep_file(path, film)
    with db.tx():
        db.insert("source_doc", id=did, film_id=film, kind="script", revision=rev, sha256=h, relpath=rel, precedence=10, locked_at=now(), locked_by=by, original_name=name or path.name, format=p["layout"])
        for i, s in enumerate(p["spans"]):
            db.insert("source_span", id=f"{did}_{i:04d}", doc_id=did, ordinal=i, kind=s["kind"], text=s["text"], text_hash=sha(s["text"]))
        from . import flow
        flow.set_hash(db, "script", film, h)
        db.event("system" if by == "system" else "user", "script.ingested", "source_doc", did, {"file": name or path.name, "sha": h, "layout": p["layout"], "revision": rev})
    return did


def current(db: DB, film) -> dict | None:
    return db.one("select * from source_doc where film_id=? and kind='script' order by revision desc limit 1", film)


def spans(db: DB, film, scene=None) -> list[dict]:
    d = current(db, film)
    if not d:
        return []
    if scene:
        s = db.one("select span_from,span_to from scene where id=?", scene)
        if s and s["span_from"] is not None:
            return db.q("select id,ordinal,kind,text from source_span where doc_id=? and ordinal>=? and ordinal<? order by ordinal", d["id"], s["span_from"], s["span_to"])
    return db.q("select id,ordinal,kind,text from source_span where doc_id=? order by ordinal", d["id"])


def make_scenes(db: DB, film, only=None):
    """Scene rows from the headings. Existing scenes keep their identity; a new script revision moves their range."""
    d = current(db, film)
    if not d:
        raise RuleRefused("import a script first")
    sp = spans(db, film)
    for s in scenes([dict(x) for x in sp], d["format"] or "labelled"):
        if only and s["number"] not in only:
            continue
        r = db.one("select id from scene where film_id=? and number=?", film, s["number"])
        if r:
            db.run("update scene set span_from=?,span_to=? where id=?", s["from"], s["to"], r["id"])
        else:
            db.insert("scene", id=f"{film}:S{s['number']:02d}", film_id=film, number=s["number"], slug=s["heading"][:120], span_from=s["from"], span_to=s["to"])
