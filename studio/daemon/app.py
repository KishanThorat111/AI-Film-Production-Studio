"""The local server. Loopback only; the page is the user's hands. Keys go in, never out."""
from __future__ import annotations

import os
import re
import secrets
import shutil
import threading
import traceback
from pathlib import Path

import anyio
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from ..agents import service
from ..connectors.adapters import CostUnknown
from ..connectors.broker import Broker, Refused
from ..connectors.secrets import SecretError
from ..core import assets, backup, base, common, cut, films, flow, project, script
from ..core.db import DB, ROOT, RuleRefused, data_dir
from ..core.jobs import Jobs, log_error

WEB = ROOT / "web"
PORT = int(os.environ.get("STUDIO_PORT", "8765"))
MAX_UPLOAD, MAX_JSON = 600 * 2 ** 20, 4 * 2 ** 20
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff2": "font/woff2"}


class BadRequest(Exception):
    pass


class Limit:
    """Counts what really arrives, whatever the headers say."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD"):
            return await self.app(scope, receive, send)
        h = {k.lower(): v for k, v in scope["headers"]}
        most = MAX_UPLOAD if h.get(b"content-type", b"").startswith(b"multipart/form-data") else MAX_JSON
        got, over, told = 0, False, False
        no = JSONResponse({"error": "that is larger than the studio accepts"}, 413)

        async def counted():
            nonlocal got, over
            if over:
                return {"type": "http.disconnect"}
            m = await receive()
            got += len(m.get("body", b""))
            if got > most:                            # stop reading; whatever the route answers now, the user hears why
                over = True
                return {"type": "http.disconnect"}
            return m

        async def answer(m):
            nonlocal told
            if over:
                if not told:
                    told = True
                    await no(scope, receive, send)
                return
            await send(m)
        try:
            if int(h.get(b"content-length") or 0) > most:
                return await no(scope, receive, send)
        except ValueError:
            return await JSONResponse({"error": "bad request"}, 400)(scope, receive, send)
        try:
            await self.app(scope, counted, answer)
        except Exception:
            if not over:
                raise
        if over and not told:
            await no(scope, receive, send)


class Context:
    """What every route needs."""

    def __init__(self, app, db, br, jobs):
        self.app, self.db, self.br, self.jobs = app, db, br, jobs

    def film(self, req: Request) -> str:
        f = req.query_params.get("film") or films.current(self.db)
        if not f:
            raise BadRequest("create a film first")
        films.get(self.db, f)
        return f

    @staticmethod
    async def json(req: Request, *need) -> dict:
        try:
            b = await req.json()
        except ValueError:
            raise BadRequest("the request is not readable") from None
        if not isinstance(b, dict):
            raise BadRequest("the request is not readable")
        miss = [k for k in need if b.get(k) in (None, "")]
        if miss:
            raise BadRequest("missing: " + ", ".join(miss))
        return b

    @staticmethod
    async def upload(file: UploadFile) -> Path:
        """Straight to disk in pieces; never the whole file in memory."""
        if not file or not file.filename:
            raise BadRequest("choose a file")
        p = common.inbox(file.filename)
        with p.open("wb") as f:
            while chunk := await file.read(1 << 20):
                await anyio.to_thread.run_sync(f.write, chunk)
        return p

    async def with_upload(self, file: UploadFile, fn):
        p = await self.upload(file)
        try:
            return await anyio.to_thread.run_sync(lambda: fn(p))
        finally:
            common.clear_inbox(p)

    @staticmethod
    async def work(fn):
        return await anyio.to_thread.run_sync(fn)


def number(v, what, lo=None, hi=None):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise BadRequest(f"{what} must be a whole number") from None
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        raise BadRequest(f"{what} must be between {lo} and {hi}")
    return n


def create(vault=None, start_jobs=True) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    db = DB(check=True)
    base.base(db)
    backup.ensure_default(db)
    for folder in project.found():
        project.load(db, folder)
    cut.ensure_sounds(db)
    if service.ensure_tasks not in flow.LISTENERS:
        flow.LISTENERS.append(service.ensure_tasks)
    service.ensure_tasks(db)
    br = Broker(db, vault)
    recovered = br.recover_after_crash()
    jobs = Jobs(db)
    ctx = Context(app, db, br, jobs)
    app.state.db, app.state.broker, app.state.jobs, app.state.ctx = db, br, jobs, ctx
    app.state.launch, app.state.session = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    app.state.recovered, app.state.migrated = recovered, db.migration
    from . import handlers
    handlers.register(ctx)
    if start_jobs:
        jobs.start()
        app.state.timer = Timer(ctx)
        app.state.timer.start()

    # ------------------------------------------------ the door
    @app.middleware("http")
    async def guard(req: Request, call):
        host = (req.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost") or (req.client and req.client.host not in ("127.0.0.1", "::1", "testclient")):
            return JSONResponse({"error": "local use only"}, 403)
        path = req.url.path
        if path not in ("/launch", "/favicon.ico"):
            if not secrets.compare_digest(req.cookies.get("studio", ""), app.state.session):
                return HTMLResponse("<meta charset=utf-8><p style='font:16px sans-serif;margin:3em'>Open the studio with <b>Start Studio.bat</b>. This address only works from there.</p>", 401)
        if req.method not in ("GET", "HEAD"):
            origin = req.headers.get("origin", "")
            if not re.fullmatch(r"http://(127\.0\.0\.1|localhost):\d+", origin) or req.headers.get("x-studio") != "1":
                return JSONResponse({"error": "blocked request"}, 403)
        r: Response = await call(req)
        r.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        r.headers["X-Content-Type-Options"] = "nosniff"
        r.headers["Referrer-Policy"] = "no-referrer"
        if path.startswith("/api"):
            r.headers["Cache-Control"] = "no-store"
        return r

    for exc in (Refused, RuleRefused, SecretError, assets.ImportProblem, CostUnknown, script.ScriptProblem, backup.BackupProblem, service.AgentRefused):
        @app.exception_handler(exc)
        async def refused(req, e):
            return JSONResponse({"error": plain(str(e))}, 409)

    @app.exception_handler(BadRequest)
    async def bad(req, e):
        return JSONResponse({"error": str(e)}, 400)

    @app.exception_handler(assets.Missing)
    async def missing(req, e):
        return JSONResponse({"error": plain(str(e)) or "not found"}, 404)

    @app.exception_handler(Exception)
    async def broke(req, e):
        ref = log_error(req.url.path, "".join(traceback.format_exception(e)))
        return JSONResponse({"error": f"Something failed inside the studio. Nothing you approved was changed. Evidence number {ref} is in data/errors.log.", "ref": ref}, 500)

    @app.get("/launch")
    def launch(code: str = ""):
        if not app.state.launch or not secrets.compare_digest(code, app.state.launch):
            return HTMLResponse("This start link has already been used. Run Start Studio.bat again.", 403)
        app.state.launch = None
        r = RedirectResponse("/", 303)
        r.set_cookie("studio", app.state.session, httponly=True, samesite="strict", path="/")
        return r

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/web/{path:path}")
    def static(path: str):
        p = (WEB / path).resolve()
        if not p.is_relative_to(WEB.resolve()) or not p.is_file():
            return Response(status_code=404)
        return FileResponse(p, media_type=TYPES.get(p.suffix), headers={"Cache-Control": "no-cache"})

    from . import routes_film, routes_make, routes_studio
    for m in (routes_studio, routes_film, routes_make):
        m.add(app, ctx)
    app.add_middleware(Limit)
    return app


class Timer(threading.Thread):
    """Every few minutes: is a copy due, is something stalled, is there rubbish to clear?"""

    def __init__(self, ctx):
        super().__init__(daemon=True, name="timer")
        self.ctx, self.stop_flag = ctx, threading.Event()

    def run(self):
        self.stop_flag.wait(60)
        while not self.stop_flag.is_set():
            try:
                for t in backup.due(self.ctx.db):
                    self.ctx.jobs.add("backup", {"target": t}, key=f"backup:{t}", ram=100, resource="io")
                cut.trim_cache()
            except Exception:
                log_error("timer", traceback.format_exc())
            self.stop_flag.wait(600)


def plain(msg: str) -> str:
    m = re.sub(r"^(CHECK constraint failed|UNIQUE constraint failed|FOREIGN KEY constraint failed):?\s*", "not allowed: ", msg)
    return m[:1200]


def disk_free_gb():
    return round(shutil.disk_usage(data_dir()).free / 2 ** 30)
