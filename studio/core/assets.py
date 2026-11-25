"""Content-addressed media store. Originals are written once, made read-only, and never changed or deleted."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

from .db import DB, data_dir, new_id, now

KIND_BY_EXT = {".mp4": "video", ".mov": "video", ".webm": "video", ".mkv": "video", ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
               ".wav": "audio", ".mp3": "audio", ".opus": "audio", ".flac": "audio", ".m4a": "audio", ".ogg": "audio"}
MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".opus": "audio/ogg", ".flac": "audio/flac", ".m4a": "audio/mp4", ".ogg": "audio/ogg"}


class ImportProblem(Exception):
    pass


class Missing(KeyError):
    """The record or its file does not exist."""

    def __str__(self):
        return str(self.args[0]) if self.args else "not found"


def media_root() -> Path:
    p = data_dir() / "media"
    p.mkdir(parents=True, exist_ok=True)
    return p


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        raise ImportProblem("the file cannot be read as media: " + (r.stderr.strip().splitlines() or ["unknown format"])[-1][:200])
    j = json.loads(r.stdout)
    if not j.get("streams"):
        raise ImportProblem("the file contains no picture or sound")
    return j


def path_of(db: DB, asset_id: str) -> Path:
    r = db.one("select relpath from asset where id=?", asset_id)
    if not r:
        raise Missing("there is no such file")
    return media_root() / r["relpath"]


def add(db: DB, src: Path, *, origin: str, storage: str = "original", kind: str | None = None, provider=None, model=None, provenance=None, actor="system",
        parents: list[tuple[str, str, dict]] | None = None, tool_version="p0", move=False) -> tuple[str, bool]:
    """Returns (asset id, is_new). A second import of the same original returns the first one."""
    src = Path(src)
    ext = src.suffix.lower()
    kind = kind or KIND_BY_EXT.get(ext)
    if not kind:
        raise ImportProblem(f"{ext or 'this file type'} is not supported")
    size = src.stat().st_size
    if size == 0:
        raise ImportProblem("the file is empty")
    h = file_sha(src)
    if storage == "original":
        old = db.one("select id from asset where sha256=? and storage_class='original'", h)
        if old:
            return old["id"], False
    pr = probe(src) if kind in ("video", "audio", "image") else None
    sub = {"original": "originals", "derived": "derived", "proxy": "proxies", "cache": "cache"}[storage]
    rel = f"{sub}/{h[:2]}/{h[2:4]}/{h}{ext}"
    dst = media_root() / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        tmp = dst.with_suffix(f"{dst.suffix}.{new_id('p')}.part")      # two imports at once never share a half-written file
        try:
            shutil.copyfile(str(src), str(tmp))
            if file_sha(tmp) != h:
                raise ImportProblem("the copy did not match the source; nothing was imported")
            try:
                os.replace(tmp, dst)
            except PermissionError:
                if not dst.exists():                      # somebody else finished the same file first: that is fine, it has the same name because it is the same
                    raise
        except OSError as e:
            raise ImportProblem("the disk is full; nothing was imported" if getattr(e, "errno", None) == 28 else f"Windows refused to write the file ({e.strerror or type(e).__name__}); nothing was imported") from None
        finally:
            tmp.unlink(missing_ok=True)
        if storage == "original":
            os.chmod(dst, stat.S_IREAD)
    aid = new_id("as")
    with db.tx():
        if storage == "original":
            old = db.one("select id from asset where sha256=? and storage_class='original'", h)
            if old:                                       # stored by a parallel import since we looked
                return old["id"], False
        db.insert("asset", id=aid, sha256=h, kind=kind, origin=origin, storage_class=storage, relpath=rel, bytes=size, probe_json=pr, provider=provider,
                  provider_model=model, provenance_json=provenance or {}, created_at=now())
        for pid, op, params in parents or []:
            db.insert("asset_edge", child_id=aid, parent_id=pid, op=op, op_params_json=params, tool_version=tool_version)
        db.event(actor, "asset.add", "asset", aid, {"origin": origin, "sha": h, "bytes": size})
    if move:                                              # only after the record exists
        src.unlink(missing_ok=True)
    return aid, True


def add_bytes(db: DB, data: bytes, ext: str, **kw):
    tmp = data_dir() / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    p = tmp / (new_id("in") + ext)
    p.write_bytes(data)
    try:
        return add(db, p, **kw)
    finally:
        p.unlink(missing_ok=True)


def set_licence(db: DB, asset_id: str, terms_id: str, plan=None, attribution=None):
    if not db.one("select 1 from asset_licence where asset_id=?", asset_id):
        db.insert("asset_licence", asset_id=asset_id, terms_id=terms_id, obtained_on=now(), plan_at_creation=plan, attribution_text=attribution)
