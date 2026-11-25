"""One film: script, plan, canon, shots, edit, review, release."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import FileResponse, Response

from ..agents import service
from ..core import assets, common, cut, docs, edit, films, flow, gates, motion, previs, release, script
from ..core.db import RuleRefused, new_id, now
from .app import BadRequest, Context, number


def add(app, c: Context):
    db, br, jobs = c.db, c.br, c.jobs

    # ------------------------------------------------ films
    @app.get("/api/films")
    def film_list():
        return {"films": films.listing(db), "current": films.current(db), "universes": db.q("select id,title from universe where id not in(select universe_id from film where is_benchmark=1)")}

    @app.post("/api/films")
    async def film_new(req: Request):
        b = await c.json(req, "title")
        size = {"16:9": (1920, 1080), "9:16": (1080, 1920), "21:9": (1920, 816)}.get(b.get("format", "16:9"))
        if not size:
            raise BadRequest("unknown picture format")
        f = films.create(db, b["title"], logline=b.get("logline", ""), universe=b.get("universe") or None, universe_title=b.get("universe_title"), fps=number(b.get("fps", 24), "frames per second"), size=size)
        films.choose(db, f)
        return {"id": f}

    @app.post("/api/films/{fid}/choose")
    def film_choose(fid: str):
        films.choose(db, fid)
        return {"ok": True}

    @app.post("/api/setting")
    async def setting(req: Request):
        b = await c.json(req, "key")
        if b["key"] not in ("review_cuts", "stand_in_sounds", "loudness"):
            raise BadRequest("unknown setting")
        films.set_setting(db, c.film(req), b["key"], b.get("value"))
        return {"ok": True}

    @app.post("/api/mode")
    async def set_mode(req: Request):
        b = await c.json(req, "service", "mode")
        flow.set_mode(db, c.film(req), b["service"], b["mode"])
        return {"ok": True}

    # ------------------------------------------------ where the film stands
    @app.get("/api/status")
    def status(req: Request):
        return gates.status(db, c.film(req), app.state)

    # ------------------------------------------------ script
    @app.get("/api/script")
    def script_state(req: Request):
        film = c.film(req)
        d = script.current(db, film)
        return {"doc": d, "revisions": db.q("select id,revision,original_name,format,locked_at from source_doc where film_id=? and kind='script' order by revision desc", film), "scenes": db.q("select s.*,(select count(*) from shot sh where sh.scene_id=s.id and sh.state<>'dropped') shots from scene s where film_id=? order by number", film),
                "spans": [{**s, "shots": [r["code"] for r in db.q("select sh.code from shot_span x join shot sh on sh.id=x.shot_id where x.span_id=? and sh.state<>'dropped'", s["id"])]} for s in script.spans(db, film)],
                "decisions": [{**x, "options": json.loads(x["options_json"]), "evidence": json.loads(x["evidence_json"] or "{}")} for x in db.q("select * from decision where film_id=? order by rowid", film)]}

    @app.post("/api/script/preview")
    async def script_preview(file: UploadFile = File(...), layout: str = Form("")):
        return await c.with_upload(file, lambda p: script.parse(p, layout or None))

    @app.post("/api/script/import")
    async def script_import(req: Request, file: UploadFile = File(...), layout: str = Form("")):
        film = c.film(req)

        def go(p):
            d = script.ingest(db, film, p, layout=layout or None, by="user", name=Path(file.filename).name)
            script.make_scenes(db, film)
            flow.changed(db, film, "script.imported")
            return {"doc": d}
        return await c.with_upload(file, go)

    @app.post("/api/decision/{did}")
    async def decide(did: str, req: Request):
        b = await c.json(req, "chosen")
        db.run("update decision set chosen=?,rationale=?,decided_by='user' where id=?", b["chosen"][:400], b.get("rationale", "")[:600], did)
        db.event("user", "decision.made", "decision", did, {"chosen": b["chosen"]})
        return {"ok": True}

    # ------------------------------------------------ documents
    @app.get("/api/docs")
    def doc_list(req: Request):
        film = c.film(req)
        return {"docs": docs.listing(db, film), "canon": service.canon_view(db, film), "next": gates.makers(db, film)}

    @app.post("/api/docs/{did}/approve")
    async def doc_approve(did: str, req: Request):
        b = await c.json(req)
        return {"id": docs.approve(db, did, b.get("note", ""), b.get("reason", ""))}

    @app.post("/api/docs/{did}/return")
    async def doc_return(did: str, req: Request):
        docs.send_back(db, did, (await c.json(req, "note"))["note"])
        return {"ok": True}

    @app.post("/api/make")
    async def make(req: Request):
        b = await c.json(req, "action")
        return {"task": service.request(db, c.film(req), b["action"], b.get("scope", "film"), b.get("note", ""))}

    # ------------------------------------------------ shots and previs
    @app.get("/api/shots")
    def shots(req: Request):
        return gates.shots(db, c.film(req))

    @app.post("/api/previs")
    async def previs_start(req: Request):
        b = await c.json(req)
        film = c.film(req)
        ids = b.get("shots") or [r["id"] for r in db.q("select sh.id from shot sh join scene s on s.id=sh.scene_id where s.film_id=? and sh.state<>'dropped'", film)]
        todo = previs.possible(db, film, ids)
        if not todo:
            raise RuleRefused("none of these shots has a previs description")
        return {"job": jobs.add("previs", {"film": film, "shots": todo}, key=f"previs:{film}:{now()}", ram=600, resource="gpu", film=film), "shots": todo}

    @app.get("/previs/{film}/{part}/{frame}")
    def previs_picture(film: str, part: str, frame: str):
        d = films.blocking_dir(db, film)
        p = (d / part / frame).resolve()
        if not p.is_relative_to(d.resolve()) or p.suffix != ".png" or not p.exists():
            return Response(status_code=404)
        return FileResponse(p)

    # ------------------------------------------------ edit
    def scope_of(req: Request, film):
        s = req.query_params.get("scope")
        if not s:
            r = db.val("select scope from cut_rev where film_id=? order by scope='film' desc,rowid desc limit 1", film) or (f"scene:{db.val('select id from scene where film_id=? order by number limit 1', film)}")
            s = r
        return s

    @app.get("/api/cut")
    def cut_state(req: Request):
        film = c.film(req)
        s = scope_of(req, film)
        return {"scope": s, "scopes": gates.scopes(db, film), "commands": {k: v[1] for k, v in edit.COMMANDS.items()}, "placeable": cut.placeable(db, film, s), **cut.state(db, film, s)}

    @app.post("/api/cut/assemble")
    async def assemble(req: Request):
        b = await c.json(req, "scene")
        return {"id": edit.first_assembly(db, c.film(req), b["scene"])}

    @app.post("/api/cut/film")
    async def film_cut(req: Request):
        """The film edit: every scene in order. Called again, it only adds scenes that are missing."""
        film = c.film(req)
        have = [r["id"] for r in db.q("select id from scene where film_id=? order by number", film) if edit.latest(db, film, f"scene:{r['id']}")]
        if not have:
            raise RuleRefused("no scene has an edit yet")
        cur = edit.latest(db, film, "film")
        parts = [{"scene": s, **next((p for p in (cur["doc"].get("parts") or []) if p["scene"] == s), {})} for s in have] if cur else [{"scene": s} for s in have]
        return edit.apply(db, film, "film", cur["rev"] if cur else 0, [{"op": "set_parts", "value": parts, "reason": "scenes in script order"}], "user")

    @app.post("/api/cut/commands")
    async def commands(req: Request):
        b = await c.json(req, "scope", "commands")
        film = c.film(req)
        return await c.work(lambda: edit.apply(db, film, b["scope"], number(b.get("base", 0), "base revision"), b["commands"], "user", dry=bool(b.get("dry"))))

    @app.post("/api/cut/approve")
    async def cut_approve(req: Request):
        b = await c.json(req, "id")
        return {"approval": gates.approve_cut(db, b["id"], b.get("reason", ""))}

    @app.post("/api/cut/note")
    async def note(req: Request):
        b = await c.json(req, "scope", "text")
        return {"id": db.insert("cut_note", id=new_id("cn"), film_id=c.film(req), scope=b["scope"], frame=b.get("frame"), clip_id=b.get("clip_id") or b.get("clip"), text=b["text"][:1000], by="user", created_at=now())}

    @app.post("/api/cut/render")
    async def render(req: Request):
        b = await c.json(req) if req.headers.get("content-length") not in (None, "0") else {}
        film = c.film(req)
        s, kind = b.get("scope") or scope_of(req, film), b.get("kind", "review")
        if kind not in cut.SCALE:
            raise BadRequest("unknown kind of render")
        cur = edit.latest(db, film, s)
        if not cur:
            raise RuleRefused("there is no edit to render")
        return {"job": jobs.add("render", {"film": film, "scope": s, "kind": kind}, key=f"render:{film}:{s}:{kind}:{now()}", ram=750 if kind != "preview" else 550, disk=600, film=film)}

    @app.get("/api/subtitles")
    def subtitles(req: Request):
        film = c.film(req)
        s = scope_of(req, film)
        tl, _, _ = edit.resolve(db, film, s)
        return Response(release.srt(db, tl), media_type="text/plain")

    # ------------------------------------------------ review: pictures, takes, clips
    @app.get("/api/review")
    def review(req: Request):
        film, out = c.film(req), []
        for a in db.q("""select a.id,a.kind,a.origin,a.relpath,a.created_at,a.provenance_json from asset a left join attempt at on at.result_asset_id=a.id left join generation_target t on t.id=at.target_id
                where a.origin in('manual_video','manual_audio','api_audio','api_image','authored') and a.storage_class='original' and a.provenance_json not like '%"synth"%' and a.provenance_json not like '%"temp_voice"%' and (t.film_id=? or (t.id is null and a.provenance_json like '%"trial"%')) order by a.rowid desc limit 80""", film):
            at = db.one("select at.id,at.outcome,at.attempt_no,t.title,t.kind,t.ref_id,t.id target_id,t.state from attempt at join generation_target t on t.id=at.target_id where at.result_asset_id=?", a["id"])
            out.append({**a, "attempt": at, "checks": common.checks(db, a["id"]), "reports": [{**r, "findings": db.q("select id,defect_class,severity,statement,reason,next_action,human_verdict from finding where report_id=?", r["id"])} for r in common.reviews_of(db, a["id"])],
                        "labels": db.q("select verdict,blind,kind,tags_json,created_at from human_label where asset_id=? order by rowid", a["id"])})
        return {"items": out}

    @app.post("/api/review/{aid}/blind")
    async def blind(aid: str, req: Request):
        b = await c.json(req, "verdict")
        db.insert("human_label", id=new_id("hl"), asset_id=aid, blind=1, kind=b.get("kind", "watch"), verdict=b["verdict"], tags_json={"note": b.get("note", ""), "defects": b.get("defects", [])}, created_at=now())
        db.event("user", "label.blind", "asset", aid, {"verdict": b["verdict"]})
        return {"ok": True}

    # ------------------------------------------------ release
    @app.get("/api/release")
    def release_state(req: Request):
        return release.state(db, c.film(req))

    @app.post("/api/release/text")
    async def release_text(req: Request):
        b = await c.json(req, "doc")
        return docs.propose(db, c.film(req), "release", "film", b["doc"], "user", changed=b.get("changed") or "edited by you")

    @app.post("/api/release/item")
    async def release_item(req: Request):
        b = await c.json(req, "item", "state")
        release.decide(db, c.film(req), b["item"], b["state"], b.get("reason", ""))
        return {"ok": True}

    @app.post("/api/release/build")
    async def release_build(req: Request):
        b = await c.json(req)
        film = c.film(req)
        return {"job": jobs.add("release", {"film": film, "short": b.get("short")}, key=f"release:{film}:{now()}", ram=800, resource="gpu", film=film)}

    @app.post("/api/release/approve")
    async def release_approve(req: Request):
        return {"approval": release.approve(db, c.film(req), (await c.json(req, "id"))["id"])}
