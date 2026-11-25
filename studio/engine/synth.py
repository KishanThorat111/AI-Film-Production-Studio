"""Small sounds made from mathematics. No source material, so no rights to clear. Placeholders until recordings replace them."""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt

SR = 48000


def _bp(x, lo, hi):
    return sosfilt(butter(2, [lo, hi], "band", fs=SR, output="sos"), x)


def _noise(n, seed):
    return np.random.default_rng(seed).normal(0, 1, n)


def room_tone(seconds=14, seed=1):
    n = int(seconds * SR)
    a = sosfilt(butter(1, 180, "low", fs=SR, output="sos"), _noise(n, seed)) * 2.2 + _bp(_noise(n, seed + 1), 300, 2400) * .12
    hum = .02 * np.sin(2 * np.pi * 60 * np.arange(n) / SR) + .008 * np.sin(2 * np.pi * 120 * np.arange(n) / SR)
    return (a + hum) * .012


def attic_air(seconds=20, seed=5):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    w = .6 + .4 * np.sin(2 * np.pi * .07 * t + 1) * np.sin(2 * np.pi * .031 * t)
    return (_bp(_noise(n, seed), 60, 420) * w + _bp(_noise(n, seed + 1), 900, 3000) * .05) * .010


def ticks(seconds=20, seed=9, rate=1.7):
    """Metal expanding: irregular, dry, with small body resonances and rare double ticks."""
    r = np.random.default_rng(seed)
    n = int(seconds * SR)
    x = np.zeros(n)
    t = .3
    while t < seconds - .2:
        f = r.uniform(2100, 4300)
        m = int(.05 * SR)
        e = np.exp(-np.arange(m) / SR * r.uniform(120, 260))
        k = np.sin(2 * np.pi * f * np.arange(m) / SR) * e + .5 * np.sin(2 * np.pi * f * 1.62 * np.arange(m) / SR) * e ** 1.5 + .3 * r.normal(0, 1, m) * e ** 6
        a = r.uniform(.15, .5) * (1.5 if r.random() < .12 else 1)
        for d in ([0, r.uniform(.06, .13)] if r.random() < .18 else [0]):
            i = int((t + d) * SR)
            x[i:i + m] += k[: len(x[i:i + m])] * a
        t += r.exponential(1 / rate) * .8 + .12
    return x * .16


def box_fan(seconds=16, seed=3):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    blade = 1 + .22 * np.sin(2 * np.pi * 46 * t + .3 * np.sin(2 * np.pi * .4 * t)) + .07 * np.sin(2 * np.pi * 92 * t)
    motor = .25 * np.sin(2 * np.pi * 118 * t) + .12 * np.sin(2 * np.pi * 236 * t + 1)
    rattle = _bp(_noise(n, seed + 4), 2600, 4200) * (np.sin(2 * np.pi * 9.2 * t) > .86) * .12
    return (_bp(_noise(n, seed), 120, 1800) * blade + motor + rattle) * .035


def scrape(seconds=1.6, seed=12):
    n = int(seconds * SR)
    t = np.linspace(0, 1, n)
    e = np.interp(t, [0, .06, .3, .42, .7, 1], [0, .9, .5, 1, .35, 0])
    g = np.random.default_rng(seed)
    grain = np.repeat(g.uniform(.3, 1, n // 300 + 1), 300)[:n]
    return (_bp(_noise(n, seed), 400, 5200) * grain + _bp(_noise(n, seed + 1), 90, 240) * .8) * e * .10


SOUNDS = {"room tone, hallway": (room_tone, "room_tone", ["room tone", "hallway", "closed", "quiet"]), "attic air": (attic_air, "ambience", ["attic", "air", "wind", "low"]),
          "heater coils ticking": (ticks, "motif", ["tick", "metal", "expansion", "heater"]), "box fan": (box_fan, "ambience", ["fan", "box fan", "motor", "bedroom"]),
          "panel scrape, placeholder": (scrape, "foley", ["scrape", "panel", "placeholder"])}
