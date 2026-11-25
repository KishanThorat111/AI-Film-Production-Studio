"""The single door to paid services. Reserve -> recheck -> send -> store -> record. It never retries on its own after something may have been charged."""
from __future__ import annotations

import json
import time

from ..core import assets, flow
from ..core.db import DB, RuleRefused, dumps, new_id, now, sha
from .adapters import REGISTRY, CostUnknown
from .http import REFUSED_BEFORE_WORK, NetError, scrub
from .secrets import SecretError, WindowsVault, fingerprint, hint

SERVICE = {"tts": "voice", "dialogue": "voice", "sfx": "sfx", "image_generate": "stills", "image_edit": "stills", "inspect_video": "inspection", "inspect_audio": "inspection"}
EXT = {"audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/ogg": ".opus", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class Refused(Exception):
    """Nothing was sent. The message explains why in plain words."""


class Broker:
    def __init__(self, db: DB, vault=None, throttle=time.sleep):
        self.db, self.vault, self.sleep = db, vault or WindowsVault(), throttle

    # -------------------------------------------------------- connectors
    def _row(self, cid):
        r = self.db.one("select * from connector where id=? and retired_at is null", cid)
        if not r:
            raise Refused("unknown connector")
        return r

    def adapter(self, cid):
        r = self._row(cid)
        if not r["secret_ref"]:
            raise Refused("no key has been entered for this connector")
        try:
            return REGISTRY[r["adapter"]](self.vault.get(r["secret_ref"]), json.loads(r["config_json"]))
        except SecretError as e:
            raise Refused(str(e)) from None

    def set_key(self, cid, secret: str):
        secret = secret.strip()
        ref = self.vault.put(cid, secret)
        with self.db.tx():
            self.db.run("update connector set generation_enabled=0,enabled_by=null,enabled_at=null where id=?", cid)   # a new key must be tested and enabled again
            self.db.run("update connector set secret_ref=?,secret_fingerprint=?,secret_hint=? where id=?", ref, fingerprint(secret), hint(secret), cid)
            self.db.event("user", "connector.key_set", "connector", cid)

    def remove_key(self, cid):
        r = self._row(cid)
        self.vault.delete(r["secret_ref"])
        with self.db.tx():
            self.db.run("update connector set generation_enabled=0,enabled_by=null,enabled_at=null,secret_ref=null,secret_fingerprint=null,secret_hint=null where id=?", cid)
            self.db.event("user", "connector.key_removed", "connector", cid)

    def set_config(self, cid, cfg: dict):
        allowed = {f["name"] for f in REGISTRY[self._row(cid)["adapter"]].config_fields}
        self.db.run("update connector set config_json=?,generation_enabled=0,enabled_by=null,enabled_at=null where id=?", dumps({k: v for k, v in cfg.items() if k in allowed}), cid)
        self.db.event("user", "connector.config", "connector", cid, {k: v for k, v in cfg.items() if k in allowed})

    def set_enabled(self, cid, on: bool):
        if on:
            a = self.db.one("select outcome from connector_check where connector_id=? and kind='auth' order by id desc limit 1", cid)
            if not a or a["outcome"] != "ok":
                raise Refused("test the connection successfully before enabling it")
            self.db.run("update connector set generation_enabled=1,enabled_by='user',enabled_at=? where id=?", now(), cid)
        else:
            self.db.run("update connector set generation_enabled=0,enabled_by=null,enabled_at=null where id=?", cid)
        self.db.event("user", "connector.enabled" if on else "connector.disabled", "connector", cid)

    def set_capability(self, cid, cap, allowed: bool, reason):
        forced = REGISTRY[self._row(cid)["adapter"]].capabilities[cap].get("forced_block")
        if allowed and forced:
            raise Refused(forced)
        self.db.run("""insert into connector_capability(connector_id,capability,policy,policy_reason,set_by) values(?,?,?,?,'user')
            on conflict(connector_id,capability) do update set policy=excluded.policy,policy_reason=excluded.policy_reason""", cid, cap, "allowed" if allowed else "blocked", reason)
        self.db.event("user", "capability.set", "connector", cid, {"capability": cap, "allowed": allowed})

    def test(self, cid) -> list[dict]:
        """Free, read-only checks. Nothing is generated."""
        try:
            checks, usage = self.adapter(cid).checks()
        except Refused as e:
            return [{"kind": "auth", "outcome": "denied", "detail": {"message": str(e)}}]
        except Exception as e:                      # an unexpected reply must not break the page, and must not count as a pass
            checks, usage = [], None
            self.db.insert("connector_check", connector_id=cid, kind="auth", outcome="error", detail_json={"message": scrub(f"{type(e).__name__}: {e}")[:300]}, checked_at=now())
        with self.db.tx():
            for c in checks:
                self.db.insert("connector_check", connector_id=cid, kind=c.kind, capability=c.capability, outcome=c.outcome, detail_json=c.detail, checked_at=now())
            if usage:
                self.db.insert("usage_snapshot", connector_id=cid, source="api", tier=usage["tier"], unit=usage["unit"], used=usage["used"], quota=usage["quota"],
                               resets_on=str(usage["resets_on"]) if usage["resets_on"] else None, raw_json=usage["raw"], taken_at=now())
            self.db.event("system", "connector.tested", "connector", cid, {"outcomes": [[c.kind, c.capability, c.outcome] for c in checks]})
        return [{"kind": c.kind, "capability": c.capability, "outcome": c.outcome, "detail": c.detail} for c in checks]

    # -------------------------------------------------------- paid requests
    def estimate(self, cid, capability, spec) -> int:
        try:
            return self.adapter_for_estimate(cid).estimate(capability, spec)
        except CostUnknown as e:
            raise Refused(f"the cost cannot be established in advance ({e}); nothing was sent") from None

    def adapter_for_estimate(self, cid):
        r = self._row(cid)
        return REGISTRY[r["adapter"]]("-" * 20, json.loads(r["config_json"]))

    def _fresh(self, cid, capability):
        """Verify again if the last look is more than ten minutes old, or older than the last charge."""
        last = self.db.one("select checked_at from connector_check where connector_id=? and kind='auth' order by id desc limit 1", cid)
        stale = not last or self.db.val("select ? < datetime('now','-10 minutes')", last["checked_at"])
        charged = self.db.val("""select max(e.seq) from event e join api_request r on r.id=e.target_id join provider_profile p on p.id=r.profile_id
            where e.verb='api.succeeded' and p.connector_id=?""", cid) or 0
        looked = self.db.val("select max(seq) from event where verb='connector.tested' and target_id=?", cid) or 0
        if stale or charged > looked:
            self.test(cid)
        bad = self.db.one("""select kind,outcome from connector_check where connector_id=? and (kind='auth' or (kind='capability' and capability=?))
            and id in (select max(id) from connector_check where connector_id=? group by kind,capability) and outcome<>'ok'""", cid, capability, cid)
        if bad:
            raise Refused(f"the connection check says '{bad['outcome']}' for {bad['kind'] if bad['kind'] == 'auth' else capability}")

    def run(self, *, film, profile, capability, envelope, attempt, spec, refs=(), requested_by, cost_class="first", purpose="production",
            voice_package=None, sfx_package=None, shot_spec=None, store=True) -> dict:
        db = self.db
        cid = db.val("select connector_id from provider_profile where id=?", profile)
        if not cid:
            raise Refused("that surface is manual")
        self._fresh(cid, capability)
        est = self.estimate(cid, capability, spec)
        rid = new_id("rq")
        try:
            with db.tx():
                db.insert("api_request", id=rid, envelope_id=envelope, profile_id=profile, shot_spec_id=shot_spec, purpose=purpose, prompt_rev=1,
                          prompt=str(spec.get("prompt") or spec.get("text") or "")[:20000], request_json=spec, request_hash=sha({"p": profile, "c": capability, "s": spec, "r": [r for r in refs]}),
                          requested_by=requested_by, est_amount=est, state="reserved", created_at=now(), idem_key=rid, capability=capability, cost_class=cost_class,
                          voice_package_id=voice_package, sfx_package_id=sfx_package, attempt_id=attempt)
                for i, a in enumerate(refs):
                    db.insert("api_request_ref", request_id=rid, ordinal=i, asset_id=a, role="reference")
                db.event(requested_by, "api.reserved", "api_request", rid, {"capability": capability, "est": est})
            # references are read before anything leaves, so a missing file can never look like a lost reply
            ref_bytes = []
            for a in refs:
                p = assets.path_of(db, a)
                ref_bytes.append((p.read_bytes(), assets.MIME.get(p.suffix.lower(), "application/octet-stream")))
            db.run("update api_request set state='sent',sent_at=? where id=?", now(), rid)
        except RuleRefused as e:
            self._never_sent(rid)
            raise Refused(str(e)) from None
        except OSError as e:
            self._never_sent(rid)
            raise Refused(f"a reference picture could not be read ({type(e).__name__}); nothing was sent") from None
        svc, t0 = SERVICE[capability], time.time()
        try:
            res = self._send_with_backoff(cid, capability, spec, ref_bytes, rid)
        except NetError as e:
            return self._failed(rid, film, svc, e)
        except Exception as e:       # reply arrived in a shape we did not expect: it may have been charged
            return self._failed(rid, film, svc, NetError("after_send", f"unexpected reply: {type(e).__name__}"))
        db.insert("time_log", id=new_id("tm"), film_id=film, activity="provider_wait", basis_json={"profile": profile, "capability": capability}, seconds=round(time.time() - t0, 2), units=1, unit="request", ref=rid, at=now())
        return self._succeeded(rid, cid, res, spec, store, capability)

    def _never_sent(self, rid):
        self.db.run("update api_request set state='failed',actual_amount=0,outcome_basis='not_sent',ended_at=? where id=? and state='reserved'", now(), rid)

    def _send_with_backoff(self, cid, capability, spec, refs, rid):
        ad = self.adapter(cid)
        ad.on_job = lambda job: self.db.run("update api_request set provider_job_id=? where id=?", str(job)[:200], rid)
        for n in range(4):
            try:
                return ad.send(capability, spec, refs)
            except NetError as e:
                # 429 means refused before any work. Nothing else is repeated automatically.
                job = self.db.val("select provider_job_id from api_request where id=?", rid)
                if e.kind == "http" and e.status == 429 and not job and n < 3 and e.code not in ("insufficient_credits", "quota_exceeded") and (e.retry_after or 0) <= 60:
                    self.sleep(max(2 ** n * 2, e.retry_after or 0))
                    continue
                raise

    def _failed(self, rid, film, svc, e: NetError):
        db = self.db
        msg = scrub(str(e))
        row = db.one("select * from api_request where id=?", rid)
        kept = [f"request {rid}"] + (["the picture or sound that already exists is untouched"] if row["attempt_id"] else [])
        if e.code == "refused":
            # refused before work = free. Refused after a finished answer (HTTP 200) counts as charged unless the provider says otherwise.
            cost = e.actual if e.actual is not None else 0 if e.status in REFUSED_BEFORE_WORK else row["est_amount"]
            db.run("update api_request set state='refused',actual_amount=?,outcome_basis=?,ended_at=? where id=?", cost, "refused_before_work" if not cost else "refused_after_work", now(), rid)
            flow.judge_attempt(db, row["attempt_id"], "refused", "system")
            pid, state, money = None, "refused", "no" if not cost else "possibly"
        elif e.kind == "before_send" or (e.kind == "http" and e.status in REFUSED_BEFORE_WORK):
            basis = "never_connected" if e.kind == "before_send" else "refused_before_work"
            db.run("update api_request set state='failed',actual_amount=0,outcome_basis=?,ended_at=? where id=?", basis, now(), rid)
            why = {401: "the key was not accepted", 402: "not enough credits", 403: "the key or plan does not allow this", 404: "the model or address was not found", 429: "the provider asks to wait"}.get(e.status, "the provider could not be reached" if basis == "never_connected" else "the provider did not accept the request")
            pid = flow.pause(db, film, svc, why, {"message": msg, "status": e.status, "code": e.code, "request": rid, "charged": "no", "safe": kept})
            state, money = "failed", "no"
        else:                                             # 5xx, lost reply, failed after acceptance: it may have been charged
            db.run("update api_request set state='unknown',ended_at=? where id=?", now(), rid)
            pid = flow.pause(db, film, svc, "the reply was lost; the provider may have charged for it", {"message": msg, "status": e.status, "request": rid, "job": row["provider_job_id"], "charged": "unknown", "safe": kept}, ("reconcile", "manual", "wait", "cancel"))
            state, money = "unknown", "unknown"
        db.event("system", f"api.{state}", "api_request", rid, {"message": msg, "status": e.status, "code": e.code})
        return {"request": rid, "state": state, "message": msg, "pause": pid, "charged": money}

    def _succeeded(self, rid, cid, res, spec, store, capability):
        db = self.db
        aid = None
        if store and res.data:
            ext = EXT.get((res.mime or "").split(";")[0], ".bin")
            if str(res.meta.get("output_format", "")).startswith("wav"):
                ext = ".wav"
            kind = "audio" if capability in ("tts", "dialogue", "sfx") else "image"
            origin = "api_audio" if kind == "audio" else "api_image"
            row = db.one("select connector_id,surface from provider_profile p join api_request r on r.profile_id=p.id where r.id=?", rid)
            aid, _ = assets.add_bytes(db, res.data, ext, kind=kind, origin=origin, provider=row["surface"],
                                      model=spec.get("model_id") or res.meta.get("model"), provenance={"request": rid, "job": res.job_id, "received_as": res.mime, "spec": spec}, actor="broker")
            db.insert("api_result", request_id=rid, asset_id=aid)
        db.run("update api_request set state='succeeded',actual_amount=?,provider_job_id=coalesce(?,provider_job_id),outcome_basis='answered',ended_at=? where id=?", res.actual, res.job_id, now(), rid)
        est = db.val("select est_amount from api_request where id=?", rid)
        db.event("broker", "api.succeeded", "api_request", rid, {"actual": res.actual, "est": est, "meta": {k: v for k, v in res.meta.items() if k in ("usage", "cost_header", "cost_credits", "output_format", "model", "finish")}})
        att = db.val("select attempt_id from api_request where id=?", rid)
        if aid:
            db.run("update attempt set result_asset_id=? where id=?", aid, att)
        out = {"request": rid, "state": "succeeded", "asset": aid, "text": res.text, "actual": res.actual, "estimate": est, "attempt": att, "meta": res.meta}
        if res.actual is not None and res.actual > est:
            # our upper bound was wrong: switch off until the user has seen it
            self.set_disabled_by_system(cid, f"a request cost {res.actual} but at most {est} was expected")
            out["warning"] = "cost exceeded the estimate; the integration was switched off"
        return out

    def set_disabled_by_system(self, cid, why):
        self.db.run("update connector set generation_enabled=0,enabled_by=null,enabled_at=null where id=?", cid)
        self.db.event("system", "connector.disabled", "connector", cid, {"why": why})

    def reconcile(self, rid) -> dict:
        db = self.db
        r = db.one("select r.*,p.connector_id from api_request r join provider_profile p on p.id=r.profile_id where r.id=?", rid)
        if not r or r["state"] != "unknown":
            raise Refused("nothing to reconcile")
        res = None
        try:
            res = self.adapter(r["connector_id"]).reconcile(r)
        except NetError as e:
            return {"state": "unknown", "message": scrub(str(e))}
        if res:
            return self._succeeded(rid, r["connector_id"], res, json.loads(r["request_json"]), True, r["capability"])
        return {"state": "unknown", "message": "the provider offers no way to look this up; check your account history and mark it by hand"}

    def settle_unknown(self, rid, charged: bool):
        """The user looked in the provider's account history and says whether it was charged."""
        db = self.db
        att = db.val("select attempt_id from api_request where id=?", rid)
        with db.tx():
            if charged:                  # paid, but the result never arrived: the attempt is used up
                db.run("update api_request set state='succeeded',actual_amount=est_amount,outcome_basis='user_checked_account' where id=? and state='unknown'", rid)
                if att and db.val("select outcome from attempt where id=?", att) == "pending":
                    flow.judge_attempt(db, att, "rejected", "user")
            else:
                db.run("update api_request set state='failed',actual_amount=0,outcome_basis='user_checked_account' where id=? and state='unknown'", rid)
        db.event("user", "api.settled", "api_request", rid, {"charged": charged})

    def recover_after_crash(self):
        """At start-up: anything left 'sent' had no recorded answer, so it may have been charged."""
        n = 0
        for r in self.db.q("select r.id,r.capability,e.film_id from api_request r join spend_envelope e on e.id=r.envelope_id where r.state='sent'"):
            self.db.run("update api_request set state='unknown',ended_at=? where id=?", now(), r["id"])
            flow.pause(self.db, r["film_id"], SERVICE[r["capability"]], "the application stopped while waiting for a reply", {"request": r["id"]}, ("reconcile", "manual", "wait", "cancel"))
            n += 1
        for r in self.db.q("select id from api_request where state='reserved'"):
            self._never_sent(r["id"])
        return n

    # -------------------------------------------------------- money view
    def money(self) -> dict:
        db = self.db
        out = {"caps": []}
        for h in db.q("select * from hard_cap"):
            caps = "('image_generate','image_edit')" if h["category"] == "image" else "('inspect_video','inspect_audio')"
            held = db.val(f"select coalesce(sum(est_amount),0) from api_request r join spend_envelope e on e.id=r.envelope_id where e.unit='musd' and r.state in('reserved','sent','unknown') and capability in {caps}")
            spent = db.val(f"select coalesce(sum(coalesce(actual_amount,est_amount)),0) from api_request r join spend_envelope e on e.id=r.envelope_id where e.unit='musd' and r.state='succeeded' and capability in {caps}")
            out["caps"].append({"category": h["category"], "limit": h["limit_musd"], "held": held, "spent": spent, "left": h["limit_musd"] - held - spent, "authority": h["authority"]})
        out["credits"] = db.q("""select e.id,e.purpose,e.limit_amount,coalesce(sum(case when r.state in('reserved','sent','unknown') then r.est_amount when r.state='succeeded' then coalesce(r.actual_amount,r.est_amount) else 0 end),0) used
            from spend_envelope e left join api_request r on r.envelope_id=e.id where e.unit='credits' and e.closed_at is null group by e.id""")
        out["by_class"] = db.q("select capability,cost_class,e.unit,count(*) n,sum(coalesce(actual_amount,est_amount)) amount from api_request r join spend_envelope e on e.id=r.envelope_id where r.state='succeeded' group by 1,2,3")
        out["manual_credits"] = db.val("select coalesce(sum(credits_confirmed),0) from audio_take where source='manual'")
        out["usage"] = db.q("select u.* from usage_snapshot u where u.id in (select max(id) from usage_snapshot group by connector_id,source)")
        out["charges"] = db.q("select * from ledger_charge order by charged_on desc")
        return out
