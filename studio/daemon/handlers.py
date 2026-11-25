"""Background jobs: what each kind does, and how to tell that its result still exists."""
from __future__ import annotations

from ..agents import dispatch
from ..core import assets, backup, cut, previs, release
from ..core.db import RuleRefused


def register(ctx):
    db, jobs = ctx.db, ctx.jobs

    def progress(jid):
        return lambda done, total, text="": jobs.progress(jid, done, total, text)

    def has_asset(args, result):
        try:
            return bool(result and assets.path_of(db, result["asset"]).exists())
        except (KeyError, TypeError, assets.Missing):
            return False

    jobs.handlers["render"] = lambda j, a: cut.render_job(db, jobs, j, a)
    jobs.valid["render"] = has_asset

    def do_backup(j, a):
        r = backup.run(db, a["target"], progress(j))
        if r["state"] == "failed":
            raise RuleRefused(r["error"])
        return r
    jobs.handlers["backup"] = do_backup
    jobs.valid["backup"] = lambda a, r: False                      # a copy is always made again
    jobs.handlers["drill"] = lambda j, a: backup.drill(db, a["target"], None, progress(j))
    jobs.valid["drill"] = lambda a, r: False
    jobs.handlers["previs"] = lambda j, a: previs.render_job(db, jobs, j, a)
    jobs.valid["previs"] = lambda a, r: False
    jobs.handlers["agent"] = lambda j, a: dispatch.run_job(db, jobs, j, a)
    jobs.valid["agent"] = lambda a, r: False
    jobs.handlers["release"] = lambda j, a: release.build_job(db, jobs, j, a)
    jobs.valid["release"] = lambda a, r: False
