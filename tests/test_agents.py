"""Role isolation and report discipline, through the real tool server."""
import json
import os
import sys

import anyio
import pytest

from studio.agents import service as s
from studio.core import packages
from studio.core.db import RuleRefused
from tests.conftest import ROOT


def report(db, t, claim, **over):
    evs = [s.get_view(db, "story-editor", t, claim["lease_token"], v)[0] for v in claim["views"]]
    r = {"verdict": "APPROVED", "coverage": [{"criterion_id": c["id"], "result": "met", "evidence_ids": [evs[0]]} for c in claim["criteria"]], "observations": [{"text": "four lines", "evidence_id": evs[0]}], "findings": [], "limitations": ["cannot hear"], "examined": ["document_text"], "escalation": None}
    return {**r, **over}, evs


def test_tasks_go_to_a_role_that_did_not_make_it(db):
    assert s.ensure_tasks(db) == 7 and s.ensure_tasks(db) == 0
    assert len(s.listing(db, "story-editor")) == 7 and s.listing(db, "cinematographer") == []


def test_report_rules(db):
    s.ensure_tasks(db)
    t = s.listing(db, "story-editor")[0]["id"]
    with pytest.raises(s.AgentRefused):
        s.claim(db, "cinematographer", t)
    c = s.claim(db, "story-editor", t)
    with pytest.raises(s.AgentRefused):
        s.claim(db, "story-editor", t)
    ok, evs = report(db, t, c)

    def refused(**k):
        with pytest.raises(s.AgentRefused) as e:
            s.submit(db, "story-editor", t, c["lease_token"], {**ok, **k})
        return str(e.value)
    assert "missing" in refused(coverage=ok["coverage"][1:])
    assert "never served" in refused(coverage=[{**ok["coverage"][0], "evidence_ids": ["ev_made_up"]}, *ok["coverage"][1:]])
    assert "was not served" in refused(examined=["full_video"]) and "was not served" in refused(examined=["audio"])
    assert "impossible" in refused(coverage=[{**ok["coverage"][0], "result": "cannot_determine", "evidence_ids": []}, *ok["coverage"][1:]])
    assert "report" in refused(verdict="PASS")
    with pytest.raises(s.AgentRefused):
        s.submit(db, "story-editor", t, "wrong", ok)
    assert s.submit(db, "story-editor", t, c["lease_token"], ok)["accepted"]
    with pytest.raises(s.AgentRefused):
        s.submit(db, "story-editor", t, c["lease_token"], ok)


def test_a_bad_review_blocks_the_user_approval(db):
    s.ensure_tasks(db)
    t = s.listing(db, "story-editor")[0]["id"]
    c = s.claim(db, "story-editor", t)
    ok, evs = report(db, t, c)
    cov = [{**ok["coverage"][0], "result": "not_met"}, *ok["coverage"][1:]]
    s.submit(db, "story-editor", t, c["lease_token"], {**ok, "verdict": "NEEDS_CORRECTION", "coverage": cov, "findings": [{"defect_class": "direction", "severity": "major", "statement": "x", "reason": "y", "evidence_id": evs[0], "next_action": "ask_user"}]})
    kind, pid = db.val("select target_type from task where id=?", t).removesuffix("_package"), db.val("select target_id from task where id=?", t)
    with pytest.raises(RuleRefused):
        packages.approve(db, kind, pid)


def test_server_exposes_its_tools_and_no_more(db, data):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    s.ensure_tasks(db)

    async def go(role):
        p = StdioServerParameters(command=sys.executable, args=["-m", "studio.agents.mcp_server", "--role", role], env={**os.environ, "STUDIO_DATA": str(data)}, cwd=str(ROOT))
        async with stdio_client(p) as (r, w):
            async with ClientSession(r, w) as c:
                await c.initialize()
                names = sorted(t.name for t in (await c.list_tools()).tools)
                tasks = json.loads((await c.call_tool("list_tasks", {})).content[0].text)
                return names, tasks
    names, tasks = anyio.run(go, "story-editor")
    assert names == ["claim_task", "get_view", "list_tasks", "submit_report", "submit_work"] and len(tasks) == 7
    assert anyio.run(go, "forensic-inspector")[0] == ["claim_task", "get_view", "list_tasks", "submit_report"]
    names, tasks = anyio.run(go, "forensic-inspector")
    assert tasks == []


def test_agents_cannot_reach_keys_money_or_settings():
    for f in ("service.py", "mcp_server.py"):
        t = (ROOT / "studio/agents" / f).read_text(encoding="utf-8")
        for w in ("secrets import WindowsVault", "Broker", "vault", "set_mode", "spend_envelope", "api_request", "subprocess", "open("):
            assert w not in t.replace("Image.open(", ""), (f, w)


def test_reviews_are_bounded_and_the_user_can_overrule_with_a_reason(db):
    """Two full reviews, one closing review, then the user decides. Nothing loops."""
    from studio.core import flow
    F, cue = "TESTFILM", "CUE-CLOTH"
    p = db.val("select id from sfx_package where cue_id=?", cue)
    with pytest.raises(RuleRefused):                       # nobody has looked yet: not even a reason helps
        packages.approve(db, "sfx", p, "I do not want to wait for this")
    for n in range(3):
        s.ensure_tasks(db)
        t = db.one("select * from task where target_id=? and state='ready'", p)
        assert t["action"] == ("closing_review" if n == 2 else "review_package"), n
        c = s.claim(db, "story-editor", t["id"])
        assert ("previous" in c["views"]) == (n == 2)
        ok, evs = report(db, t["id"], c)
        cov = [{**ok["coverage"][0], "result": "not_met"}, *ok["coverage"][1:]]
        s.submit(db, "story-editor", t["id"], c["lease_token"], {**ok, "verdict": "NEEDS_CORRECTION", "coverage": cov, "findings": [{"defect_class": "x", "severity": "major", "statement": "s", "reason": "r", "evidence_id": evs[0], "next_action": "ask_user"}]})
        if n < 2:
            d = packages.doc_of(db, "sfx", p)
            d["prompt"] = d["prompt"] + f" Version {n}."
            d["changed_vs_prior"] = f"change {n}"
            p = packages.register(db, "sfx", F, d, manual_profile="sfx_manual", api_profile="elevenlabs_api", cue=cue, maker="sound-designer")
    s.ensure_tasks(db)
    assert not db.one("select 1 from task where target_id=? and state='ready'", p)
    with pytest.raises(RuleRefused):
        packages.approve(db, "sfx", p)
    with pytest.raises(RuleRefused):
        packages.approve(db, "sfx", p, "ok")               # a real reason is needed
    packages.approve(db, "sfx", p, "The remaining points do not matter for this sound")
    a = db.one("select * from approval where target_id=?", p)
    assert a["outcome"] == "waived" and a["actor"] == "user" and flow.live(db, "G9_sfx_package", p)
