"""Makers: an unrelated screenplay goes from script to a rendered assembly through the same doors agents use. No agent runs here; the documents are fixtures."""
import json
import sys

import pytest

from studio.agents import dispatch, service as s
from studio.core import docs, edit, films, flow, gates, script
from studio.core.db import RuleRefused
from tests.conftest import ROOT

WAIVE = "tests do not run independent reviewers"


@pytest.fixture
def film(db):
    flow.LISTENERS[:] = [s.ensure_tasks]
    f = films.create(db, "Low Water")
    script.ingest(db, f, ROOT / "benchmark/lowwater/script.fountain")
    script.make_scenes(db, f)
    yield f
    flow.LISTENERS.clear()


def hand_in(db, film, action, scope, work, changed=None, note=""):
    role = s.MAKE[action][0]
    t = s.request(db, film, action, scope, note)
    c = s.claim(db, role, t)
    views = {}
    for v in c["views"]:
        ev, path, data = s.get_view(db, role, t, c["lease_token"], v)
        views[v] = path or data
        assert path or len(json.dumps(data)) > 2, v
    return s.submit_work(db, role, t, c["lease_token"], work(views) if callable(work) else work, changed), views


def spans(db, film, kinds=("action", "dialogue", "narration", "sound"), scene=None):
    return [x for x in script.spans(db, film, scene) if x["kind"] in kinds]


def plan_doc(db, film):
    sc = db.q("select id,number from scene where film_id=? order by number", film)
    first = spans(db, film)[0]["id"]
    return {"schema_version": "1", "film_id": film, "script_sha256": script.current(db, film)["sha256"], "tone": {"statement": "quiet dread; nothing hurries", "cite": [first]},
            "entities": [{"id": "LW-NORA", "kind": "character", "name": "Nora", "facts": [{"field": "age", "value": "58", "basis": "stated", "cite": [first]}]}, {"id": "LW-TOMAS", "kind": "character", "name": "Tomas", "facts": []}, {"id": "LW-BOATHOUSE", "kind": "environment", "name": "Boathouse", "facts": []}, {"id": "LW-JETTY", "kind": "environment", "name": "Jetty", "facts": []}],
            "scenes": [{"number": x["number"], "objective": "the water is gone", "audience_before": "nothing", "audience_after": "something was kept under it", "emotion": "unease", "beats": [{"id": f"b{x['number']}.{i}", "what": p["text"][:80], "visual_need": "see it", "cite": [p["id"]]} for i, p in enumerate(spans(db, film, scene=x["id"]))]} for x in sc],
            "knowledge": [{"fact": "Nora expected this", "known_by": ["LW-NORA"], "when_beat": "b1.0", "fairness": "planted", "cite": [first]}], "invariants": [{"id": "no-water", "rule": "no water is seen in scene 2", "severity": "critical", "method": "inspected", "checker": "free_inspection", "params": {}, "cite": [first]}],
            "open_issues": [{"question": "Whose coat is the second one?", "quotes": ["It is Nora's oilskin."], "options": ["Nora's own", "a second oilskin"]}], "coverage": {"mapped_spans": len(spans(db, film)), "non_visual_spans": 0, "unmapped_spans": 0}}


def canon_doc():
    return {"schema_version": "1", "entities": [
        {"id": "LW-NORA", "kind": "character", "name": "Nora", "spec": {"age": 58, "height_cm": 164, "hair": "grey, cut at the jaw"}, "must_hold": ["grey hair cut at the jaw", "dark green oilskin with one missing button"], "may_vary": ["mud on the hem"], "facts": [{"field": "age", "basis": "invented"}]},
        {"id": "LW-TOMAS", "kind": "character", "name": "Tomas", "spec": {"age": 31, "height_cm": 181}, "must_hold": ["full dark beard, short"], "facts": [{"field": "beard", "basis": "invented"}]},
        {"id": "LW-BOATHOUSE", "kind": "environment", "name": "Boathouse", "spec": {"size_m": [6, 9, 3.4]}, "must_hold": ["iron ring on the left wall at 1.2 m"], "facts": [{"field": "ring", "basis": "invented"}]}]}


