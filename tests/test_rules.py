"""Records that cannot be bent, money that tells the truth, attempts that cannot trap. Each test failed on the P0 code."""
import sqlite3
import threading

import pytest

from studio.connectors.broker import Broker, Refused
from studio.connectors.secrets import MemoryVault
from studio.core import assets, films, flow, packages, work
from studio.core.db import DB, RuleRefused
from tests.conftest import review
from tests.fakes import png
from tests.test_money_and_connectors import ready, sfx_ready
from tests.test_workflow import tone, vpkg

F = "TESTFILM"


@pytest.fixture
def br(db, fake):
    return Broker(db, MemoryVault(), throttle=lambda s: None)


# ---------------------------------------------------------------- D-05 records
def test_records_cannot_be_rewritten(db, br, fake):
    pk = sfx_ready(br)
    work.generate_sfx(db, br, pk)
    t = db.insert("task", id="tsx", film_id=F, kind="review", action="x", role="story-editor", packet_json={}, packet_hash="px", state="accepted")
    db.insert("report", id="rpx", task_id=t, verdict="APPROVED", body_json={}, schema_version="2", modality_json=[], created_at="t")
    db.insert("finding", id="fnx", report_id="rpx", defect_class="c", severity="info", statement="s", reason="r", next_action="none")
    a = db.val("select id from asset where kind='audio' limit 1")
    db.insert("human_label", id="hlx", asset_id=a, blind=1, kind="listen", verdict="good", created_at="t")
    for q in ("update api_request set actual_amount=0", "update api_request set est_amount=1", "delete from api_request", "delete from api_result", "update spend_envelope set limit_amount=99999999", "delete from spend_envelope", "update spend_envelope set max_per_request=99999999",
              "update connector set generation_enabled=1,enabled_by='user' where id='openai_images'", "delete from report", "update report set verdict='REJECTED'", "delete from finding", "update finding set severity='critical'", "update sfx_package set doc_json='{}'", "update voice_package set doc_json='{}'",
              "update provider_profile set expires_on='2099-01-01'", "delete from human_label", "update human_label set verdict='bad'", "delete from check_result", "update check_result set outcome='pass' where outcome<>'pass' or 1", "delete from event", "update event set actor='x'", "delete from attempt", "update attempt set reason='x'"):
        c = db.conn()
        c.execute("begin")
        try:
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(q)
        except BaseException:
            c.execute("rollback")
            raise AssertionError(f"allowed: {q}")
        c.execute("rollback")
    db.run("update finding set human_verdict='false' where id='fnx'")          # the user's own verdict may be added


def test_a_changed_log_is_noticed(db):
    for i in range(5):
        db.event("user", "x", "y", str(i))
    assert db.verify_events() == []
    c = db.conn()
    c.execute("drop trigger event_no_update")
    n = db.val("select max(seq) from event") - 2
    c.execute("update event set payload_json='{\"changed\":1}' where seq=?", (n,))
    assert n in db.verify_events()


def test_caps_hold_when_many_ask_at_once(db, br):
    ready(br, "google_images", "stills")
    for i in range(60):
        t = flow.target(db, F, "image", f"c{i}", "c")
        db.insert("attempt", id=f"ca{i}", target_id=t, attempt_no=1, origin="api", spec_json={}, spec_hash="s", created_at="t")
    got = []

    def go(i):
        x = DB(db.path)
        try:
            x.insert("api_request", id=f"cr{i}", envelope_id="env_images", profile_id="google_images_api", purpose="x", prompt_rev=1, prompt="x", request_json={}, request_hash=f"h{i}", requested_by="user", est_amount=300000, state="reserved", created_at="t", idem_key=f"k{i}", capability="image_generate", attempt_id=f"ca{i}")
            got.append(1)
        except RuleRefused:
            pass
    T = [threading.Thread(target=go, args=(i,)) for i in range(60)]
    [t.start() for t in T]
    [t.join() for t in T]
    assert len(got) == 33 and db.val("select sum(est_amount) from api_request") <= 10_000_000


