"""Local audio inspection: numbers only. It never claims to have heard a performance."""
from __future__ import annotations

import difflib
import re
import subprocess
from pathlib import Path

import numpy as np
import pyloudnorm

VERSION = "a0.1"
NUM = {str(i): w for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}


def decode(path: Path, sr=48000) -> np.ndarray:
    """Everything becomes 48 kHz float, shape (samples, channels)."""
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    ch = int((p.stdout.strip() or "1").split(",")[0])
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, timeout=300)
    a = np.frombuffer(r.stdout, np.float32)
    return a.reshape(-1, ch) if len(a) else np.zeros((0, ch), np.float32)


def words(s):
    s = re.sub(r"\[[^\]]*\]", " ", s.lower().replace("’", "'"))
    return [NUM.get(w, w) for w in re.findall(r"[a-z0-9']+", s)]


def compare_words(script: str, heard: str) -> list[dict]:
    a, b = words(script), words(heard)
    return [{"op": o, "script": a[i:j], "heard": b[k:m]} for o, i, j, k, m in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o != "equal"]


def measure(path: Path, probe: dict | None = None, expect: dict | None = None) -> dict:
    expect = expect or {}
    x = decode(path)
    items, sr = [], 48000
    if len(x) < sr // 20:
        return {"version": VERSION, "outcome": "fail", "items": [("length", "fail", "almost no audio")], "facts": {}}
    m = x.mean(1)
    dur = len(m) / sr
    fr = sr // 50
    e = 20 * np.log10(np.sqrt(np.mean(m[: len(m) // fr * fr].reshape(-1, fr) ** 2, 1)) + 1e-9)
    floor, top = float(np.percentile(e, 8)), float(np.percentile(e, 95))
    active = e > max(floor + 10, top - 35)
    idx = np.where(active)[0]
    lead, tail = (idx[0] / 50, dur - (idx[-1] + 1) / 50) if len(idx) else (dur, 0)
    peak = float(np.abs(x).max())
    clipped = int((np.abs(x) > .999).sum())
    dc = float(m.mean())
    loud = float(pyloudnorm.Meter(sr).integrated_loudness(x)) if dur > .5 else float("nan")
    d = np.abs(np.diff(m))
    clicks = int(((d > .35) & (np.concatenate([[0], d[:-1]]) < .08)).sum())
    gaps = []
    if len(idx):
        s = None
        for i in range(idx[0], idx[-1] + 1):
            if not active[i] and s is None:
                s = i
            if active[i] and s is not None:
                if i - s > 35:
                    gaps.append((round(s / 50, 2), round(i / 50, 2)))
                s = None
    spec = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2
    f = np.fft.rfftfreq(len(m), 1 / sr)
    cum = np.cumsum(spec) / (spec.sum() + 1e-12)
    bw = float(f[np.searchsorted(cum, .9995)])
    fmt = ""
    if probe:
        s0 = next((s for s in probe["streams"] if s["codec_type"] == "audio"), {})
        fmt = f"{s0.get('codec_name')} {s0.get('sample_rate')} Hz {int(s0.get('bit_rate') or probe['format'].get('bit_rate') or 0) // 1000} kbps"
    items.append(("clipping", "fail" if clipped > 20 else "warn" if clipped else "pass", f"{clipped} samples at full scale, peak {20 * np.log10(peak + 1e-9):.1f} dBFS"))
    items.append(("noise floor", "pass" if floor < -55 else "warn" if floor < -42 else "fail", f"{floor:.0f} dB in the quietest parts"))
    items.append(("clicks", "pass" if clicks == 0 else "warn", f"{clicks} possible clicks"))
    items.append(("start and end", "pass" if lead < 1 and tail < 2 else "warn", f"{lead:.2f}s before, {tail:.2f}s after"))
    items.append(("DC offset", "pass" if abs(dc) < .01 else "warn", f"{dc:.4f}"))
    if expect.get("duration"):
        lo, hi = expect["duration"]
        items.append(("duration", "pass" if lo <= dur <= hi else "warn", f"{dur:.2f}s, wanted {lo}-{hi}s"))
    if expect.get("silent_below"):
        items.append(("silence", "pass" if top < expect["silent_below"] else "fail", f"loudest part {top:.0f} dB"))
    o = [i[1] for i in items]
    return {"version": VERSION, "outcome": "fail" if "fail" in o else "warn" if "warn" in o else "pass", "items": items,
            "facts": {"duration": round(dur, 3), "lufs": None if np.isnan(loud) else round(loud, 1), "peak_db": round(20 * np.log10(peak + 1e-9), 1), "floor_db": round(floor, 1),
                      "bandwidth_hz": int(bw), "channels": x.shape[1], "format": fmt, "pauses": gaps[:20], "speech_s": round(float(active.sum()) / 50, 2)},
            "envelope": [round(float(v), 1) for v in e[:: max(1, len(e) // 400)]],
            "examined": ["levels, noise, clicks, pauses, bandwidth"], "not_examined": ["performance, emotion, pronunciation quality (your ears)"]}


_model = None


def transcribe(path: Path) -> dict | None:
    """Local speech recognition, if its model has been installed. None = not available (reported, never assumed)."""
    global _model
    from ..core.db import data_dir
    folder = data_dir() / "models" / "whisper-base.en"
    if not (folder / "model.bin").exists():
        return None
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    if _model is None:
        _model = WhisperModel(str(folder), device="cpu", compute_type="int8")
    x = decode(path, 16000).mean(1)
    seg, _ = _model.transcribe(x, language="en", beam_size=5, word_timestamps=True, condition_on_previous_text=False)
    ws = [{"w": w.word.strip(), "a": round(w.start, 2), "b": round(w.end, 2)} for s in seg for w in s.words]
    return {"text": " ".join(w["w"] for w in ws), "words": ws}


def inspect_voice(path, probe, script_text, expect=None) -> dict:
    rep = measure(path, probe, expect)
    tr = transcribe(path)
    if tr is None:
        rep["items"].append(("words against script", "warn", "speech recognition is not installed; listen for the words yourself"))
    else:
        rep["transcript"] = tr
        diff = compare_words(script_text, tr["text"])
        rep["word_differences"] = diff
        n = len(words(script_text))
        rep["items"].append(("words against script", "pass" if not diff else "warn",
                             "every word matches" if not diff else "; ".join(f"script '{' '.join(d['script']) or '-'}' / heard '{' '.join(d['heard']) or '-'}'" for d in diff[:6])))
        if tr["words"]:
            rep["facts"]["words_per_minute"] = round(60 * n / max(.3, tr["words"][-1]["b"] - tr["words"][0]["a"]))
        rep["examined"].append("a local transcript (it can mishear)")
    o = [i[1] for i in rep["items"]]
    rep["outcome"] = "fail" if "fail" in o else "warn" if "warn" in o else "pass"
    return rep