def shot_doc(db, film, sc):
    sp = spans(db, film, scene=sc)
    previs = {"rooms": [{"id": "r", "x": 0, "y": 0, "w": 6, "d": 9, "h": 3.4, "doors": [{"wall": "s", "at": 2, "width": 1.2, "height": 2.1}]}], "props": [{"kind": "box", "x": 3, "y": 4, "z": 0, "w": 1.6, "d": 4, "h": 1}], "figures": [{"name": "Nora", "x": 1.4, "y": 3, "rot": -90, "pose": "stand", "height": 1.64}], "lights": [{"kind": "point", "x": 3, "y": 1, "z": 3, "power": 120}], "camera": {"x": 4.6, "y": .9, "z": 1.5, "tx": 1.6, "ty": 3.2, "tz": 1.3, "lens_mm": 35}}
    return {"schema_version": "1", "scene": sc, "intention": "stay with the hands and let the absence arrive late", "shots": [
        {"code": f"A{i + 1}", "tier": "B", "frames": 96, "spans": [x["id"] for x in sp[i::3]], "purpose": "what this shot is for in the scene", "camera": {"mode": "cinematic", "size": size, "move": "none"}, "light": "grey dawn through boards", "technique": "still_move", "canon": ["LW-NORA"], **({"previs": previs} if i == 0 else {})} for i, size in enumerate(("wide", "medium", "close"))]}


def through(db, film):
    r, _ = hand_in(db, film, "make_plan", "film", plan_doc(db, film))
    docs.approve(db, r["id"], waive_reason=WAIVE)
    r, v = hand_in(db, film, "make_canon", "film", canon_doc())
    assert "plan" in v and v["guide"]["you_hand_in"] == "canon"
    docs.approve(db, r["id"], waive_reason=WAIVE)
    sc = db.val("select id from scene where film_id=? and number=1", film)
    r, v = hand_in(db, film, "make_shots", f"scene:{sc}", shot_doc(db, film, sc))
    docs.approve(db, r["id"], waive_reason=WAIVE)
    return sc


def test_a_foreign_script_reaches_an_edit(db, film, data):
    sc = through(db, film)
    assert db.val("select count(*) from script_line where film_id=?", film) == 6
    assert db.val("select text from script_line where film_id=? order by rowid limit 1", film) == "You're early."
    edit.first_assembly(db, film, sc)
    r, v = hand_in(db, film, "make_cut", f"scene:{sc}", {"base_revision": 1, "note": "arrive late at the absence", "commands": [{"op": "trim", "id": "A2", "dur": 60, "reason": "leave on the look"}, {"op": "plan_silence", "silence": {"id": "S1", "at": {"after": v_last(db, film, sc), "frame": 12}, "frames": 24, "max_dbfs": -50, "reason": "before the knocks"}, "reason": "the script's quiet"}], "requests": [{"kind": "coverage", "shot": "A3", "text": "a closer hand on the hull"}]})
    assert r["revision"] == 2 and "A2" in " ".join(r["changes"])
    assert v["material"]["shots"][0]["status"] == "storyboard card" and "commands" in v["engine"]
    assert gates.progress(db, film)[0]["done"] == 1 and any("coverage" in n["text"] for n in db.q("select text from cut_note where film_id=?", film))


def v_last(db, film, sc):
    return edit.short(edit.lines_of(db, film, sc)[-1]["id"])


def test_mix_and_finish_may_touch_only_their_part(db, film):
    sc = through(db, film)
    edit.first_assembly(db, film, sc)
    with pytest.raises(s.AgentRefused, match="not part of this task"):
        hand_in(db, film, "make_mix", f"scene:{sc}", {"base_revision": 1, "commands": [{"op": "trim", "id": "A1", "dur": 10, "reason": "not mine"}]})
    with pytest.raises(s.AgentRefused, match="not part of this task"):
        hand_in(db, film, "make_post", f"scene:{sc}", {"base_revision": 1, "commands": [{"op": "remove", "id": "A1", "reason": "not mine"}]})


@pytest.mark.parametrize("break_,word", [
    (lambda d: d["scenes"][0]["beats"].pop(), "belong to no beat"), (lambda d: d["scenes"].pop(), "not in the plan"), (lambda d: d["scenes"][0]["beats"][0]["cite"].append("made-up"), "not in the script"),
    (lambda d: d.update(script_sha256="0"), "another revision"), (lambda d: d["open_issues"][0]["quotes"].append("She laughs."), "quote is not in the script"), (lambda d: d["entities"][0]["facts"][0].update(cite=[]), "non-empty")])
def test_a_plan_cannot_leave_things_out_or_invent(db, film, break_, word):
    d = plan_doc(db, film)
    break_(d)
    with pytest.raises(s.AgentRefused, match=word):
        hand_in(db, film, "make_plan", "film", d)


def test_shots_must_cover_the_scene(db, film):
    r, _ = hand_in(db, film, "make_plan", "film", plan_doc(db, film))
    docs.approve(db, r["id"], waive_reason=WAIVE)
    r, _ = hand_in(db, film, "make_canon", "film", canon_doc())
    docs.approve(db, r["id"], waive_reason=WAIVE)
    sc = db.val("select id from scene where film_id=? and number=1", film)
    d = shot_doc(db, film, sc)
    d["shots"][0]["spans"] = d["shots"][0]["spans"][:1]
    with pytest.raises(s.AgentRefused, match="no shot covers"):
        hand_in(db, film, "make_shots", f"scene:{sc}", d)


