"""From an approved film edit to a checked YouTube package: master, Shorts, captions, thumbnail candidates, text, credits, rights, disclosure. Nothing is uploaded."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

import cv2
import numpy as np

from ..engine import render, sound
from . import assets, backup, cut, edit, films, flow, gates
from .db import DB, RuleRefused, data_dir, dumps, new_id, now, sha

ITEMS = [("film_approved", "The film edit is approved by you"), ("material", "No stand-ins are left"), ("rights", "Every picture and sound may be used"), ("clips", "Every generated clip passed its gate"), ("voices", "Voices are cast and lines approved"), ("technical", "The delivered file passed every check"), ("captions", "Captions were made and looked at"),
         ("text", "Title, description and tags are chosen"), ("thumbnail", "A thumbnail is chosen"), ("disclosure", "The AI disclosure is written"), ("credits", "Required credits are in the description"), ("backup", "A verified copy exists on another drive"), ("watched", "You watched the whole master with sound")]
MANUAL = {"captions", "thumbnail", "watched"}


def scope_for(db: DB, film):
    return "film" if edit.latest(db, film, "film") else db.val("select scope from cut_rev where film_id=? order by rowid desc limit 1", film)


# ------------------------------------------------------------ captions
def cues(db: DB, tl) -> list[tuple[float, float, str]]:
    """One caption per phrase, timed from the recognised words where they exist, otherwise from the length of the take."""
    out, fps = [], tl["fps"][0] / tl["fps"][1]
    end = tl["frames"] / fps
    for a in sorted((a for a in tl["audio"] if a.get("line")), key=lambda a: a["at_sample"]):
        text = db.val("select text from script_line where id=?", a["line"]) or ""
        t0 = a["at_sample"] / 48000
        dur = min(edit.seconds_of(db, a["asset"]), a.get("dur_samples", 10 ** 12) / 48000)
        tk = db.one("select word_timing_json from audio_take where asset_id=?", a["asset"])
        words = json.loads((tk or {}).get("word_timing_json") or "[]") or []
        for part, f0, f1 in _phrases(text):
            if words and len(words) >= len(text.split()) * .8:
                i, j = int(f0 * (len(words) - 1)), max(int(f0 * (len(words) - 1)), int(np.ceil(f1 * len(words))) - 1)
                s, e = t0 + words[i]["a"], t0 + words[min(j, len(words) - 1)]["b"] + .15
            else:
                s, e = t0 + dur * f0, t0 + dur * f1
            out.append((max(0, s), min(end, max(e, s + .7)), part))
    out.sort()
    return [(s, min(e, out[i + 1][0] - .04) if i + 1 < len(out) and e > out[i + 1][0] > s + .3 else e, t) for i, (s, e, t) in enumerate(out)]


def _phrases(text, most=74):
    """Pieces of at most two short lines, cut at punctuation where possible. Positions as fractions of the words."""
    w, out, cur, start = text.split(), [], [], 0
    for i, x in enumerate(w):
        cur.append(x)
        n = len(" ".join(cur))
        if n > most - 12 and (x[-1:] in ".,;:!?" or n > most) or (x[-1:] in ".!?" and n > 26) or i == len(w) - 1:
            out.append((" ".join(cur), start / len(w), (i + 1) / len(w)))
            cur, start = [], i + 1
    return out


def _stamp(t, sep=","):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}{sep}{ms % 1000:03d}"


def srt(db: DB, tl) -> str:
    return "\n".join(f"{i}\n{_stamp(s)} --> {_stamp(e)}\n{chr(10).join(textwrap.wrap(t, 42))}\n" for i, (s, e, t) in enumerate(cues(db, tl), 1))


def vtt(db: DB, tl) -> str:
    return "WEBVTT\n\n" + "\n".join(f"{_stamp(s, '.')} --> {_stamp(e, '.')}\n{chr(10).join(textwrap.wrap(t, 42))}\n" for s, e, t in cues(db, tl))


# ------------------------------------------------------------ credits and rights
def credits(db: DB, tl) -> list[str]:
    out = []
    for a in sorted(cut.used_assets(tl)):
        for r in db.q("select r.source,r.version,r.url,r.attribution_required,al.attribution_text from root_rights r left join asset_licence al on al.asset_id=r.root_id where r.asset_id=?", a):
            if r["attribution_required"] or r["attribution_text"]:
                out.append(r["attribution_text"] or f"{r['source']} {r['version'] or ''} {r['url'] or ''}".strip())
    return list(dict.fromkeys(out))


def made_with(db: DB, tl) -> dict:
    """What was generated, for the disclosure. Facts from provenance, not from memory."""
    n = {"generated pictures": 0, "generated clips": 0, "synthetic voices": 0, "generated sounds": 0}
    for a in cut.used_assets(tl):
        o = db.one("select origin,kind,provider,provenance_json from asset where id=?", a)
        if not o:
            continue
        if o["origin"] == "api_image" or "generated" in (o["provenance_json"] or ""):
            n["generated pictures"] += 1
        if o["origin"] == "manual_video":
            n["generated clips"] += 1
        if o["origin"] in ("api_audio", "manual_audio"):
            n["synthetic voices" if db.one("select 1 from audio_take where asset_id=?", a) else "generated sounds"] += 1
    return n


def label_of(db: DB, aid):
    p = json.loads(db.val("select provenance_json from asset where id=?", aid) or "{}")
    return str(p.get("synth") or p.get("title") or p.get("temp_voice") or p.get("shot") or p.get("previs_key") or p.get("blocking") or aid)[:60]


# ------------------------------------------------------------ the checklist
def checklist(db: DB, film) -> list[dict]:
    scope = scope_for(db, film)
    cur = edit.latest(db, film, scope) if scope else None
    if not cur:
        return [{"item": i, "title": t, "state": "open", "evidence": {"why": "there is no edit yet"}} for i, t in ITEMS]
    try:
        tl, status, prob = edit.resolve(db, film, scope, cur["doc"])
    except RuleRefused as e:
        return [{"item": i, "title": t, "state": "open", "evidence": {"why": str(e)}} for i, t in ITEMS]
    auto = {}
    auto["film_approved"] = (cur["status"] == "approved" and flow.live(db, "G10_film" if scope == "film" else "G8_scene", cur["id"]), {"scope": scope, "revision": cur["rev"], "status": cur["status"]})
    stand = {k: v for k, v in status.items() if v not in gates.FINISHED}
    auto["material"] = (not stand, {"stand_ins": len(stand), "first": list(stand.items())[:5]})
    bad = cut.final_blockers(db, film, tl)
    auto["rights"] = (not bad, {"blocked": [f"{label_of(db, b['asset'])}: {b['why']}" for b in bad[:8]]})
    clips = {c["asset"] for t in tl["video"] for c in t["clips"] if c.get("asset") and db.val("select origin from asset where id=?", c["asset"]) == "manual_video"}
    open_clips = [a for a in clips if not flow.live(db, "G7_clip", a)]
    auto["clips"] = (not open_clips, {"clips": len(clips), "without_gate": open_clips})
    temp = [k for k, v in status.items() if k.startswith(("voice", "s")) and v in ("temporary computer voice", "not made yet")]
    auto["voices"] = (not temp, {"temporary": temp[:6]})
    m = db.one("select r.* from render r join timeline_rev t on t.id=r.timeline_rev_id where t.cut_rev_id=? and r.kind='master' order by r.rowid desc limit 1", cur["id"])
    q = json.loads(m["qa_json"]) if m else {}
    auto["technical"] = (q.get("outcome") == "pass", {"render": m["id"] if m else None, "outcome": q.get("outcome"), "not_passed": [i for i in q.get("items", []) if i[1] != "pass"]})
    txt = films.setting(db, film, "release_text")
    auto["text"] = (bool(txt and txt.get("chosen_title")), {"title": (txt or {}).get("chosen_title")})
    auto["disclosure"] = (bool(txt and txt.get("disclosure")), {"made_with": made_with(db, tl), "text": (txt or {}).get("disclosure")})
    cr = credits(db, tl)
    inside = [c for c in cr if txt and c in (txt.get("description", "") + " ".join(txt.get("credits", [])))]
    auto["credits"] = (len(inside) == len(cr), {"required": cr, "missing": [c for c in cr if c not in inside]})
    b = backup.state(db)
    auto["backup"] = (b["newest_hours"] is not None and b["newest_hours"] < 24 and b["separate_drives"] >= 1, {"newest_hours": b["newest_hours"], "drives": b["separate_drives"]})
    out = []
    for i, t in ITEMS:
        row = db.one("select * from release_item where film_id=? and item=?", film, i)
        if i in MANUAL:
            ev = json.loads(row["evidence_json"] or "{}") if row else {}
            fresh = row and ev.get("cut") == cur["id"]
            state = row["state"] if fresh or (row and row["state"] == "waived") else "open"
            out.append({"item": i, "title": t, "state": state, "evidence": ev, "manual": True})
        else:
            ok, ev = auto[i]
            state = "pass" if ok else row["state"] if row and row["state"] == "waived" else "fail" if i in ("rights", "technical") else "open"
            out.append({"item": i, "title": t, "state": state, "evidence": ev, "manual": False, "why_waived": json.loads(row["evidence_json"] or "{}").get("reason") if row and state == "waived" else None})
    return out


def decide(db: DB, film, item, state, reason=""):
    if item not in dict(ITEMS) or state not in ("pass", "open", "waived"):
        raise RuleRefused("unknown item")
    if state == "pass" and item not in MANUAL:
        raise RuleRefused("this item is checked by the studio, not by a click")
    if state == "waived" and (len(reason.strip()) < 10 or item in ("rights", "technical", "watched", "film_approved")):
        raise RuleRefused("rights, technical checks, the film approval and watching it cannot be waived" if len(reason.strip()) >= 10 else "write why")
    scope = scope_for(db, film)
    cur = edit.latest(db, film, scope) if scope else None
    if item == "watched" and not (cur and db.one("select 1 from render r join timeline_rev t on t.id=r.timeline_rev_id where t.cut_rev_id=? and r.kind='master'", cur["id"])):
        raise RuleRefused("there is no master of this edit to watch")
    ev = {"cut": cur["id"] if cur else None, "reason": reason.strip() or None, "at": now()}
    db.run("insert into release_item(film_id,item,state,evidence_json,decided_by) values(?,?,?,?,'user') on conflict(film_id,item) do update set state=excluded.state,evidence_json=excluded.evidence_json,decided_by='user'", film, item, state, dumps(ev))
    db.event("user", "release.item", "film", film, {"item": item, "state": state, "reason": reason})


def state(db: DB, film):
    scope = scope_for(db, film)
    items = checklist(db, film)
    tl = None
    if scope:
        try:
            tl, _, _ = edit.resolve(db, film, scope)
        except RuleRefused:
            pass
    return {"scope": scope, "items": items, "ready": all(i["state"] in ("pass", "waived") for i in items), "text": films.setting(db, film, "release_text"), "packages": [{**p, "doc": json.loads(p.pop("doc_json"))} for p in db.q("select * from release_package where film_id=? order by rowid desc limit 12", film)],
            "credits": credits(db, tl) if tl else [], "made_with": made_with(db, tl) if tl else {}, "seconds": tl["frames"] * tl["fps"][1] / tl["fps"][0] if tl else None, "captions": len(cues(db, tl)) if tl else 0, "drafts": [d for d in db.q("select id,rev,status,made_by,created_at from work_doc where film_id=? and kind='release' order by rev desc limit 5", film)]}


# ------------------------------------------------------------ building the package
def timecode(frame, fps):
    s = int(frame / fps)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def description(db: DB, film, tl, txt) -> str:
    fps = tl["fps"][0] / tl["fps"][1]
    parts = [txt["description"].strip()]
    if txt.get("chapters"):
        parts.append("\n".join(f"{timecode(c['frame'], fps)} {c['title']}" for c in txt["chapters"]))
    cr = list(dict.fromkeys((txt.get("credits") or []) + credits(db, tl)))
    if cr:
        parts.append("Credits\n" + "\n".join(cr))
    if txt.get("disclosure"):
        parts.append(txt["disclosure"]["note"])
    return "\n\n".join(parts)


def vertical(tl, a, b, x=0.0, style="crop"):
    """A 9:16 version of part of the film: cropped around x (-1 left ... 1 right), or the whole width with black above and below."""
    t = copy.deepcopy(tl)
    w, h = tl["size"]
    t["size"], t["frames"] = [1080, 1920], b - a
    grow = (1920 / 1080) / (h / w) if style == "crop" else 1.0
    spf = 48000 * tl["fps"][1] / tl["fps"][0]
    for tr in t["video"]:
        keep = []
        for c in tr["clips"]:
            s, e = max(c["at"], a), min(c["at"] + c["dur"], b)
            if e <= s:
                continue
            cutoff = s - c["at"]
            if cutoff and c["kind"] == "video" and not isinstance(c.get("speed"), list):
                c["src_in"] = c.get("src_in", 0) + int(round(cutoff * float(c.get("speed") or 1)))
            shift = lambda k: [{**p, "f": p["f"] - cutoff} for p in k] if isinstance(k, list) and k and isinstance(k[0], dict) else k
            tr_ = {k: shift(v) for k, v in c.get("transform", {}).items()}
            mul = lambda v, m: [{**p, "v": p["v"] * m} for p in v] if isinstance(v, list) else v * m
            tr_["scale"] = mul(tr_.get("scale", 1.0), grow)
            if style == "crop" and x:
                tr_["pos"] = [-x * (grow - 1) / 2, 0] if not isinstance(tr_.get("pos"), list) or not tr_["pos"] or not isinstance(tr_["pos"][0], dict) else tr_["pos"]
            c["transform"] = tr_
            c["effects"] = [{**f, "params": {k: shift(v) for k, v in f["params"].items()}} for f in c.get("effects", [])]
            if cutoff:
                c.pop("transition_in", None)
            c["at"], c["dur"] = s - a, e - s
            keep.append(c)
        tr["clips"] = keep
    t["video"] = [tr for tr in t["video"] if tr["clips"]]
    aud = []
    for s in t["audio"]:
        p = s["at_sample"] - int(a * spf)
        L = s.get("dur_samples")
        if p >= (b - a) * spf:
            continue
        if p < 0:
            s["src_in"] = s.get("src_in", 0) - p
            if L is not None:
                L += p
                if L <= 0:
                    continue
                s["dur_samples"] = L
            p = 0
        s["at_sample"] = int(p)
        aud.append(s)
    t["audio"] = aud
    t["silences"] = [{**s, "from_frame": s["from_frame"] - a} for s in t.get("silences", []) if s["from_frame"] >= a and s["from_frame"] + s["frames"] <= b]
    t["markers"] = []
    return t


def build_job(db: DB, jobs, jid, args):
    film = args["film"]
    scope = scope_for(db, film)
    cur = edit.latest(db, film, scope)
    if not cur:
        raise RuntimeError("there is no edit")
    tl, status, prob = edit.resolve(db, film, scope, cur["doc"], make=True)
    txt = films.setting(db, film, "release_text") or {}
    sh = args.get("short")
    kind = "short" if sh is not None else "long"
    n = 1 + (db.val("select max(rev) from release_package where film_id=? and kind=?", film, kind) or 0)
    out = data_dir() / "release" / "".join(c if c.isalnum() or c in "-_" else "_" for c in film) / f"{kind}-{n:02d}"
    out.mkdir(parents=True, exist_ok=True)
    res = lambda a: edit._path(db, a)
    stand = [k for k, v in status.items() if v not in gates.FINISHED]
    rights = cut.final_blockers(db, film, tl)
    if kind == "short":
        s = txt["shorts"][int(sh)] if isinstance(sh, int) else sh
        a, b = int(s["from_frame"]), min(int(s["to_frame"]), tl["frames"])
        if b - a < 24:
            raise RuntimeError("a Short needs at least a second")
        tl = vertical(tl, a, b, float(s.get("x", 0)), s.get("style", "crop"))
        render.validate(tl)
        (out / "TITLE.txt").write_text(s["title"], encoding="utf-8")
    final = not stand and not rights
    tl["loudness"] = -14.0                                # YouTube plays at -14; louder is only turned down
    work = data_dir() / "renders" / f"release_{jid}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        r, segs = cut.picture(tl, res, jobs, jid)
        m = render.join(segs, work / "master.mkv")
        jobs.progress(jid, tl["frames"], tl["frames"], "mixing sound")
        mx = sound.mix(tl, res)
        y, info = sound.master(mx, tl["loudness"])
        wav = sound.write(y, work / "mix.wav")
        del y, mx
        name = "film.mp4" if kind == "long" else "short.mp4"
        render.deliver(m, wav, out / name, tl["fps"], kbps=16000 if kind == "long" else 12000)
        jobs.progress(jid, tl["frames"], tl["frames"], "checking the file")
        qa = render.qa(out / name, tl, wav)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    (out / "captions.en.srt").write_text(srt(db, tl), encoding="utf-8")
    (out / "captions.en.vtt").write_text(vtt(db, tl), encoding="utf-8")
    if kind == "long":
        if txt:
            (out / "TITLE.txt").write_text(txt.get("chosen_title") or "\n".join(txt.get("title_options", [])), encoding="utf-8")
            (out / "DESCRIPTION.txt").write_text(description(db, film, tl, txt), encoding="utf-8")
            (out / "TAGS.txt").write_text(", ".join(txt.get("tags", [])), encoding="utf-8")
        thumbs(out / name, out, txt.get("thumbnail", {}).get("text", ""), txt.get("thumbnail", {}).get("frame"))
    (out / "UPLOAD.txt").write_text(upload_notes(db, film, tl, txt, kind, final, qa, rights, stand), encoding="utf-8")
    prov = [{"asset": a, **(db.one("select sha256,kind,origin,provider,provider_model,created_at from asset where id=?", a) or {}), "licence": db.val("select terms_id from asset_licence where asset_id=?", a)} for a in sorted(cut.used_assets(tl))]
    (out / "provenance.json").write_text(json.dumps(prov, indent=1), encoding="utf-8")
    files = {p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(out.iterdir()) if p.is_file()}
    doc = {"film": film, "scope": scope, "cut": cur["id"], "revision": cur["rev"], "kind": kind, "final": final, "stand_ins": stand[:20], "rights": [x["why"] for x in rights][:10], "qa": qa, "loudness": info, "files": files, "made": now()}
    (out / "MANIFEST.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    aid, _ = assets.add(db, out / name, origin="rendered", storage="derived", provenance={"release": film, "kind": kind, "cut": cur["id"]}, actor="engine")
    rid = cut.save_timeline(db, film, scope if kind == "long" else f"short:{n}", tl, status, cur["id"])
    if kind == "long" and final:
        rn = db.insert("render", id=new_id("rn"), timeline_rev_id=rid, kind="master", engine_version=render.ENGINE_VERSION, gpu_key=r.g.key, settings_json={"size": tl["size"]}, asset_id=aid, qa_json={"outcome": qa["outcome"], "items": qa["items"], "facts": {"loudness": info}, "status": status}, created_at=now(), seconds=0)
        for a in cut.used_assets(tl):
            if not db.one("select 1 from asset_use where film_id=? and asset_id=? and timeline_rev_id=? and purpose='final'", film, a, rid):
                db.insert("asset_use", id=new_id("us"), film_id=film, asset_id=a, timeline_rev_id=rid, purpose="final", used_as="master")
    else:
        rn = None
    pid = db.insert("release_package", id=new_id("rl"), film_id=film, rev=n, kind=kind, render_id=rn, doc_json=doc, folder=str(out), status="ready" if final and qa["outcome"] == "pass" else "draft", created_at=now())
    db.event("system", "release.built", "release_package", pid, {"final": final, "qa": qa["outcome"]})
    return {"package": pid, "asset": aid, "folder": str(out), "final": final}


def upload_notes(db, film, tl, txt, kind, final, qa, rights, stand):
    mw = made_with(db, tl)
    L = [("THIS IS A DRAFT, NOT FOR UPLOAD" if not final else "READY FOR YOUR UPLOAD"), ""]
    if stand:
        L.append(f"Stand-ins left: {len(stand)} ({', '.join(stand[:6])})")
    if rights:
        L.append(f"Rights open: {len(rights)}")
    L += [f"Technical checks: {qa['outcome']}", "", "In YouTube Studio, by hand:", "1. Upload the video file; visibility Private first.", "2. Title, description and tags from the text files.", "3. Subtitles: upload captions.en.srt, language English.", "4. Audience: not made for kids.",
          f"5. Altered or synthetic content: {'YES' if (txt.get('disclosure') or {}).get('altered_or_synthetic', True) else 'no'} - used: " + ", ".join(f"{v} {k}" for k, v in mw.items() if v), "6. Thumbnail: the one you chose (below 2 MB).", "7. Watch the processed version once before making it public."]
    if txt.get("content_notes"):
        L += ["", "Content notes: " + "; ".join(txt["content_notes"])]
    return "\n".join(L)


def thumbs(video: Path, out: Path, text="", frame=None):
    """Candidates from the film itself: sharp, not black, spread over the running time. The choice is the user's."""
    cap = cv2.VideoCapture(str(video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    score, k = [], 0
    step = max(1, n // 160)
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if k % step == 0 or k == frame:
            g = cv2.cvtColor(cv2.resize(f, (480, 270)), cv2.COLOR_BGR2GRAY)
            lum = g.mean() / 255
            s = cv2.Laplacian(g, cv2.CV_32F).var() ** .5 * (1 if .04 < lum < .8 else .2) * (1 + g.std() / 80)
            score.append((1e9 if k == frame else s, k))
        k += 1
    cap.release()
    best = []
    for s, i in sorted(score, reverse=True):
        if all(abs(i - j) > n / 14 for j in best):
            best.append(i)
        if len(best) == 4:
            break
    cap, want, k = cv2.VideoCapture(str(video)), set(best), 0
    while want:
        ok, f = cap.read()
        if not ok:
            break
        if k in want:
            want.discard(k)
            i = best.index(k) + 1
            w = cv2.resize(f, (1280, int(1280 * f.shape[0] / f.shape[1])), interpolation=cv2.INTER_AREA)[:720]
            if w.shape[0] < 720:
                pad = np.zeros((720, 1280, 3), np.uint8)
                pad[(720 - w.shape[0]) // 2:(720 - w.shape[0]) // 2 + w.shape[0]] = w
                w = pad
            cv2.imwrite(str(out / f"thumbnail-{i}.jpg"), w, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if text:
                from PIL import Image, ImageDraw
                im = Image.fromarray(w[..., ::-1])
                d, f_ = ImageDraw.Draw(im), render.font(92)
                d.text((48, 720 - 150), text, font=f_, fill=(245, 240, 230), stroke_width=5, stroke_fill=(0, 0, 0))
                im.save(out / f"thumbnail-{i}-text.jpg", quality=90)
        k += 1
    cap.release()


def approve(db: DB, film, pid):
    p = db.one("select * from release_package where id=? and film_id=?", pid, film)
    if not p:
        raise RuleRefused("there is no such package")
    if p["status"] != "ready":
        raise RuleRefused("this package is a draft: it still contains stand-ins, open rights, or a failed check")
    if p["kind"] == "long":
        bad = [i["title"] for i in checklist(db, film) if i["state"] not in ("pass", "waived")]
        if bad:
            raise RuleRefused("still open: " + "; ".join(bad[:5]))
    flow.set_hash(db, "release_package", pid, sha(p["doc_json"]))
    with db.tx():
        db.run("update release_package set status='approved',approved_by='user' where id=?", pid)
        a = flow.approve(db, "G11_release", "release_package", pid, [("release_package", pid)], "user", {"kind": p["kind"]})
        if p["kind"] == "long":
            db.run("update film set status='released' where id=?", film)
    flow.changed(db, film, "released")
    return a
