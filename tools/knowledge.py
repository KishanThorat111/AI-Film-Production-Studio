"""Writes docs/knowledge/ from the program itself, so that the pages cannot say something the code does not do.

    python tools/knowledge.py          writes the pages
    python tools/knowledge.py --check  exits with 1 if a page differs (tests run this)
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "knowledge"
NOTE = "<!-- made by tools/knowledge.py from the code; do not edit -->\n"


def pages() -> dict[str, str]:
    from studio.agents import service as s
    from studio.connectors.adapters import REGISTRY
    from studio.connectors.http import REFUSED_BEFORE_WORK
    from studio.core import backup, docs, edit, films, gates, jobs, migrate, packages, release
    from studio.core.db import DB
    from studio.engine import render, sound
    from studio.engine.compositor import ENGINE_VERSION
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        db = DB(Path(t) / "k.db")
        tables = [r["name"] for r in db.q("select name from sqlite_master where type='table' and name not like 'sqlite_%' and name not like 'lesson_fts%' order by name")]
        rules = [(r["name"], (re.findall(r"RAISE\(ABORT,'([^']+)'", r["sql"]) or [""])[0]) for r in db.q("select name,sql from sqlite_master where type='trigger' order by name")]
        db.close()
    row = lambda *c: "| " + " | ".join(str(x).replace("|", "/").replace("\n", " ") for x in c) + " |"
    tbl = lambda head, rows: "\n".join([row(*head), row(*["---"] * len(head))] + [row(*r) for r in rows])
    g = lambda role, k: (re.search(rf"^{k}:\s*(\S+)", s.agent_file(role).read_text(encoding="utf-8"), re.M) or [None, "-"])[1]
    desc = lambda role: (re.search(r"^description:\s*(.+)$", s.agent_file(role).read_text(encoding="utf-8"), re.M) or [None, ""])[1].replace(" Use with a studio task id.", "")
    p = {}
    p["agents.md"] = "# Agents\n\nEach has a file in `.claude/agents/` and a role-scoped tool server. None can approve, spend, change a setting, read a file or see a key.\n\n" + tbl(["Role", "Does", "Model", "Effort", "Kind"], [(r, desc(r), g(r, "model"), g(r, "effort"), ", ".join(k for k, l in (("maker", s.MAKERS), ("checker", s.CHECKERS)) if r in l)) for r in s.ROLES]) + \
        "\n\n## What makers are asked for\n\n" + tbl(["Task", "Role", "Hands in", "Sees"], [(a, r, k, ", ".join(v)) for a, (r, k, _, v) in s.MAKE.items()]) + "\n\n## What checkers are asked for\n\n" + "\n\n".join(f"**{k}**\n\n" + "\n".join(f"- {c['id']}{'' if c['mandatory'] else ' (advice)'}: {c['text']}" for c in v) for k, v in s.CRITERIA.items()) + \
        f"\n\nAt most {s.FULL_REVIEWS} full reviews of a package, then one closing review, then the user decides.\n"
    p["workflow.md"] = "# Workflow and gates\n\n" + tbl(["Gate", "What is approved"], gates.GATES) + "\n\n## Documents\n\n" + tbl(["Document", "Made by", "Reviewed by", "Built on", "Gate"], [(k, v["maker"], ", ".join(v["reviewers"]) or "its packages are reviewed", ", ".join(v["needs"]) or "-", v["gate"] or "-") for k, v in docs.KINDS.items()]) + \
        "\n\nAn approval records the hash of everything it was based on. When one of these changes, the approval no longer holds. A rule's objection cannot be overruled; a reviewer's can, by the user, with a reason.\n\n## Before release\n\n" + tbl(["Item", "Meaning"], release.ITEMS) + "\n"
    p["engine.md"] = f"# Media engine {ENGINE_VERSION}\n\nEverything the timeline accepts is done; anything else is refused. `tests/test_edit.py` checks each entry.\n\n## Edit commands\n\n" + tbl(["Command", "Meaning"], [(k, v[1]) for k, v in edit.COMMANDS.items()]) + "\n\nTransitions: " + ", ".join(sorted(render.TRANSITIONS)) + ". Blends: " + ", ".join(sorted(render.BLEND)) + ". Text: " + ", ".join(sorted(render.TEXT_OPS)) + ".\n\n## Picture effects\n\n" + \
        tbl(["Effect", "Parameters and defaults"], [(k, ", ".join(f"{n}={d:g}" for n, d in v[0]) or "-") for k, v in sorted(render.OPS.items()) if k not in ("place", "over", "matte")]) + "\n\n## Sound\n\nBuses: " + ", ".join(sound.BUSES) + ". Effects: " + ", ".join(sorted(render.SOUND_OPS)) + ". Default ducking: " + "; ".join(f"{d['target']} {d['db']:g} dB under {d['key']}" for d in sound.DUCK) + ". Keyframes count film frames. Rooms, distance, walls and moving sources are simulated; ambience can loop.\n"
    p["providers.md"] = "# Providers\n\nKeys live in Windows Credential Manager only. Hosts are fixed, redirects refused. The Gemini app (video) is always used by hand; there is no video-generation capability.\n\n" + tbl(["Connector", "Unit", "Capabilities"], [(A.title, A.unit, "; ".join(f"{v['title']}{' (blocked: ' + v['forced_block'] + ')' if v.get('forced_block') else ''}" for v in A.capabilities.values())) for A in REGISTRY.values()]) + \
        f"\n\nFree after sending only when the answer proves that no work was done: HTTP {', '.join(map(str, sorted(REFUSED_BEFORE_WORK)))}. Everything else, including 5xx and an accepted job whose result is lost, is *unknown*: it stays reserved, is never sent again, and the user settles it. Only HTTP 429 is repeated by itself, at most three times.\n"
    p["database.md"] = f"# Database\n\nSchema version {len(migrate.steps())}: " + ", ".join(f"{n} {name}" for n, name, _ in migrate.steps()) + f". {len(tables)} tables.\n\n" + ", ".join(f"`{t}`" for t in tables) + "\n\n## Rules the database itself enforces\n\n" + tbl(["Rule", "What it says"], rules) + "\n"
    p["operations.md"] = "# Operations\n\n" + tbl(["What", "Value"], [("Copies kept", ", ".join(f"{v} {k}" for k, v in backup.KEEP.items())), ("Copied besides database and media", ", ".join(backup.EXPENSIVE)), ("Disk that stays free", f"{jobs.FLOOR_GB} GB"), ("Memory that stays free", f"{jobs.RESERVE_MB} MB"), ("Film allowances", ", ".join(films.ALLOWANCE)), ("Voice models known", ", ".join(packages.MODELS))]) + \
        "\n\nIf the studio does not start: `python -m studio.recover list <place>`, then `restore` into a new folder, then start with `STUDIO_DATA` set to it.\n"
    p["README.md"] = "# Studio knowledge\n\nShort pages made from the program. The state of the work in [IMPLEMENTATION](../IMPLEMENTATION.md), daily use in the [guide](../USER-GUIDE.md).\n\n" + tbl(["Page", "Kept true by"], [(f"[{k}]({k})", "test_knowledge") for k in sorted(p)]) + "\n"
    return {k: NOTE + v for k, v in p.items()}


if __name__ == "__main__":
    want = pages()
    if "--check" in sys.argv:
        bad = [k for k, v in want.items() if not (OUT / k).exists() or (OUT / k).read_text(encoding="utf-8") != v]
        print("different: " + ", ".join(bad) if bad else "all pages match the code")
        sys.exit(bool(bad))
    OUT.mkdir(parents=True, exist_ok=True)
    for k, v in want.items():
        (OUT / k).write_text(v, encoding="utf-8", newline="\n")
    print(f"{len(want)} pages written")