def test_images_refuse_real_people_loops_and_lost_references(db, film):
    r, _ = hand_in(db, film, "make_plan", "film", plan_doc(db, film))
    docs.approve(db, r["id"], waive_reason=WAIVE)
    r, _ = hand_in(db, film, "make_canon", "film", canon_doc())
    docs.approve(db, r["id"], waive_reason=WAIVE)
    img = lambda **k: {"code": "I1", "kind": "anchor", "entity": "LW-NORA", "aspect": "3:4", "refs": [], "shows": ["LW-NORA"], "prompt": "Portrait photograph of an invented woman of 58, grey hair cut at the jaw, dark green oilskin with one missing button, plain background.", **k}
    for bad, word in ((img(prompt=img()["prompt"] + " She looks like Jane Smith."), "real one"), (img(refs=["NOPE"]), "does not exist"), (img(kind="reference", refs=["I1"]), "built from itself")):
        with pytest.raises(RuleRefused, match=word):
            docs.propose(db, film, "image_plan", "film", {"schema_version": "1", "images": [bad]}, "image-maker")


def test_documents_are_reviewed_by_others_and_go_stale(db, film, tmp_path):
    r, _ = hand_in(db, film, "make_plan", "film", plan_doc(db, film))
    t = db.one("select * from task where target_id=? and kind='review'", r["id"])
    assert t["role"] == "continuity-inspector" and t["state"] == "ready"
    with pytest.raises(RuleRefused, match="not reviewed yet"):
        docs.approve(db, r["id"])
    c = s.claim(db, t["role"], t["id"])
    evs = [s.get_view(db, t["role"], t["id"], c["lease_token"], v)[0] for v in c["views"]]
    s.submit(db, t["role"], t["id"], c["lease_token"], {"verdict": "APPROVED", "coverage": [{"criterion_id": x["id"], "result": "met", "evidence_ids": evs[:1]} for x in c["criteria"]], "observations": [{"text": "the plan", "evidence_id": evs[0]}], "findings": [], "limitations": [], "examined": ["document_text"], "escalation": None})
    docs.approve(db, r["id"])
    r2, _ = hand_in(db, film, "make_canon", "film", canon_doc())
    p = tmp_path / "rev2.fountain"
    p.write_text((ROOT / "benchmark/lowwater/script.fountain").read_text(encoding="utf-8").replace("three", "four") + "\nA gull.\n", encoding="utf-8")
    script.ingest(db, film, p)
    assert docs.stale(db, docs.current(db, film, "production_plan")) == ["script"]
    assert not flow.live(db, "G1_plan", r["id"])                # the approval of the plan no longer holds


def test_returned_work_comes_back_with_the_reason(db, film):
    r, _ = hand_in(db, film, "make_plan", "film", plan_doc(db, film))
    docs.send_back(db, r["id"], "the mother must not seem surprised")
    d = plan_doc(db, film)
    d["tone"]["statement"] = "she knows"
    with pytest.raises(s.AgentRefused, match="what changed"):
        hand_in(db, film, "make_plan", "film", d)
    t = db.one("select id from task where action='make_plan' and state='leased'")
    s.release_lease(db, t["id"], "test")
    db.run("update task set state='cancelled' where id=?", t["id"])
    r2, v = hand_in(db, film, "make_plan", "film", d, "answers the note about the mother")
    assert v["previous"]["returned_because"] == "the mother must not seem surprised" and r2["revision"] == 2


def test_the_sound_guide_says_what_the_rules_want(db, film):
    """A real sound-designer needed four tries because the guide did not say these things."""
    sc = through(db, film)
    t = s.request(db, film, "make_sound", f"scene:{sc}")
    c = s.claim(db, "sound-designer", t)
    g = s.get_view(db, "sound-designer", t, c["lease_token"], "guide")[2]
    from studio.core import packages
    assert f"{film}:CAST-" in g["audition_lines"] and all(f in g["formats"] for f in packages.FORMATS)
    assert "empty" in s.get_view(db, "sound-designer", t, c["lease_token"], "library")[2]["note"]
    assert s.get_view(db, "sound-designer", t, c["lease_token"], "lines")[2][0]["id"].startswith(film)


def test_large_views_are_paged():
    text, n = s.paged([{"id": i, "t": "x" * 900} for i in range(200)], 1)
    assert n > 1 and len(text) <= s.PAGE + 200 and json.loads(text)["of"] == n
    assert sum(len(json.loads(s.paged([{"id": i, "t": "x" * 900} for i in range(200)], p)[0])["items"]) for p in range(1, n + 1)) == 200