# ---------------------------------------------------------------- D-03, D-04 money
def test_a_sent_request_is_never_free_without_proof(db, br, fake):
    pk = sfx_ready(br)
    fake.behaviour = "drop"
    r = work.generate_sfx(db, br, pk)
    c = db.conn()
    for basis in (None, "not_sent", "never_connected", "because I say so"):
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("update api_request set state='failed',actual_amount=0,outcome_basis=? where id=?", (basis, r["request"]))


class Reply:
    def __init__(self, j):
        self._j, self.content, self.headers = j, b"", {}

    def json(self):
        return self._j


def bfl(monkeypatch, poll):
    from studio.connectors import adapters

    def req(method, url, **k):
        if url.endswith("/credits"):
            return Reply({"credits": 900})
        if method == "POST":
            return Reply({"id": "job-1", "polling_url": "https://api.bfl.ai/v1/get_result?id=job-1", "cost": 1.4})
        return poll()
    monkeypatch.setattr(adapters, "request", req)
    monkeypatch.setattr(adapters.time, "sleep", lambda s: None)


@pytest.mark.parametrize("what", ["404", "lost", "failed", "garbage"])
def test_an_accepted_job_is_not_free_and_its_number_is_kept(db, br, monkeypatch, what):
    from studio.connectors.http import NetError

    def poll():
        if what == "404":
            raise NetError("http", "HTTP 404: gone", 404, "x")
        if what == "lost":
            raise NetError("after_send", "lost")
        return Reply({"status": "Failed"}) if what == "failed" else Reply({"status": "Ready"})
    bfl(monkeypatch, poll)
    ready(br, "bfl_images", "stills")
    if what == "lost":
        monkeypatch.setattr(type(br.adapter("bfl_images")), "_poll", lambda s, *a: (_ for _ in ()).throw(NetError("after_send", "lost")))
    r = work.generate_image(db, br, F, "IMG-LEAD-ANCHOR", "bfl_images")
    row = db.one("select state,actual_amount,provider_job_id from api_request")
    assert r["state"] == "unknown" and row == {"state": "unknown", "actual_amount": None, "provider_job_id": "job-1"}
    assert next(c for c in br.money()["caps"] if c["category"] == "image")["held"] > 0


def test_a_found_job_can_be_collected_later(db, br, monkeypatch):
    from studio.connectors.http import NetError
    state = {"ok": False}

    def poll():
        if not state["ok"]:
            raise NetError("http", "HTTP 404", 404)
        return Reply({"status": "Ready", "result": {"sample": "https://delivery.bfl.ai/x.png"}, "cost": 1.4})
    bfl(monkeypatch, poll)
    ready(br, "bfl_images", "stills")
    r = work.generate_image(db, br, F, "IMG-LEAD-ANCHOR", "bfl_images")
    state["ok"] = True
    from studio.connectors import adapters
    inner = adapters.request

    def with_image(method, url, **k):
        if "delivery" in url:
            x = Reply({})
            x.content, x.headers = png(1536, 2048), {"content-type": "image/png"}
            return x
        return inner(method, url, **k)
    monkeypatch.setattr(adapters, "request", with_image)
    out = br.reconcile(r["request"])
    assert out["state"] == "succeeded" and db.val("select actual_amount from api_request") == 14000 and db.val("select count(*) from api_request") == 1


def test_a_missing_reference_is_noticed_before_sending(db, br, fake):
    import os
    import stat
    ready(br, "google_images", "stills")
    r = work.generate_image(db, br, F, "IMG-LEAD-ANCHOR", "google_images")
    work.judge(db, r["attempt"], "accepted", "good")
    p = assets.path_of(db, r["asset"])
    os.chmod(p, stat.S_IWRITE)
    p.unlink()
    n = fake.gen
    with pytest.raises(Refused, match="nothing was sent"):
        work.generate_image(db, br, F, "IMG-LEAD-34L", "google_images")
    assert fake.gen == n and db.val("select outcome_basis from api_request order by rowid desc limit 1") == "not_sent"


def test_a_higher_price_than_agreed_is_refused(db, br, fake):
    ready(br, "google_images", "stills")
    with pytest.raises(Refused, match="agreed"):
        work.generate_image(db, br, F, "IMG-LEAD-ANCHOR", "google_images", expect=1)
    assert fake.gen == 0


