"""Face similarity as a measurement, never a verdict. Models: YuNet (MIT) and SFace (Apache 2.0) from OpenCV Zoo.
The published same-person threshold (0.363 cosine) is for photographs; generated faces, profiles, darkness and small faces are not calibrated.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..core.db import data_dir

VERSION = "f0.1"
SAME, LOW = .50, .363
_m = None


def available() -> bool:
    d = data_dir() / "models" / "face"
    return (d / "face_detection_yunet_2023mar.onnx").exists() and (d / "face_recognition_sface_2021dec.onnx").exists()


def _models():
    global _m
    if _m is None:
        d = data_dir() / "models" / "face"
        _m = (cv2.FaceDetectorYN.create(str(d / "face_detection_yunet_2023mar.onnx"), "", (640, 640), .6), cv2.FaceRecognizerSF.create(str(d / "face_recognition_sface_2021dec.onnx"), ""))
    return _m


def faces(img: np.ndarray):
    det, rec = _models()
    s = min(1.0, 1280 / max(img.shape[:2]))
    a = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img
    det.setInputSize((a.shape[1], a.shape[0]))
    _, f = det.detect(a)
    out = []
    for x in (f if f is not None else []):
        v = rec.feature(rec.alignCrop(a, x)).ravel()
        out.append({"vec": v / (np.linalg.norm(v) + 1e-9), "size": float(x[3] / a.shape[0]), "score": float(x[-1])})
    return sorted(out, key=lambda z: -z["size"])


def compare(img: np.ndarray, refs: list[np.ndarray]) -> dict:
    """Against the approved references: best match counts, because angles differ."""
    if not available():
        return {"outcome": "warn", "text": "face comparison is not installed"}
    rv = [f[0]["vec"] for f in (faces(r) for r in refs) if f]
    if not rv:
        return {"outcome": "warn", "text": "no face could be read in the references"}
    f = faces(img)
    if not f:
        return {"outcome": "warn", "text": "no face found here (profile, darkness, hair or distance); judge it by eye"}
    s = max(float(np.dot(f[0]["vec"], v)) for v in rv)
    small = f[0]["size"] < .08
    return {"outcome": "pass" if s >= SAME and not small else "warn", "similarity": round(s, 3), "faces": len(f),
            "text": f"similarity {s:.2f} to the approved face" + (" (small in frame, less reliable)" if small else "") + (f"; {len(f)} faces in the picture" if len(f) > 1 else "") + ("" if s >= SAME else "; look closely" if s >= LOW else "; probably a different face")}


def track(path: Path, refs: list[np.ndarray], step=6) -> dict:
    """Similarity through a clip, every sixth frame."""
    import av
    if not available():
        return {"outcome": "warn", "text": "face comparison is not installed"}
    rv = [f[0]["vec"] for f in (faces(r) for r in refs) if f]
    if not rv:
        return {"outcome": "warn", "text": "no face could be read in the references"}
    out, seen = [], 0
    with av.open(str(path)) as c:
        for i, fr in enumerate(c.decode(video=0)):
            if i % step:
                continue
            seen += 1
            f = faces(fr.to_ndarray(format="bgr24"))
            if f:
                out.append((i, max(float(np.dot(f[0]["vec"], v)) for v in rv)))
    if len(out) < 3:
        return {"outcome": "warn", "text": f"a face was readable in {len(out)} of {seen} sampled frames; judge identity by eye", "series": out}
    lo = min(out, key=lambda x: x[1])
    drop = float(np.median([s for _, s in out])) - lo[1]
    ok = lo[1] >= LOW and drop < .25
    return {"outcome": "pass" if ok else "warn", "series": [(i, round(s, 3)) for i, s in out], "text": f"readable in {len(out)} of {seen} sampled frames; lowest similarity {lo[1]:.2f} at frame {lo[0]}" + ("" if ok else "; look at those frames")}