def test_a_kill_at_any_step_loses_and_doubles_nothing(db, film):
    """Leases expire, the task is free again, the second run hands in, nothing is there twice."""
    t = s.request(db, film, "make_plan")
    c = s.claim(db, "story-editor", t)
    s.get_view(db, "story-editor", t, c["lease_token"], "guide")
    db.run("update task set lease_until=datetime('now','-1 minute') where id=?", t)
    assert [x["id"] for x in s.listing(db, "story-editor") if x["kind"] == "make"] == [t]
    with pytest.raises(s.AgentRefused):
        s.submit_work(db, "story-editor", t, c["lease_token"], plan_doc(db, film))
    c2 = s.claim(db, "story-editor", t)
    s.get_view(db, "story-editor", t, c2["lease_token"], "guide")
    s.submit_work(db, "story-editor", t, c2["lease_token"], plan_doc(db, film))
    with pytest.raises(s.AgentRefused):
        s.submit_work(db, "story-editor", t, c2["lease_token"], plan_doc(db, film))
    assert db.val("select count(*) from work_doc where film_id=?", film) == 1


# ---------------------------------------------------------------- starting agents
@pytest.fixture
def claude(tmp_path, monkeypatch, data):
    """A stand-in for the Claude Code program: writes a result like the real one."""
    py = tmp_path / "fake_claude.py"
    py.write_text("import json,os,sys\nr=json.loads(os.environ['FAKE_RESULT'])\nprint(json.dumps({'type':'system','subtype':'init'}))\nprint(json.dumps(r))\n", encoding="utf-8")
    cmd = tmp_path / "claude.cmd"
    cmd.write_text(f'@"{sys.executable}" "{py}" %*\r\n', encoding="utf-8")
    monkeypatch.setenv("STUDIO_CLAUDE", str(cmd))
    return lambda **k: monkeypatch.setenv("FAKE_RESULT", json.dumps({"type": "result", "is_error": False, "num_turns": 5, "result": "done", "usage": {"input_tokens": 10, "output_tokens": 5000, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 800}, "modelUsage": {"claude-x": {"outputTokens": 5000}}, **k}))


class NoJobs:
    def progress(self, *a):
        pass

    def stopping(self, *a):
        return False

    def add(self, kind, args, **k):
        return "job"


def test_a_run_is_recorded_and_the_task_is_free_again(db, film, claude):
    claude()
    t = s.request(db, film, "make_plan")
    r = dispatch.run_job(db, NoJobs(), "j", {"task": t})
    assert r["outcome"] == "nothing_handed_in" and db.val("select output_tokens from usage_log") == 5000 and db.val("select state from task where id=?", t) == "ready"
    dispatch.run_job(db, NoJobs(), "j", {"task": t})
    assert db.val("select state from task where id=?", t) == "expired"                       # no endless loop


def test_the_plans_limit_stops_everything_until_the_user_resumes(db, film, claude):
    claude(is_error=True, result="Claude usage limit reached. Resets 3pm")
    t = s.request(db, film, "make_plan")
    assert dispatch.run_job(db, NoJobs(), "j", {"task": t})["outcome"] == "limit"
    with pytest.raises(RuleRefused, match="paused"):
        dispatch.start(db, NoJobs(), t)
    dispatch.resume(db)
    dispatch.start(db, NoJobs(), t)


def test_the_weekly_allowance_is_respected(db, film, claude):
    claude()
    t = s.request(db, film, "make_plan")
    dispatch.set_allowance(db, 100)
    with pytest.raises(RuleRefused, match="allowance"):
        dispatch.start(db, NoJobs(), t)


def test_the_agent_gets_only_its_tools(db, film):
    t = db.one("select * from task where id=?", s.request(db, film, "make_plan"))
    c = dispatch.command(t, "claude", ROOT / "x.json")
    assert "--strict-mcp-config" in c and "dontAsk" in c and "Bash" not in " ".join(c) and "submit_work" in " ".join(c)


def test_a_refused_draft_is_corrected_with_fixes_only():
    from studio.agents import service as s
    d = {"a": {"list": [{"x": 1}, {"x": 2}]}, "b": "old"}
    out = s.patch(d, [{"path": "a/list/1/x", "value": 9}, {"path": "/b", "value": "new"}, {"path": "a/list/-", "value": {"x": 3}}, {"path": "a/list/0", "remove": True}])
    assert out == {"a": {"list": [{"x": 9}, {"x": 3}]}, "b": "new"} and d["b"] == "old"
    for bad in ([], "x", [{"path": "a/nothing/1", "value": 1}], [{"path": "", "value": 1}], [{"path": "a/list/7/x", "value": 1}], [{"path": "b"}]):
        with pytest.raises(s.AgentRefused):
            s.patch(d, bad)
