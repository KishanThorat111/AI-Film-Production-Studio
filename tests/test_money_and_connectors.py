"""Spending, switches, failures and secrets, through the real broker against local stand-in providers."""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from studio.connectors.adapters import REGISTRY, CostUnknown, ElevenLabs, GeminiInspect
from studio.connectors.broker import Broker, Refused
from studio.connectors.http import NetError, request, scrub
from studio.connectors.secrets import MemoryVault, fingerprint, same_key
from studio.core import flow, packages, preflight, work
from studio.core.db import RuleRefused
from tests.conftest import review
from tests.fakes import GOOD

F = "TESTFILM"


@pytest.fixture
def br(db, fake):
    return Broker(db, MemoryVault(), throttle=lambda s: None)


def ready(br, cid, *modes):
    br.set_key(cid, GOOD)
    if cid == "gemini_inspect":
        br.set_config(cid, {"billing_confirmed": True})
    br.test(cid)
    br.set_enabled(cid, True)
    for m in modes:
        flow.set_mode(br.db, F, m, "api")


def sfx_ready(br, cue="CUE-CLOTH", credits=3000):
    db = br.db
    ready(br, "elevenlabs", "sfx")
    db.insert("spend_envelope", id="ec", film_id=F, purpose="sound effects, included credits", limit_amount=credits, max_per_request=2500, opened_by="user", unit="credits")
    p = db.val("select id from sfx_package where cue_id=?", cue)
    review(db, "sfx", p, "story-editor")
    packages.approve(db, "sfx", p)
    return p


# ---------------------------------------------------------------- secrets
def test_key_is_never_in_the_database_or_logs(br, db, data):
    br.set_key("elevenlabs", GOOD)
    br.test("elevenlabs")
    br.set_key("openai_images", "sk_wrong_key_9999999999")
    r = br.test("openai_images")
    db.conn().execute("pragma wal_checkpoint(full)")
    for f in data.rglob("*"):
        if f.is_file():
            b = f.read_bytes()
            assert GOOD.encode() not in b and b"sk_wrong_key_9999999999" not in b, f
    assert "sk_wrong_key_9999999999" not in json.dumps(r) and any(c["outcome"] == "denied" for c in r)
    assert db.val("select secret_hint from connector where id='elevenlabs'") == "•••• 6789"


def test_fingerprint_is_one_way_and_salted():
    a, b = fingerprint(GOOD), fingerprint(GOOD)
    assert a != b and same_key(GOOD, a) and not same_key(GOOD + "x", a) and GOOD not in a


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_vault_round_trip():
    from studio.connectors.secrets import SecretError, WindowsVault
    v = WindowsVault()
    ref = v.put("pytest-selftest", "pytest-selftest-value-123456")
    try:
        assert v.get(ref) == "pytest-selftest-value-123456"
        # the stored entry is not readable as a file: it is held by Windows, encrypted for this account
    finally:
        v.delete(ref)
    with pytest.raises(SecretError):
        v.get(ref)


def test_scrubbing():
    assert "AIza" not in scrub("bad key AIzaSyD-abcdefghijk1234") and "hunter" not in scrub("Bearer hunter2secret")


def test_only_known_hosts(monkeypatch):
    for u in ("https://evil.example/x", "http://api.openai.com/v1/models", "https://api.openai.com.evil.example/"):
        with pytest.raises(NetError) as e:
            request("GET", u)
        assert e.value.kind == "before_send"


# ---------------------------------------------------------------- switches
def test_connected_is_not_enabled(br, db, fake):
    p = db.val("select id from sfx_package where cue_id='CUE-CLOTH'")
    with pytest.raises(RuleRefused):                       # no independent review yet
        packages.approve(db, "sfx", p)
    br.set_key("elevenlabs", GOOD)
    with pytest.raises(Refused):                           # untested
        br.set_enabled("elevenlabs", True)
    br.test("elevenlabs")
    with pytest.raises(Refused):                           # manual mode
        work.generate_sfx(db, br, p)
    flow.set_mode(db, F, "sfx", "api")
    db.insert("spend_envelope", id="ec", film_id=F, purpose="sound effects, included credits", limit_amount=3000, max_per_request=2500, opened_by="user", unit="credits")
    review(db, "sfx", p, "story-editor")
    packages.approve(db, "sfx", p)
    with pytest.raises(Refused) as e:                      # connected and tested, still off
        work.generate_sfx(db, br, p)
    assert "off" in str(e.value)
    assert fake.gen == 0


def test_music_can_never_be_allowed(br):
    with pytest.raises(Refused):
        br.set_capability("elevenlabs", "music", True, "please")