def test_allowances_divide_the_total_and_never_raise_it(db, br):
    f = films.create(db, "Another film")
    with pytest.raises(RuleRefused):
        films.open_allowance(db, f, "image", 10_000_001)
    films.open_allowance(db, f, "image", 4_000_000)
    assert films.envelope(db, f, "image") and films.cap_left(db, "image") == 10_000_000


# ---------------------------------------------------------------- D-08, D-09 attempts
def test_an_attempt_that_never_ran_blocks_nothing(db, br, fake):
    with pytest.raises(Refused):
        work.generate_image(db, br, F, "IMG-LEAD-ANCHOR", "google_images")          # not connected
    ready(br, "google_images", "stills")
    r = work.generate_image(db, br, F, "IMG-LEAD-ANCHOR", "google_images", prompt="a changed prompt " * 5)
    assert r["state"] == "succeeded" and len(flow.counted(db, db.val("select id from generation_target where ref_id='IMG-LEAD-ANCHOR'"))) == 1


def test_import_by_hand_after_a_refused_start(db, br, tmp_path):
    with pytest.raises(Refused):
        work.generate_image(db, br, F, "IMG-LEAD-ANCHOR", "google_images")
    p = tmp_path / "i.png"
    p.write_bytes(png(1536, 2048))
    assert work.import_image(db, F, "IMG-LEAD-ANCHOR", p)["asset"]


def test_an_attempt_with_a_possible_charge_cannot_be_taken_back(db, br, fake):
    pk = sfx_ready(br)
    fake.behaviour = "drop"
    work.generate_sfx(db, br, pk)
    with pytest.raises(RuleRefused):
        flow.withdraw(db, db.val("select id from attempt"), "I want it gone")


def test_an_accepted_take_can_be_replaced_and_stays_until_then(db, tmp_path):
    p = vpkg(db)
    a = work.import_voice(db, p, "T1-N1", tone(tmp_path / "a.wav"))
    work.judge(db, a["attempt"], "accepted", "ok")
    tid = db.val("select id from generation_target where ref_id='T1-N1'")
    with pytest.raises(RuleRefused):
        work.import_voice(db, p, "T1-N1", tone(tmp_path / "b.wav", f=300), None, "", "a better take exists now")
    flow.reopen(db, tid, "the second half is too fast")
    b = work.import_voice(db, p, "T1-N1", tone(tmp_path / "c.wav", f=310), None, "", "a calmer reading of the second half")
    cur = "select asset_id from slot_current c join slot s on s.id=c.slot_id where s.ref_id='T1-N1'"
    assert db.val(cur) == a["asset"]
    work.judge(db, b["attempt"], "accepted", "better")
    assert db.val(cur) == b["asset"] and db.val("select count(*) from slot_version") == 2


def test_free_transport_failures_never_lock_an_asset(db, br, fake):
    pk = sfx_ready(br)
    fake.behaviour = "quota"
    for _ in range(5):
        r = work.generate_sfx(db, br, pk)
        assert r["state"] == "failed"
        flow.resolve_pause(db, r["pause"], "retry")
    fake.behaviour = "ok"
    assert work.generate_sfx(db, br, pk)["state"] == "succeeded"
    assert len(flow.counted(db, db.val("select id from generation_target where kind='sfx'"))) == 1


# ---------------------------------------------------------------- D-02 tasks appear when they are needed
def test_review_tasks_appear_without_a_restart(db, tmp_path):
    from studio.agents import service
    from tests.test_workflow import make_images
    flow.LISTENERS[:] = [service.ensure_tasks]
    try:
        make_images(db, tmp_path)
        pk = work.approve_pair(db, work.make_pair(db, "T1-07")["id"], True)
        assert db.one("select role from task where target_id=? and state='ready'", pk)["role"] == "cinematographer"
    finally:
        flow.LISTENERS.clear()


def test_a_failing_listener_does_not_undo_the_users_work(db, data):
    def bad(*a):
        raise ValueError("boom")
    flow.LISTENERS[:] = [bad]
    try:
        flow.changed(db, F, "x")
        assert "boom" in (data / "errors.log").read_text()
    finally:
        flow.LISTENERS.clear()
