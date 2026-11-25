"""The way back when the studio does not start. Needs no database.

    python -m studio.recover list    E:/AIFilmStudio-Backup
    python -m studio.recover check   E:/AIFilmStudio-Backup 20261125-170000
    python -m studio.recover restore E:/AIFilmStudio-Backup 20261125-170000 C:/somewhere/new-data

Then start the studio on the restored folder: set STUDIO_DATA to it and run Start Studio.bat.
"""
import json
import sys
from pathlib import Path

from .core import backup


def main(a):
    if len(a) < {"list": 2, "check": 3, "restore": 4}.get(a[0] if a else "", 99):
        print(__doc__)
        return 2
    root = Path(a[1])
    if a[0] == "list":
        for n in backup.sets(root):
            m = json.loads((root / "sets" / n / "MANIFEST.json").read_text(encoding="utf-8"))
            print(n, f"{len(m['files'])} files, {sum(f['bytes'] for f in m['files'].values()) / 2 ** 20:.0f} MB, code {'yes' if m['code'] else 'no'}")
        return 0
    if a[0] == "check":
        bad = backup.verify_set(root, a[2])
        print("complete" if not bad else f"{len(bad)} problems: {bad[:5]}")
        return int(bool(bad))
    r = backup.restore(root, a[2], Path(a[3]))
    print(json.dumps(r, indent=1))
    return int(r["outcome"] != "equal")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