def test_there_is_no_video_generation_anywhere():
    for a in REGISTRY.values():
        assert not [k for k in a.capabilities if "video" in k and not k.startswith("inspect")]
    from studio.core import migrate
    c = sqlite3.connect(":memory:")
    for _, _, raw in migrate.steps():
        c.executescript(raw.decode("utf-8"))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into connector_capability values('x','video_generate','allowed','x','user')")


def test_replacing_a_key_switches_the_connector_off(br, db):
    ready(br, "elevenlabs")
    br.set_key("elevenlabs", "another_key_1234567890")
    assert db.val("select generation_enabled from connector where id='elevenlabs'") == 0


# ---------------------------------------------------------------- sound effect by API
def test_sfx_happy_path_and_licence(br, db, fake):
    p = sfx_ready(br)
    r = work.generate_sfx(db, br, p)
    real = int(40 * packages.doc_of(db, "sfx", p)["duration_seconds"])          # what the stand-in provider charges
    assert r["state"] == "succeeded" and r["actual"] == real and r["estimate"] >= real
    a = r["asset"]
    with pytest.raises(RuleRefused):                       # must not reach the reusable library
        work.admit(db, a, "studio", None, "foley", "cloth", [])
    db.run("update licence_terms set verified_by='user' where id='elevenlabs_sfx'")
    with pytest.raises(RuleRefused):
        work.admit(db, a, "studio", None, "foley", "cloth", [])
    db.insert("asset_use", id="u1", film_id=F, asset_id=a, purpose="final", used_as="x")
    db.insert("film", id="other", universe_id="testworld", title="o", fps_num=24, width=1, height=1, status="development")
    with pytest.raises(RuleRefused):                       # nor another film
        db.insert("asset_use", id="u2", film_id="other", asset_id=a, purpose="temp", used_as="x")


def test_three_attempts_then_stop(br, db, fake):
    p = sfx_ready(br)
    cue = "CUE-CLOTH"
    r = work.generate_sfx(db, br, p)
    with pytest.raises(RuleRefused):                       # not judged
        work.generate_sfx(db, br, p, "too bright for the closet")
    work.judge(db, r["attempt"], "needs_correction", "hangers too bright")
    with pytest.raises(RuleRefused):                       # identical specification
        work.generate_sfx(db, br, p, "too bright for the closet")
    for n in (2, 3):
        doc = packages.doc_of(db, "sfx", p)
        doc["prompt"] = doc["prompt"].replace("no music", f"softer hangers {n}, no music")
        doc["changed_vs_prior"] = "softer hanger contact"
        p = packages.register(db, "sfx", F, doc, manual_profile="sfx_manual", api_profile="elevenlabs_api", cue=cue, maker="sound-designer")
        with pytest.raises(Refused):                       # a new revision is not approved by the old approval
            work.generate_sfx(db, br, p, "too bright for the closet")
        db.run("update attempt set outcome=outcome")       # no effect on history
        review(db, "sfx", p, "story-editor")
        packages.approve(db, "sfx", p)
        r = work.generate_sfx(db, br, p, "too bright for the closet")
        assert r["state"] == "succeeded"
        work.judge(db, r["attempt"], "rejected", "still wrong")
    assert db.val("select state from generation_target where kind='sfx' and ref_id=?", cue) == "stopped"
    n = fake.gen
    doc["prompt"] += " Dry."
    p = packages.register(db, "sfx", F, {**doc, "changed_vs_prior": "drier"}, manual_profile="sfx_manual", api_profile="elevenlabs_api", cue=cue, maker="sound-designer")
    review(db, "sfx", p, "story-editor")
    packages.approve(db, "sfx", p)
    with pytest.raises(RuleRefused):
        work.generate_sfx(db, br, p, "one more please please")
    assert fake.gen == n
    flow.extend(db, db.val("select id from generation_target where kind='sfx'"), 1, "I want one more")
    assert work.generate_sfx(db, br, p, "one more please please")["state"] == "succeeded"


def test_failed_request_without_answer_does_not_use_up_attempts(br, db, fake):
    p = sfx_ready(br)
    fake.behaviour = "quota"
    r = work.generate_sfx(db, br, p)
    assert r["state"] == "failed" and r["pause"] and r["charged"] == "no"
    with pytest.raises(Refused):                           # paused until the user decides
        work.generate_sfx(db, br, p)
    assert fake.gen == 1
    flow.resolve_pause(db, r["pause"], "retry")
    fake.behaviour = "ok"
    r2 = work.generate_sfx(db, br, p)
    assert r2["state"] == "succeeded" and db.val("select count(*) from attempt") == 1


