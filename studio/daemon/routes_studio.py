"""The studio itself: connectors, money, readiness, backup, library, learning, agents, log."""
from __future__ import annotations

import json
import time
from pathlib import Path

import psutil
from fastapi import File, Form, Request, UploadFile
from fastapi.responses import FileResponse, Response

from ..agents import dispatch, service
from ..connectors.adapters import REGISTRY
from ..connectors.broker import Refused
from ..core import assets, backup, common, cut, films, flow, ops, preflight, voice
from ..core.db import new_id, now
from ..engine import synth
from .app import BadRequest, Context, disk_free_gb, number


def add(app, c: Context):
    db, br, jobs = c.db, c.br, c.jobs

    # ------------------------------------------------ connectors
    @app.get("/api/connectors")
    def connectors():
        out = []
        for k in db.q("select c.*,s.authentication,s.availability from connector c join connector_status s on s.id=c.id where retired_at is null"):
            A = REGISTRY[k["adapter"]]
            caps = []
            for cid, v in A.capabilities.items():
                pol = db.one("select * from connector_capability where connector_id=? and capability=?", k["id"], cid) or {}
                last = db.one("select outcome,checked_at,detail_json from connector_check where connector_id=? and kind='capability' and capability=? order by id desc limit 1", k["id"], cid)
                caps.append({"id": cid, "title": v["title"], "generates": v["generates"], "forced_block": v.get("forced_block"), "policy": pol.get("policy", "blocked"), "reason": pol.get("policy_reason"), "result": last["outcome"] if last else None, "detail": json.loads(last["detail_json"]) if last else None})
            ent = db.one("select outcome,detail_json from connector_check where connector_id=? and kind='entitlement' order by id desc limit 1", k["id"])
            auth = db.one("select detail_json,checked_at from connector_check where connector_id=? and kind='auth' order by id desc limit 1", k["id"])
            good = db.val("select max(checked_at) from connector_check where connector_id=? and kind='auth' and outcome='ok'", k["id"])
            out.append({"id": k["id"], "title": A.title, "key_help": A.key_help, "fields": A.config_fields, "config": json.loads(k["config_json"]), "configured": bool(k["secret_ref"]), "hint": k["secret_hint"], "authentication": k["authentication"], "last_worked": good,
                        "availability": k["availability"], "enabled": bool(k["generation_enabled"]), "capabilities": caps, "unit": A.unit, "entitlement": {"outcome": ent["outcome"], **json.loads(ent["detail_json"])} if ent else None,
                        "auth_message": json.loads(auth["detail_json"]).get("message") if auth else None, "tested_at": auth["checked_at"] if auth else None,
                        "usage": db.one("select tier,unit,used,quota,resets_on,taken_at,source from usage_snapshot where connector_id=? and source='api' order by id desc limit 1", k["id"]),
                        "history": db.q("select kind,capability,outcome,checked_at from connector_check where connector_id=? order by id desc limit 12", k["id"])})
        return {"connectors": out, "manual": db.q("select id,surface,verified_on,expires_on,source_url from provider_profile where kind like '%manual'")}

    @app.post("/api/connectors/{cid}/key")
    async def key(cid: str, req: Request):
        br.set_key(cid, (await c.json(req, "key"))["key"])
        return {"ok": True}

    @app.delete("/api/connectors/{cid}/key")
    def unkey(cid: str):
        br.remove_key(cid)
        return {"ok": True}

    @app.post("/api/connectors/{cid}/test")
    def test(cid: str):
        return {"checks": br.test(cid)}

    @app.post("/api/connectors/{cid}/enable")
    async def enable(cid: str, req: Request):
        br.set_enabled(cid, bool((await c.json(req)).get("on")))
        return {"ok": True}

    @app.post("/api/connectors/{cid}/config")
    async def config(cid: str, req: Request):
        br.set_config(cid, await c.json(req))
        return {"ok": True}

    @app.post("/api/connectors/{cid}/capability")
    async def capability(cid: str, req: Request):
        b = await c.json(req, "capability")
        br.set_capability(cid, b["capability"], bool(b.get("allowed")), b.get("reason") or "set by you")
        return {"ok": True}

    # ------------------------------------------------ money
    @app.get("/api/money")
    def money(req: Request):
        m = br.money()
        film = films.current(db)
        m["requests"] = db.q("select r.id,r.capability,r.state,r.outcome_basis,r.est_amount,r.actual_amount,r.created_at,e.unit,e.film_id,p.surface,r.cost_class from api_request r join spend_envelope e on e.id=r.envelope_id join provider_profile p on p.id=r.profile_id order by r.rowid desc limit 40")
        m["allowances"] = db.q("select id,film_id,purpose,limit_amount,unit,closed_at from spend_envelope where closed_at is null")
        m["film"] = film
        m["cap_left"] = {k: films.cap_left(db, k) for k in films.ALLOWANCE}
        return m

    @app.post("/api/money/allowance")
    async def allowance(req: Request):
        b = await c.json(req, "kind", "usd")
        try:
            musd = round(float(b["usd"]) * 1e6)
        except (TypeError, ValueError):
            raise BadRequest("write an amount in dollars") from None
        return {"id": films.open_allowance(db, c.film(req), b["kind"], musd)}

    @app.post("/api/money/credits")
    async def release_credits(req: Request):
        b = await c.json(req, "service", "amount")
        return {"id": films.release_credits(db, c.film(req), b["service"], number(b["amount"], "credits"))}

    @app.post("/api/money/report")
    async def report_usage(req: Request):
        b = await c.json(req, "used", "quota")
        db.insert("usage_snapshot", connector_id="elevenlabs", source="user_reported", tier="starter", unit="credits", used=number(b["used"], "used", 0), quota=number(b["quota"], "quota", 1), raw_json={}, taken_at=now())
        return {"ok": True}

    @app.post("/api/money/charge")
    async def charge(req: Request):
        b = await c.json(req, "provider", "kind", "amount", "currency")
        try:
            minor = round(float(b["amount"]) * 100)
        except (TypeError, ValueError):
            raise BadRequest("write an amount") from None
        db.insert("ledger_charge", id=new_id("lc"), provider=b["provider"][:40], kind=b["kind"], amount_minor=minor, currency=b["currency"][:3].upper(), period=b.get("period"), confirmed_by="user", charged_on=b.get("date") or now()[:10])
        return {"ok": True}

    @app.post("/api/pause/{pid}")
    async def pause(pid: str, req: Request):
        flow.resolve_pause(db, pid, (await c.json(req, "resolution"))["resolution"])
        return {"ok": True}

    @app.post("/api/unknown/{rid}")
    async def unknown(rid: str, req: Request):
        b = await c.json(req, "action")
        if b["action"] == "reconcile":
            return await c.work(lambda: br.reconcile(rid))
        if b["action"] not in ("charged", "free"):
            raise BadRequest("charged or free")
        br.settle_unknown(rid, b["action"] == "charged")
        return {"ok": True}

    # ------------------------------------------------ readiness and health
    @app.post("/api/preflight/{stage}")
    async def run_preflight(stage: str, req: Request):
        if stage not in preflight.STAGES:
            raise BadRequest("unknown stage")
        film = c.film(req)
        return await c.work(lambda: preflight.run(db, film, stage))

    @app.get("/api/preflight")
    def last_preflight(req: Request):
        r = db.one("select * from preflight_run where film_id=? order by rowid desc limit 1", c.film(req))
        return {"run": r, "items": [{**i, "evidence": json.loads(i["evidence_json"])} for i in db.q("select * from preflight_item where run_id=?", r["id"])] if r else []}

    @app.get("/api/health")
    def health():
        return ops.health(db, jobs, app.state)

    @app.get("/api/machine")
    def machine():
        m = psutil.virtual_memory()
        return {"ram_free_gb": round(m.available / 2 ** 30, 1), "ram_total_gb": round(m.total / 2 ** 30, 1), "disk_free_gb": disk_free_gb(), "studio_mb": psutil.Process().memory_info().rss // 2 ** 20}

    # ------------------------------------------------ jobs
    @app.get("/api/jobs")
    def job_list():
        return {"jobs": [{**j, "progress": json.loads(j["progress_json"] or "{}"), "result": json.loads(j["result_json"] or "null")} for j in db.q("select id,kind,state,progress_json,error,film_id,created_at,started_at,ended_at,result_json from media_job order by rowid desc limit 40")], "stalled": [x["id"] for x in jobs.stalled()]}

    @app.post("/api/jobs/{jid}/cancel")
    def cancel(jid: str):
        jobs.cancel(jid)
        return {"ok": True}

    @app.post("/api/jobs/{jid}/resume")
    def resume(jid: str):
        jobs.resume(jid)
        return {"ok": True}

    # ------------------------------------------------ backup
    @app.get("/api/backup")
    def backup_state():
        return backup.state(db)

    @app.post("/api/backup")
    async def backup_now(req: Request):
        ids = [t["id"] for t in db.q("select id from backup_target where enabled=1")]
        b = await c.json(req) if req.headers.get("content-length") not in (None, "0") else {}
        return {"jobs": [jobs.add("backup", {"target": t}, key=f"backup:{t}", ram=100, resource="io") for t in ([b["target"]] if b.get("target") else ids)]}

    @app.post("/api/backup/adopt")
    def adopt():
        backup.adopt(db)
        return {"ok": True}

    @app.post("/api/backup/target")
    async def backup_target(req: Request):
        b = await c.json(req, "path")
        return {"id": backup.add_target(db, b["path"], b.get("label", ""))}

    @app.post("/api/backup/target/{tid}/off")
    def backup_off(tid: str):
        db.run("update backup_target set enabled=0 where id=?", tid)
        db.event("user", "backup.target_off", "backup_target", tid)
        return {"ok": True}

    @app.post("/api/backup/{tid}/drill")
    def drill(tid: str):
        return {"job": jobs.add("drill", {"target": tid}, key=f"drill:{tid}", ram=150, resource="io")}

    # ------------------------------------------------ library and rights
    @app.get("/api/library")
    def library(q: str = ""):
        return {"items": common.search_library(db, q), "terms": db.q("select * from licence_terms order by id"), "candidates": [x for x in cut.library_ready(db) if not x["admitted"]]}

    @app.post("/api/library/terms/{tid}/verify")
    def verify(tid: str):
        common.verify_terms(db, tid)
        return {"ok": True}

    @app.post("/api/library/admit")
    async def admit(req: Request):
        b = await c.json(req, "asset")
        prov = db.val("select provenance_json from asset where id=?", b["asset"])
        if prov is None:
            raise assets.Missing("there is no such file")
        prov = json.loads(prov)
        title = b.get("title") or prov.get("synth") or prov.get("title") or "untitled"
        info = synth.SOUNDS.get(title) if "synth" in prov else None
        common.admit(db, b["asset"], b.get("scope", "studio"), b.get("scope_ref"), b.get("kind") or (info[1] if info else prov.get("library_kind") or "foley"), title, b.get("tags") or (info[2] if info else title.lower().split()))
        return {"ok": True}

    @app.post("/api/library/import")
    async def library_import(file: UploadFile = File(...), terms: str = Form(""), title: str = Form(""), kind: str = Form("foley"), credit: str = Form("")):
        name = title.strip() or Path(file.filename or "sound").stem
        return await c.with_upload(file, lambda p: voice.import_library(db, p, terms, name, kind, credit))

    # ------------------------------------------------ learning and versions
    @app.get("/api/learning")
    def learning(req: Request):
        film = c.film(req)
        return {"lessons": db.q("select * from lesson order by rowid desc"), "proposals": db.q("select * from policy_proposal order by rowid desc"),
                "stats": db.q("select t.kind,count(distinct t.id) targets,sum(a.attempt_no=1 and a.outcome='accepted') first_try,sum(a.outcome='accepted') accepted,count(a.id) attempts from generation_target t left join attempt_counted a on a.target_id=t.id where t.film_id=? group by t.kind", film),
                "slots": [{**s, "versions": db.q("select * from slot_version where slot_id=? order by version_no desc", s["id"])} for s in db.q("select * from slot where film_id=?", film)], "times": ops.estimates(db)}

    @app.post("/api/lesson/{lid}")
    async def lesson(lid: str, req: Request):
        st = (await c.json(req, "status"))["status"]
        db.run("update lesson set status=?,approved_by=? where id=?", st, "user" if st == "adopted" else None, lid)
        db.event("user", "lesson." + st, "lesson", lid)
        return {"ok": True}

    @app.post("/api/version/{vid}/{action}")
    async def version(vid: str, action: str, req: Request):
        if action == "restore":
            return {"id": flow.restore_version(db, vid, (await c.json(req)).get("reason") or "your choice")}
        if action not in ("approve", "decline"):
            raise BadRequest("approve, decline or restore")
        flow.decide_version(db, vid, action == "approve")
        return {"ok": True}

    # ------------------------------------------------ agents
    @app.get("/api/agents")
    def agents(req: Request):
        return ops.agents(db, c.film(req))

    @app.post("/api/agents/run/{task}")
    async def agent_run(task: str):
        return {"job": dispatch.start(db, jobs, task)}

    @app.post("/api/agents/run-waiting")
    async def agent_batch(req: Request):
        b = await c.json(req)
        return {"jobs": dispatch.start_batch(db, jobs, c.film(req), number(b.get("most", 3), "number of tasks", 1, 3))}

    @app.post("/api/agents/allowance")
    async def agent_allowance(req: Request):
        b = await c.json(req, "tokens")
        dispatch.set_allowance(db, number(b["tokens"], "tokens", 0, 5_000_000))
        return {"ok": True}

    @app.post("/api/agents/resume")
    def agent_resume():
        dispatch.resume(db)
        return {"ok": True}

    @app.post("/api/task/{tid}/cancel")
    def task_cancel(tid: str):
        db.run("update task set state='cancelled',closed_at=? where id=? and state in('ready','blocked')", now(), tid)
        db.event("user", "task.cancelled", "task", tid)
        return {"ok": True}

    @app.post("/api/finding/{fid}")
    async def finding(fid: str, req: Request):
        v = (await c.json(req, "verdict"))["verdict"]
        db.run("update finding set human_verdict=? where id=?", v, fid)
        db.event("user", "finding.judged", "finding", fid, {"verdict": v})
        return {"ok": True}

    # ------------------------------------------------ log and files
    @app.get("/api/log")
    def log(before: int = 10 ** 12):
        return {"events": db.q("select seq,at,actor,verb,target_type,target_id,payload_json from event where seq<? order by seq desc limit 150", before)}

    @app.get("/media/{aid}")
    def media(aid: str):
        p = assets.path_of(db, aid)
        if not p.exists():
            raise assets.Missing("this file is missing on the disk; restore it from a copy (Backup page)")
        return FileResponse(p, media_type=assets.MIME.get(p.suffix.lower(), "application/octet-stream"))

    @app.get("/view/{aid}/{name}")
    def view(aid: str, name: str):
        ok = lambda s: "".join(x for x in s if x.isalnum() or x in "_-")
        p = common.views_dir(ok(aid)) / (ok(name) + ".png")
        return FileResponse(p) if p.exists() else Response(status_code=404)
