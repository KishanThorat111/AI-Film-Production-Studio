import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ["STUDIO_BACKUP"] = "Z:/not-there/backup"
os.environ["STUDIO_PROJECTS"] = str(ROOT / "tests")


@pytest.fixture
def fake(monkeypatch):
    from tests.fakes import Fake
    f = Fake()
    monkeypatch.setenv("STUDIO_FAKE_PROVIDERS", f.url)
    yield f
    f.stop()


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return tmp_path / "data"


FILM = ROOT / "tests" / "film"


@pytest.fixture(scope="session")
def template(tmp_path_factory):
    """One studio with the test film, copied for every test: much faster than building it each time."""
    d = tmp_path_factory.mktemp("template")
    old = os.environ.get("STUDIO_DATA")
    os.environ["STUDIO_DATA"] = str(d)
    from studio.core import base, project
    from studio.core.db import DB
    x = DB()
    base.base(x)
    project.load(x, FILM)
    x.conn().execute("pragma wal_checkpoint(truncate)")
    x.close()
    os.environ.pop("STUDIO_DATA") if old is None else os.environ.__setitem__("STUDIO_DATA", old)
    return d


@pytest.fixture
def db(data, template):
    from studio.core.db import DB
    shutil.copytree(template, data, dirs_exist_ok=True)
    return DB()


@pytest.fixture(scope="session")
def pictures(tmp_path_factory):
    """Previs pictures for the test film: rendered noise with the right names, not a story."""
    import cv2
    import numpy as np
    d = tmp_path_factory.mktemp("previs")
    for k, (s, frames, depth) in enumerate((("T1-01", (0, 119), 0), ("T1-02", (0,), 1), ("T1-03", (0,), 0), ("T1-04", (0, 155), 0), ("T1-05", (0,), 0), ("T1-06A", (0,), 0), ("T1-06B", (0,), 1), ("T1-06C", (0,), 1), ("T1-07", (0, 119), 0), ("T1-08", (0,), 0))):
        (d / s).mkdir()
        r = np.random.default_rng(k)
        im = cv2.resize(r.integers(20, 200, (9, 16, 3), np.uint8), (1280, 720), interpolation=cv2.INTER_CUBIC)
        for f in frames:
            cv2.imwrite(str(d / s / f"{f:04d}.png"), cv2.add(im, int(f // 40)))
        if depth:
            cv2.imwrite(str(d / s / "depth.png"), np.tile(np.linspace(0, 255, 1280, dtype=np.uint8), (720, 1)))
    return d


@pytest.fixture
def blocking(data, pictures):
    shutil.copytree(pictures, data / "blocking", dirs_exist_ok=True)
    return data


def review(db, kind, pid, role, verdict="APPROVED"):
    """A stand-in for an independent specialist's accepted report (tests only)."""
    from studio.core.db import new_id, now
    t = db.insert("task", id=new_id("ts"), film_id="TESTFILM", kind="review", action="review_package", role=role, gate="x", target_type=f"{kind}_package", target_id=pid, packet_json={}, packet_hash=new_id("h"), state="accepted")
    db.insert("report", id=new_id("rp"), task_id=t, verdict=verdict, body_json={"findings": []}, schema_version="2", modality_json=["transcript"], created_at=now())
