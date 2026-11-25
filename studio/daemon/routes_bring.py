"""Bringing in files made by hand, what is known about a file, where things are kept, stopping the studio."""
from __future__ import annotations

from pathlib import Path

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import FileResponse

from ..core import assets, backup, bring, common, images, motion, voice
from ..core.db import data_dir
from .app import BadRequest, Context


def add(app, c: Context):
    db, jobs = c.db, c.jobs

    def terms():
        return db.q("select id,source,attribution_required,verified_by,r_youtube from licence_terms where r_sync='yes' order by id<>'own',source")

    # ------------------------------------------------ one place for every file
    @app.get("/api/bring")
    def waiting(req: Request):
        film = c.film(req)
        return {"files": bring.waiting(db, film), "terms": terms(), "labels": [r["label"] for r in db.q("select label from account_label order by label")], "places": places()}

    @app.post("/api/bring")
    async def bring_in(req: Request, file: UploadFile = File(...)):
        film, name = c.film(req), file.filename or "file"
        return await c.with_upload(file, lambda p: bring.stage(db, film, p, name))

    @app.post("/api/bring/{fid}/put")
    async def put(fid: str, req: Request):
        b = await c.json(req, "to")
        return await c.work(lambda: bring.put(db, fid, b["to"], b))

    @app.post("/api/bring/{fid}/discard")
    def discard(fid: str):
        bring.discard(db, fid)
        return {"ok": True}

    @app.get("/waiting/{fid}")
    def waiting_file(fid: str):
        p = bring.path_of(fid)
        return FileResponse(p, media_type=assets.MIME.get(p.suffix.lower(), "application/octet-stream"))

    # ------------------------------------------------ needs that no agent has written down
    @app.post("/api/images/add")
    async def image_need(req: Request):
        b = await c.json(req, "code", "kind")
        return {"code": images.add_need(db, c.film(req), b["code"], b["kind"], entity=b.get("entity") or None, shot=b.get("shot") or None, role=b.get("role") or None, aspect=b.get("aspect") or "16:9", prompt=b.get("prompt", ""))}

    @app.get("/api/images/choices")
    def image_choices(req: Request):
        film = c.film(req)
        return {"entities": db.q("select e.id,e.name,e.kind from film_canon f join canon_entity e on e.id=f.entity_id where f.film_id=? and e.kind in('character','wardrobe','environment','prop','look') order by e.kind,e.name", film),
                "shots": db.q("select sh.id,sh.code from shot sh join scene s on s.id=sh.scene_id where s.film_id=? and sh.state<>'dropped' order by s.number,sh.ordinal,sh.code", film)}

    @app.post("/api/cues")
    async def cue_new(req: Request):
        b = await c.json(req, "scene", "kind", "purpose")
        return {"id": voice.add_cue(db, c.film(req), b["scene"], b["kind"], b["purpose"])}

    @app.post("/api/cues/{cue}/import")
    async def cue_import(cue: str, req: Request, file: UploadFile = File(...), terms_id: str = Form("", alias="terms"), title: str = Form(""), reason: str = Form(""), credit: str = Form("")):
        film, name = c.film(req), title or (file.filename or "")
        return await c.with_upload(file, lambda p: voice.import_cue(db, film, cue, p, terms_id, name, reason, credit))

    @app.post("/api/cues/{cue}/library")
    async def cue_library(cue: str, req: Request):
        voice.use_library(db, c.film(req), cue, (await c.json(req, "asset"))["asset"])
        return {"ok": True}

    @app.post("/api/shots/{shot}/clip")
    async def shot_clip(shot: str, req: Request, file: UploadFile = File(...), label: str = Form(""), model: str = Form(""), reason: str = Form("")):
        film = c.film(req)
        return await c.with_upload(file, lambda p: motion.import_clip(db, film, shot, p, label, model, reason))

    # ------------------------------------------------ one file
    @app.get("/api/asset/{aid}")
    def asset(aid: str):
        return {**common.about_file(db, aid), "terms": terms()}

    @app.post("/api/asset/{aid}/licence")
    async def asset_licence(aid: str, req: Request):
        b = await c.json(req, "terms")
        common.licence(db, aid, b["terms"], b.get("credit", ""))
        return {"ok": True}

    @app.post("/api/library/terms")
    async def new_terms(req: Request):
        b = await c.json(req, "source", "url")
        return {"id": common.add_terms(db, b["source"], b["url"], b.get("rights") or {}, bool(b.get("attribution")), b.get("note", ""))}

    # ------------------------------------------------ where things are
    def places():
        d = data_dir()
        return [{"id": k, "title": t, "path": str((d / p).resolve())} for k, t, p in (("originals", "Everything you brought in, unchanged and read-only", "media/originals"), ("derived", "Made from it by the studio", "media/derived"), ("packages", "Frames and prompts for the Gemini app", "packages"), ("releases", "Packages for YouTube", "release"), ("data", "The whole studio: database and settings", "."))]

    @app.post("/api/reveal")
    async def reveal(req: Request):
        b = await c.json(req)
        if b.get("asset"):
            common.reveal(assets.path_of(db, b["asset"]))
        else:
            p = next((x for x in places() if x["id"] == b.get("place")), None)
            if not p:
                raise BadRequest("unknown place")
            Path(p["path"]).mkdir(parents=True, exist_ok=True)
            common.reveal(Path(p["path"]))
        return {"ok": True}

    # ------------------------------------------------ stopping
    @app.post("/api/stop")
    async def stop(req: Request):
        """Ends the studio in order. A render goes on from its last finished part at the next start."""
        s = getattr(app.state, "server", None)
        if s is None:
            raise BadRequest("the studio is run by something else here; close its window")
        running = db.q("select kind from media_job where state='running'")
        if running and not (await c.json(req)).get("anyway"):
            return {"running": [r["kind"] for r in running]}
        jobs.stop()
        s.should_exit = True
        return {"stopping": True}
