"""Films, their settings and their allowances. The program knows no film by name."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .db import DB, RuleRefused, data_dir, new_id, now

ALLOWANCE = {"image": ("pictures, all providers", 350_000), "inspection": ("Gemini inspection", 450_000)}
CAPS = {"image": "('image_generate','image_edit')", "inspection": "('inspect_video','inspect_audio')"}


def get(db: DB, film) -> dict:
    r = db.one("select * from film where id=?", film)
    if not r:
        raise RuleRefused("there is no such film")
    return r


def listing(db: DB):
    return db.q("""select f.id,f.title,f.logline,f.status,f.is_benchmark,f.created_at,u.title universe,(select count(*) from scene s where s.film_id=f.id) scenes,
        (select count(*) from shot sh join scene s on s.id=sh.scene_id where s.film_id=f.id) shots from film f join universe u on u.id=f.universe_id order by f.is_benchmark,f.created_at desc,f.rowid desc""")


def create(db: DB, title, *, logline="", universe=None, universe_title=None, fps=24, size=(1920, 1080), film_id=None, benchmark=False, status="development", profile=None, by="user") -> str:
    title = title.strip()
    if not 1 <= len(title) <= 120:
        raise RuleRefused("give the film a title")
    if fps not in (24, 25, 30):
        raise RuleRefused("24, 25 or 30 frames per second")
    fid = film_id or "F-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:24] + "-" + new_id("x")[-4:]
    with db.tx():
        if universe and not db.one("select 1 from universe where id=?", universe):
            db.insert("universe", id=universe, title=universe_title or title)
        if not universe:
            universe = db.insert("universe", id=new_id("un"), title=universe_title or title)
        if not benchmark and db.one("select 1 from film where universe_id=? and is_benchmark=1", universe):
            raise RuleRefused("benchmark universes are kept apart from real films")
        db.insert("film", id=fid, universe_id=universe, title=title, logline=logline.strip()[:400], fps_num=fps, width=size[0], height=size[1], is_benchmark=int(benchmark), status=status, profile_json=profile or {}, created_at=now())
        db.event(by, "film.created", "film", fid, {"title": title})
    return fid


def current(db: DB) -> str | None:
    """The film the pages show. The user's choice; otherwise the newest real film, otherwise a benchmark."""
    r = db.val("select value from meta where key='current_film'")
    if r and db.one("select 1 from film where id=?", r):
        return r
    return db.val("select id from film order by is_benchmark,created_at desc,rowid desc limit 1")


def choose(db: DB, film):
    get(db, film)
    db.run("insert into meta(key,value) values('current_film',?) on conflict(key) do update set value=excluded.value", film)


def setting(db: DB, film, key, default=None):
    v = db.val("select value_json from film_setting where film_id=? and key=?", film, key)
    return json.loads(v) if v is not None else default


def set_setting(db: DB, film, key, value, by="user"):
    db.run("insert into film_setting(film_id,key,value_json,set_by,set_at) values(?,?,?,?,?) on conflict(film_id,key) do update set value_json=excluded.value_json,set_by=excluded.set_by,set_at=excluded.set_at",
           film, key, json.dumps(value, ensure_ascii=False), by, now())
    db.event(by, "film.setting", "film", film, {"key": key})


def profile(db: DB, film) -> dict:
    return json.loads(get(db, film)["profile_json"] or "{}")


def fps(db: DB, film) -> int:
    return get(db, film)["fps_num"]


def blocking_dir(db: DB, film) -> Path:
    """Previs pictures of this film. An imported project may keep them in an older place."""
    return data_dir() / "blocking" / profile(db, film).get("blocking", re.sub(r"[^A-Za-z0-9_-]", "_", film))


# ------------------------------------------------------------ money a film may use
def cap_left(db: DB, kind) -> int:
    cap = db.val("select limit_musd from hard_cap where category=?", kind) or 0
    used = db.val(f"""select coalesce(sum(case when r.state in('reserved','sent','unknown') then max(r.est_amount,coalesce(r.actual_amount,0)) else coalesce(r.actual_amount,case r.state when 'succeeded' then r.est_amount else 0 end) end),0)
        from api_request r join spend_envelope e on e.id=r.envelope_id where e.unit='musd' and r.capability in {CAPS[kind]}""")
    return cap - used


def envelope(db: DB, film, kind):
    like = {"image": "%image%", "inspection": "%inspection%", "voice": "%voice%", "sfx": "%sound%"}[kind]
    like2 = {"image": "%picture%"}.get(kind, like)
    unit = "musd" if kind in ALLOWANCE else "credits"
    return db.val("select id from spend_envelope where film_id=? and unit=? and (purpose like ? or purpose like ?) and closed_at is null order by rowid desc limit 1", film, unit, like, like2)


def open_allowance(db: DB, film, kind, musd: int) -> str:
    """The user lets this film use part of the approved total. It can only divide the total, never raise it."""
    if kind not in ALLOWANCE:
        raise RuleRefused("unknown kind of allowance")
    left = cap_left(db, kind)
    if not 10_000 <= musd <= left:
        raise RuleRefused(f"choose between $0.01 and ${left / 1e6:.2f}, which is what remains of the approved total")
    purpose, most = ALLOWANCE[kind]
    with db.tx():
        for e in db.q("select id from spend_envelope where film_id=? and unit='musd' and purpose=? and closed_at is null", film, purpose):
            db.run("update spend_envelope set closed_at=? where id=?", now(), e["id"])
        eid = db.insert("spend_envelope", id=new_id("env"), film_id=film, purpose=purpose, limit_amount=musd, max_per_request=min(most, musd), opened_by="user", unit="musd")
        db.event("user", "allowance.opened", "film", film, {"kind": kind, "musd": musd})
    return eid


def release_credits(db: DB, film, service, n: int) -> str:
    purpose = {"voice": "voice, included credits", "sfx": "sound effects, included credits"}.get(service)
    if not purpose:
        raise RuleRefused("unknown service")
    if not 1 <= n <= 30000:
        raise RuleRefused("release between 1 and 30,000 credits")
    u = db.one("select quota-used left_ from usage_snapshot where connector_id='elevenlabs' and source='api' order by id desc limit 1")
    if not u:
        raise RuleRefused("test the ElevenLabs connection first, so the real balance is known")
    if n > u["left_"]:
        raise RuleRefused(f"only {u['left_']} included credits are left")
    with db.tx():
        for e in db.q("select id from spend_envelope where film_id=? and purpose=? and closed_at is null", film, purpose):
            db.run("update spend_envelope set closed_at=? where id=?", now(), e["id"])
        eid = db.insert("spend_envelope", id=new_id("env"), film_id=film, purpose=purpose, limit_amount=n, max_per_request=min(n, 2500), opened_by="user", unit="credits")
        db.event("user", "credits.released", "film", film, {"service": service, "amount": n})
    return eid
