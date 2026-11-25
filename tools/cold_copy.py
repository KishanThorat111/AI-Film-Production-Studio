"""Cold copy of the whole project folder, with a manifest and a read-back check. Run with the studio closed.

    python tools/cold_copy.py <target folder that does not exist yet>

Standard library only, so it works when the studio itself does not.
"""
import hashlib
import json
import os
import pathlib
import shutil
import sys
import time

SKIP = {".venv", "__pycache__", ".pytest_cache", "node_modules"}
ROOT = pathlib.Path(__file__).resolve().parents[1]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(target):
    target = pathlib.Path(target)
    if target.exists():
        sys.exit("the target exists already; a cold copy never overwrites")
    part = target.with_name(target.name + ".part")
    if part.exists():
        sys.exit(f"an unfinished copy exists at {part}; remove it yourself after looking at it")
    part.mkdir(parents=True)
    files, t0 = {}, time.time()
    for d, dirs, names in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in SKIP]
        for n in names:
            s = pathlib.Path(d) / n
            rel = s.relative_to(ROOT).as_posix()
            dst = part / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            h = sha(s)
            shutil.copy2(s, dst)
            files[rel] = {"sha256": h, "bytes": s.stat().st_size}
    bad = [r for r, f in files.items() if sha(part / r) != f["sha256"]]
    if bad:
        sys.exit(f"{len(bad)} files differ after copying, for example {bad[:3]}; nothing was published")
    (part / "MANIFEST.json").write_text(json.dumps({"made": time.strftime("%Y-%m-%d %H:%M:%S"), "source": str(ROOT), "files": files}, indent=0), encoding="utf-8")
    part.rename(target)
    print(f"{len(files)} files, {sum(f['bytes'] for f in files.values()) / 2 ** 20:.0f} MB, all read back and equal, {time.time() - t0:.0f} s -> {target}")


if __name__ == "__main__":
    main(sys.argv[1])