def test_server_error_may_have_been_charged(br, db, fake):
    """The audit's D-03: a 5xx answer after sending proves nothing about money."""
    p = sfx_ready(br)
    fake.behaviour = "fail500"
    r = work.generate_sfx(db, br, p)
    assert r["state"] == "unknown" and r["charged"] == "unknown" and db.val("select actual_amount from api_request") is None
    flow.resolve_pause(db, r["pause"], "retry")
    fake.behaviour = "ok"
    with pytest.raises(Refused):                           # never sent again on its own
        work.generate_sfx(db, br, p)
    assert fake.gen == 1 and br.money()["credits"][0]["used"] > 0
    br.settle_unknown(r["request"], False)                 # the user looked: nothing was charged
    assert work.generate_sfx(db, br, p)["state"] == "succeeded" and fake.gen == 2


def test_lost_reply_is_never_resent(br, db, fake):
    p = sfx_ready(br)
    fake.behaviour = "drop"
    r = work.generate_sfx(db, br, p)
    assert r["state"] == "unknown" and fake.gen == 1
    fake.behaviour = "ok"
    flow.resolve_pause(db, r["pause"], "retry")
    with pytest.raises(Refused):
        work.generate_sfx(db, br, p)
    assert fake.gen == 1
    held = br.money()["credits"][0]["used"]
    assert held >= int(40 * packages.doc_of(db, "sfx", p)["duration_seconds"])
    br.settle_unknown(r["request"], False)
    assert work.generate_sfx(db, br, p)["state"] == "succeeded"


def test_rate_limit_waits_and_continues(br, db, fake):
    p = sfx_ready(br)
    fake.behaviour = "429once"
    assert work.generate_sfx(db, br, p)["state"] == "succeeded" and fake.gen == 2


@pytest.mark.parametrize("b,word", [("quota", "credits"), ("noperm", "does not allow")])
def test_clear_refusals(br, db, fake, b, word):
    p = sfx_ready(br)
    fake.behaviour = b
    r = work.generate_sfx(db, br, p)
    assert r["state"] == "failed" and word in db.val("select reason from stage_pause")


def test_included_credits_never_spill(br, db, fake):
    p = sfx_ready(br)
    fake.used = fake.quota - 60                            # the account says 60 left; the sound needs up to 200
    br.test("elevenlabs")
    with pytest.raises(Refused) as e:
        work.generate_sfx(db, br, p)
    assert "included credits" in str(e.value) and fake.gen == 0


def test_website_use_is_seen(br, db, fake):
    """Credits used on the website lower the same balance."""
    p = sfx_ready(br)
    fake.used = 29950
    db.run("delete from event where verb='x'") if False else None
    br.test("elevenlabs")
    with pytest.raises(Refused):
        work.generate_sfx(db, br, p)


def test_released_amount_is_a_limit(br, db, fake):
    p = sfx_ready(br, credits=150)
    with pytest.raises(Refused):
        work.generate_sfx(db, br, p)
    assert fake.gen == 0


def test_unknown_cost_is_not_sent():
    with pytest.raises(CostUnknown):
        ElevenLabs("k" * 20).estimate("sfx", {"text": "x"})
    with pytest.raises(CostUnknown):
        GeminiInspect("k" * 20, {"model": "gemini-9-new"}).estimate("inspect_video", {"kind": "video", "seconds": 8, "fps": 12, "prompt": "x"})


# ---------------------------------------------------------------- images
def test_images_end_to_end_and_cap(br, db, fake, blocking):
    ready(br, "google_images", "stills")
    r = work.generate_image(db, br, F, "IMG-LEAD-ANCHOR", "google_images")
    assert r["state"] == "succeeded", r
    with pytest.raises(RuleRefused):                       # built only from approved images
        work.generate_image(db, br, F, "IMG-LEAD-34L", "google_images")
    work.judge(db, r["attempt"], "accepted", "good")
    assert work.generate_image(db, br, F, "IMG-LEAD-34L", "google_images")["state"] == "succeeded"
    assert work.generate_image(db, br, F, "IMG-T1-02", "google_images")["state"] == "succeeded"
    spent = next(c for c in br.money()["caps"] if c["category"] == "image")
    assert 0 < spent["spent"] < 400_000


def test_aggregate_cap_across_providers(br, db, fake, blocking):
    ready(br, "google_images", "stills")
    ready(br, "openai_images")
    tid = flow.target(db, F, "image", "fill", "fill")
    db.insert("attempt", id="afill", target_id=tid, attempt_no=1, origin="api", spec_json={}, spec_hash="q", created_at="t")
    db.insert("spend_envelope", id="envbig", film_id=F, purpose="image test", limit_amount=10_000_000, max_per_request=10_000_000, opened_by="user", unit="musd")
    db.insert("api_request", id="rfill", envelope_id="envbig", profile_id="google_images_api", purpose="x", prompt_rev=1, prompt="x", request_json={}, request_hash="fill", requested_by="user", est_amount=9_950_000, state="reserved", created_at="t", idem_key="fill", capability="image_generate", attempt_id="afill")
    with pytest.raises(Refused) as e:                      # another provider, same ceiling
        work.generate_image(db, br, F, "IMG-LEAD-ANCHOR", "openai_images")
    assert "hard cap" in str(e.value) or "envelope" in str(e.value)
    with pytest.raises(sqlite3.IntegrityError):
        db.conn().execute("update hard_cap set limit_musd=50000000")


