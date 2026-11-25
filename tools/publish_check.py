"""What may go to the public repository? Run by the pre-push hook, and by hand:  python tools/publish_check.py [commit]

Refuses: secrets, databases, media, documents, private folders, local paths, private e-mail addresses in files or commits, and words from the
owner's private list (private/publish-words.txt, one regular expression per line, never published).
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = re.compile(r"(^|/)(data|private|\.venv|node_modules|\.codex|\.agents)(/|$)|\.(db|db-wal|db-shm|sqlite3?|docx?|pdf|env|pem|key|p12|pfx|mp4|mov|mkv|webm|avi|wav|mp3|flac|ogg|opus|m4a|psd|blend|bundle|log|jsonl|zip|7z)$|(^|/)\.env|credentials|secrets?\.json", re.I)
SECRET = re.compile(r"AIza[0-9A-Za-z_-]{30,}|\bsk[-_](?:proj[-_])?[A-Za-z0-9]{24,}|\bxox[abp]-|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY|AKIA[0-9A-Z]{16}|bfl_[A-Za-z0-9]{20,}")
PATHS = re.compile(r"[A-Za-z]:[\\/]+(?:Users|KISHAN)[\\/]|/c/(?:Users|KISHAN)/|/home/[a-z]+/|/Users/[a-z]+/", re.I)
MAIL = re.compile(r"[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com|anthropic\.com|example\.(?:com|org))[A-Za-z0-9-]+\.[A-Za-z]{2,}")
FAKE = ("sk_test_good_key_0123456789", "sk_wrong_key_9999999999")
BIG = 2_000_000


def git(*a, raw=False):
    r = subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True)
    return r.stdout if raw else r.stdout.decode("utf-8", "replace")


def words():
    p = ROOT / "private" / "publish-words.txt"
    return [re.compile(w.strip(), re.I) for w in p.read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")] if p.exists() else []


def check_tree(commit, bad):
    w = words()
    for line in git("ls-tree", "-r", "-l", "-z", commit).split("\0"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        size = int(meta.split()[3]) if meta.split()[3].isdigit() else 0
        if FILES.search(path):
            bad.append(f"{path}: this kind of file or folder is never published")
            continue
        if size > BIG:
            bad.append(f"{path}: {size} bytes")
        b = git("show", f"{commit}:{path}", raw=True)
        if b"\0" in b[:4000]:
            continue
        for n, l in enumerate(b.decode("utf-8", "replace").splitlines(), 1):
            s = l
            for f in FAKE:
                s = s.replace(f, "")
            for rx, what in ((SECRET, "looks like a key"), (PATHS, "local path"), (MAIL, "e-mail address")):
                if rx.search(s) and path not in ("tools/publish_check.py",):
                    bad.append(f"{path}:{n}: {what}")
            for rx in w:
                if rx.search(l):
                    bad.append(f"{path}:{n}: private word")


def check_commits(commit, known, bad):
    rng = [commit] + ([f"^{k}" for k in known if k])
    for c in git("rev-list", *rng).split():
        who = git("show", "-s", "--format=%ae%n%ce%n%B", c)
        for m in MAIL.findall(who):
            bad.append(f"commit {c[:8]}: private e-mail address")
        for rx in words():
            if rx.search(who):
                bad.append(f"commit {c[:8]}: private word in its message")
        check_tree(c, bad)


def main(a):
    commit, known = a[0] if a else "HEAD", a[1:]
    bad = []
    check_commits(commit, known, bad)
    bad = list(dict.fromkeys(bad))
    print("\n".join(bad[:60]) if bad else f"nothing private found in {commit} and its history")
    return int(bool(bad))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
