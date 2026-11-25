"""Readiness: only what the chosen workflow really needs. A login alone is never 'ready'."""
from __future__ import annotations

import json
import shutil
import subprocess

import psutil

from . import flow
from .db import DB, data_dir, new_id, now, sha

STAGES = {"benchmark": ["local", "stills", "voice", "sfx", "video", "inspection", "rights"], "stills": ["stills"], "voice": ["voice"], "sfx": ["sfx"], "video": ["video"], "inspection": ["inspection"], "edit": ["local", "rights"]}
CAP = {"voice": ("elevenlabs", "tts", "voice_manual"), "sfx": ("elevenlabs", "sfx", "sfx_manual"), "inspection": ("gemini_inspect", "inspect_video", "inspect_manual")}


def _last(db, cid, kind, cap=None):
    return db.one("select * from connector_check where connector_id=? and kind=? and coalesce(capability,'')=? order by id desc limit 1", cid, kind, cap or "")


def connector_state(db: DB, cid, cap) -> tuple[str, dict, str | None]:
    c = db.one("select * from connector where id=?", cid)
    if not c or not c["secret_ref"]:
        return "blocked", {"credentials": False}, "Open Connectors and enter the key"
    a = _last(db, cid, "auth")
    if not a:
        return "unknown", {"tested": False}, "Open Connectors and press Test"
    if a["outcome"] != "ok":
        return "blocked", {"auth": a["outcome"], "detail": json.loads(a["detail_json"])}, "Fix the key in Connectors and test again"
    k = _last(db, cid, "capability", cap)
    if not k or k["outcome"] != "ok":
        return "blocked", {"capability": k["outcome"] if k else "not tested", "detail": json.loads(k["detail_json"]) if k else {}}, "This key or plan does not offer the capability; see Connectors"
    pol = db.one("select * from connector_capability where connector_id=? and capability=?", cid, cap)
    if not pol or pol["policy"] != "allowed":
        return "blocked", {"policy": pol["policy_reason"] if pol else "not allowed"}, "Allow the capability in Connectors"
    if not c["generation_enabled"]:
        return "blocked", {"enabled": False}, "Connected and tested, but switched off. Enable it in Connectors when you want to use it"
    return "ready", {"auth": "ok", "capability": json.loads(k["detail_json"]), "checked_at": a["checked_at"]}, None


def _manual(db, pid):
    p = db.one("select * from provider_profile where id=?", pid)
    ok = p and p["expires_on"] >= now()[:10]
    return ("manual_ready", {"surface": p["surface"], "verified": p["verified_on"]}, None) if ok else ("blocked", {"profile": "expired" if p else "missing"}, "The facts about this manual surface must be verified again")


