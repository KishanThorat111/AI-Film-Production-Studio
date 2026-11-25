"""Storyboards and camera tests in a simple 3D set. A shot describes its set in metres; a separate Blender process renders it, with depth."""
from __future__ import annotations

import json
import subprocess
import sys
import time

from . import films
from .db import DB, ROOT, data_dir, new_id

WORKER = ROOT / "studio" / "engine" / "previs_worker.py"


def _spec(db: DB, sid):
    r = db.one("select sh.code,sh.scene_id,ss.spec_json from shot sh join shot_spec ss on ss.shot_id=sh.id where sh.id=? and ss.status<>'superseded' order by ss.rev desc limit 1", sid)
    return (r["code"], json.loads(r["spec_json"]), r["scene_id"]) if r else (None, None, None)


def scene_of(db: DB, film, sid):
    """The complete set for a shot: its own description on top of a set named in an earlier shot."""
    code, j, sc = _spec(db, sid)
    p = (j or {}).get("previs")
    if not p:
        return None
    if p.get("set"):
        for r in db.q("select ss.spec_json from shot sh join shot_spec ss on ss.shot_id=sh.id join scene s on s.id=sh.scene_id where s.film_id=? and ss.status<>'superseded' and sh.state<>'dropped' order by s.number,sh.ordinal", film):
            o = json.loads(r["spec_json"])
            b = o.get("previs") or {}
            # the set is the shot of that code, or the first shot that builds a set under that name
            if o["code"] != code and (o["code"] == p["set"] or b.get("set") == p["set"]) and (b.get("rooms") or b.get("props")):
                p = {**b, **p, "rooms": p.get("rooms") or b.get("rooms", []), "props": b.get("props", []) + [x for x in p.get("props", []) if x not in b.get("props", [])], "lights": p.get("lights") or b.get("lights", []), "figures": p.get("figures", [])}
                break
    return {**p, "code": code, "frames": int(p.get("frames") or j.get("frames") or 1)}


def possible(db: DB, film, ids) -> list[str]:
    return [i for i in ids if (_spec(db, i)[1] or {}).get("previs")]


def check(p) -> list[str]:
    """Mistakes that can be seen in the numbers."""
    out = []
    cam = p.get("camera", {})
    if not all(k in cam for k in ("x", "y", "z", "tx", "ty", "tz")):
        out.append("the camera needs a place (x,y,z) and a point it looks at (tx,ty,tz)")
    if not (p.get("rooms") or p.get("props") or p.get("figures")):
        out.append("there is nothing to see")
    for f in p.get("figures", []):
        if not 0.4 <= float(f.get("height", 1.7)) <= 2.4:
            out.append("a figure's height is not human")
    return out


def render_job(db: DB, jobs, jid, args):
    film, done = args["film"], []
    fl = films.get(db, film)
    for n, sid in enumerate(args["shots"]):
        p = scene_of(db, film, sid)
        bad = check(p)
        if bad:
            done.append({"shot": sid, "error": "; ".join(bad)})
            continue
        out = films.blocking_dir(db, film) / sid.replace(":", "_")
        tmp = data_dir() / "tmp" / f"previs_{new_id('v')}.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"scene": p, "fps": fl["fps_num"], "size": [fl["width"], fl["height"]], "out": str(out), "moving": bool(p.get("camera", {}).get("to")) and p["frames"] > 1}), encoding="utf-8")
        pr = subprocess.Popen([sys.executable, str(WORKER), str(tmp)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        t0 = time.time()
        while pr.poll() is None:
            time.sleep(.5)
            jobs.progress(jid, n, len(args["shots"]), f"previs of {p['code']}, {time.time() - t0:.0f} s")
            if jobs.stopping(jid):
                pr.kill()
                raise KeyboardInterrupt
            if time.time() - t0 > 1800:
                pr.kill()
                raise RuntimeError("the 3D worker took longer than half an hour")
        tmp.unlink(missing_ok=True)
        if pr.returncode != 0:
            done.append({"shot": sid, "error": (pr.stderr.read().strip().splitlines() or ["the 3D worker stopped"])[-1][:200]})
        else:
            done.append({"shot": sid, "folder": out.name, "seconds": round(time.time() - t0, 1)})
    if all("error" in d for d in done):
        raise RuntimeError("; ".join(f"{d['shot']}: {d['error']}" for d in done)[:400])
    return {"shots": done}