def test_content_refusal_is_an_attempt_not_a_loop(br, db, fake):
    ready(br, "google_images", "stills")
    fake.behaviour = "refuse"
    r = work.generate_image(db, br, F, "IMG-LEAD-ANCHOR", "google_images")
    assert r["state"] == "refused" and db.val("select outcome from attempt") == "refused" and fake.gen == 1


def test_inspection_budget_is_separate(br, db, fake, data, tmp_path):
    from tests.test_inspect import clip
    from studio.core import assets
    ready(br, "gemini_inspect", "inspection")
    aid, _ = assets.add(db, clip(tmp_path), origin="manual_video")
    r = work.gemini_inspect(db, br, F, aid)
    assert r["state"] == "succeeded" and r["report"]["answer"]["defects"] and r["report"]["outcome"] == "warn"
    assert r["actual"] == 30000 * 1.5 + 1000 * 7.5
    c = {x["category"]: x["spent"] for x in br.money()["caps"]}
    assert c["image"] == 0 and c["inspection"] == r["actual"]
    with pytest.raises(Refused):                           # the same clip is not paid for twice
        work.gemini_inspect(db, br, F, aid)
    assert db.val("select outcome from attempt where id=(select attempt_id from api_request limit 1)") == "accepted"


def test_cost_above_estimate_switches_off(br, db, fake, monkeypatch):
    p = sfx_ready(br)
    monkeypatch.setattr(ElevenLabs, "estimate", lambda s, c, sp: 10)
    r = work.generate_sfx(db, br, p)
    assert r.get("warning") and db.val("select generation_enabled from connector where id='elevenlabs'") == 0


# ---------------------------------------------------------------- crash
def test_crash_between_sending_and_answer(br, db, fake):
    p = sfx_ready(br)
    tid = flow.target(db, F, "sfx", "CUE-CLOTH", "x")
    at = flow.start_attempt(db, tid, "api", {"x": 1})
    db.insert("api_request", id="rq", envelope_id="ec", profile_id="elevenlabs_api", purpose="x", prompt_rev=1, prompt="x", request_json={}, request_hash="h", requested_by="user", est_amount=200, state="reserved", created_at="2026-11-25 00:00:00", idem_key="i", capability="sfx", sfx_package_id=p, attempt_id=at)
    db.run("update api_request set state='sent' where id='rq'")
    assert Broker(db, MemoryVault()).recover_after_crash() == 1
    assert db.val("select state from api_request where id='rq'") == "unknown" and db.one("select 1 from stage_pause where resolution is null")


# ---------------------------------------------------------------- preflight
def test_preflight_needs_only_what_is_used(br, db, fake):
    it = {i["requirement"]: i["state"] for i in preflight.run(db, F)["items"]}
    assert it["Voices"] == "manual_ready" and it["Sound effects"] == "manual_ready" and it["Gemini video (by hand)"] == "manual_ready" and it["Gemini inspection"] == "not_required"
    flow.set_mode(db, F, "sfx", "api")
    it = {i["requirement"]: i for i in preflight.run(db, F, "sfx")["items"]}
    assert it["Sound effects"]["state"] == "blocked" and "key" in it["Sound effects"]["next_step"]
    br.set_key("elevenlabs", GOOD)
    br.test("elevenlabs")
    it = {i["requirement"]: i for i in preflight.run(db, F, "sfx")["items"]}
    assert it["Sound effects"]["state"] == "blocked" and "Enable" in it["Sound effects"]["next_step"]
    br.set_enabled("elevenlabs", True)
    it = {i["requirement"]: i["state"] for i in preflight.run(db, F, "sfx")["items"]}
    assert it["Sound effects"] == "ready" and it["Sound effects: included credits"] == "blocked"


def test_bad_key_and_unreachable(br, db, fake, monkeypatch):
    br.set_key("gemini_inspect", "wrong_key_000000000")
    assert any(c["kind"] == "auth" and c["outcome"] == "denied" for c in br.test("gemini_inspect"))
    monkeypatch.setenv("STUDIO_FAKE_PROVIDERS", "http://127.0.0.1:9")
    assert any(c["kind"] == "availability" and c["outcome"] == "unreachable" for c in br.test("gemini_inspect"))