def run(db: DB, film, stage="benchmark") -> dict:
    items = []

    def add(req, service, mode, res, cid=None, cap=None, required=True):
        st, ev, nxt = res
        items.append({"requirement": req, "service": service, "mode": mode, "connector_id": cid, "capability": cap, "state": st, "evidence": ev, "next_step": nxt})

    for s in STAGES[stage]:
        if s == "local":
            ff = shutil.which("ffmpeg")
            enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout if ff else ""
            add("ffmpeg with NVENC", "local", "local", ("ready", {"path": ff}, None) if "hevc_nvenc" in enc and "h264_nvenc" in enc else ("blocked", {}, "Install ffmpeg with NVENC"))
            try:
                import wgpu
                info = dict(wgpu.gpu.request_adapter_sync(power_preference="high-performance").info)
                add("Graphics card", "local", "local", ("ready", {"device": info["device"], "backend": info["backend_type"]}, None) if info["adapter_type"] == "DiscreteGPU" else ("blocked", info, "The dedicated GPU is not available"))
            except Exception as e:
                add("Graphics card", "local", "local", ("blocked", {"error": type(e).__name__}, "Check the graphics driver"))
            m = psutil.virtual_memory()
            free = m.available / 2 ** 30
            add("Memory for rendering", "local", "local", ("ready", {"available_gb": round(free, 1)}, None) if free > 1.2 else ("blocked", {"available_gb": round(free, 1)}, "Close Chrome or other programs; rendering needs about 1.2 GB free"))
            d = shutil.disk_usage(data_dir()).free / 2 ** 30
            add("Disk space", "local", "local", ("ready", {"free_gb": round(d)}, None) if d > 40 else ("blocked", {"free_gb": round(d)}, "Less than 40 GB free; renders are refused"))
            from . import backup
            bs = backup.state(db)
            add("Backup", "local", "local", ("ready", {"newest_hours": round(bs["newest_hours"], 1), "separate_drives": bs["separate_drives"]}, None) if bs["level"] == "good" else
                ("unknown", {"newest_hours": bs["newest_hours"], "separate_drives": bs["separate_drives"]}, "Make a copy on the Backup page" if bs["newest_hours"] is None or bs["newest_hours"] > 24 else "Add a second place on another drive"))
            sp = (data_dir() / "models/whisper-base.en/model.bin").exists()
            add("Speech check (words against script)", "local", "local", ("ready", {}, None) if sp else ("unknown", {}, "Optional: without it, word checks rely on your ears"))
            from ..inspect import face
            add("Face comparison", "local", "local", ("ready", {}, None) if face.available() else ("unknown", {}, "Optional: without it, identity is judged by eye only"))
        elif s == "stills":
            if flow.mode(db, film, "stills") != "api":
                add("Still images", "stills", "api", ("blocked", {"mode": "not switched on"}, "Set still images to API mode on the Status page"), None, "image_generate")
            else:
                ready = []
                for c in db.q("select id from connector where id in('google_images','openai_images','bfl_images')"):
                    st = connector_state(db, c["id"], "image_generate")
                    if st[0] == "ready":
                        ready.append(c["id"])
                add("Still images", "stills", "api", ("ready", {"providers": ready}, None) if ready else ("blocked", {"providers": []}, "Connect, test and enable one image provider"), ready[0] if ready else None, "image_generate")
            cap = db.one("select h.limit_musd-coalesce((select sum(coalesce(actual_amount,est_amount)) from api_request r join spend_envelope e on e.id=r.envelope_id where e.unit='musd' and r.capability like 'image%' and r.state in('succeeded','reserved','sent','unknown')),0) left_ from hard_cap h where category='image'")
            left = cap["left_"] if cap else 0
            add("Image budget", "stills", "local", ("ready", {"usd_left": round(left / 1e6, 3)}, None) if left > 20_000 else ("blocked", {"usd_left": round(left / 1e6, 3)}, "The $10 cap is used up; nothing more will be sent"))
        elif s in ("voice", "sfx"):
            cid, cap, man = CAP[s]
            md = flow.mode(db, film, s)
            name = "Voices" if s == "voice" else "Sound effects"
            if md == "manual":
                add(name, s, "manual", _manual(db, man), None, cap)
            else:
                add(name, s, "api", connector_state(db, cid, cap), cid, cap)
                u = db.one("select * from usage_snapshot where connector_id=? and source='api' order by id desc limit 1", cid)
                env = db.one("select coalesce(sum(limit_amount),0) l from spend_envelope where film_id=? and unit='credits' and closed_at is null", film)["l"]
                if not u:
                    add(f"{name}: included credits", s, "api", ("unknown", {}, "Test the connection so the real balance is read"), cid, cap)
                else:
                    lft = u["quota"] - u["used"]
                    add(f"{name}: included credits", s, "api", ("ready", {"left": lft, "read_at": u["taken_at"], "released": env}, None) if lft > 500 and env > 0 else
                        ("blocked", {"left": lft, "released": env}, "Release some included credits on the Budget page" if lft > 500 else "Not enough included credits; wait for the reset or use manual mode"), cid, cap)
        elif s == "video":
            add("Gemini video (by hand)", "video", "manual", _manual(db, "gemini_app"))
        elif s == "inspection":
            if flow.mode(db, film, "inspection") != "api":
                add("Gemini inspection", "inspection", "manual", ("not_required", {"note": "off; local inspection works without it"}, None), None, "inspect_video")
            else:
                add("Gemini inspection", "inspection", "api", connector_state(db, "gemini_inspect", "inspect_video"), "gemini_inspect", "inspect_video")
        elif s == "rights":
            un = db.q("select id,source from licence_terms where verified_by is null")
            used = db.q("""select distinct l.source from asset_licence al join licence_terms l on l.id=al.terms_id where l.verified_by is null""")
            add("Licence records", "rights", "local", ("ready", {"unverified_unused": [u["source"] for u in un]}, None) if not used else ("blocked", {"unverified": [u["source"] for u in used]}, "Read and confirm these licence records on the Library page before a final mix"))
    rid = new_id("pf")
    with db.tx():
        db.insert("preflight_run", id=rid, film_id=film, stage=stage, basis_hash=sha([[i["requirement"], i["state"]] for i in items]), created_at=now())
        for i in items:
            db.insert("preflight_item", run_id=rid, requirement=i["requirement"], service=i["service"], mode=i["mode"], connector_id=i["connector_id"], capability=i["capability"], state=i["state"], evidence_json=i["evidence"], next_step=i["next_step"])
    bad = [i for i in items if i["state"] == "blocked"]
    return {"id": rid, "stage": stage, "at": now(), "items": items, "summary": "ready" if not bad else f"{len(bad)} need attention"}
