"""Making: images, voices, sounds, Gemini clips. Anything that costs shows its price first and sends back the price that was agreed to."""
from __future__ import annotations

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import FileResponse

from ..core import assets, common, flow, gates, images, motion, packages, voice
from .app import BadRequest, Context, number


def add(app, c: Context):
    db, br = c.db, c.br

    def credits_of(s):
        return int(s) if s.strip().isdigit() else None

    def agreed(b, name):
        """Generation is refused unless the price on the button was agreed to."""
        if b.get(name) is None:
            raise BadRequest("look at the price first")
        return number(b[name], "price", 0)

    # ------------------------------------------------ targets and attempts
    @app.post("/api/target/{tid}/extend")
    async def extend(tid: str, req: Request):
        b = await c.json(req)
        flow.extend(db, tid, number(b.get("extra", 1), "attempts", 1, 3), b.get("reason", ""))
        return {"ok": True}

    @app.post("/api/target/{tid}/reopen")
    async def reopen(tid: str, req: Request):
        b = await c.json(req, "reason")
        flow.reopen(db, tid, b["reason"])
        return {"ok": True}

    @app.post("/api/attempt/{aid}/withdraw")
    async def withdraw(aid: str, req: Request):
        flow.withdraw(db, aid, (await c.json(req)).get("reason") or "taken back by you")
        return {"ok": True}

    @app.post("/api/attempt/{aid}/judge")
    async def judge(aid: str, req: Request):
        b = await c.json(req, "verdict")
        if b["verdict"] not in ("accepted", "needs_correction", "rejected"):
            raise BadRequest("accepted, needs_correction or rejected")
        common.judge(db, aid, b["verdict"], b.get("note", ""))
        return {"ok": True}

    @app.post("/api/package/{kind}/{pid}/approve")
    async def approve_package(kind: str, pid: str, req: Request):
        if kind not in packages.GATE:
            raise BadRequest("unknown kind of package")
        return {"approval": packages.approve(db, kind, pid, (await c.json(req)).get("reason", ""))}

    # ------------------------------------------------ images
    @app.get("/api/images")
    def image_list(req: Request):
        film = c.film(req)
        return {**images.image_state(db, film), "mode": flow.mode(db, film, "stills"), "allowance": bool(__import__("studio.core.films", fromlist=["x"]).envelope(db, film, "image"))}

    @app.post("/api/images/{img}/estimate")
    async def image_estimate(img: str, req: Request):
        b = await c.json(req, "provider")
        spec, refs = images.spec_for(db, c.film(req), img, b["provider"], b.get("prompt") or None)
        return {"musd": br.estimate(b["provider"], "image_edit" if refs else "image_generate", spec), "references": refs}

    @app.post("/api/images/{img}/generate")
    async def image_generate(img: str, req: Request):
        b = await c.json(req, "provider")
        film, price = c.film(req), agreed(b, "expect")
        return await c.work(lambda: images.generate_image(db, br, film, img, b["provider"], b.get("prompt") or None, b.get("reason", ""), price))

    @app.post("/api/images/{img}/import")
    async def image_import(img: str, req: Request, file: UploadFile = File(...), note: str = Form(""), reason: str = Form("")):
        film = c.film(req)
        return await c.with_upload(file, lambda p: images.import_image(db, film, img, p, note, reason))

    # ------------------------------------------------ voice
    @app.get("/api/voice")
    def voices(req: Request):
        film = c.film(req)
        return {"packages": voice.voice_state(db, film), "mode": flow.mode(db, film, "voice"), "models": packages.MODELS}

    @app.post("/api/voice/{pid}/{line}/import")
    async def voice_import(pid: str, line: str, file: UploadFile = File(...), credits: str = Form(""), note: str = Form(""), reason: str = Form(""), voice_name: str = Form("", alias="voice"), voice_kind: str = Form("")):
        return await c.with_upload(file, lambda p: voice.import_voice(db, pid, line, p, credits_of(credits), note, reason, None, voice_name, voice_kind))

    @app.post("/api/voice/{pid}/{line}/estimate")
    async def voice_estimate(pid: str, line: str, req: Request):
        b = await c.json(req)
        return {"credits": br.estimate("elevenlabs", "tts", voice.tts_spec(db, pid, line, b.get("voice_id", "x")))}

    @app.post("/api/voice/{pid}/{line}/generate")
    async def voice_generate(pid: str, line: str, req: Request):
        b = await c.json(req, "voice_id")
        price = agreed(b, "expect")
        return await c.work(lambda: voice.generate_voice(db, br, pid, line, b["voice_id"], b.get("reason", ""), price))

    # ------------------------------------------------ sound effects
    @app.get("/api/sfx")
    def sfx(req: Request):
        film = c.film(req)
        return {"packages": voice.sfx_state(db, film), "mode": flow.mode(db, film, "sfx")}

    @app.post("/api/sfx/{pid}/import")
    async def sfx_import(pid: str, file: UploadFile = File(...), credits: str = Form(""), reason: str = Form("")):
        return await c.with_upload(file, lambda p: voice.import_sfx(db, pid, p, credits_of(credits), reason))

    @app.get("/api/sfx/{pid}/estimate")
    def sfx_estimate(pid: str):
        return {"credits": br.estimate("elevenlabs", "sfx", voice._sfx_spec(packages.doc_of(db, "sfx", pid)))}

    @app.post("/api/sfx/{pid}/generate")
    async def sfx_generate(pid: str, req: Request):
        b = await c.json(req)
        price = agreed(b, "expect")
        return await c.work(lambda: voice.generate_sfx(db, br, pid, b.get("reason", ""), price))

    # ------------------------------------------------ Gemini video, by hand
    @app.get("/api/video")
    def video(req: Request):
        return {"shots": motion.video_state(db, c.film(req)), "labels": db.q("select label from account_label order by label")}

    @app.post("/api/video/{shot}/pair")
    async def pair(shot: str):
        return await c.work(lambda: motion.make_pair(db, shot))

    @app.post("/api/video/pair/{fid}/approve")
    async def pair_ok(fid: str, req: Request):
        b = await c.json(req)
        return {"package": motion.approve_pair(db, fid, bool(b.get("accept_warnings")))}

    @app.get("/api/video/{pid}/frame/{n}")
    def frame_file(pid: str, n: int):
        d = packages.doc_of(db, "video", pid)
        if not 1 <= n <= len(d["attachments"]):
            raise assets.Missing("there is no such frame")
        a = d["attachments"][n - 1]
        shot = motion.shot_of_pair(db, db.val("select frame_pair_id from video_package where id=?", pid))
        return FileResponse(motion.package_folder(shot) / a["file"], filename=a["file"])

    @app.post("/api/video/{pid}/log")
    async def vlog(pid: str, req: Request):
        b = await c.json(req, "state")
        return {"id": motion.log_generation(db, pid, b["state"], b.get("label", ""), b.get("model", ""), b.get("mode", ""), b.get("usage", ""))}

    @app.post("/api/video/{pid}/import")
    async def video_import(pid: str, file: UploadFile = File(...), label: str = Form(""), model: str = Form(""), usage: str = Form(""), reason: str = Form("")):
        return await c.with_upload(file, lambda p: motion.import_video(db, pid, p, label, model, usage, reason))

    @app.post("/api/clip/{aid}/approve")
    async def clip_ok(aid: str, req: Request):
        return {"approval": gates.approve_clip(db, aid, (await c.json(req)).get("reason", ""))}

    # ------------------------------------------------ inspection
    @app.post("/api/inspect/trial")
    async def trial(file: UploadFile = File(...)):
        """Inspect any clip without attaching it to a shot: to try the inspection on your own material."""
        def go(p):
            aid, new = assets.add(db, p, origin="manual_video", provenance={"trial": True}, actor="user")
            return {"asset": aid, "new": new, "report": motion.inspect_video(db, aid) if db.val("select kind from asset where id=?", aid) == "video" else None}
        return await c.with_upload(file, go)

    @app.post("/api/inspect/{aid}/estimate")
    async def gem_price(aid: str):
        kind, spec = motion.gemini_spec(db, aid)
        return {"musd": br.estimate("gemini_inspect", f"inspect_{kind}", spec)}

    @app.post("/api/inspect/{aid}/gemini")
    async def gem(aid: str, req: Request):
        b = await c.json(req)
        film, price = c.film(req), agreed(b, "expect")
        return await c.work(lambda: motion.gemini_inspect(db, br, film, aid, expect=price))
