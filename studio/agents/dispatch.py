"""Starts one Claude Code agent for one task, only when the user (or the showrunner on the user's word) asks, inside a weekly allowance, and records what it used."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..core.db import DB, ROOT, RuleRefused, data_dir, new_id, now
from . import service

DEFAULT_ALLOWANCE = 300_000        # output tokens in seven days; 26 reviews at high effort used about 250,000 in P0
MOST = 3
TIMEOUT = 40 * 60


def claude() -> str | None:
    """The Claude Code program: STUDIO_CLAUDE, the PATH, or the newest VS Code extension."""
    e = os.environ.get("STUDIO_CLAUDE")
    if e:
        return e if Path(e).exists() else None
    w = shutil.which("claude")
    if w:
        return w
    ver = lambda p: [int(x) for x in re.findall(r"\d+", p.parent.parent.parent.name)[:3]]
    c = sorted(Path.home().glob(".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe"), key=ver)
    return str(c[-1]) if c else None


def allowance(db: DB) -> int:
    v = db.val("select value from meta where key='agent_allowance'")
    return int(v) if v is not None else DEFAULT_ALLOWANCE


def set_allowance(db: DB, tokens: int):
    db.run("insert into meta(key,value) values('agent_allowance',?) on conflict(key) do update set value=excluded.value", str(tokens))
    db.event("user", "agents.allowance", "studio", "studio", {"output_tokens_per_week": tokens})


def usage(db: DB) -> dict:
    u = db.one("select coalesce(sum(output_tokens),0) out_,count(*) runs from usage_log where at>datetime('now','-7 days')")
    return {"week_output_tokens": u["out_"], "week_runs": u["runs"], "allowance": allowance(db), "left": max(0, allowance(db) - u["out_"]), "paused": db.val("select value from meta where key='agents_paused'") or None, "claude": bool(claude()), "typical": typical(db)}


def typical(db: DB, role=None) -> int:
    v = [r["output_tokens"] for r in db.q("select output_tokens from usage_log where output_tokens is not null and outcome='handed_in' and (? is null or role=?) order by rowid desc limit 20", role, role)]
    return int(sum(v) / len(v)) if v else 9600


def resume(db: DB):
    db.run("delete from meta where key='agents_paused'")
    db.event("user", "agents.resumed", "studio", "studio")


def _check(db: DB, role):
    u = usage(db)
    if not u["claude"]:
        raise RuleRefused("Claude Code was not found on this computer; install it or set STUDIO_CLAUDE")
    if u["paused"]:
        raise RuleRefused(f"agents are paused: {u['paused']}")
    if not service.agent_file(role).exists():
        raise RuleRefused(f"the role {role} has no agent file yet")
    if typical(db, role) > u["left"]:
        raise RuleRefused(f"this week's allowance has {u['left']:,} output tokens left and a {role} run usually needs {typical(db, role):,}. Raise the allowance on the Agents page or wait.")


def start(db: DB, jobs, task_id) -> str:
    t = db.one("select * from task where id=?", task_id)
    if not t or t["state"] != "ready":
        raise RuleRefused("this task is not waiting")
    _check(db, t["role"])
    if db.val("select count(*) from media_job where kind='agent' and state in('queued','running')") >= MOST:
        raise RuleRefused(f"at most {MOST} agents at a time")
    db.event("user", "agent.started", "task", task_id, {"role": t["role"]})
    return jobs.add("agent", {"task": task_id}, key=f"agent:{task_id}:{new_id('x')}", ram=300, disk=10, resource="net", film=t["film_id"])


def start_batch(db: DB, jobs, film, most=MOST) -> list[str]:
    out = []
    for t in db.q("select id,role from task where film_id=? and state='ready' order by priority,rowid", film):
        if len(out) >= min(most, MOST):
            break
        if service.agent_file(t["role"]).exists():
            out.append(start(db, jobs, t["id"]))
    if not out:
        raise RuleRefused("no task is waiting for an agent that has a file")
    return out


def command(task, exe: str, config: Path) -> list[str]:
    role = task["role"]
    tools = ["list_tasks", "claim_task", "get_view"] + (["submit_report"] if role in service.CHECKERS else []) + (["submit_work"] if role in service.MAKERS else [])
    return [exe, "-p", f"Do studio task {task['id']}.", "--agent", role, "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
            "--allowedTools", ",".join(f"mcp__studio__{t}" for t in tools), "--mcp-config", str(config), "--strict-mcp-config"]


def run_job(db: DB, jobs, jid, args):
    t = db.one("select * from task where id=?", args["task"])
    if not t or t["state"] != "ready":
        return {"state": "not waiting"}
    _check(db, t["role"])
    folder = data_dir() / "agent-runs"
    folder.mkdir(parents=True, exist_ok=True)
    cfg = data_dir() / "tmp" / f"mcp_{t['role']}.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"mcpServers": {"studio": {"type": "stdio", "command": sys.executable, "args": ["-m", "studio.agents.mcp_server", "--role", t["role"]], "env": {"STUDIO_DATA": str(data_dir().resolve()), "PYTHONPATH": str(ROOT)}}}}), encoding="utf-8")
    log = folder / f"{t['id']}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    jobs.progress(jid, 0, 1, f"{t['role']} is working")
    t0 = time.time()
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    with log.open("wb") as f:
        p = subprocess.Popen(command(t, claude(), cfg), cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        while p.poll() is None:
            time.sleep(1.5)
            jobs.progress(jid, int(time.time() - t0), TIMEOUT, f"{t['role']} is working")
            if jobs.stopping(jid) or time.time() - t0 > TIMEOUT:
                p.kill()
                p.wait()
                break
    res = read_result(log)
    return record(db, t, res, time.time() - t0)


def read_result(log: Path) -> dict:
    out = {}
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            j = json.loads(line)
        except ValueError:
            out.setdefault("raw", line[:300])
            continue
        if isinstance(j, dict) and j.get("type") == "result":
            out = {**out, **j}
    return out


def record(db: DB, t, res: dict, seconds: float) -> dict:
    state = db.val("select state from task where id=?", t["id"])
    u = res.get("usage") or {}
    text = str(res.get("result") or res.get("raw") or "")
    limited = bool(re.search(r"(usage|5-hour|hour|weekly|session) limit|limit reached|hit your .*limit|quota|overloaded", text, re.I)) and (bool(res.get("is_error")) or len(text) < 200)
    outcome = "handed_in" if state == "accepted" else "limit" if limited else "nothing_handed_in"
    model = max(res.get("modelUsage", {}).items(), key=lambda kv: kv[1].get("outputTokens", 0))[0] if res.get("modelUsage") else None
    db.insert("usage_log", id=new_id("ul"), task_id=t["id"], model=model, input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"), cached_tokens=u.get("cache_read_input_tokens"), cache_write_tokens=u.get("cache_creation_input_tokens"), source="reported" if u else "unavailable",
              role=t["role"], film_id=t["film_id"], seconds=round(seconds, 1), turns=res.get("num_turns"), agent_hash=db.val("select agent_hash from task where id=?", t["id"]), outcome=outcome, at=now())
    db.insert("time_log", id=new_id("tm"), film_id=t["film_id"], activity="agent", basis_json={"role": t["role"], "action": t["action"], "model": model}, seconds=round(seconds, 2), ref=t["id"], at=now())
    service.release_lease(db, t["id"], text)
    service.ensure_tasks(db)
    if limited:                                   # the plan's limit: stop starting agents until the user says so
        db.run("insert into meta(key,value) values('agents_paused',?) on conflict(key) do update set value=excluded.value", "Claude's usage limit was reached; " + text[:160])
        db.event("system", "agents.paused", "studio", "studio", {"why": text[:200]})
    elif outcome != "handed_in" and db.val("select count(*) from usage_log where task_id=? and outcome='nothing_handed_in'", t["id"]) >= 2:
        db.run("update task set state='expired',closed_at=?,why_blocked='two runs ended without a result' where id=? and state='ready'", now(), t["id"])
    return {"outcome": outcome, "output_tokens": u.get("output_tokens"), "said": text[:300]}
